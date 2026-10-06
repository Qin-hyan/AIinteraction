/**
 * @file http_server.h
 * @brief HTTP 服务器 - Web 仪表盘 + 传感器 API
 *
 * Week 1: 基础 Web 仪表盘 + REST API
 * Week 2: 远程采集指令 + request_id 追踪 + 任务状态反馈
 */

#ifndef HTTP_SERVER_H
#define HTTP_SERVER_H

#include "esp_err.h"
#include "qma6100p.h"
#include "adc_button.h"
#include "camera_app.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ================================================================
 * Week 2: 采集任务状态枚举
 * ================================================================ */

/** @brief 手动采集任务状态 */
typedef enum {
    COLLECT_IDLE = 0,       /**< 无活跃任务 */
    COLLECT_SUBMITTED,      /**< 已提交，等待设备处理 */
    COLLECT_RECEIVED,       /**< 设备已接收命令 */
    COLLECT_COMPLETED,      /**< 采集完成，新数据可用 */
    COLLECT_FAILED,         /**< 采集失败（传感器错误） */
    COLLECT_TIMEOUT,        /**< 采集超时（设备无响应） */
} collect_status_t;

/* ================================================================
 * Week 2: 任务参数与证据字段
 * ================================================================ */

/** @brief 请求记录环形缓冲区容量 */
#define COLLECT_RECORD_MAX   6

/** @brief 完成条件窗口：服务器受理后必须在此时限内拿到设备结果 */
#define COLLECT_TIMEOUT_MS   5000

/** @brief 观测来源标记（不把缓存值伪装成新采集） */
#define OBS_SOURCE_LIVE      "live"      /**< 手动采集指令产生的真实读取 */
#define OBS_SOURCE_CADENCE   "cadence"   /**< 周期上报（板端周期采样）产生的真实读取 */
#define OBS_SOURCE_NONE      "none"      /**< 尚未产生观测 */
#define OBS_REQ_CADENCE      "cadence"   /**< 周期采样的关联请求号（非手动请求） */

/**
 * @brief 已保存观测（板上"已存数据"缓存）
 *
 * 只有真实传感器读取才会更新本结构；"刷新已存数据"只读它、不触发采集。
 * 时间字段均为 esp_timer 相对时间（时间质量 relative），板端无可靠墙钟。
 */
typedef struct {
    bool    valid;                /**< 是否已有观测 */
    char    record_id[24];        /**< 观测标识，如 obs-00013 */
    char    request_id[32];       /**< 关联请求号：req-xxx 或 cadence */
    char    source[12];           /**< live / cadence / none */
    int     seq;                  /**< 观测序号（板端入库读取序号） */
    int     accel_x;              /**< 本次观测 X 轴 (mg) */
    int     accel_y;              /**< 本次观测 Y 轴 (mg) */
    int     accel_z;              /**< 本次观测 Z 轴 (mg) */
    char    button[16];           /**< 本次观测按键状态 */
    int64_t observed_ms;          /**< 采集时间（相对，ms since boot） */
    int64_t received_ms;          /**< 入库时间（相对，ms since boot） */
} observation_t;

/**
 * @brief 单条请求记录：请求 → 设备回执 → 新观测
 */
typedef struct {
    char request_id[32];          /**< 请求号 */
    char status[16];              /**< completed / failed / timeout */
    char source[12];              /**< 观测来源 live / none */
    int  seq;                     /**< 本次观测序号（0=未产生观测） */
    int  accel_x;                 /**< 新观测 X (mg) */
    int  accel_y;
    int  accel_z;
    char button[16];
    int  submitted_ms;            /**< 服务器受理时间（相对 ms） */
    int  received_ms;             /**< 设备回执时间（相对 ms，-1=无回执） */
    int  completed_ms;            /**< 结果时间（相对 ms） */
    int  elapsed_ms;              /**< 受理 → 结果耗时 (ms) */
    bool has_observation;         /**< 是否带回与本次请求关联的新观测 */
} collect_record_t;

/* ================================================================
 * Week 3: Camera capture types
 * ================================================================ */

/** @brief 照片槽位最大数量 */
#define CAPTURE_SLOTS_MAX   9

/** @brief 照片槽位 */
typedef struct {
    char     request_id[32];   /**< 关联请求号 */
    bool     valid;            /**< 是否有效 */
    int64_t  captured_us;      /**< 拍摄时间 (µs since boot) */
    int      width;            /**< 图片宽度 */
    int      height;           /**< 图片高度 */
    size_t   size_bytes;       /**< JPEG 文件大小 */
    char     file_name[48];    /**< 文件名 */
    int      task_seq;         /**< 任务序号 */
    char     source[16];       /**< 来源: manual / auto */
    uint8_t *jpeg_buf;         /**< JPEG 数据 */
    size_t   jpeg_len;         /**< JPEG 数据长度 */
    bool     uploaded;         /**< 是否已上传 */
} capture_slot_t;

/** @brief 定时自动抓拍配置 */
typedef struct {
    bool     enabled;          /**< 是否启用 */
    int      interval_s;       /**< 抓拍周期 (秒) */
    int      batch_limit;      /**< 批次上限 (0=不限) */
    int      batch_count;      /**< 当前批次计数 */
    int64_t  last_capture_us;  /**< 上次抓拍时间 */
} auto_capture_cfg_t;

/** @brief 采集任务上下文（HTTP 任务与主循环共享） */
typedef struct {
    char              request_id[32];    /**< 唯一请求 ID */
    collect_status_t  status;            /**< 当前任务状态 */
    int64_t           submitted_at_us;   /**< 服务器受理时间 (µs since boot) */
    int64_t           received_at_us;    /**< 设备回执时间 (µs since boot, 0=未回执) */
    int64_t           completed_at_us;   /**< 结果时间 (µs since boot) */
    int64_t           deadline_us;       /**< 完成条件窗口截止 (µs since boot) */
    int               accel_x;           /**< 本次采集 X 轴 (mg) */
    int               accel_y;           /**< 本次采集 Y 轴 (mg) */
    int               accel_z;           /**< 本次采集 Z 轴 (mg) */
    char              button[16];        /**< 本次采集按键状态 */
    bool              auto_refresh;      /**< 周期上报开关（板端周期采样 + 页面刷新） */
    int               task_seq;          /**< 手动采集次数编号（重复点击可逐次区分） */
    int               obs_seq;           /**< 观测序号（板端入库读取次数） */
    int               direct_read_count; /**< 页面直读次数（不产生观测，单列留证） */
    int               poll_count;        /**< 周期采样次数 */
    observation_t     last;              /**< 已存观测（供"刷新已存数据"读取） */
    collect_record_t  records[COLLECT_RECORD_MAX]; /**< 请求记录环形缓冲区 */
    int               record_count;      /**< 有效记录条数 */
    int               record_head;       /**< 环形写指针 */
    /* Week 3: Camera capture */
    bool              capture_camera;    /**< 本次任务是否需要摄像头抓拍 */
    capture_slot_t    cap_slots[CAPTURE_SLOTS_MAX]; /**< 照片槽位（环形） */
    int               cap_head;          /**< 照片写指针 */
    int               cap_count;         /**< 已拍照片总数 */
    auto_capture_cfg_t auto_cap;         /**< 定时自动抓拍配置 */
    char              last_cap_request_id[32]; /**< 最后一次抓拍的请求号 */
} collect_task_t;

/**
 * @brief 传感器上下文（传递给 HTTP 服务器）
 */
typedef struct {
    qma6100p_handle_t    imu;        /**< QMA6100P 句柄 */
    adc_button_handle_t  btn;        /**< ADC 按键句柄 */
    const char          *device_id;  /**< 设备标识（目标设备） */
    const char          *sensor_src; /**< Week 2: 传感源说明（单位留证） */
    collect_task_t      *task;       /**< Week 2: 采集任务状态（共享） */
    camera_handle_t      cam;        /**< Week 3: 摄像头句柄 */
} sensor_ctx_t;

/**
 * @brief 启动 HTTP 服务器
 * @param[in] ctx 传感器上下文
 * @return ESP_OK 成功
 */
esp_err_t http_server_start(const sensor_ctx_t *ctx);

/**
 * @brief 停止 HTTP 服务器
 */
void http_server_stop(void);

#ifdef __cplusplus
}
#endif

#endif /* HTTP_SERVER_H */