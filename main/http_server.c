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
/* ================================================================
 * Week 2: JSON 组装工具（观测 / 请求记录）
 * ================================================================ */

/** @brief 统一 JSON 响应 */
static esp_err_t send_json(httpd_req_t *req, cJSON *root)
{
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!js) return ESP_FAIL;
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, js, strlen(js));
    cJSON_free(js);
    return ESP_OK;
}

/**
 * @brief 已存观测字段写入目标 JSON（字段保持平铺，便于前端复用）
 * @param[in] now_ms 当前相对时间，用于计算 data_age_ms
 */
static void obs_fill(cJSON *j, const observation_t *o, int64_t now_ms)
{
    if (!o || !o->valid) {
        cJSON_AddBoolToObject(j, "valid", false);
        cJSON_AddStringToObject(j, "source", OBS_SOURCE_NONE);
        cJSON_AddNumberToObject(j, "data_age_ms", -1);
        return;
    }
    cJSON_AddBoolToObject(j, "valid", true);
    cJSON_AddStringToObject(j, "record_id", o->record_id);
    cJSON_AddStringToObject(j, "request_id", o->request_id);
    cJSON_AddStringToObject(j, "source", o->source);
    cJSON_AddNumberToObject(j, "seq", o->seq);
    cJSON_AddNumberToObject(j, "accel_x", o->accel_x);
    cJSON_AddNumberToObject(j, "accel_y", o->accel_y);
    cJSON_AddNumberToObject(j, "accel_z", o->accel_z);
    cJSON_AddStringToObject(j, "button", o->button);
    cJSON_AddNumberToObject(j, "observed_ms", (double)o->observed_ms);
    cJSON_AddNumberToObject(j, "received_ms", (double)o->received_ms);
    cJSON_AddNumberToObject(j, "data_age_ms", (double)(now_ms - o->observed_ms));
    cJSON_AddStringToObject(j, "time_quality", "relative"); /* 板端无可靠墙钟 */
}

/** @brief 已存观测 → 嵌套在 "observation" 字段下 */
static void obs_to_json(cJSON *parent, const observation_t *o, int64_t now_ms)
{
    obs_fill(cJSON_AddObjectToObject(parent, "observation"), o, now_ms);
}

/** @brief 单条请求记录 → 追加到 JSON 数组 */
static void record_to_json(cJSON *arr, const collect_record_t *r)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "request_id", r->request_id);
    cJSON_AddStringToObject(j, "status", r->status);
    cJSON_AddBoolToObject(j, "has_observation", r->has_observation);
    cJSON_AddStringToObject(j, "source", r->source);
    cJSON_AddNumberToObject(j, "seq", r->seq);
    cJSON_AddNumberToObject(j, "accel_x", r->accel_x);
    cJSON_AddNumberToObject(j, "accel_y", r->accel_y);
    cJSON_AddNumberToObject(j, "accel_z", r->accel_z);
    cJSON_AddStringToObject(j, "button", r->button);
    cJSON_AddNumberToObject(j, "submitted_ms", r->submitted_ms);
    cJSON_AddNumberToObject(j, "received_ms", r->received_ms);
    cJSON_AddNumberToObject(j, "completed_ms", r->completed_ms);
    cJSON_AddNumberToObject(j, "elapsed_ms", r->elapsed_ms);
    cJSON_AddItemToArray(arr, j);
}

/** @brief 按请求号查找设备端记录（找到返回 1） */
static int find_record(const collect_task_t *t, const char *rid,
                       collect_record_t *out)
{
    for (int k = 0; k < t->record_count; k++) {
        int idx = (t->record_head - 1 - k + COLLECT_RECORD_MAX * 2)
                  % COLLECT_RECORD_MAX;
        if (strcmp(t->records[idx].request_id, rid) == 0) {
            if (out) *out = t->records[idx];
            return 1;
        }
    }
    return 0;
}

/** @brief 由请求记录还原观测视图（用于历史请求查询） */
static void record_to_obs(const collect_record_t *r, observation_t *o)
{
    memset(o, 0, sizeof(*o));
    if (!r->has_observation) return;
    o->valid = true;
    snprintf(o->record_id, sizeof(o->record_id), "obs-%05d", r->seq);
    snprintf(o->request_id, sizeof(o->request_id), "%s", r->request_id);
    snprintf(o->source, sizeof(o->source), "%s", r->source);
    o->seq = r->seq;
    o->accel_x = r->accel_x;
    o->accel_y = r->accel_y;
    o->accel_z = r->accel_z;
    snprintf(o->button, sizeof(o->button), "%s", r->button);
    o->observed_ms = r->completed_ms;  /* 观测时间与结果时间同一时钟 */
    o->received_ms = r->completed_ms;
}

/* === Week 2 INDEX_HTML (CSS only) === */
static const char INDEX_HTML[] =
"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
"<title>ESP32-S3-EYE Sensor Dashboard</title>"
"<style>"
"*{margin:0;padding:0;box-sizing:border-box}"
"body{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;"
"min-height:100vh;max-width:1200px;margin:0 auto;padding:24px 32px 48px;scroll-behavior:smooth}"
"h1{color:#58a6ff;margin-bottom:8px;font-size:1.6em}"
".sub{color:#8b949e;font-size:.85em;margin-bottom:24px}"
".topnav{display:flex;align-items:center;background:#0d1117;border-bottom:1px solid #21262d;padding:0 32px;height:50px;margin:-24px -32px 24px;position:sticky;top:0;z-index:10}"
".topnav .brand{color:#f0f6fc;font-weight:700;font-size:1em;margin-right:28px}"
".topnav a{color:#8b949e;text-decoration:none;padding:14px 20px;font-size:.88em;border-bottom:2px solid transparent}"
".topnav a:hover{color:#c9d1d9}"
".topnav a.act{color:#58a6ff;border-bottom-color:#58a6ff;font-weight:600}"
".card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:0;margin:0 0 22px;width:100%;overflow:hidden}"
".card h2{color:#f0f6fc;font-size:1em;font-weight:600;margin:0;padding:15px 24px;border-bottom:1px solid #21262d;display:flex;justify-content:space-between;align-items:center}"
".panel-badge{font-size:.73em;padding:4px 12px;border-radius:12px;background:#1a2332;color:#a5d6ff;border:1px solid #1f6feb;font-weight:400}"
".steps{display:none;align-items:center;justify-content:center;margin-top:16px;padding:12px 16px;background:#0d1117;border-radius:8px;border:1px solid #21262d}"
".steps.show{display:flex}"
".step{padding:5px 14px;border-radius:6px;font-size:.8em;font-weight:600;color:#30363d}"
".step.a{color:#58a6ff;background:#1a2332}.step.d{color:#7ee787;background:#12261e}.step.f{color:#f85149;background:#2d1216}"
".step-arrow{color:#21262d;margin:0 4px;font-size:.85em}"
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
/* === Week 2 新增样式：对比区 / 记录表 / 信息条 === */
".card-body{padding:20px 24px}"
".infostrip{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}"
".info-item{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:9px 14px;font-size:.78em;color:#8b949e}"
".info-item b{color:#c9d1d9;font-weight:600;font-family:monospace}"
".actions{display:flex;gap:12px;flex-wrap:wrap}"
".act-btn{flex:1;min-width:250px;padding:14px 18px;border-radius:8px;border:1px solid transparent;font-size:.92em;font-weight:600;cursor:pointer;transition:.2s}"
".act-btn.ghost{background:#161b22;border-color:#30363d;color:#c9d1d9}"
".act-btn.ghost:hover{border-color:#58a6ff;color:#58a6ff}"
".act-btn.primary{background:#238636;color:#fff}"
".act-btn.primary:hover{background:#2ea043}"
".act-btn.busy{background:#30363d;color:#8b949e;cursor:not-allowed;border-color:transparent}"
".hint{color:#8b949e;font-size:.78em;line-height:1.75;margin-top:12px}"
".hint.warn{color:#d29922}.hint.ok{color:#7ee787}.hint.err{color:#f85149}"
".cmp{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px}"
"@media(max-width:820px){.cmp{grid-template-columns:1fr}}"
".cmp-col{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:14px 16px}"
".cmp-col h4{font-size:.82em;color:#8b949e;font-weight:600;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;gap:8px}"
".cmp-col.new{border-color:#238636}"
".kv{display:flex;justify-content:space-between;gap:10px;font-size:.78em;padding:5px 0;border-bottom:1px dashed #21262d}"
".kv:last-child{border-bottom:none}"
".kv span:first-child{color:#8b949e;white-space:nowrap}"
".kv span:last-child{font-family:monospace;color:#c9d1d9;text-align:right;word-break:break-all}"
".badge{font-size:.72em;padding:3px 10px;border-radius:10px;font-weight:600;white-space:nowrap}"
".badge.ok{background:#12261e;color:#7ee787;border:1px solid #238636}"
".badge.stale{background:#2d1c12;color:#d29922;border:1px solid #9e6a03}"
".badge.none{background:#21262d;color:#8b949e;border:1px solid #30363d}"
"table.log{width:100%;border-collapse:collapse;font-size:.76em}"
"table.log th{text-align:left;color:#8b949e;font-weight:600;padding:8px 10px;border-bottom:1px solid #30363d;white-space:nowrap}"
"table.log td{padding:8px 10px;border-bottom:1px solid #161b22;font-family:monospace;color:#c9d1d9;vertical-align:top}"
"table.log tr:last-child td{border-bottom:none}"
"table.log td.mut{color:#8b949e}"
".pill{font-size:.72em;padding:2px 9px;border-radius:10px;font-weight:600;white-space:nowrap}"
".pill.completed{background:#12261e;color:#7ee787}"
".pill.failed{background:#2d1216;color:#f85149}"
".pill.timeout{background:#2d1c12;color:#d29922}"
".pill.pending{background:#1a2332;color:#a5d6ff}"
".pill.none{background:#21262d;color:#8b949e}"
".table-wrap{overflow-x:auto}"
"</style></head><body>"
"<div id=\"toast\" style=\"position:fixed;top:60px;left:50%;transform:translateX(-50%);background:#1a2332;color:#a5d6ff;padding:10px 24px;border-radius:8px;font-size:.88em;z-index:99;opacity:0;transition:opacity .3s;pointer-events:none;border:1px solid #1f6feb\"></div>"
"<nav class=\"topnav\"><span class=\"brand\">ESP32-S3-EYE · WK2</span>"
"<a href=\"#live-monitor\">传感器示波器</a><a href=\"#evidence-log\">报文监视</a>"
"<a class=\"act\" href=\"#collect-console\">控制中心</a><a href=\"#\" onclick=\"toast('外场与导出 — 功能开发中');return false\">外场与导出</a></nav>"
"<div class=\"card\" id=\"collect-console\"><h2>📡 远程采集控制台<span class=\"panel-badge\" id=\"taskBadge\">手动指令 v2.0</span></h2><div class=\"card-body\">"
"<div class=\"infostrip\">"
"<div class=\"info-item\">目标设备 <b id=\"infoDev\">—</b></div>"
"<div class=\"info-item\">传感源 <b id=\"infoSrc\">—</b></div>"
"<div class=\"info-item\">完成条件 <b>收到与本请求号关联的新观测</b></div>"
"<div class=\"info-item\">超时窗口 <b id=\"infoTimeout\">—</b></div>"
"<div class=\"info-item\">手动请求编号 <b id=\"infoTaskSeq\">第 0 次</b></div></div>"
"<div class=\"actions\">"
"<button class=\"act-btn ghost\" id=\"refreshBtn\" onclick=\"refreshStored()\">🔄 刷新已存数据（只读板上已保存观测）</button>"
"<button class=\"act-btn primary\" id=\"collectBtn\" onclick=\"triggerCollect()\">⚡ 采集一次最新数据（下发指令）</button></div>"
"<div class=\"hint\" id=\"actionHint\">两个动作的区别：<b>刷新已存数据</b>只读取板上已保存的观测，不触发采集，观测序号 seq 与采集时间都不会变；"
"<b>采集一次最新数据</b>会创建 request_id 并让开发板真正读一次传感器，返回与本请求号关联的新观测。</div>"
"<div id=\"taskInfo\" style=\"display:none\"><div class=\"steps show\" id=\"cmdProgress\">"
"<span class=\"step\" id=\"st1\">已提交·已受理</span><span class=\"step-arrow\">→</span>"
"<span class=\"step\" id=\"st2\">设备已接收</span><span class=\"step-arrow\">→</span>"
"<span class=\"step\" id=\"st3\">完成·新观测</span></div>"
"<div class=\"kv\"><span>Request ID</span><span id=\"reqId\">—</span></div>"
"<div class=\"kv\"><span>任务状态</span><span><span id=\"taskStatus\" class=\"pill pending\">—</span></span></div>"
"<div class=\"kv\"><span>受理 / 设备回执 / 结果（相对时间）</span><span id=\"taskTimes\">—</span></div></div>"
"<div class=\"hint\" id=\"statusNote\"></div>"
"<div class=\"cmp\">"
"<div class=\"cmp-col\" id=\"colStored\"><h4>🗄 已存观测（刷新已存数据得到）<span class=\"badge none\" id=\"storedBadge\">无数据</span></h4>"
"<div class=\"kv\"><span>观测标识 / 序号</span><span id=\"sRecId\">—</span></div>"
"<div class=\"kv\"><span>来源</span><span id=\"sSource\">—</span></div>"
"<div class=\"kv\"><span>关联请求号</span><span id=\"sReqId\">—</span></div>"
"<div class=\"kv\"><span>采集时间（相对）</span><span id=\"sObsTime\">—</span></div>"
"<div class=\"kv\"><span>数据年龄</span><span id=\"sAge\">—</span></div>"
"<div class=\"kv\"><span>X / Y / Z (mg)</span><span id=\"sAcc\">—</span></div>"
"<div class=\"kv\"><span>按键</span><span id=\"sBtn\">—</span></div></div>"
"<div class=\"cmp-col new\" id=\"colNew\"><h4>⚡ 本次新观测（须与本请求号关联）<span class=\"badge none\" id=\"newBadge\">等待下发</span></h4>"
"<div class=\"kv\"><span>观测标识 / 序号</span><span id=\"nRecId\">—</span></div>"
"<div class=\"kv\"><span>来源</span><span id=\"nSource\">—</span></div>"
"<div class=\"kv\"><span>关联请求号</span><span id=\"nReqId\">—</span></div>"
"<div class=\"kv\"><span>采集时间（相对）</span><span id=\"nObsTime\">—</span></div>"
"<div class=\"kv\"><span>受理 → 结果耗时</span><span id=\"nElapsed\">—</span></div>"
"<div class=\"kv\"><span>X / Y / Z (mg)</span><span id=\"nAcc\">—</span></div>"
"<div class=\"kv\"><span>按键</span><span id=\"nBtn\">—</span></div></div>"
"</div>"
"<div class=\"hint ok\">证明方式：观测序号 seq 每完成一次真实采集 +1，且新观测的关联请求号必须等于本次 request_id；刷新已存数据不会改变 seq 与采集时间，因此可以区分“页面重显旧值”与“开发板真的做了新采集”。时间均为板端相对时间（时间质量 relative），设备无可靠墙钟，页面不显示伪造的绝对时间。</div>"
"</div></div>"
"<div class=\"card\" id=\"live-monitor\"><h2>📐 实时传感器监控<span class=\"panel-badge\" id=\"liveBadge\">● 页面直读</span></h2><div class=\"card-body\">"
"<div class=\"row\"><span class=\"label\">X Axis</span><span class=\"value x\" id=\"accX\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Y Axis</span><span class=\"value y\" id=\"accY\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Z Axis</span><span class=\"value z\" id=\"accZ\">--</span></div>"
"<div class=\"row\"><span class=\"label\">Button</span><span id=\"btnState\"><span class=\"btn-none\">No press</span></span></div>"
"<div class=\"kv\"><span>页面直读次数（不产生观测）</span><span id=\"directCount\">—</span></div>"
"<div class=\"toggle-row\"><span class=\"label\">🔄 周期上报（板端每 1s 采样并保存观测 + 页面 500ms 刷新）</span>"
"<label class=\"toggle\"><input type=\"checkbox\" id=\"autoToggle\" checked "
"onchange=\"toggleAuto(this.checked)\"><span class=\"toggle-slider\"></span></label></div>"
"<div class=\"hint\" id=\"liveHint\">周期上报开启中：板上已存观测每 1s 更新一次。</div></div></div>"
"<div class=\"status-bar\"><span class=\"dot\" id=\"liveDot\"></span>"
"<span>Live &middot; <span id=\"ts\">--</span></span></div>"
"<div class=\"error\" id=\"error\" style=\"display:none\"></div>"
"<div class=\"card\" id=\"evidence-log\"><h2>📋 报文监视 · 请求—回执—观测记录<span class=\"panel-badge\">设备端保留最近 6 条</span></h2><div class=\"card-body\">"
"<div class=\"actions\"><button class=\"act-btn ghost\" style=\"min-width:200px\" onclick=\"loadRecords()\">🔄 刷新记录</button></div>"
"<div class=\"table-wrap\"><table class=\"log\"><thead><tr>"
"<th>#</th><th>请求号</th><th>状态</th><th>来源</th><th>观测（seq / X,Y,Z mg）</th><th>受理→结果</th><th>记录来源</th></tr></thead>"
"<tbody id=\"recBody\"><tr><td class=\"mut\" colspan=\"7\">暂无记录：先点一次“采集一次最新数据”。</td></tr></tbody></table></div>"
"<div class=\"hint\">设备端记录由开发板保存（请求号、设备回执时间、新观测、耗时）；浏览器端记录用于“设备关机/离线”时页面仍能追踪本次尝试。超时记录明确标注“未收到设备结果”，不把旧值标成本次成功。</div>"
"</div></div>"
"<script>"
"function toast(msg){var t=document.getElementById('toast');t.textContent=msg;t.style.opacity='1';setTimeout(function(){t.style.opacity='0'},2000);}"
"function switchNav(e){var as=document.querySelectorAll('.topnav a');as.forEach(function(a){a.classList.remove('act')});e.classList.add('act');}"
"document.querySelectorAll('.topnav a[href^=\"#\"]').forEach(function(a){if(a.getAttribute('onclick'))return;a.addEventListener('click',function(e){var t=document.querySelector(this.getAttribute('href'));if(t){e.preventDefault();switchNav(this);t.scrollIntoView({behavior:'smooth',block:'start'})}})});"
"var autoRefresh=true,autoTimer=null,ageTimer=null,collectPolling=null,currentReqId=null,currentTaskSeq=0;"
"var storedObs=null,storedAt=0,webLog=[],deviceRecs=[],netFail=0,pendingTask=false,snapshotKept=false;"
"var errEl=document.getElementById('error');"
"function $(id){return document.getElementById(id);}"
"function txt(id,v){var e=$(id);if(e)e.textContent=v;}"
"function p2(n){return (n<10?'0':'')+n;}"
"function fmtRel(ms){if(ms==null||ms<0)return '—';var s=Math.floor(ms/1000);"
"return 'T+'+p2(Math.floor(s/3600))+':'+p2(Math.floor(s%3600/60))+':'+p2(s%60)+'.'+('00'+Math.floor(ms%1000)).slice(-3);}"
"function setNote(msg,cls){var e=$('statusNote');if(e){e.textContent=msg;e.className='hint '+(cls||'');}}"
"function setBadge(id,text,cls){var e=$(id);if(e){e.textContent=text;e.className='badge '+cls;}}"
"function setHint(msg,cls){var e=$('liveHint');if(e){e.textContent=msg;e.className='hint '+(cls||'');}}"
"function step(n,cls){var e=$('st'+n);if(e)e.className='step '+(cls||'');}"
"function fetchLive(){"
"fetch('/api/sensor').then(function(r){"
"if(!r.ok)throw new Error('HTTP '+r.status);return r.json();"
"}).then(function(d){errEl.style.display='none';"
"txt('infoDev',d.device_id||'—');"
"txt('infoSrc',d.sensor_source||'—');"
"txt('infoTimeout',(d.timeout_ms||0)+' ms');"
"txt('infoTaskSeq','第 '+(d.task_seq||0)+' 次');"
"txt('directCount',(d.direct_read_count||0)+' 次');"
"txt('accX',(d.accel_x!=null?d.accel_x+' mg':'--'));"
"txt('accY',(d.accel_y!=null?d.accel_y+' mg':'--'));"
"txt('accZ',(d.accel_z!=null?d.accel_z+' mg':'--'));"
"var bs=$('btnState');"
"if(d.button&&d.button!=='NONE')bs.innerHTML='<span class=\"btn-active\">'+d.button+'</span>';"
"else bs.innerHTML='<span class=\"btn-none\">No press</span>';"
"txt('ts',new Date().toLocaleTimeString());"
"var tg=$('autoToggle');if(tg&&tg.checked!==!!d.auto_refresh)tg.checked=!!d.auto_refresh;"
"}).catch(function(ex){errEl.style.display='block';errEl.textContent='Error: '+ex.message;});}"
"function startAuto(){stopAuto();if(autoRefresh){fetchLive();autoTimer=setInterval(fetchLive,500);}}"
"function stopAuto(){if(autoTimer){clearInterval(autoTimer);autoTimer=null;}}"
"function triggerCollect(){"
"var btn=$('collectBtn');if(btn.disabled)return;"
"btn.disabled=true;btn.className='act-btn busy';btn.textContent='提交中…';"
"setNote('正在创建 request_id 并下发采集指令…','');"
"netFail=0;pendingTask=true;"
"fetch('/api/collect',{method:'POST'}).then(function(r){"
"if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(d){"
"currentReqId=d.request_id;currentTaskSeq=d.task_seq||currentTaskSeq;"
"if(d.message==='Task in progress'){toast('已有任务进行中，沿用请求号 '+currentReqId);}"
"else{toast('已下发请求 '+currentReqId+'（第 '+(d.task_seq||0)+' 次手动采集）');}"
"$('taskInfo').style.display='block';"
"txt('reqId',currentReqId);"
"txt('infoTaskSeq','第 '+(d.task_seq||0)+' 次');"
"resetNewObs();"
"setBadge('newBadge','等待设备回执','none');"
"$('cmdProgress').className='steps show';"
"updateTaskStatus(d.status);btn.textContent='等待设备…';startPolling();"
"}).catch(function(ex){"
"errEl.style.display='block';errEl.textContent='Error: '+ex.message;"
"setNote('下发失败：'+ex.message+'（设备可能已关机或不在同一网络）','err');"
"webLog.unshift({request_id:currentReqId||'(请求未创建)',status:'timeout',source:'none',seq:0,acc:'',elapsed_ms:null,from:'web'});"
"pendingTask=false;loadRecords();resetCollectBtn();}"
");}"
"function updateTaskStatus(st){"
"var el=$('taskStatus');if(!el)return;"
"var label={idle:'空闲',submitted:'已提交·已受理',received:'设备已接收',completed:'完成·新观测',failed:'失败',timeout:'超时·未收到设备结果',unknown:'未找到该请求号'};"
"var cls=(st==='completed')?'completed':(st==='failed')?'failed':(st==='timeout')?'timeout':((st==='submitted'||st==='received')?'pending':'none');"
"el.className='pill '+cls;el.textContent=label[st]||st;"
"if(st==='submitted'){step(1,'a');}"
"else if(st==='received'){step(1,'d');step(2,'a');}"
"else if(st==='completed'){step(1,'d');step(2,'d');step(3,'d');}"
"else if(st==='failed'||st==='timeout'||st==='unknown'){step(3,'f');}"
"}"
"function startPolling(){"
"if(collectPolling)clearInterval(collectPolling);"
"var attempts=0;netFail=0;"
"collectPolling=setInterval(function(){"
"if(!currentReqId){clearInterval(collectPolling);collectPolling=null;return;}"
"attempts++;"
"fetch('/api/collect/status?request_id='+encodeURIComponent(currentReqId))"
".then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})"
".then(function(d){netFail=0;updateTaskStatus(d.status);"
"txt('taskTimes','受理 '+fmtRel(d.submitted_ms)+' · 设备回执 '+(d.received_ms>=0?fmtRel(d.received_ms):'未回执')+' · 结果 '+(d.completed_ms?fmtRel(d.completed_ms):'—'));"
"if(d.status==='completed'){clearInterval(collectPolling);collectPolling=null;pendingTask=false;"
"renderNewObs(d);loadRecords();resetCollectBtn();}"
"else if(d.status==='failed'||d.status==='timeout'||d.status==='unknown'){"
"clearInterval(collectPolling);collectPolling=null;pendingTask=false;"
"setBadge('newBadge',d.status==='timeout'?'超时·无新观测':'无新观测',d.status==='timeout'?'stale':'none');"
"setNote(d.note||'本次未收到新观测：已存观测保持原采集时间与序号，未改标为本次完成。','warn');"
"loadRecords();resetCollectBtn();}"
"else if(attempts>=30){clearInterval(collectPolling);collectPolling=null;pendingTask=false;"
"updateTaskStatus('timeout');"
"setNote('超过等待窗口仍未收到设备结果；这不等于硬件故障，请检查供电与网络后重试。','warn');"
"loadRecords();resetCollectBtn();}"
"}).catch(function(ex){"
"netFail++;"
"setNote('暂未连接上设备（第 '+netFail+' 次重试）…若设备已关机，页面会等待并进入超时，不会把旧值当作本次结果。','warn');"
"if(netFail>=8||attempts>=30){clearInterval(collectPolling);collectPolling=null;pendingTask=false;"
"updateTaskStatus('timeout');"
"setNote('暂未收到设备结果（连接失败 '+netFail+' 次）。这不代表硬件故障；旧观测保持原采集时间，未被标成本次成功。','warn');"
"webLog.unshift({request_id:currentReqId,status:'timeout',source:'none',seq:0,acc:'',elapsed_ms:null,from:'web'});"
"loadRecords();resetCollectBtn();}});},500);}"
"function resetNewObs(){txt('nRecId','—');txt('nSource','—');txt('nReqId','—');txt('nObsTime','—');txt('nElapsed','—');txt('nAcc','—');txt('nBtn','—');}"
"function renderNewObs(d){"
"var o=d.observation;"
"if(!o){setBadge('newBadge','无新观测','none');setNote(d.note||'本次未带回新观测。','warn');return;}"
"txt('nRecId',o.record_id+' / seq '+o.seq);"
"txt('nSource',o.source==='live'?'live（手动采集的真实读取）':o.source);"
"txt('nReqId',o.request_id+(o.request_id===currentReqId?' ✓ 与本次请求一致':' ⚠ 与本次请求不一致'));"
"txt('nObsTime',fmtRel(o.observed_ms));"
"txt('nElapsed',(d.elapsed_ms!=null?d.elapsed_ms+' ms':'—'));"
"txt('nAcc',o.accel_x+' / '+o.accel_y+' / '+o.accel_z);"
"txt('nBtn',o.button||'—');"
"setBadge('newBadge','新观测已关联本次请求','ok');"
"setNote('完成：请求 '+currentReqId+' 已收到设备新观测 '+o.record_id+'（seq '+o.seq+'）。左侧保留采集前快照，点“刷新已存数据”可看到它已更新为本次观测；对比两者的 seq 与采集时间即可确认不是重显旧值。','ok');"
"snapshotKept=true;updateAge();}"
"function resetCollectBtn(){var btn=$('collectBtn');"
"btn.disabled=false;btn.className='act-btn primary';"
"btn.textContent=pendingTask?'等待设备…':'⚡ 采集一次最新数据（下发指令）';}"
"function toggleAuto(on){autoRefresh=on;"
"var dot=$('liveDot');"
"if(on){dot.className='dot';startAuto();setHint('周期上报开启中：板上已存观测每 1s 更新一次。','');}"
"else{dot.className='dot paused';stopAuto();"
"setHint('周期上报已暂停：板端停止周期采样，命令通道保持可用；已存观测保留原采集时间（数据未更新 ≠ 硬件故障）。','warn');}"
"fetch('/api/auto_refresh?on='+(on?'1':'0'),{method:'POST'}).catch(function(){});}"
"function refreshStored(){"
"fetch('/api/observation/last').then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})"
".then(function(d){storedObs=d;storedAt=Date.now();snapshotKept=false;renderStored();})"
".catch(function(ex){setNote('读取已存数据失败：'+ex.message,'err');});}"
"function renderStored(){"
"var d=storedObs;if(!d)return;"
"if(!d.valid){setBadge('storedBadge','无数据','none');"
"txt('sRecId','—');txt('sSource','—');txt('sReqId','—');txt('sObsTime','—');txt('sAcc','—');txt('sBtn','—');txt('sAge','—');return;}"
"txt('sRecId',d.record_id+' / seq '+d.seq);"
"txt('sSource',d.source==='cadence'?'cadence（周期上报的真实读取）':(d.source==='live'?'live（手动采集的真实读取）':d.source));"
"txt('sReqId',d.request_id);"
"txt('sObsTime',fmtRel(d.observed_ms));"
"txt('sAcc',d.accel_x+' / '+d.accel_y+' / '+d.accel_z);"
"txt('sBtn',d.button||'—');"
"updateAge();}"
"function updateAge(){"
"var d=storedObs;if(!d||!d.valid)return;"
"var age=(d.data_age_ms||0)+(Date.now()-storedAt);"
"var stale=age>(d.fresh_window_ms||3000);"
"txt('sAge',(age/1000).toFixed(1)+' s'+(snapshotKept?'（采集前快照，点“刷新已存数据”更新）':(stale?'（数据未更新，保留旧采集时间）':'')));"
"setBadge('storedBadge',snapshotKept?'采集前快照':(stale?'未更新 · 保旧采集时间':'新鲜'),snapshotKept?'none':(stale?'stale':'ok'));}"
"function loadRecords(){"
"fetch('/api/collect/history').then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})"
".then(function(d){deviceRecs=d.records||[];renderRecords();})"
".catch(function(){});}"
"function renderRecords(){"
"var rows=webLog.slice();var i;"
"for(i=0;i<deviceRecs.length;i++){var r=deviceRecs[i];"
"rows.push({request_id:r.request_id,status:r.status,source:r.source,seq:r.seq,"
"acc:r.has_observation?(r.accel_x+'/'+r.accel_y+'/'+r.accel_z):'',elapsed_ms:r.elapsed_ms,"
"submitted_ms:r.submitted_ms,received_ms:r.received_ms,completed_ms:r.completed_ms,from:'device'});}"
"var body=$('recBody');if(!body)return;"
"if(!rows.length){body.innerHTML='<tr><td class=\"mut\" colspan=\"7\">暂无记录：先点一次“采集一次最新数据”。</td></tr>';return;}"
"var html='';"
"for(i=0;i<Math.min(rows.length,12);i++){var x=rows[i];"
"var st=x.status;"
"var cls=st==='completed'?'completed':(st==='failed'?'failed':(st==='timeout'?'timeout':'none'));"
"var label=st==='completed'?'完成':(st==='failed'?'失败':(st==='timeout'?'超时·未收到设备结果':'—'));"
"var tstr=(x.from==='device')?(fmtRel(x.submitted_ms)+' → '+(x.received_ms>=0?('回执 '+fmtRel(x.received_ms)+' → '):'')+fmtRel(x.completed_ms)):'—';"
"html+='<tr><td>'+(i+1)+'</td><td>'+x.request_id+'</td>';"
"html+='<td><span class=\"pill '+cls+'\">'+label+'</span></td>';"
"html+='<td>'+x.source+((x.seq)?(' / seq '+x.seq):'')+'</td>';"
"html+='<td>'+(x.acc||'—')+'</td>';"
"html+='<td>'+tstr+((x.elapsed_ms!=null)?(' ('+x.elapsed_ms+' ms)'):'')+'</td>';"
"html+='<td>'+(x.from==='device'?'设备端（板端记录）':'浏览器端（页面追踪）')+'</td></tr>';}"
"body.innerHTML=html;}"
"function init(){startAuto();refreshStored();loadRecords();ageTimer=setInterval(updateAge,1000);}"
"init();</script></body></html>";
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
    bool direct_read = false;   /* 页面直读：真实读一次，但不产生"已存观测" */
    cJSON_AddStringToObject(root, "device_id",
        s_ctx.device_id ? s_ctx.device_id : "esp32s3-eye");
    cJSON_AddStringToObject(root, "sensor_source",
        s_ctx.sensor_src ? s_ctx.sensor_src : "unknown");
    cJSON_AddStringToObject(root, "source", "direct_read");
    cJSON_AddStringToObject(root, "time_quality", "relative");
    if (s_ctx.imu) {
        qma6100p_acce_value_t val;
        if (qma6100p_get_acce(s_ctx.imu, &val) == ESP_OK) {
            cJSON_AddNumberToObject(root, "accel_x", (int)(val.acce_x * 1000));
            cJSON_AddNumberToObject(root, "accel_y", (int)(val.acce_y * 1000));
            cJSON_AddNumberToObject(root, "accel_z", (int)(val.acce_z * 1000));
            direct_read = true;
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
        if (direct_read) s_ctx.task->direct_read_count++;  /* 直读计数留证 */
        cJSON_AddBoolToObject(root, "auto_refresh", s_ctx.task->auto_refresh);
        cJSON_AddNumberToObject(root, "timeout_ms", COLLECT_TIMEOUT_MS);
        cJSON_AddNumberToObject(root, "task_seq", s_ctx.task->task_seq);
        cJSON_AddNumberToObject(root, "obs_seq", s_ctx.task->obs_seq);
        cJSON_AddNumberToObject(root, "direct_read_count",
                                s_ctx.task->direct_read_count);
        cJSON_AddNumberToObject(root, "poll_count", s_ctx.task->poll_count);
    }
    return send_json(req, root);
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
        cJSON_AddNumberToObject(root, "task_seq", s_ctx.task->task_seq);
        cJSON_AddNumberToObject(root, "timeout_ms", COLLECT_TIMEOUT_MS);
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
    s_ctx.task->received_at_us = 0;                              /* 设备尚未回执 */
    s_ctx.task->completed_at_us = 0;
    s_ctx.task->deadline_us = now + (int64_t)COLLECT_TIMEOUT_MS * 1000;
    s_ctx.task->task_seq++;                    /* 逐次编号：重复点击也可区分 */
    ESP_LOGI(TAG, "[COLLECT] New #%d: %s (完成条件窗口 %d ms)",
             s_ctx.task->task_seq, s_ctx.task->request_id, COLLECT_TIMEOUT_MS);
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "request_id", s_ctx.task->request_id);
    cJSON_AddStringToObject(root, "status", "submitted");
    cJSON_AddNumberToObject(root, "task_seq", s_ctx.task->task_seq);
    cJSON_AddNumberToObject(root, "timeout_ms", COLLECT_TIMEOUT_MS);
    cJSON_AddStringToObject(root, "message", "submitted");
    return send_json(req, root);
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
    collect_task_t *t = s_ctx.task;
    int64_t now_ms = esp_timer_get_time() / 1000;
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "request_id", t->request_id);
    cJSON_AddNumberToObject(root, "task_seq", t->task_seq);
    cJSON_AddNumberToObject(root, "timeout_ms", COLLECT_TIMEOUT_MS);

    /* ---- 请求号校验：不把别次请求的结果当作"本次完成" ---- */
    if (rid[0] != '\0' && strcmp(rid, t->request_id) != 0) {
        cJSON *j = cJSON_CreateObject();
        cJSON_AddStringToObject(j, "request_id", rid);
        cJSON_AddNumberToObject(j, "task_seq", 0);
        collect_record_t rec;
        if (find_record(t, rid, &rec)) {
            cJSON_AddStringToObject(j, "status", rec.status);
            cJSON_AddBoolToObject(j, "from_history", true);
            cJSON_AddBoolToObject(j, "matches_current", false);
            cJSON_AddBoolToObject(j, "linked", rec.has_observation);
            cJSON_AddNumberToObject(j, "submitted_ms", rec.submitted_ms);
            cJSON_AddNumberToObject(j, "received_ms", rec.received_ms);
            cJSON_AddNumberToObject(j, "completed_ms", rec.completed_ms);
            cJSON_AddNumberToObject(j, "elapsed_ms", rec.elapsed_ms);
            observation_t o;
            record_to_obs(&rec, &o);
            obs_to_json(j, &o, now_ms);
            cJSON_AddStringToObject(j, "note",
                "该请求号已结束，结果来自设备端请求记录；不影响当前任务状态。");
        } else {
            cJSON_AddStringToObject(j, "status", "unknown");
            cJSON_AddBoolToObject(j, "from_history", false);
            cJSON_AddBoolToObject(j, "matches_current", false);
            cJSON_AddBoolToObject(j, "linked", false);
            cJSON_AddStringToObject(j, "note",
                "未找到该请求号（设备可能已重启）；绝不会把当前数值当作本次采集结果。");
        }
        return send_json(req, j);
    }

    /* ---- 当前请求：状态 → 设备回执 → 新观测 ---- */
    cJSON_AddBoolToObject(root, "matches_current", true);
    cJSON_AddBoolToObject(root, "from_history", false);
    const char *st = collect_status_name(t->status);
    cJSON_AddStringToObject(root, "status", st);
    cJSON_AddNumberToObject(root, "submitted_ms", (double)t->submitted_at_us / 1000.0);
    cJSON_AddNumberToObject(root, "received_ms",
                            t->received_at_us ? (double)t->received_at_us / 1000.0 : -1);
    cJSON_AddNumberToObject(root, "completed_ms",
                            t->completed_at_us ? (double)t->completed_at_us / 1000.0 : 0);
    if (t->completed_at_us) {
        cJSON_AddNumberToObject(root, "elapsed_ms",
            (double)(t->completed_at_us - t->submitted_at_us) / 1000.0);
    }
    /* 只有"观测的关联请求号 = 本次请求号"才算本次带回结果 */
    bool linked = t->last.valid && strcmp(t->last.request_id, t->request_id) == 0;
    cJSON_AddBoolToObject(root, "linked", linked);
    obs_to_json(root, linked ? &t->last : NULL, now_ms);

    if (t->status == COLLECT_TIMEOUT) {
        cJSON_AddStringToObject(root, "note",
            "超时：窗口内未收到设备结果。这不代表硬件故障；已存观测保持原采集时间与序号，未被改标为本次完成。");
    } else if (t->status == COLLECT_FAILED) {
        cJSON_AddStringToObject(root, "note",
            "失败：设备执行了但传感器读取未成功，本次未产生新观测。");
    } else if (t->status == COLLECT_COMPLETED && linked) {
        cJSON_AddStringToObject(root, "note",
            "完成：已收到与本请求号关联的新观测。");
    } else {
        cJSON_AddStringToObject(root, "note", "已受理，等待设备执行与回执…");
    }
    return send_json(req, root);
}

/* ================================================================
 * Week 2: GET /api/observation/last —— "刷新已存数据"
 *   只读取板上已保存观测，绝不触发采集（与"采集一次最新数据"相对照）
 * ================================================================ */
static esp_err_t observation_last_handler(httpd_req_t *req)
{
    if (!s_ctx.task) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No task");
        return ESP_FAIL;
    }
    int64_t now_ms = esp_timer_get_time() / 1000;
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "action", "refresh_stored");
    cJSON_AddBoolToObject(root, "triggered_read", false);  /* 明确：未触发采集 */
    cJSON_AddNumberToObject(root, "fresh_window_ms", 3000);
    obs_fill(root, &s_ctx.task->last, now_ms);
    cJSON *age = cJSON_GetObjectItem(root, "data_age_ms");
    cJSON_AddBoolToObject(root, "stale",
        !(age && age->valuedouble >= 0 && age->valuedouble <= 3000));
    cJSON_AddStringToObject(root, "note",
        "读取的是板上已保存观测，本次未读取传感器（观测序号与采集时间不会变化）。");
    return send_json(req, root);
}

/* ================================================================
 * Week 2: GET /api/collect/history —— 请求—回执—观测记录（设备端）
 * ================================================================ */
static esp_err_t history_handler(httpd_req_t *req)
{
    if (!s_ctx.task) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No task");
        return ESP_FAIL;
    }
    collect_task_t *t = s_ctx.task;
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "count", t->record_count);
    cJSON *arr = cJSON_AddArrayToObject(root, "records");
    for (int k = 0; k < t->record_count; k++) {   /* 最新的在前 */
        int idx = (t->record_head - 1 - k + COLLECT_RECORD_MAX * 2)
                  % COLLECT_RECORD_MAX;
        record_to_json(arr, &t->records[idx]);
    }
    cJSON_AddStringToObject(root, "note",
        "设备端请求记录（请求→设备回执→新观测）；超时/失败记录不含新观测。");
    return send_json(req, root);
}

/* ================================================================
 * Week 2: POST /api/auto_refresh?on=0|1 —— 周期上报开关
 *   暂停后板端停止周期采样，但命令通道（/api/collect）保持可用
 * ================================================================ */
static esp_err_t auto_refresh_handler(httpd_req_t *req)
{
    if (!s_ctx.task) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No task");
        return ESP_FAIL;
    }
    char qbuf[64] = {0};
    bool on = !s_ctx.task->auto_refresh;    /* 无参数时切换 */
    if (httpd_req_get_url_query_str(req, qbuf, sizeof(qbuf)) == ESP_OK) {
        char v[8] = {0};
        if (httpd_query_key_value(qbuf, "on", v, sizeof(v)) == ESP_OK) {
            on = (v[0] == '1');
        }
    }
    s_ctx.task->auto_refresh = on;
    ESP_LOGI(TAG, "[CADENCE] 周期上报 %s", on ? "开启" : "暂停（命令通道保持可用）");
    cJSON *root = cJSON_CreateObject();
    cJSON_AddBoolToObject(root, "auto_refresh", on);
    cJSON_AddStringToObject(root, "note",
        on ? "周期上报已开启：板端每 1s 采样并保存观测。"
           : "周期上报已暂停：板端停止周期采样，命令通道仍可用；已存观测保留原采集时间。");
    return send_json(req, root);
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
static const httpd_uri_t uri_obs_last = {   /* Week 2: 刷新已存数据（只读） */
    .uri = "/api/observation/last", .method = HTTP_GET,
    .handler = observation_last_handler, .user_ctx = NULL,
};
static const httpd_uri_t uri_history = {    /* Week 2: 请求—回执—观测记录 */
    .uri = "/api/collect/history", .method = HTTP_GET,
    .handler = history_handler, .user_ctx = NULL,
};
static const httpd_uri_t uri_auto = {       /* Week 2: 周期上报开关 */
    .uri = "/api/auto_refresh", .method = HTTP_POST,
    .handler = auto_refresh_handler, .user_ctx = NULL,
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
        s_ctx.task->deadline_us = 0;
        s_ctx.task->received_at_us = 0;
    }
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;
    config.max_uri_handlers = 12;
    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) { ESP_LOGE(TAG, "HTTP start fail"); return ret; }
    httpd_register_uri_handler(s_server, &uri_root);
    httpd_register_uri_handler(s_server, &uri_sensor);
    httpd_register_uri_handler(s_server, &uri_collect);
    httpd_register_uri_handler(s_server, &uri_collect_st);
    httpd_register_uri_handler(s_server, &uri_obs_last);
    httpd_register_uri_handler(s_server, &uri_history);
    httpd_register_uri_handler(s_server, &uri_auto);
    ESP_LOGI(TAG, "HTTP ready (Week 2: +collect/status/history/stored) port %d",
             config.server_port);
    return ESP_OK;
}

void http_server_stop(void)
{
    if (s_server) { httpd_stop(s_server); s_server = NULL; }
}
