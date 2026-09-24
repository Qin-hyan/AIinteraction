/**
 * @file http_server.c
 * @brief HTTP 服务器 - 内嵌 Web 仪表盘 + REST API
 */

#include "http_server.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "cJSON.h"
#include <string.h>

static const char *TAG = "HTTP";
static httpd_handle_t s_server = NULL;
static sensor_ctx_t  s_ctx = {0};
/* Embedded dashboard HTML */
static const char INDEX_HTML[] =
"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
"<title>ESP32-S3-EYE Sensor Dashboard</title>"
"<style>"
"*{margin:0;padding:0;box-sizing:border-box}"
"body{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;"
"min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px}"
"h1{color:#58a6ff;margin-bottom:8px;font-size:1.6em}"
".sub{color:#8b949e;font-size:.85em;margin-bottom:24px}"
".card{background:#161b22;border:1px solid #30363d;border-radius:8px;"
"padding:20px;margin:10px;width:100%;max-width:480px}"
".card h2{color:#f0f6fc;font-size:1.1em;margin-bottom:16px;"
"border-bottom:1px solid #30363d;padding-bottom:8px}"
".row{display:flex;justify-content:space-between;align-items:center;"
"padding:8px 0;border-bottom:1px solid #21262d}"
".row:last-child{border-bottom:none}"
".label{color:#8b949e;font-size:.9em}"
".value{font-size:1.3em;font-weight:bold;font-family:monospace}"
".value.x{color:#f78166}.value.y{color:#7ee787}.value.z{color:#a5d6ff}"
".btn-active{background:#238636;color:#fff;padding:4px 12px;border-radius:4px}"
".btn-none{color:#8b949e;font-style:italic}"
".status{display:flex;align-items:center;gap:8px;font-size:.8em;color:#8b949e;margin-top:16px}"
".dot{width:8px;height:8px;border-radius:50%;background:#3fb950;animation:pulse 1s infinite}"
"@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}"
".error{color:#f85149;text-align:center;padding:24px}"
"</style></head><body>"
"<h1>ESP32-S3-EYE Sensor Dashboard</h1>"
"<p class=\"sub\">Week 1: QMA6100P IMU + ADC Button - <span id=\"devid\"></span></p>"
"<div class=\"card\"><h2>Accelerometer (QMA6100P)</h2>"
"<div class=\"row\"><span class=\"label\">X Axis</span>"
"<span class=\"value x\" id=\"accX\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Y Axis</span>"
"<span class=\"value y\" id=\"accY\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Z Axis</span>"
"<span class=\"value z\" id=\"accZ\">--</span></div></div>"
"<div class=\"card\"><h2>Button State (ADC)</h2>"
"<div class=\"row\"><span class=\"label\">Current</span>"
"<span id=\"btnState\"><span class=\"btn-none\">No press</span></span></div></div>"
"<div class=\"status\"><span class=\"dot\"></span>"
"<span>Live &middot; Refresh: 500ms &middot; <span id=\"ts\">--</span></span></div>"
"<div class=\"error\" id=\"error\" style=\"display:none\"></div>"
"<script>var e=document.getElementById('error');"
"function fetchData(){fetch('/api/sensor').then(function(r){"
"if(!r.ok)throw new Error('HTTP '+r.status);return r.json();"
"}).then(function(d){e.style.display='none';"
"document.getElementById('devid').textContent=d.device_id||'';"
"document.getElementById('accX').textContent=(d.accel_x!=null?d.accel_x+' mg':'--');"
"document.getElementById('accY').textContent=(d.accel_y!=null?d.accel_y+' mg':'--');"
"document.getElementById('accZ').textContent=(d.accel_z!=null?d.accel_z+' mg':'--');"
"var bs=document.getElementById('btnState');"
"if(d.button&&d.button!=='NONE')bs.innerHTML='<span class=\\\"btn-active\\\">'+d.button+'</span>';"
"else bs.innerHTML='<span class=\\\"btn-none\\\">No press</span>';"
"document.getElementById('ts').textContent=new Date().toLocaleTimeString();"
"}).catch(function(ex){e.style.display='block';e.textContent='Error: '+ex.message;});}"
"fetchData();setInterval(fetchData,500);</script></body></html>";
/* ================================================================
 * HTTP Request Handlers
 * ================================================================ */

static esp_err_t root_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_send(req, INDEX_HTML, strlen(INDEX_HTML));
    return ESP_OK;
}

static esp_err_t sensor_api_handler(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "device_id",
        s_ctx.device_id ? s_ctx.device_id : "esp32s3-eye");
    if (s_ctx.imu) {
        qma6100p_acce_value_t val;
        if (qma6100p_get_acce(s_ctx.imu, &val) == ESP_OK) {
            cJSON_AddNumberToObject(root, "accel_x", (int)(val.acce_x * 1000));
            cJSON_AddNumberToObject(root, "accel_y", (int)(val.acce_y * 1000));
            cJSON_AddNumberToObject(root, "accel_z", (int)(val.acce_z * 1000));
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
    if (s_ctx.btn) {
        adc_button_t btn = adc_button_read(s_ctx.btn);
        cJSON_AddStringToObject(root, "button", adc_button_name(btn));
    } else {
        cJSON_AddStringToObject(root, "button", "disabled");
    }
    cJSON_AddNumberToObject(root, "uptime_ms",
                             (double)esp_timer_get_time() / 1000.0);
    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_str, strlen(json_str));
    cJSON_free(json_str);
    return ESP_OK;
}

static const httpd_uri_t uri_root = {
    .uri = "/", .method = HTTP_GET,
    .handler = root_handler, .user_ctx = NULL,
};
static const httpd_uri_t uri_sensor = {
    .uri = "/api/sensor", .method = HTTP_GET,
    .handler = sensor_api_handler, .user_ctx = NULL,
};

esp_err_t http_server_start(const sensor_ctx_t *ctx)
{
    if (!ctx) return ESP_ERR_INVALID_ARG;
    s_ctx = *ctx;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;
    config.max_uri_handlers = 8;
    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "HTTP start failed");
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
    }
}
