/**
 * @file help_uplink.c
 * @brief Week 03: 求助事件上行推送实现
 *
 * 使用 ESP-IDF esp_http_client 将 help_event_t 构造为 JSON，
 * 通过 HTTP POST 推送到外部 VPS 服务器。
 *
 * 【教学测试专用，非真实紧急求助】
 */

#include "help_uplink.h"
#include "adc_button.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "cJSON.h"
#include <string.h>

static const char *TAG = "W3-UPLINK";

/* ================================================================
 * 内部辅助
 * ================================================================ */

/**
 * @brief 时间戳或 null 写入 JSON
 *
 * 值 = 0 表示"尚未发生"→ JSON null；
 * 值 > 0 写入数值（μs since boot）。
 */
static void add_timestamp_or_null(cJSON *obj, const char *key, int64_t value_us)
{
    if (value_us == 0) {
        cJSON_AddNullToObject(obj, key);
    } else {
        cJSON_AddNumberToObject(obj, key, (double)value_us);
    }
}

/* ================================================================
 * 公开 API
 * ================================================================ */

bool help_uplink_is_configured(void)
{
    /* CONFIG_HELP_VPS_URL 是 Kconfig string 类型，始终有定义；
     * 空字符串表示未配置。 */
    return CONFIG_HELP_VPS_URL[0] != '\0';
}

esp_err_t help_uplink_send_event(const help_event_t *event)
{
    if (!event) {
        ESP_LOGE(TAG, "invalid argument: event is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    /* ---- 检查 VPS URL 是否已配置 ---- */
    /* CONFIG_HELP_VPS_URL 是 Kconfig string 类型，始终有定义。 */
    if (CONFIG_HELP_VPS_URL[0] == '\0') {
        ESP_LOGW(TAG, "VPS URL is empty — skipping send");
        return ESP_ERR_INVALID_STATE;
    }

    /* ---- 1. 构造 JSON 载荷 ---- */
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        ESP_LOGE(TAG, "failed to create cJSON root");
        return ESP_FAIL;
    }

    /* 映射 help_event_t 所有字段到 JSON */
    cJSON_AddStringToObject(root, "help_id", event->help_id);
    cJSON_AddStringToObject(root, "status",
                            help_status_name(event->status));
    cJSON_AddStringToObject(root, "trigger_button",
                            adc_button_name(event->trigger_button));
    cJSON_AddNumberToObject(root, "event_counter",
                            event->event_counter);
    add_timestamp_or_null(root, "triggered_at_us",
                          event->triggered_at_us);
    /* sent_at_us = 设备端 HTTP POST 完成时刻，非 VPS 上传时间 */
    add_timestamp_or_null(root, "sent_at_us",
                          event->sent_at_us);
    add_timestamp_or_null(root, "remote_received_at_us",
                          event->remote_received_at_us);
    add_timestamp_or_null(root, "cancelled_at_us",
                          event->cancelled_at_us);
    cJSON_AddBoolToObject(root, "remote_confirmed",
                          event->remote_confirmed);
    cJSON_AddStringToObject(root, "remote_note",
                            event->remote_note);

    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    if (!json_str) {
        ESP_LOGE(TAG, "failed to print JSON");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "sending event %s to %s",
             event->help_id, CONFIG_HELP_VPS_URL);

    /* ---- 2. 初始化 HTTP 客户端 ---- */
    esp_http_client_config_t http_cfg = {
        .url         = CONFIG_HELP_VPS_URL,
        .method      = HTTP_METHOD_POST,
        .timeout_ms  = CONFIG_HELP_UPLINK_TIMEOUT_MS,
        .keep_alive_enable = false,
    };

    esp_http_client_handle_t client = esp_http_client_init(&http_cfg);
    if (!client) {
        ESP_LOGE(TAG, "failed to init HTTP client");
        cJSON_free(json_str);
        return ESP_FAIL;
    }

    /* 设置 Content-Type 和请求体 */
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, json_str, strlen(json_str));

    /* ---- 3. 执行请求 ---- */
    esp_err_t err = esp_http_client_perform(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP POST failed for %s: %s (err=%d)",
                 event->help_id, esp_err_to_name(err), err);
        esp_http_client_cleanup(client);
        cJSON_free(json_str);
        return ESP_FAIL;
    }

    int status_code = esp_http_client_get_status_code(client);
    ESP_LOGI(TAG, "HTTP POST %d for event %s",
             status_code, event->help_id);

    /* 资源释放 */
    esp_http_client_cleanup(client);
    cJSON_free(json_str);

    /* ---- 4. 检查 HTTP 状态码 ---- */
    if (status_code < 200 || status_code >= 300) {
        ESP_LOGW(TAG, "VPS returned non-2xx status: %d for event %s",
                 status_code, event->help_id);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "event %s sent successfully (status=%d)",
             event->help_id, status_code);
    return ESP_OK;
}