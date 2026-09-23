/**
 * @file wifi_app.c
 * @brief Wi-Fi STA 模式实现
 */

#include "wifi_app.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "freertos/event_groups.h"
#include <string.h>

static const char *TAG = "WIFI";
static EventGroupHandle_t s_wifi_event_group;
static const int WIFI_CONNECTED_BIT = BIT0;
static const int WIFI_FAIL_BIT      = BIT1;
static char s_ip_str[16] = "0.0.0.0";

static void wifi_event_handler(void *arg,
                                esp_event_base_t base,
                                int32_t id, void *data)
{
    if (base == WIFI_EVENT) {
        if (id == WIFI_EVENT_STA_START) {
            esp_wifi_connect();
        } else if (id == WIFI_EVENT_STA_DISCONNECTED) {
            ESP_LOGW(TAG, "Disconnected, retrying...");
            esp_wifi_connect();
            xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *evt = (ip_event_got_ip_t *)data;
        snprintf(s_ip_str, sizeof(s_ip_str),
                 IPSTR, IP2STR(&evt->ip_info.ip));
        ESP_LOGI(TAG, "Got IP: %s", s_ip_str);
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

esp_err_t wifi_app_init(void)
{
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_cfg = {
        .sta = {
            .ssid = CONFIG_ESP_WIFI_SSID,
            .password = CONFIG_ESP_WIFI_PASSWORD,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Connecting to SSID: %s", CONFIG_ESP_WIFI_SSID);

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT, pdFALSE, pdFALSE,
        pdMS_TO_TICKS(30000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "Wi-Fi connected OK");
        return ESP_OK;
    }
    ESP_LOGE(TAG, "Wi-Fi connection failed!");
    return ESP_FAIL;
}

const char *wifi_app_get_ip(void)
{
    return s_ip_str;
}