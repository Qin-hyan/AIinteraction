/**
 * @file wifi_app.h
 * @brief Wi-Fi STA 模式管理
 */

#ifndef WIFI_APP_H
#define WIFI_APP_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 初始化 Wi-Fi STA 并连接路由器
 *
 * SSID 和密码在 menuconfig 中配置:
 *   "Example Wi-Fi SSID" / "Example Wi-Fi Password"
 *
 * @return ESP_OK 连接成功
 */
esp_err_t wifi_app_init(void);

/**
 * @brief 获取本机 IPv4 地址字符串
 * @return IP 地址 (如 "192.168.1.100")，失败返回 "0.0.0.0"
 */
const char *wifi_app_get_ip(void);

#ifdef __cplusplus
}
#endif

#endif /* WIFI_APP_H */