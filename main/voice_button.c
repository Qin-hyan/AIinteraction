/**
 * @file voice_button.c
 * @brief Week 05 Step 2B: PLAY 按键 → PC 端语音录音 HTTP 控制实现
 *
 * 详见 voice_button.h 的接口与契约说明。
 *
 * 【范围】本文件只做「按键 → HTTP 控制请求 + 本地已确认状态」：
 *   - 不采集音频、不做 ASR/LLM/TTS（属于 Week 05 后续步骤）
 *   - 不复用 help_uplink 的单一事件槽，不修改 Week 03 Help 状态机与 LED 反馈
 */

#include "voice_button.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_http_client.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "W5-VOICE";

/* ================================================================
 * 常量
 * ================================================================ */

/* 任务栈：esp_http_client 内部缓冲 + cJSON 解析 + 512B 响应缓冲 + URL/body 缓冲，
 * 取 5120 字节（help_uplink 用 4096 且不解析响应体，此处需更大余量）。 */
#define VOICE_TASK_STACK_SIZE   5120
/* 任务优先级 3：低于按键扫描任务（BTN_TASK_PRIORITY=5），
 * 请求最长阻塞 CONFIG_VOICE_HTTP_TIMEOUT_MS，不阻塞主循环（优先级 1）。 */
#define VOICE_TASK_PRIORITY     3
/* 队列长度 2：同时最多 1 个在途请求（在途时新请求会被拒绝），留 1 个容错余量。 */
#define VOICE_REQ_QUEUE_LEN     2
#define VOICE_BASE_URL_MAX      128   /* 生效基地址上限（超长拒绝启动，不截断） */
#define VOICE_URL_MAX           192   /* 基地址 + /api/voice/start|stop */
#define VOICE_BODY_MAX          256   /* 请求体 JSON（PC 端只要求是 JSON 对象） */
#define VOICE_RESP_BUF_SIZE     512   /* 响应体上限；超出按截断处理，不当成成功 */
#define VOICE_REQUEST_ID_MAX    24    /* voice-{ms}-{nnnn} */
#define VOICE_ERROR_CODE_MAX    24    /* PC 返回 JSON 的 error 字段 */

#define VOICE_DEVICE_ID         "esp32s3-eye-wk01"  /* 与 main.c DEVICE_ID 同一块板 */
#define VOICE_PATH_START        "/api/voice/start"
#define VOICE_PATH_STOP         "/api/voice/stop"

/* ================================================================
 * 内部状态（s_lock 保护；voice_task 是唯一写入者）
 * ================================================================ */

static TaskHandle_t      s_task      = NULL;
static QueueHandle_t     s_req_queue = NULL;
static SemaphoreHandle_t s_lock      = NULL;

static char s_base_url[VOICE_BASE_URL_MAX];
static bool s_configured = false;   /* 仅在 voice_button_start() 中写入，之后只读 */

/**
 * 已确认状态：只有收到成功 HTTP 响应（2xx + ok=true + state 匹配）才更新。
 * 与 s_status.state 的区别：请求在途时 s_status.state = REQUESTING，
 * 而 s_confirmed 始终保持最近一次被 PC 确认的状态（失败时保持不变）。
 */
static voice_ctrl_state_t  s_confirmed   = VOICE_CTRL_DISABLED;
static voice_ctrl_status_t s_status      = {0};  /* state 零值 = VOICE_CTRL_DISABLED */
static uint32_t            s_req_counter = 0;

/* ================================================================
 * HTTP 响应收集（事件回调累积响应体，与 PC 端 Step 2A 契约配合）
 * ================================================================ */

typedef struct {
    char   body[VOICE_RESP_BUF_SIZE];
    size_t len;
    bool   truncated;   /**< true = 响应体超过缓冲，已截断（不静默当作成功） */
} voice_resp_t;

static esp_err_t voice_http_event_handler(esp_http_client_event_t *evt)
{
    voice_resp_t *resp = (voice_resp_t *)evt->user_data;

    if (evt->event_id == HTTP_EVENT_ON_DATA && resp != NULL &&
        evt->data != NULL && evt->data_len > 0) {
        size_t space = sizeof(resp->body) - 1 - resp->len;
        size_t copy  = ((size_t)evt->data_len < space) ? (size_t)evt->data_len : space;

        if (copy > 0) {
            memcpy(resp->body + resp->len, evt->data, copy);
            resp->len += copy;
            resp->body[resp->len] = '\0';
        }
        if (copy < (size_t)evt->data_len) {
            resp->truncated = true;
        }
    }
    return ESP_OK;
}

/* ================================================================
 * 辅助函数
 * ================================================================ */

/** @brief 读取 JSON 字符串字段；不存在/非字符串/NULL → ""（cJSON 对 NULL 对象安全） */
static const char *json_string_or_empty(const cJSON *obj, const char *key)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (cJSON_IsString(item) && item->valuestring != NULL) {
        return item->valuestring;
    }
    return "";
}

/** @brief 生成可追踪关联 ID：voice-{ms since boot}-{counter:04d}（命名沿用 help-{ms}-{nnnn}） */
static void make_request_id(char *out, size_t out_len)
{
    uint32_t n = 0;

    if (s_lock != NULL && xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
        n = ++s_req_counter;
        xSemaphoreGive(s_lock);
    }
    snprintf(out, out_len, "voice-%d-%04u",
             (int)(esp_timer_get_time() / 1000), (unsigned)n);
}

/**
 * @brief PC 端拒绝（非 2xx）时的明确说明与恢复提示
 *
 * 只打印说明，不修改任何本地状态：重复请求 / 状态不一致都不得被伪装成成功。
 */
static void log_pc_error_hint(const char *request_id, int http_status,
                              const char *error_code)
{
    if (strcmp(error_code, "already_recording") == 0) {
        ESP_LOGW(TAG, "[%s] PC 已在录音（本地确认状态可能不一致）：本地状态保持不变；"
                      "如需恢复请先在 PC 端调用 /api/voice/stop", request_id);
    } else if (strcmp(error_code, "not_recording") == 0) {
        ESP_LOGW(TAG, "[%s] PC 当前没有录音（本地确认状态可能不一致）：本地状态保持不变；"
                      "下次 PLAY 将重试「开始录音」", request_id);
    } else if (http_status == 503 || http_status == 500) {
        ESP_LOGW(TAG, "[%s] PC 端录音服务/设备异常（HTTP %d）：请检查 PC 端麦克风与服务日志",
                  request_id, http_status);
    }
}

/* ================================================================
 * 单次 HTTP 请求（仅在本模块任务中调用，可阻塞至超时）
 * ================================================================ */

/**
 * @brief 执行一次语音控制 HTTP POST，并按 Step 2A 契约校验响应
 *
 * 请求体为可选 JSON 对象（PC 端只要求「是 JSON 对象」），并附带
 * Content-Type: application/json —— PC 端据此解析 body（否则返回 400）。
 *
 * @param[in]  action         请求动作
 * @param[in]  request_id     关联 ID（日志与状态用）
 * @param[out] out_http_status HTTP 状态码；0 = 未取得响应
 * @param[out] out_error_code  PC 返回 JSON 的 error 字段（无 → ""）
 * @param[in]  error_code_len  out_error_code 缓冲长度
 * @param[out] out_frames      stop 成功时的帧数（可传 NULL）
 * @param[out] out_duration_s  stop 成功时的时长秒数（可传 NULL）
 *
 * @return
 *     - ESP_OK                      2xx 且响应体符合契约（ok=true 且 state 匹配）
 *     - VOICE_ERR_PC_REJECTED       非 2xx（PC 未受理）
 *     - VOICE_ERR_RESPONSE_INVALID  2xx 但响应体非 JSON 对象 / 缺少契约要求字段
 *     - VOICE_ERR_STATE_MISMATCH    2xx 但 state 与请求动作期望不一致
 *     - 其它 esp_err_t              esp_http_client_perform() 的连接/超时等传输层错误
 */
static esp_err_t voice_post_action(voice_ctrl_action_t action, const char *request_id,
                                   int *out_http_status, char *out_error_code,
                                   size_t error_code_len,
                                   uint32_t *out_frames, double *out_duration_s)
{
    const bool   is_start       = (action == VOICE_ACTION_START);
    const char  *expected_state = is_start ? "recording" : "idle";
    char         url[VOICE_URL_MAX];
    char         body[VOICE_BODY_MAX];
    voice_resp_t resp;

    memset(&resp, 0, sizeof(resp));

    snprintf(url, sizeof(url), "%s%s", s_base_url,
             is_start ? VOICE_PATH_START : VOICE_PATH_STOP);
    snprintf(body, sizeof(body),
             "{\"event\":\"voice_btn\",\"button\":\"PLAY\",\"action\":\"%s\","
             "\"device_id\":\"%s\",\"request_id\":\"%s\",\"requested_at_ms\":%lld}",
             is_start ? "start" : "stop", VOICE_DEVICE_ID, request_id,
             (long long)(esp_timer_get_time() / 1000));

    esp_http_client_config_t cfg = {
        .url               = url,
        .method            = HTTP_METHOD_POST,
        .timeout_ms        = CONFIG_VOICE_HTTP_TIMEOUT_MS,
        .keep_alive_enable = false,
        .event_handler     = voice_http_event_handler,
        .user_data         = &resp,
    };

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        ESP_LOGE(TAG, "[%s] esp_http_client_init 失败（内存不足？）", request_id);
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, (int)strlen(body));

    esp_err_t err    = esp_http_client_perform(client);
    int       status = (err == ESP_OK) ? esp_http_client_get_status_code(client) : 0;

    if (out_http_status != NULL) {
        *out_http_status = status;
    }
    if (out_error_code != NULL && error_code_len > 0) {
        out_error_code[0] = '\0';
    }

    /* ---- 1. 传输层失败（连接拒绝 / 超时 / DNS / 无路由）：不伪装成功 ---- */
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "[%s] HTTP POST 失败：%s (err=0x%X, url=%s)",
                 request_id, esp_err_to_name(err), err, url);
        esp_http_client_cleanup(client);
        return err;
    }

    /* ---- 2. 非 2xx：PC 未受理（含重复请求 409） ---- */
    if (status < 200 || status >= 300) {
        cJSON      *root = cJSON_Parse(resp.body);
        const char *code = json_string_or_empty(root, "error");

        if (out_error_code != NULL && error_code_len > 0) {
            snprintf(out_error_code, error_code_len, "%s", code);
        }
        ESP_LOGW(TAG, "[%s] PC 未受理请求：HTTP %d, pc_error=%s, body_len=%u, truncated=%d",
                 request_id, status, code[0] != '\0' ? code : "(无)",
                 (unsigned)resp.len, (int)resp.truncated);
        log_pc_error_hint(request_id, status, code);
        cJSON_Delete(root);
        esp_http_client_cleanup(client);
        return VOICE_ERR_PC_REJECTED;
    }

    /* ---- 3. 2xx：必须先通过 Step 2A 契约校验，才允许更新本地确认状态 ---- */
    cJSON *root = cJSON_Parse(resp.body);
    if (!cJSON_IsObject(root)) {
        ESP_LOGE(TAG, "[%s] 2xx 但响应体不是合法 JSON 对象（len=%u, truncated=%d）→ 不更新本地状态",
                 request_id, (unsigned)resp.len, (int)resp.truncated);
        cJSON_Delete(root);
        esp_http_client_cleanup(client);
        return VOICE_ERR_RESPONSE_INVALID;
    }

    const char *code = json_string_or_empty(root, "error");
    if (out_error_code != NULL && error_code_len > 0) {
        snprintf(out_error_code, error_code_len, "%s", code);
    }

    if (!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "ok"))) {
        ESP_LOGE(TAG, "[%s] 2xx 但 ok != true（pc_error=%s）→ 不更新本地状态",
                 request_id, code[0] != '\0' ? code : "(无)");
        cJSON_Delete(root);
        esp_http_client_cleanup(client);
        return VOICE_ERR_RESPONSE_INVALID;
    }

    const char *state = json_string_or_empty(root, "state");
    if (strcmp(state, expected_state) != 0) {
        ESP_LOGE(TAG, "[%s] 响应 state=\"%s\" 与请求动作 %s 期望的 \"%s\" 不一致 → 不更新本地状态",
                 request_id, state[0] != '\0' ? state : "(缺失)",
                 voice_ctrl_action_name(action), expected_state);
        cJSON_Delete(root);
        esp_http_client_cleanup(client);
        return VOICE_ERR_STATE_MISMATCH;
    }

    if (is_start) {
        /* Step 2A：start 成功响应含 target_path（仅校验存在性，不打印路径） */
        if (!cJSON_IsString(cJSON_GetObjectItemCaseSensitive(root, "target_path"))) {
            ESP_LOGE(TAG, "[%s] 2xx 但 start 响应缺少 target_path → 契约不符，不更新本地状态",
                     request_id);
            cJSON_Delete(root);
            esp_http_client_cleanup(client);
            return VOICE_ERR_RESPONSE_INVALID;
        }
    } else {
        /* Step 2A：stop 成功响应含 wav 元数据（证明确实写出录音文件，只取数值字段） */
        const cJSON *wav = cJSON_GetObjectItemCaseSensitive(root, "wav");
        if (!cJSON_IsObject(wav)) {
            ESP_LOGE(TAG, "[%s] 2xx 但 stop 响应缺少 wav 元数据 → 契约不符，不更新本地状态",
                     request_id);
            cJSON_Delete(root);
            esp_http_client_cleanup(client);
            return VOICE_ERR_RESPONSE_INVALID;
        }
        if (out_frames != NULL) {
            const cJSON *frames = cJSON_GetObjectItemCaseSensitive(wav, "frames");
            *out_frames = cJSON_IsNumber(frames) ? (uint32_t)frames->valuedouble : 0;
        }
        if (out_duration_s != NULL) {
            const cJSON *dur = cJSON_GetObjectItemCaseSensitive(wav, "duration_seconds");
            *out_duration_s = cJSON_IsNumber(dur) ? dur->valuedouble : 0.0;
        }
    }
    ESP_LOGI(TAG, "[%s] PC 已受理：HTTP %d, state=%s", request_id, status, state);
    cJSON_Delete(root);
    esp_http_client_cleanup(client);
    return ESP_OK;
}

/* ================================================================
 * 结果应用：只有成功响应才更新「已确认状态」
 * ================================================================ */

/**
 * @brief 把一次请求的结果写入本地状态
 *
 * 成功（ESP_OK）→ 更新 s_confirmed 与展示状态；
 * 失败 → 保持 s_confirmed 不变（不伪装成已开始/已结束录音），
 *        只累加失败计数并记录失败原因，供日志与状态显示使用。
 */
static void voice_apply_result(voice_ctrl_action_t action, esp_err_t err,
                               int http_status, const char *error_code,
                               uint32_t frames, double duration_s,
                               const char *request_id)
{
    voice_ctrl_state_t confirmed;
    uint32_t           consecutive;
    uint32_t           busy_rejected;

    if (s_lock == NULL || xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) {
        return;
    }

    s_status.request_in_flight = false;
    s_status.last_error        = err;
    s_status.last_http_status  = http_status;
    s_status.last_result_ms    = esp_timer_get_time() / 1000;
    snprintf(s_status.last_error_code, sizeof(s_status.last_error_code), "%s",
             (error_code != NULL) ? error_code : "");
    snprintf(s_status.last_request_id, sizeof(s_status.last_request_id), "%s",
             (request_id != NULL) ? request_id : "");

    if (err == ESP_OK) {
        s_status.success_count++;
        s_status.consecutive_failures = 0;
        s_confirmed = (action == VOICE_ACTION_START) ? VOICE_CTRL_RECORDING
                                                    : VOICE_CTRL_IDLE;
        s_status.state           = s_confirmed;
        s_status.confirmed_state = s_confirmed;
        xSemaphoreGive(s_lock);

        if (action == VOICE_ACTION_START) {
            ESP_LOGI(TAG, "[%s] 已确认：PC 已开始录音 → 本地确认状态 RECORDING"
                          "（下次 PLAY 请求结束录音）", request_id);
        } else {
            ESP_LOGI(TAG, "[%s] 已确认：PC 已结束录音 → 本地确认状态 IDLE"
                          "（wav: frames=%u, %.2f s）",
                     request_id, (unsigned)frames, duration_s);
        }
        return;
    }

    /* 失败：保持已确认状态（下次 PLAY 重试同一动作），绝不伪装成功 */
    s_status.failure_count++;
    s_status.consecutive_failures++;
    s_status.state           = s_confirmed;
    s_status.confirmed_state = s_confirmed;
    confirmed     = s_confirmed;
    consecutive   = s_status.consecutive_failures;
    busy_rejected = s_status.busy_reject_count;
    xSemaphoreGive(s_lock);

    ESP_LOGW(TAG, "[%s] 请求 %s 失败：%s（HTTP=%d, pc_error=%s）；本地确认状态保持 %s（未修改）",
             request_id, voice_ctrl_action_name(action), voice_ctrl_err_name(err),
             http_status,
             (error_code != NULL && error_code[0] != '\0') ? error_code : "(无)",
             voice_ctrl_state_name(confirmed));
    if (consecutive >= 2) {
        ESP_LOGW(TAG, "[%s] 语音控制已连续失败 %u 次（累计抑制重复请求 %u 次）："
                      "请检查 CONFIG_VOICE_PC_URL、PC 端服务与防火墙；"
                      "文字入口与 Week 03 功能不受影响",
                 request_id, (unsigned)consecutive, (unsigned)busy_rejected);
    }
}

/* ================================================================
 * 后台任务（执行 HTTP 请求的唯一上下文）
 * ================================================================ */

static void voice_task(void *arg)
{
    (void)arg;
    voice_ctrl_action_t action;
    char                request_id[VOICE_REQUEST_ID_MAX];

    ESP_LOGI(TAG, "语音控制任务已启动（base=%s, timeout=%d ms, 队列长度=%d）",
             s_base_url, CONFIG_VOICE_HTTP_TIMEOUT_MS, VOICE_REQ_QUEUE_LEN);

    while (1) {
        if (xQueueReceive(s_req_queue, &action, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        int      http_status = 0;
        uint32_t frames      = 0;
        double   duration_s  = 0.0;
        char     error_code[VOICE_ERROR_CODE_MAX] = "";

        make_request_id(request_id, sizeof(request_id));
        ESP_LOGI(TAG, "[%s] 执行 PLAY 语音请求：%s（POST %s%s）", request_id,
                 voice_ctrl_action_name(action), s_base_url,
                 (action == VOICE_ACTION_START) ? VOICE_PATH_START : VOICE_PATH_STOP);

        esp_err_t err = voice_post_action(action, request_id, &http_status,
                                          error_code, sizeof(error_code),
                                          &frames, &duration_s);

        voice_apply_result(action, err, http_status, error_code,
                           frames, duration_s, request_id);
    }
}

/* ================================================================
 * 请求入队（按键/主循环路径调用，非阻塞）
 * ================================================================ */

/**
 * @brief 非阻塞地把一个动作放入请求队列
 *
 * 先置「在途」标记再入队，保证任务取到请求时本地状态已一致；
 * 同一时刻只允许 1 个在途请求：请求进行中再次触发会被明确拒绝并计数
 * （不排队、不改变已确认状态）。队列发送失败时回滚在途标记。
 *
 * @param[in] action 请求动作
 * @param[in] source 日志来源标记（如 "PLAY"）
 * @return true 已接受；false 未接受（未启用 / 在途 / 队列满）
 */
static bool voice_enqueue_action(voice_ctrl_action_t action, const char *source)
{
    voice_ctrl_state_t confirmed;

    if (!s_configured || s_req_queue == NULL || s_lock == NULL) {
        ESP_LOGW(TAG, "%s 忽略：语音控制未启用（CONFIG_VOICE_PC_URL 为空）", source);
        return false;
    }

    if (xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) {
        ESP_LOGE(TAG, "%s 忽略：获取状态锁失败", source);
        return false;
    }

    confirmed = s_confirmed;

    if (s_status.request_in_flight) {
        voice_ctrl_action_t in_flight = s_status.in_flight_action;
        uint32_t            rejected  = ++s_status.busy_reject_count;

        xSemaphoreGive(s_lock);
        ESP_LOGW(TAG, "%s 忽略（重复请求）：上一个语音请求（%s）仍在进行中；"
                      "本地确认状态保持 %s（未修改，累计抑制 %u 次）",
                 source, voice_ctrl_action_name(in_flight),
                 voice_ctrl_state_name(confirmed), (unsigned)rejected);
        return false;
    }

    s_status.request_in_flight = true;
    s_status.in_flight_action  = action;
    s_status.state             = VOICE_CTRL_REQUESTING;  /* 展示状态：在途，尚未确认 */
    xSemaphoreGive(s_lock);

    if (xQueueSend(s_req_queue, &action, 0) != pdTRUE) {
        if (xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
            s_status.request_in_flight = false;
            s_status.state             = s_confirmed;
            xSemaphoreGive(s_lock);
        }
        ESP_LOGE(TAG, "%s 失败：请求队列已满（未发出 HTTP 请求，本地状态未改变）", source);
        return false;
    }

    ESP_LOGI(TAG, "%s 已入队：%s（非阻塞；入队前本地确认状态=%s）", source,
             voice_ctrl_action_name(action), voice_ctrl_state_name(confirmed));
    return true;
}

/* ================================================================
 * 初始化
 * ================================================================ */

esp_err_t voice_button_start(void)
{
    if (s_task != NULL) {
        ESP_LOGW(TAG, "语音控制已启动，忽略重复初始化");
        return ESP_OK;
    }

    /* ---- 1. 读取并校验 PC 服务地址（来自 Kconfig，仓库内不含真实私有 IP） ---- */
    const char *url = CONFIG_VOICE_PC_URL;

    if (url[0] == '\0') {
        /* 未配置：功能关闭。此处尚无任务/锁，直接写状态是安全的。 */
        s_configured             = false;
        s_confirmed              = VOICE_CTRL_DISABLED;
        s_status.configured      = false;
        s_status.state           = VOICE_CTRL_DISABLED;
        s_status.confirmed_state = VOICE_CTRL_DISABLED;

        ESP_LOGW(TAG, "CONFIG_VOICE_PC_URL 未配置 → 语音控制关闭（不创建任务/队列）；"
                      "PLAY 按键保持 Week 03 教学测试事件行为；"
                      "启用方式：idf.py menuconfig → Week 05 Voice Control");
        return VOICE_ERR_NOT_CONFIGURED;
    }

    if (strncmp(url, "http://", 7) != 0 && strncmp(url, "https://", 8) != 0) {
        ESP_LOGE(TAG, "CONFIG_VOICE_PC_URL 必须以 http:// 或 https:// 开头（当前长度 %u）→ 语音控制关闭",
                 (unsigned)strlen(url));
        return ESP_ERR_INVALID_ARG;
    }

    if (strlen(url) >= sizeof(s_base_url)) {
        /* 明确失败，不静默截断（避免拼出错误 URL 却表现为可用） */
        ESP_LOGE(TAG, "CONFIG_VOICE_PC_URL 过长（%u ≥ %u 字节）→ 拒绝启动",
                 (unsigned)strlen(url), (unsigned)sizeof(s_base_url));
        return ESP_ERR_INVALID_SIZE;
    }

    snprintf(s_base_url, sizeof(s_base_url), "%s", url);
    for (size_t len = strlen(s_base_url); len > 0 && s_base_url[len - 1] == '/'; len--) {
        s_base_url[len - 1] = '\0';   /* 去掉结尾 '/'，避免拼出 //api/voice/start */
    }

    /* ---- 2. 互斥锁 + 请求队列 + 独立任务 ---- */
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        ESP_LOGE(TAG, "创建状态互斥锁失败");
        return ESP_ERR_NO_MEM;
    }

    s_req_queue = xQueueCreate(VOICE_REQ_QUEUE_LEN, sizeof(voice_ctrl_action_t));
    if (s_req_queue == NULL) {
        vSemaphoreDelete(s_lock);
        s_lock = NULL;
        ESP_LOGE(TAG, "创建语音请求队列失败");
        return ESP_ERR_NO_MEM;
    }

    s_configured = true;
    if (xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
        s_confirmed              = VOICE_CTRL_IDLE;
        s_status.configured      = true;
        s_status.state           = VOICE_CTRL_IDLE;
        s_status.confirmed_state = VOICE_CTRL_IDLE;
        snprintf(s_status.base_url, sizeof(s_status.base_url), "%s", s_base_url);
        xSemaphoreGive(s_lock);
    }

    if (xTaskCreate(voice_task, "voice_ctl", VOICE_TASK_STACK_SIZE, NULL,
                    VOICE_TASK_PRIORITY, &s_task) != pdPASS) {
        ESP_LOGE(TAG, "创建语音控制任务失败");
        if (xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
            s_status.configured      = false;
            s_status.state           = VOICE_CTRL_DISABLED;
            s_status.confirmed_state = VOICE_CTRL_DISABLED;
            xSemaphoreGive(s_lock);
        }
        vQueueDelete(s_req_queue);
        vSemaphoreDelete(s_lock);
        s_req_queue  = NULL;
        s_lock       = NULL;
        s_configured = false;
        s_confirmed  = VOICE_CTRL_DISABLED;
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "语音控制已启用：PC 服务 = %s（请求超时 %d ms；PLAY 专用于开始/结束录音）",
             s_base_url, CONFIG_VOICE_HTTP_TIMEOUT_MS);
    return ESP_OK;
}

/* ================================================================
 * 公开 API：查询与请求
 * ================================================================ */

bool voice_button_is_configured(void)
{
    /* s_configured 只在 voice_button_start() 中写入，之后只读（bool 读取原子） */
    return s_configured;
}

bool voice_button_request(voice_ctrl_action_t action)
{
    if (action != VOICE_ACTION_START && action != VOICE_ACTION_STOP) {
        ESP_LOGE(TAG, "无效动作 %d（未发出任何 HTTP 请求）", (int)action);
        return false;
    }
    return voice_enqueue_action(action, "语音请求");
}

bool voice_button_request_toggle(void)
{
    voice_ctrl_action_t action;
    voice_ctrl_state_t  confirmed;

    if (!s_configured || s_lock == NULL) {
        ESP_LOGW(TAG, "PLAY 忽略：语音控制未启用（CONFIG_VOICE_PC_URL 为空）");
        return false;
    }

    if (xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) {
        ESP_LOGE(TAG, "PLAY 忽略：获取状态锁失败");
        return false;
    }
    confirmed = s_confirmed;
    action    = (confirmed == VOICE_CTRL_RECORDING) ? VOICE_ACTION_STOP
                                                    : VOICE_ACTION_START;
    xSemaphoreGive(s_lock);

    ESP_LOGI(TAG, "PLAY 按下（本地确认状态=%s）→ 请求 %s",
             voice_ctrl_state_name(confirmed), voice_ctrl_action_name(action));
    return voice_enqueue_action(action, "PLAY");
}

void voice_button_get_status(voice_ctrl_status_t *out)
{
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));

    if (s_lock == NULL) {
        /* 尚未初始化：零值即 VOICE_CTRL_DISABLED */
        return;
    }
    if (xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) {
        ESP_LOGW(TAG, "读取语音状态快照失败（获取锁失败）");
        return;
    }
    *out = s_status;
    xSemaphoreGive(s_lock);
}

/* ================================================================
 * 公开 API：名称映射
 * ================================================================ */

const char *voice_ctrl_state_name(voice_ctrl_state_t state)
{
    switch (state) {
        case VOICE_CTRL_DISABLED:   return "DISABLED";
        case VOICE_CTRL_IDLE:       return "IDLE";
        case VOICE_CTRL_REQUESTING: return "REQUESTING";
        case VOICE_CTRL_RECORDING:  return "RECORDING";
        default:                    return "?";
    }
}

const char *voice_ctrl_action_name(voice_ctrl_action_t action)
{
    switch (action) {
        case VOICE_ACTION_START: return "START(开始录音)";
        case VOICE_ACTION_STOP:  return "STOP(结束录音)";
        default:                 return "?";
    }
}

const char *voice_ctrl_err_name(esp_err_t err)
{
    switch (err) {
        case ESP_OK:                     return "OK";
        case VOICE_ERR_NOT_CONFIGURED:   return "NOT_CONFIGURED(PC 地址未配置)";
        case VOICE_ERR_PC_REJECTED:      return "PC_REJECTED(PC 返回非 2xx)";
        case VOICE_ERR_RESPONSE_INVALID: return "RESPONSE_INVALID(响应不符合契约)";
        case VOICE_ERR_STATE_MISMATCH:   return "STATE_MISMATCH(响应 state 与请求不一致)";
        case VOICE_ERR_REQUEST_BUSY:     return "REQUEST_BUSY(已有请求在途)";
        case ESP_ERR_TIMEOUT:            return "TIMEOUT(请求超时)";
        case ESP_ERR_HTTP_CONNECT:       return "HTTP_CONNECT_FAILED(连接 PC 失败)";
        case ESP_ERR_INVALID_ARG:        return "INVALID_ARG(地址/参数非法)";
        case ESP_ERR_INVALID_SIZE:       return "INVALID_SIZE(地址过长)";
        case ESP_ERR_NO_MEM:             return "NO_MEM(内存不足)";
        default:                         return esp_err_to_name(err);
    }
}
