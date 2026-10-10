/**
 * @file voice_button.h
 * @brief Week 05 Step 2B: PLAY 按键 → PC 端语音录音 HTTP 控制（固件侧）
 *
 * 功能
 * ----
 *   有效 PLAY 按键按下（由 help_event.c 的 30 Hz 扫描任务去抖后入队）：
 *     第 1 次（本地已确认未录音） → POST {PC}/api/voice/start  开始录音
 *     第 2 次（本地已确认在录音） → POST {PC}/api/voice/stop   结束录音
 *   只有收到 HTTP 2xx 且响应体符合 PC 端契约（ok=true 且 state 与请求动作一致）时，
 *   才更新本地「已确认状态」。请求失败一律保持原状态——不伪造「已开始/已结束录音」。
 *
 * PC 端接口（Week 05 Step 2A: `service/voice_control_server.py`，本模块不改其实现）
 * ------------------------------------------------------------------------------
 *   POST /api/voice/start → 200 {ok:true,state:"recording",target_path:...}
 *                           409 already_recording / 400 invalid_request /
 *                           503 audio_device_error / 500 ...
 *   POST /api/voice/stop  → 200 {ok:true,state:"idle",wav:{frames,...}}
 *                           409 not_recording / 422 empty_recording / 503 / 500
 *   请求体可选；带 body 时必须是 JSON 对象，且必须带
 *   Content-Type: application/json（否则 PC 端返回 400 invalid_request）。
 *
 * 非阻塞设计
 * ----------
 *   主循环只做非阻塞入队（voice_button_request_toggle）；HTTP 请求由独立 FreeRTOS
 *   任务（优先级 3 < 按键扫描任务优先级 5）执行，最长阻塞
 *   CONFIG_VOICE_HTTP_TIMEOUT_MS（默认 5000 ms）后返回，按键扫描与主循环不受阻塞。
 *   同一时刻只允许 1 个在途请求：请求在途时再次按 PLAY 会被明确拒绝并记录日志
 *   （计入 busy_reject_count），既不排队也不改变本地状态。
 *   本模块不复用 help_uplink 的单一事件槽，也不改变 Week 03 Help 状态机。
 *
 * 开关与配置（Kconfig）
 * --------------------
 *   CONFIG_VOICE_PC_URL（默认空）：
 *     空 → 功能关闭：不创建任务/队列，启动时打印明确提示，
 *          PLAY 按键行为与 Week 03 完全一致（仍触发教学测试事件）；
 *     非空 → 形如 http://192.168.x.x:8080（不含路径），固件自动追加
 *          /api/voice/start 或 /api/voice/stop。
 *   本模块不读取/不写入任何凭据；仓库内不得写入真实电脑私有 IP。
 *
 * 典型用法（main.c）
 * ------------------
 *   voice_button_start();                        // 未配置时返回 VOICE_ERR_NOT_CONFIGURED
 *   if (btn == BTN_PLAY && voice_button_is_configured()) {
 *       voice_button_request_toggle();           // 非阻塞；在途请求会被拒绝并记日志
 *   }
 *   voice_ctrl_status_t st;
 *   voice_button_get_status(&st);                // 供状态显示（Step 7）使用
 */

#ifndef VOICE_BUTTON_H
#define VOICE_BUTTON_H

#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ================================================================
 * 模块自定义错误码（esp_err_t 用户自定义区，0x10000 起）
 * ================================================================ */

#define VOICE_ERR_NOT_CONFIGURED   ((esp_err_t)0x10001) /**< PC 地址未配置（功能关闭） */
#define VOICE_ERR_PC_REJECTED      ((esp_err_t)0x10002) /**< PC 返回非 2xx（未受理） */
#define VOICE_ERR_RESPONSE_INVALID ((esp_err_t)0x10003) /**< 2xx 但响应体不符合契约 */
#define VOICE_ERR_STATE_MISMATCH   ((esp_err_t)0x10004) /**< 2xx 但 state 与请求动作不一致 */
#define VOICE_ERR_REQUEST_BUSY     ((esp_err_t)0x10005) /**< 已有在途请求，重复请求被拒绝 */

/* ================================================================
 * 动作与状态
 * ================================================================ */

/** @brief 语音控制动作（与 PC 端 /api/voice/{start,stop} 一一对应） */
typedef enum {
    VOICE_ACTION_START = 1, /**< 开始录音 → POST /api/voice/start */
    VOICE_ACTION_STOP  = 2, /**< 结束录音 → POST /api/voice/stop  */
} voice_ctrl_action_t;

/**
 * @brief 本地（设备侧）语音控制状态
 *
 * VOICE_CTRL_REQUESTING 表示「有请求在途」，此刻 state 不是已确认状态；
 * 已确认状态单独保存在 voice_ctrl_status_t::confirmed_state 中。
 */
typedef enum {
    VOICE_CTRL_DISABLED = 0, /**< CONFIG_VOICE_PC_URL 未配置，语音控制关闭 */
    VOICE_CTRL_IDLE,         /**< 已确认：PC 未在录音 */
    VOICE_CTRL_REQUESTING,   /**< 请求在途，尚未收到 PC 响应（状态未确认） */
    VOICE_CTRL_RECORDING,    /**< 已确认：PC 已在录音 */
} voice_ctrl_state_t;

/* ================================================================
 * 状态快照（供日志/状态显示；全部字段由模块内部锁保护）
 * ================================================================ */

/** @brief 语音控制状态快照（voice_button_get_status() 读取一次即一致性快照） */
typedef struct {
    bool                configured;        /**< CONFIG_VOICE_PC_URL 是否已配置 */
    voice_ctrl_state_t  state;             /**< 展示状态；在途时为 VOICE_CTRL_REQUESTING */
    voice_ctrl_state_t  confirmed_state;   /**< 已确认状态（只有成功响应才更新） */
    bool                request_in_flight; /**< 是否有 HTTP 请求在途 */
    voice_ctrl_action_t in_flight_action;  /**< 在途请求的动作 */
    char                base_url[128];     /**< 生效的 PC 服务基地址（来自 Kconfig） */
    uint32_t            success_count;     /**< 成功次数（2xx 且动作确认一致） */
    uint32_t            failure_count;     /**< 失败次数（连接/超时/非 2xx/响应不符） */
    uint32_t            consecutive_failures; /**< 连续失败次数（成功时清零） */
    uint32_t            busy_reject_count; /**< 因在途请求而被拒绝的 PLAY 按键次数 */
    esp_err_t           last_error;        /**< 最近一次结果错误码（ESP_OK = 成功） */
    int                 last_http_status;  /**< 最近一次 HTTP 状态码；0 = 未取得响应 */
    char                last_error_code[24]; /**< PC 返回 JSON 的 error 字段；无 = "" */
    char                last_request_id[24]; /**< 最近一次请求关联 ID: voice-{ms}-{nnnn} */
    int64_t             last_result_ms;    /**< 最近一次请求完成时刻 (ms since boot) */
} voice_ctrl_status_t;

/* ================================================================
 * 公开 API
 * ================================================================ */

/**
 * @brief 初始化语音控制（校验 Kconfig 地址 → 创建互斥锁/队列/独立任务）
 *
 * 未配置 CONFIG_VOICE_PC_URL 时**不创建任何资源**，打印明确提示并返回
 * VOICE_ERR_NOT_CONFIGURED（此时 PLAY 按键保持 Week 03 行为）。
 *
 * @return
 *     - ESP_OK                      已启用
 *     - VOICE_ERR_NOT_CONFIGURED    PC 地址为空（功能关闭）
 *     - ESP_ERR_INVALID_ARG         地址缺少 http:// 或 https:// 前缀
 *     - ESP_ERR_INVALID_SIZE        地址过长（拒绝截断）
 *     - ESP_ERR_NO_MEM              互斥锁/队列/任务创建失败
 */
esp_err_t voice_button_start(void);

/**
 * @brief 语音控制是否已启用（即 CONFIG_VOICE_PC_URL 非空且初始化成功）
 *
 * main.c 据此决定 PLAY 按键的路由：为 false 时 PLAY 完全走 Week 03 分支。
 *
 * @return true 已启用；false 未启用（功能关闭）
 */
bool voice_button_is_configured(void);

/**
 * @brief 按「已确认状态」自动选择动作并入队（PLAY 按键语义，非阻塞）
 *
 * 已确认未录音 → 入队 VOICE_ACTION_START；已确认在录音 → 入队 VOICE_ACTION_STOP。
 * 请求在途时拒绝本次请求（计入 busy_reject_count，不排队、不改变本地状态）。
 *
 * @return true 请求已被接受（HTTP 将在独立任务中执行）；false 未接受（附日志说明原因）
 */
bool voice_button_request_toggle(void);

/**
 * @brief 入队一个显式动作（非阻塞，供后续步骤/测试使用）
 *
 * @param[in] action  VOICE_ACTION_START 或 VOICE_ACTION_STOP
 * @return true 请求已被接受；false 未接受（未启用/在途/队列满）
 */
bool voice_button_request(voice_ctrl_action_t action);

/**
 * @brief 读取状态快照（非阻塞，加锁拷贝）
 * @param[out] out 输出快照；out 为 NULL 时不动作
 */
void voice_button_get_status(voice_ctrl_status_t *out);

/**
 * @brief 状态/动作/错误码的可读名称（日志与状态显示用）
 */
const char *voice_ctrl_state_name(voice_ctrl_state_t state);
const char *voice_ctrl_action_name(voice_ctrl_action_t action);
const char *voice_ctrl_err_name(esp_err_t err);

#ifdef __cplusplus
}
#endif

#endif /* VOICE_BUTTON_H */