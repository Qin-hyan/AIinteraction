/**
 * @file http_server.c
 * @brief HTTP 服务器实现 - 内嵌 Web 仪表盘 + REST API
 *
 * 端点:
 *   GET  /            → 嵌入式网页仪表盘
 *   GET  /api/sensor  → JSON 传感器数据
 */

#include "http_server.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "cJSON.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "HTTP";
static httpd_handle_t s_server = NULL;
static sensor_ctx_t  s_ctx = {0};

/* ================================================================
 * 嵌入式 HTML 网页 (暗色主题仪表盘)
 * ================================================================ */
static const char INDEX_HTML[] =
"<!DOCTYPE html>\n"
"<html lang=\"zh-CN\">\n"
"<head>\n"
"<meta charset=\"UTF-8\">\n"
"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
"<title>ESP32-S3-EYE Sensor Dashboard</title>\n"
"<style>\n"
"*{margin:0;padding:0;box-sizing:border-box}\n"
"body{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;"
"min-height:100vh;display:flex;flex-direction:column;align-items:center;"
"padding:20px}\n"
"h1{color:#58a6ff;margin-bottom:8px;font-size:1.6em}\n"
".sub{color:#8b949e;font-size:.85em;margin-bottom:24px}\n"
".card{background:#161b22;border:1px solid #30363d;border-radius:8px;"
"padding:20px;margin:10px;width:100%;max-width:480px}\n"
".card h2{color:#f0f6fc;font-size:1.1em;margin-bottom:16px;"
"border-bottom:1px solid #30363d;padding-bottom:8px}\n"
".row{display:flex;justify-content:space-between;align-items:center;"
"padding:8px 0;border-bottom:1px solid #21262d}\n"
".row:last-child{border-bottom:none}\n"
".label{color:#8b949e;font-size:.9em}\n"
".value{font-size:1.3em;font-weight:bold;"
"font-family:'Cascadia Code',monospace}\n"
".value.x{color:#f78166}\n.value.y{color:#7ee787}\n"
".value.z{color:#a5d6ff}\n.value.btn{color:#d2a8ff}\n"
".btn-active{background:#238636;color:#fff;padding:4px 12px;"
"border-radius:4px;font-size:.9em}\n"
".btn-none{color:#8b949e;font-style:italic}\n"
".status{display:flex;align-items:center;gap:8px;"
"font-size:.8em;color:#8b949e;margin-top:16px}\n"
".dot{width:8px;height:8px;border-radius:50%;background:#3fb950;"
"animation:pulse 1s infinite}\n"
"@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}\n"
".error{color:#f85149;text-align:center;padding:24px}\n"
"</style>\n"
"</head>\n"
"<body>\n"
"<h1>ESP32-S3-EYE Sensor Dashboard</h1>\n"
"<p class=\"sub\">Week 1: QMA6100P IMU + ADC Button "
"— <span id=\"devid\"></span></p>\n"
"<div class=\"card\">\n"
"<h2>Accelerometer (QMA6100P)</h2>\n"
"<div class=\"row\"><span class=\"label\">X Axis</span>"
"<span class=\"value x\" id=\"accX\">--</span></div>\n"
"<div class=\"row\"><span class=\"label\">Y Axis</span>"
"<span class=\"value y\" id=\"accY\">--</span></div>\n"
"<div class=\"row\"><span class=\"label\">Z Axis</span>"
"<span class=\"value z\" id=\"accZ\">--</span></div>\n"
"<div class=\"row\"><span class=\"label\">Range</span>"
"<span style=\"color:#8b949e;font-size:.9em\">+/-8g</span></div>\n"
"</div>\n"
"<div class=\"card\">\n"
"<h2>Button State (ADC)</h2>\n"
"<div class=\"row\"><span class=\"label\">Current</span>"
"<span id=\"btnState\"><span class=\"btn-none\">No press</span></span></div>\n"
"<div class=\"row\"><span class=\"label\">ADC Voltage</span>"
"<span id=\"btnVoltage\" style=\"color:#8b949e;font-size:.9em\">-- mV</span></div>\n"
"</div>\n"
"<div class=\"status\">"
"<span class=\"dot\" id=\"dot\"></span>"
"<span>Live &middot; Refresh: 500ms &middot; "
"<span id=\"ts\">--</span></span></div>\n"
"<div class=\"error\" id=\"error\" style=\"display:none\"></div>\n"
"<script>\n"
"const ERR=document.getElementById('error');\n"
"async function fetchData(){\n"
"try{\n"
"const r=await fetch('/api/sensor');\n"
"if(!r.ok)throw new Error('HTTP '+r.status);\n"
"const d=await r.json();\n"
"ERR.style.display='none';\n"
"document.getElementById('devid').textContent=d.device_id||'';\n"
"document.getElementById('accX').textContent="
"(d.accel_x!=null?d.accel_x+' mg':'--');\n"
"document.getElementById('accY').textContent="
"(d.accel_y!=null?d.accel_y+' mg':'--');\n"
"document.getElementById('accZ').textContent="
"(d.accel_z!=null?d.accel_z+' mg':'--');\n"
"const bs=document.getElementById('btnState');\n"
"if(d.button&&d.button!=='NONE'){\n"
"bs.innerHTML='<span class=\"btn-active\">'+d.button+'</span>';\n"
"}else{\n"
"bs.innerHTML='<span class=\"btn-none\">No press</span>';\n"
"}\n"
"document.getElementById('btnVoltage').textContent="
/* ================================================================
 * HTTP 请求处理器
 * ================================================================ */

/** GET / → 仪表盘网页 */
static esp_err_t root_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_send(req, INDEX_HTML, strlen(INDEX_HTML));
    return ESP_OK;
}

/** GET /api/sensor → JSON 传感器数据 */
static esp_err_t sensor_api_handler(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();

    /* 设备标识 */
    cJSON_AddStringToObject(root, "device_id",
        s_ctx.device_id ? s_ctx.device_id : "esp32s3-eye");

    /* IMU 数据 */
    if (s_ctx.imu) {
        qma6100p_data_t imu_data;
        if (qma6100p_read_accel(s_ctx.imu, &imu_data) == ESP_OK) {
            cJSON_AddNumberToObject(root, "accel_x", imu_data.x);
            cJSON_AddNumberToObject(root, "accel_y", imu_data.y);
            cJSON_AddNumberToObject(root, "accel_z", imu_data.z);
        } else {
            cJSON_AddNullToObject(root, "accel_x");
            cJSON_AddNullToObject(root, "accel_y");
            cJSON_AddNullToObject(root, "accel_z");
        }
    } else {
        cJSON_AddNullToObject(root, "accel_x");
        cJSON_AddNullToObject(root, "accel_y");
        cJSON_AddNullToObject(root, "accel_z");
    }

    /* 按键状态 */
    if (s_ctx.btn) {
        adc_button_t btn = adc_button_read(s_ctx.btn);
        cJSON_AddStringToObject(root, "button", adc_button_name(btn));
    } else {
        cJSON_AddStringToObject(root, "button", "disabled");
    }

    /* 系统运行时间 (ms) */
    cJSON_AddNumberToObject(root, "uptime_ms",
                             (double)esp_timer_get_time() / 1000.0);

    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_str, strlen(json_str));
    cJSON_free(json_str);

    return ESP_OK;
}

/* ---- URI 路由表 ---- */
static const httpd_uri_t uri_root = {
    .uri      = "/",
    .method   = HTTP_GET,
    .handler  = root_handler,
    .user_ctx = NULL,
};

static const httpd_uri_t uri_sensor = {
    .uri      = "/api/sensor",
    .method   = HTTP_GET,
    .handler  = sensor_api_handler,
    .user_ctx = NULL,
};

/* ================================================================
 * 公开 API
 * ================================================================ */

esp_err_t http_server_start(const sensor_ctx_t *ctx)
{
    if (!ctx) return ESP_ERR_INVALID_ARG;
    s_ctx = *ctx;

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;
    config.max_uri_handlers = 8;

    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Start server failed: %s", esp_err_to_name(ret));
        return ret;
    }

    httpd_register_uri_handler(s_server, &uri_root);
    httpd_register_uri_handler(s_server, &uri_sensor);

    ESP_LOGI(TAG, "HTTP server started on port %d", config.server_port);
    return ESP_OK;
}

void http_server_stop(void)
{
    if (s_server) {
        httpd_stop(s_server);
        s_server = NULL;
        ESP_LOGI(TAG, "HTTP server stopped");
    }
}
"(d.btn_voltage_mv!=null?d.btn_voltage_mv+' mV':'-- mV');\n"
"document.getElementById('ts').textContent="
"new Date().toLocaleTimeString();\n"
"}catch(e){\n"
"ERR.style.display='block';\n"
"ERR.textContent='Connection error: '+e.message+' - retrying...';\n"
"}\n"
"}\n"
"fetchData();\nsetInterval(fetchData,500);\n"
"</script>\n"
"</body>\n"
"</html>";