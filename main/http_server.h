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

/** @brief 采集任务上下文 */
typedef struct {
    char              request_id[32];    /**< 唯一请求 ID */
    collect_status_t  status;            /**< 当前任务状态 */
    int64_t           submitted_at_us;   /**< 提交时间戳 (micros) */
    int64_t           completed_at_us;   /**< 完成时间戳 (micros) */
    int               accel_x;           /**< 本次采集 X 轴 (mg) */
    int               accel_y;           /**< 本次采集 Y 轴 (mg) */
    int               accel_z;           /**< 本次采集 Z 轴 (mg) */
    char              button[16];        /**< 本次采集按键状态 */
    bool              auto_refresh;      /**< 周期自动刷新是否启用 */
} collect_task_t;

/**
 * @brief 传感器上下文（传递给 HTTP 服务器）
 */
typedef struct {
    qma6100p_handle_t    imu;        /**< QMA6100P 句柄 */
    adc_button_handle_t  btn;        /**< ADC 按键句柄 */
    const char          *device_id;  /**< 设备标识 */
    collect_task_t      *task;       /**< Week 2: 采集任务状态（共享） */
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