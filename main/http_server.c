/**
 * @file http_server.c
 * @brief HTTP Server - Web Dashboard + REST API
 * Week 1: Basic dashboard + sensor API
 * Week 2: Remote collect + request_id + task status
 */
#include "http_server.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "cJSON.h"
#include <string.h>
#include <stdio.h>
static const char *TAG = "HTTP";
static httpd_handle_t s_server = NULL;
static sensor_ctx_t  s_ctx = {0};
static const char *collect_status_name(collect_status_t s) {
 switch(s){case COLLECT_IDLE:return "idle";case COLLECT_SUBMITTED:return "submitted";case COLLECT_RECEIVED:return "received";case COLLECT_COMPLETED:return "completed";case COLLECT_FAILED:return "failed";case COLLECT_TIMEOUT:return "timeout";default:return "unknown";}
}
/* === Week 2 INDEX_HTML (CSS only) === */
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
"padding:20px;margin:10px;width:100%;max-width:520px}"
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
".status-bar{display:flex;align-items:center;gap:8px;font-size:.8em;color:#8b949e;"
"margin-top:16px;flex-wrap:wrap}"
".dot{width:8px;height:8px;border-radius:50%;background:#3fb950;animation:pulse 1s infinite}"
".dot.paused{background:#d29922;animation:none}"
"@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}"
".error{color:#f85149;text-align:center;padding:24px}"
".collect-btn{display:block;width:100%;padding:14px 20px;margin:12px 0;"
"border:none;border-radius:6px;font-size:1.05em;font-weight:bold;cursor:pointer;"
"transition:background .2s}"
".collect-btn.ready{background:#238636;color:#fff}"
".collect-btn.ready:hover{background:#2ea043}"
".collect-btn.busy{background:#30363d;color:#8b949e;cursor:not-allowed}"
".task-status{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;"
"border-radius:20px;font-size:.85em;font-weight:600;margin:4px 0}"
".task-status.submitted{background:#1a2332;color:#a5d6ff;border:1px solid #1f6feb}"
".task-status.received{background:#1a2332;color:#d2a8ff;border:1px solid #8256d0}"
".task-status.completed{background:#12261e;color:#7ee787;border:1px solid #238636}"
".task-status.failed{background:#2d1216;color:#f85149;border:1px solid #da3633}"
".task-status.timeout{background:#2d1c12;color:#d29922;border:1px solid #9e6a03}"
".task-spinner{width:14px;height:14px;border:2px solid #30363d;"
"border-top:2px solid #58a6ff;border-radius:50%;animation:spin .8s linear infinite}"
"@keyframes spin{to{transform:rotate(360deg)}}"
".rid{font-family:monospace;font-size:.75em;color:#484f58}"
".collect-result{margin-top:16px;padding:16px;background:#0d1117;"
"border:1px solid #30363d;border-radius:8px;display:none}"
".collect-result.show{display:block}"
".collect-result h3{color:#f0f6fc;font-size:.95em;margin-bottom:12px}"
".toggle-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0}"
".toggle{position:relative;display:inline-block;width:44px;height:24px}"
".toggle input{opacity:0;width:0;height:0}"
".toggle-slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;"
"background:#30363d;border-radius:24px;transition:.3s}"
".toggle-slider:before{position:absolute;content:'';height:18px;width:18px;"
"left:3px;bottom:3px;background:#8b949e;border-radius:50%;transition:.3s}"
".toggle input:checked+.toggle-slider{background:#238636}"
".toggle input:checked+.toggle-slider:before{transform:translateX(20px);background:#fff}"
"</style></head><body>"
"<h1>ESP32-S3-EYE Sensor Dashboard</h1>"
"<p class=\"sub\">Week 2: 远程采集指令 + 执行结果反馈 <span id=\"devid\"></span></p>"
"<div class=\"card\"><h2>📡 远程采集指令</h2>"
"<button class=\"collect-btn ready\" id=\"collectBtn\" "
"onclick=\"triggerCollect()\">⚡ 采集一次最新数据</button>"
"<div id=\"taskInfo\" style=\"display:none\">"
"<div style=\"margin-top:8px\"><span class=\"task-status idle\" id=\"taskStatus\">"
"—</span></div>"
"<div class=\"rid\">Request ID: <span id=\"reqId\">—</span></div></div>"
"<div class=\"collect-result\" id=\"collectResult\">"
"<h3>📊 本次新观测</h3>"
"<div class=\"row\"><span class=\"label\">X Axis</span>"
"<span class=\"value x\" id=\"cAccX\">—</span></div>"
"<div class=\"row\"><span class=\"label\">Y Axis</span>"
"<span class=\"value y\" id=\"cAccY\">—</span></div>"
"<div class=\"row\"><span class=\"label\">Z Axis</span>"
"<span class=\"value z\" id=\"cAccZ\">—</span></div>"
"<div class=\"row\"><span class=\"label\">Button</span>"
"<span id=\"cBtn\">—</span></div>"
"<div class=\"row\"><span class=\"label\">耗时</span>"
"<span id=\"cElapsed\">—</span></div></div></div>"
"<div class=\"card\"><h2>📐 实时传感器 (周期刷新)</h2>"
"<div class=\"row\"><span class=\"label\">X Axis</span>"
"<span class=\"value x\" id=\"accX\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Y Axis</span>"
"<span class=\"value y\" id=\"accY\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Z Axis</span>"
"<span class=\"value z\" id=\"accZ\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Button</span>"
"<span id=\"btnState\"><span class=\"btn-none\">No press</span></span></div>"
"<div class=\"toggle-row\"><span class=\"label\">🔄 自动刷新</span>"
"<label class=\"toggle\"><input type=\"checkbox\" id=\"autoToggle\" checked "
"onchange=\"toggleAuto(this.checked)\"><span class=\"toggle-slider\"></span></label>"
"</div></div>"
"<div class=\"status-bar\"><span class=\"dot\" id=\"liveDot\"></span>"
"<span>Live &middot; <span id=\"ts\">--</span></span></div>"
"<div class=\"error\" id=\"error\" style=\"display:none\"></div>"
"<script>"
"var autoRefresh=true,autoTimer=null,collectPolling=null,currentReqId=null;"
"var errEl=document.getElementById('error');"
"function fetchLive(){"
"fetch('/api/sensor').then(function(r){"
"if(!r.ok)throw new Error('HTTP '+r.status);return r.json();"
"}).then(function(d){errEl.style.display='none';"
"document.getElementById('devid').textContent=d.device_id||'';"
"document.getElementById('accX').textContent=(d.accel_x!=null?d.accel_x+' mg':'--');"
"document.getElementById('accY').textContent=(d.accel_y!=null?d.accel_y+' mg':'--');"
"document.getElementById('accZ').textContent=(d.accel_z!=null?d.accel_z+' mg':'--');"
"var bs=document.getElementById('btnState');"
"if(d.button&&d.button!=='NONE')bs.innerHTML='<span class=\"btn-active\">'+d.button+'</span>';"
"else bs.innerHTML='<span class=\"btn-none\">No press</span>';"
"document.getElementById('ts').textContent=new Date().toLocaleTimeString();"
"}).catch(function(ex){errEl.style.display='block';errEl.textContent='Error: '+ex.message;});}"
"function startAuto(){stopAuto();if(autoRefresh){fetchLive();autoTimer=setInterval(fetchLive,500);}}"
"function stopAuto(){if(autoTimer){clearInterval(autoTimer);autoTimer=null;}}"
"function triggerCollect(){"
"var btn=document.getElementById('collectBtn');if(btn.disabled)return;"
"btn.disabled=true;btn.className='collect-btn busy';btn.textContent='提交中...';"
"fetch('/api/collect',{method:'POST'}).then(function(r){"
"if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(d){"
"currentReqId=d.request_id;"
"document.getElementById('taskInfo').style.display='block';"
"document.getElementById('reqId').textContent=currentReqId;"
"document.getElementById('collectResult').className='collect-result';"
"updateTaskStatus(d.status);btn.textContent='等待设备…';startPolling();"
"}).catch(function(ex){"
"errEl.style.display='block';errEl.textContent='Error: '+ex.message;"
"btn.disabled=false;btn.className='collect-btn ready';btn.textContent='采集一次最新数据';}"
");}"
"function updateTaskStatus(st){"
"var el=document.getElementById('taskStatus');el.className='task-status '+st;"
"if(st==='completed')el.innerHTML='✅ completed';"
"else if(st==='failed')el.innerHTML='❌ failed';"
"else if(st==='timeout')el.innerHTML='⏰ timeout';"
"else if(st==='submitted'||st==='received')"
"el.innerHTML='<span class=\"task-spinner\"></span> '+st;"
"else el.textContent=st;}"
"function startPolling(){"
"if(collectPolling)clearInterval(collectPolling);"
"var attempts=0;"
"collectPolling=setInterval(function(){"
"if(!currentReqId){clearInterval(collectPolling);collectPolling=null;return;}"
"attempts++;"
"fetch('/api/collect/status?request_id='+encodeURIComponent(currentReqId))"
".then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})"
".then(function(d){updateTaskStatus(d.status);"
"if(d.status==='completed'){clearInterval(collectPolling);collectPolling=null;"
"document.getElementById('cAccX').textContent=(d.accel_x!=null?d.accel_x+' mg':'--');"
"document.getElementById('cAccY').textContent=(d.accel_y!=null?d.accel_y+' mg':'--');"
"document.getElementById('cAccZ').textContent=(d.accel_z!=null?d.accel_z+' mg':'--');"
"document.getElementById('cBtn').textContent=d.button||'--';"
"document.getElementById('cElapsed').textContent=(d.elapsed_ms||0)+' ms';"
"document.getElementById('collectResult').className='collect-result show';"
"resetCollectBtn();}"
"else if(d.status==='failed'||d.status==='timeout'){"
"clearInterval(collectPolling);collectPolling=null;resetCollectBtn();}"
"else if(attempts>=60){clearInterval(collectPolling);collectPolling=null;"
"updateTaskStatus('timeout');resetCollectBtn();}"
"}).catch(function(ex){if(attempts>=60){clearInterval(collectPolling);"
"collectPolling=null;resetCollectBtn();}});},500);}"
"function resetCollectBtn(){var btn=document.getElementById('collectBtn');"
"btn.disabled=false;btn.className='collect-btn ready';btn.textContent='采集一次最新数据';}"
"function toggleAuto(on){autoRefresh=on;"
"var dot=document.getElementById('liveDot');"
"if(on){dot.className='dot';startAuto();}else{dot.className='dot paused';stopAuto();}}"
"startAuto();</script></body></html>";
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
    if (s_ctx.task) {
        cJSON_AddBoolToObject(root, "auto_refresh", s_ctx.task->auto_refresh);
    }
    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_str, strlen(json_str));
    cJSON_free(json_str);
    return ESP_OK;
}
/* ================================================================
 * Week 2: POST /api/collect
 * ================================================================ */
static esp_err_t collect_api_handler(httpd_req_t *req)
{
    if (!s_ctx.task) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No task");
        return ESP_FAIL;
    }
    if (s_ctx.task->status == COLLECT_SUBMITTED ||
        s_ctx.task->status == COLLECT_RECEIVED) {
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "request_id", s_ctx.task->request_id);
        cJSON_AddStringToObject(root, "status",
                                collect_status_name(s_ctx.task->status));
        cJSON_AddStringToObject(root, "message", "Task in progress");
        char *js = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, js, strlen(js));
        cJSON_free(js);
        return ESP_OK;
    }
    static int s_counter = 0;
    s_counter++;
    int64_t now = esp_timer_get_time();
    snprintf(s_ctx.task->request_id, sizeof(s_ctx.task->request_id),
             "req-%lld-%04d", (long long)(now / 1000), s_counter % 10000);
    s_ctx.task->status = COLLECT_SUBMITTED;
    s_ctx.task->submitted_at_us = now;
    s_ctx.task->completed_at_us = 0;
    ESP_LOGI(TAG, "[COLLECT] New: %s", s_ctx.task->request_id);
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "request_id", s_ctx.task->request_id);
    cJSON_AddStringToObject(root, "status", "submitted");
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, js, strlen(js));
    cJSON_free(js);
    return ESP_OK;
}
/* ================================================================
 * Week 2: GET /api/collect/status
 * ================================================================ */
static esp_err_t collect_status_handler(httpd_req_t *req)
{
    if (!s_ctx.task) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No task");
        return ESP_FAIL;
    }
    char qbuf[128] = {0};
    char rid[64] = {0};
    if (httpd_req_get_url_query_str(req, qbuf, sizeof(qbuf)) == ESP_OK) {
        char v[64] = {0};
        if (httpd_query_key_value(qbuf, "request_id", v, sizeof(v)) == ESP_OK) {
            strncpy(rid, v, sizeof(rid) - 1);
        }
    }
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "request_id", s_ctx.task->request_id);
    const char *st = collect_status_name(s_ctx.task->status);
    cJSON_AddStringToObject(root, "status", st);
    if (s_ctx.task->status == COLLECT_COMPLETED) {
        cJSON_AddNumberToObject(root, "accel_x", s_ctx.task->accel_x);
        cJSON_AddNumberToObject(root, "accel_y", s_ctx.task->accel_y);
        cJSON_AddNumberToObject(root, "accel_z", s_ctx.task->accel_z);
        cJSON_AddStringToObject(root, "button", s_ctx.task->button);
        int64_t e = s_ctx.task->completed_at_us - s_ctx.task->submitted_at_us;
        cJSON_AddNumberToObject(root, "elapsed_ms", (double)e / 1000.0);
    }
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, js, strlen(js));
    cJSON_free(js);
    return ESP_OK;
}

/* ================================================================
 * URI Routing
 * ================================================================ */
static const httpd_uri_t uri_root = {
    .uri = "/", .method = HTTP_GET,
    .handler = root_handler, .user_ctx = NULL,
};
static const httpd_uri_t uri_sensor = {
    .uri = "/api/sensor", .method = HTTP_GET,
    .handler = sensor_api_handler, .user_ctx = NULL,
};
static const httpd_uri_t uri_collect = {
    .uri = "/api/collect", .method = HTTP_POST,
    .handler = collect_api_handler, .user_ctx = NULL,
};
static const httpd_uri_t uri_collect_st = {
    .uri = "/api/collect/status", .method = HTTP_GET,
    .handler = collect_status_handler, .user_ctx = NULL,
};

/* ================================================================
 * Public API
 * ================================================================ */
esp_err_t http_server_start(const sensor_ctx_t *ctx)
{
    if (!ctx) return ESP_ERR_INVALID_ARG;
    s_ctx = *ctx;
    if (s_ctx.task) {
        s_ctx.task->status = COLLECT_IDLE;
        s_ctx.task->request_id[0] = '\0';
        s_ctx.task->auto_refresh = true;
    }
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;
    config.max_uri_handlers = 10;
    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) { ESP_LOGE(TAG, "HTTP start fail"); return ret; }
    httpd_register_uri_handler(s_server, &uri_root);
    httpd_register_uri_handler(s_server, &uri_sensor);
    httpd_register_uri_handler(s_server, &uri_collect);
    httpd_register_uri_handler(s_server, &uri_collect_st);
    ESP_LOGI(TAG, "HTTP ready (Week 2: +collect) port %d", config.server_port);
    return ESP_OK;
}

void http_server_stop(void)
{
    if (s_server) { httpd_stop(s_server); s_server = NULL; }
}
