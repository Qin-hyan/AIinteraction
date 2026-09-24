/**
 * @file http_server.h
 * @brief HTTP 服务器 - Web 仪表盘 + 传感器 API
 */

#ifndef HTTP_SERVER_H
#define HTTP_SERVER_H

#include "esp_err.h"
#include "qma6100p.h"
#include "adc_button.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 传感器上下文（传递给 HTTP 服务器）
 */
typedef struct {
    qma6100p_handle_t    imu;        /**< QMA6100P 句柄 */
    adc_button_handle_t  btn;        /**< ADC 按键句柄 */
    const char          *device_id;  /**< 设备标识 */
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