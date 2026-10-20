"""
vision_feedback_server.py — Week 08 W8-3: 本地视觉反馈闭环 HTTP 服务

作用：
  - 在 `127.0.0.1` 上提供一个**仅服务本功能**的最小 HTTP 接口与页面：
      GET  /                         反馈界面（单页，无外部 CDN）
      GET  /api/vision/health        模型/阈值/设备/存储配置摘要（脱敏）
      GET  /api/vision/gallery       代理设备 `/api/camera/gallery`
      GET  /api/vision/status        代理设备 `/api/camera/status`
      GET  /api/vision/photo?index=N 代理设备照片 JPEG（不落盘）
      POST /api/vision/analyze       本地 JPEG（请求体）或设备照片（`{"index":N}`）
      POST /api/vision/recapture     代理设备 `/api/camera/capture`（按需抓拍）
      GET  /api/vision/records       最近分析记录 + 人工反馈历史
      POST /api/vision/feedback      保存人工反馈（可用/不可用/无法判断 + 原因）

边界：
  - 独立进程、独立端口（默认 8090），**不并入**语音服务（8080）或设备仪表盘；
  - 默认只监听 `127.0.0.1`；改用其他绑定地址时会在启动日志中明确告警；
  - 不修改固件、不修改 Week 07 定时抓拍策略、不自动启动常驻服务；
  - 不改设备地址：设备地址必须由 `VISION_DEVICE_BASE_URL` 或 `--device-url`
    显式提供，未配置时设备相关接口返回 `503 device_not_configured`；
  - 图像不持久化；仅保存 SHA-256/字节数与设备侧引用；
  - 模型不可用/推理失败时返回明确错误，**不降级**为规则判断；
  - 不设生产阈值：未配置 `VISION_DBCNN_SCORE_THRESHOLD` 时判定恒为
    `needs_review`，界面明确提示分数尚未校准。

启动（使用已验证的隔离 venv，见 `docs/vision-feedback.md`）：
  <venv>\\Scripts\\python.exe -m service.vision_feedback_server --port 8090
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from . import vision_feedback as vf
from .vision_dbcnn import (
    ENV_MODEL_PATH,
    ENV_MODEL_VERSION,
    DbcnnConfigError,
    DbcnnDependencyError,
    DbcnnInferenceError,
    DbcnnModelError,
)

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

DEFAULT_HOST = "127.0.0.1"
"""默认仅监听本机回环地址，避免把本地推理能力暴露到局域网。"""

DEFAULT_PORT = 8090
"""独立端口；语音控制服务使用 8080，设备仪表盘使用 80。"""

MAX_BODY_BYTES = vf.MAX_INPUT_BYTES + 1024
"""请求体上限（JPEG 上限 + JSON 余量）。"""

JSON_ROUTES = {
    "/api/vision/analyze",
    "/api/vision/recapture",
    "/api/vision/feedback",
}
GET_ROUTES = {
    "/",
    "/index.html",
    "/api/vision/health",
    "/api/vision/gallery",
    "/api/vision/status",
    "/api/vision/photo",
    "/api/vision/records",
}

LOGGER = logging.getLogger("vision.feedback")


# ──────────────────────────────────────────────
# 异常映射
# ──────────────────────────────────────────────


def map_exception(exc: BaseException) -> tuple[int, str, dict[str, Any]]:
    """把领域异常映射为 (HTTP 状态码, 机器可读错误码, 额外字段)。"""
    if isinstance(exc, DbcnnConfigError):
        return 503, "model_not_configured", {}
    if isinstance(exc, DbcnnDependencyError):
        return 503, "model_dependency_unavailable", {}
    if isinstance(exc, DbcnnModelError):
        return 500, "model_load_failed", {}
    if isinstance(exc, DbcnnInferenceError):
        return 502, "inference_failed", {}
    if isinstance(exc, vf.VisionConfigError):
        return 500, "threshold_config_invalid", {}
    if isinstance(exc, vf.DeviceNotConfigured):
        return 503, "device_not_configured", {}
    if isinstance(exc, vf.DevicePhotoNotFound):
        return 404, "photo_not_found", {}
    if isinstance(exc, vf.DeviceHttpError):
        return 502, "device_http_error", {"device_status": exc.status}
    if isinstance(exc, vf.DeviceUnreachable):
        return 502, "device_unreachable", {}
    if isinstance(exc, vf.DeviceProtocolError):
        return 502, "device_protocol_error", {}
    if isinstance(exc, vf.PayloadTooLargeError):
        return 413, "payload_too_large", {}
    if isinstance(exc, vf.FeedbackValidationError):
        return 400, "invalid_feedback", {}
    if isinstance(exc, vf.FeedbackNotFoundError):
        return 404, "record_not_found", {}
    if isinstance(exc, vf.FeedbackConflictError):
        return 409, "duplicate_feedback", {}
    if isinstance(exc, vf.FeedbackStoreError):
        return 500, "storage_error", {}
    if isinstance(exc, vf.VisionFeedbackError):
        return 500, "vision_feedback_error", {}
    if isinstance(exc, UnsupportedMediaType):
        return 415, "unsupported_media_type", {}
    if isinstance(exc, ValueError):
        return 400, "invalid_request", {}
    return 500, "internal_error", {}



class UnsupportedMediaType(ValueError):
    """请求的 Content-Type 不被支持（对应 HTTP 415）。"""


# ──────────────────────────────────────────────
# HTTP 处理器
# ──────────────────────────────────────────────


class VisionFeedbackHandler(BaseHTTPRequestHandler):
    """最小 JSON/HTML 处理器；服务实例挂在 `self.server.service`。"""

    server_version = "VisionFeedback/1.0"
    protocol_version = "HTTP/1.1"

    # ── 基础工具 ──────────────────────────────

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: Optional[dict[str, str]] = None,
        close: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._common_headers()
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._common_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._common_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def service(self) -> vf.VisionFeedbackService:
        return self.server.service  # type: ignore[attr-defined]

    # ── 请求体 ────────────────────────────────

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("缺少 Content-Length 头")
        try:
            length = int(raw_length)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Content-Length 非法: {raw_length!r}") from exc
        if length < 0:
            raise ValueError("Content-Length 不得为负数")
        if length > MAX_BODY_BYTES:
            raise vf.PayloadTooLargeError(
                f"请求体超过上限 {MAX_BODY_BYTES} 字节（实际 {length}）"
            )
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("请求体读取不完整")
        return body

    def _json_body(self) -> dict[str, Any]:
        body = self._read_body()
        if not body:
            raise ValueError("请求体为空")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"请求体不是合法 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    # ── 错误响应 ──────────────────────────────

    def _respond_exception(
        self,
        exc: BaseException,
        overrides: Optional[dict[type, str]] = None,
    ) -> None:
        status, code, extra = map_exception(exc)
        if overrides:
            for exc_type, override in overrides.items():
                if isinstance(exc, exc_type):
                    code = override
                    break
        if status >= 500:
            LOGGER.warning(
                "请求失败 %s %s -> %s: %s", self.command, self.path, code, exc
            )
            if not isinstance(exc, vf.VisionFeedbackError):
                LOGGER.debug("%s", traceback.format_exc())
        self._send_json(
            status,
            {"error": code, "message": str(exc), "status": status, **extra},
            close=isinstance(exc, vf.PayloadTooLargeError),
        )

    def _guard(
        self, action: Any, overrides: Optional[dict[type, str]] = None
    ) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 — 统一转换为 JSON 错误响应
            self._respond_exception(exc, overrides)

    # ── 路由 ──────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        self._guard(lambda: self._dispatch("GET"))

    def do_POST(self) -> None:  # noqa: N802
        self._guard(lambda: self._dispatch("POST"))

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)

        if method == "GET" and path in JSON_ROUTES:
            self._allow_only("POST")
            return
        if method == "POST" and path in GET_ROUTES:
            self._allow_only("GET")
            return

        if method == "GET":
            if path in ("/", "/index.html"):
                self._send_html(INDEX_HTML)
                return
            if path == "/api/vision/health":
                self._send_json(200, self.service.health())
                return
            if path == "/api/vision/gallery":
                self._send_json(200, self.service.gallery())
                return
            if path == "/api/vision/status":
                self._send_json(200, self.service.device_status())
                return
            if path == "/api/vision/photo":
                index = self._query_int(query, "index")
                self._send_bytes(200, "image/jpeg", self.service.photo(index))
                return
            if path == "/api/vision/records":
                limit = self._query_int(query, "limit", default=20, maximum=200)
                self._send_json(200, self.service.records(limit=limit))
                return
        else:
            if path == "/api/vision/analyze":
                self._post_analyze()
                return
            if path == "/api/vision/recapture":
                # 允许无请求体：仅在客户端声明了长度时消费，避免连接复用错位
                if self.headers.get("Content-Length") is not None:
                    self._read_body()
                self._send_json(200, self.service.recapture())
                return
            if path == "/api/vision/feedback":
                self._guard(
                    self._post_feedback,
                    overrides={vf.FeedbackStoreError: "feedback_save_failed"},
                )
                return

        self._send_json(
            404,
            {"error": "not_found", "message": f"未知路径: {path}", "status": 404},
        )

    def _allow_only(self, method: str) -> None:
        self._send_json(
            405,
            {
                "error": "method_not_allowed",
                "message": f"该路径只接受 {method}",
                "status": 405,
            },
            headers={"Allow": method},
        )

    @staticmethod
    def _query_int(
        query: dict[str, list[str]],
        key: str,
        *,
        default: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> int:
        values = query.get(key)
        if not values:
            if default is not None:
                return default
            raise ValueError(f"缺少查询参数 {key}")
        try:
            value = int(values[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"查询参数 {key} 必须是整数") from exc
        if value < 0:
            raise ValueError(f"查询参数 {key} 不得为负数")
        if maximum is not None and value > maximum:
            raise ValueError(f"查询参数 {key} 不得超过 {maximum}")
        return value

    # ── 具体动作 ──────────────────────────────

    def _post_analyze(self) -> None:
        """两种输入：JSON `{"index": N}`（设备照片）或原始 JPEG 字节（本地图片）。"""
        content_type = (
            (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        )
        if content_type == "application/json":
            payload = self._json_body()
            index = payload.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index 必须是整数")
            record = self.service.analyze_device_photo(index)
        elif content_type == "image/jpeg":
            body = self._read_body()
            record = self.service.analyze(body, source=vf.SOURCE_LOCAL_UPLOAD)
        else:
            raise UnsupportedMediaType(
                "Content-Type 必须是 application/json（设备照片）"
                " 或 image/jpeg（本地图片）"
            )
        self._send_json(200, record)

    def _post_feedback(self) -> None:
        payload = self._json_body()
        event = self.service.submit_feedback(payload)
        self._send_json(201, {"saved": True, "feedback": event})


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    service: Optional[vf.VisionFeedbackService] = None,
) -> ThreadingHTTPServer:
    """创建（但不启动）服务实例；测试可传 `port=0` 使用临时端口。"""
    httpd = ThreadingHTTPServer((host, port), VisionFeedbackHandler)
    httpd.daemon_threads = True
    httpd.service = service or vf.VisionFeedbackService()  # type: ignore[attr-defined]
    return httpd



# ──────────────────────────────────────────────
# 单页界面（无外部资源，离线可用）
# ──────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Week 08 · 视觉反馈闭环</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--fg:#e6edf3;--dim:#8b949e;
--accent:#58a6ff;--ok:#3fb950;--warn:#d29922;--err:#f85149;--chip:#21262d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:18px 22px 10px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:19px}
h2{margin:18px 0 8px;font-size:15px;color:var(--accent)}
.sub{color:var(--dim);font-size:12px}
main{padding:14px 18px 40px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.chip{background:var(--chip);border:1px solid var(--line);border-radius:14px;
padding:3px 10px;font-size:12px;color:var(--dim)}
.chip b{color:var(--fg);font-weight:600}
.banner{border:1px solid var(--warn);background:#2d2210;color:#f0d58c;
border-radius:8px;padding:10px 12px;font-size:13px;margin:10px 0}
.banner.err{border-color:var(--err);background:#2d1216;color:#ffb4ae}
.banner.info{border-color:var(--line);background:var(--panel);color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.hint{color:var(--dim);font-size:12px}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tab{flex:1;padding:6px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--chip);color:var(--dim);cursor:pointer;font-size:13px}
.tab.on{border-color:var(--accent);color:var(--fg);background:#1f2a3a}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}
.gal-card{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#0b0f14}
.gal-card img{width:100%;height:96px;object-fit:cover;display:block;background:#000}
.gal-meta{padding:6px;font-size:11px;color:var(--dim);word-break:break-all}
button{border:1px solid var(--line);background:var(--chip);color:var(--fg);
border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12.5px}
button:hover:not(:disabled){border-color:var(--accent)}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:#1f6feb;border-color:#1f6feb;color:#fff}
button.warn{background:#8b2c2c;border-color:#b34747;color:#ffd7d7}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}
.shot{background:#000;border:1px solid var(--line);border-radius:8px;text-align:center}
.shot img{max-width:100%;max-height:240px;display:block;margin:0 auto}
.kv{display:grid;grid-template-columns:132px 1fr;gap:4px 10px;font-size:12.5px;margin:6px 0}
.kv div:nth-child(odd){color:var(--dim)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;
border:1px solid var(--line);background:var(--chip)}
.badge.ok{color:#7ee787;border-color:#2ea043}
.badge.warn{color:#f0d58c;border-color:var(--warn)}
.badge.err{color:#ffb4ae;border-color:var(--err)}
.badge.dim{color:var(--dim)}
.block{border-top:1px dashed var(--line);margin-top:12px;padding-top:8px}
.block h3{margin:0 0 4px;font-size:13px;color:var(--fg)}
.note{font-size:11.5px;color:var(--dim)}
.score{font-size:22px;font-weight:600;letter-spacing:.4px}
.verdicts{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.verdicts label{border:1px solid var(--line);border-radius:6px;padding:6px 10px;
cursor:pointer;background:var(--chip);font-size:12.5px}
select,textarea{width:100%;background:#0b0f14;color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:6px;font-size:12.5px;font-family:inherit}
textarea{min-height:56px;resize:vertical}
.rec{width:100%;border-collapse:collapse;font-size:11.5px}
.rec th,.rec td{border-bottom:1px solid var(--line);padding:5px 4px;text-align:left;
vertical-align:top;color:var(--fg)}
.rec th{color:var(--dim);font-weight:500}
.rec tr:hover td{background:#1b2129;cursor:pointer}
.mono{font-family:Consolas,monospace;font-size:11.5px;word-break:break-all}
.warn-text{color:#f0d58c}
.err-text{color:#ffb4ae}
.ok-text{color:#7ee787}
</style>
</head>


<body>
<header>
  <h1>Week 08 · 视觉反馈闭环（本地 DBCNN + 规则诊断 + 人工反馈）</h1>
  <div class="sub">DBCNN 只输出质量分数；Week 07 规则诊断与模型输出分开展示；人工反馈单独保存，不写回模型结果。</div>
  <div id="chips" class="chips"></div>
</header>
<main>
  <div id="banner" class="banner info">正在读取服务状态…</div>
  <div class="grid">
    <section class="card">
      <h2>1 · 选择输入</h2>
      <div class="tabs">
        <button id="tabDevice" class="tab on" type="button">设备照片</button>
        <button id="tabLocal" class="tab" type="button">本地图片</button>
      </div>
      <div id="devicePane">
        <div class="row">
          <button id="btnRefreshGal" type="button">刷新照片</button>
          <button id="btnRecapture" class="warn" type="button">重新采集</button>
        </div>
        <div id="galMsg" class="hint">尚未读取。</div>
        <div id="galGrid" class="gal"></div>
        <div class="note">重新采集复用设备既有 <span class="mono">POST /api/camera/capture</span>，
          不改变 Week 07 定时抓拍策略；不会自动重复抓拍。</div>
      </div>
      <div id="localPane" style="display:none">
        <div class="row">
          <input id="fileInput" type="file" accept="image/jpeg">
          <button id="btnLocalAnalyze" class="primary" type="button">分析这张图片</button>
        </div>
        <div id="localMsg" class="hint">图片只发送到本机 127.0.0.1 服务，不上传云端。</div>
      </div>
    </section>

    <section class="card">
      <h2>2 · 结果（模型 / 规则 / 判定分列）</h2>
      <div id="resultPane"><div class="hint">尚未分析。选择设备照片或本地图片后开始。</div></div>
    </section>

    <section class="card">
      <h2>3 · 人工反馈</h2>
      <div id="fbTarget" class="hint">尚未选择记录。</div>
      <div id="verdictBox" class="verdicts">
        <label><input type="radio" name="verdict" value="usable"> 可用</label>
        <label><input type="radio" name="verdict" value="unusable"> 不可用</label>
        <label><input type="radio" name="verdict" value="undetermined"> 无法判断</label>
      </div>
      <select id="fbReason" disabled>
        <option value="">（可选）纠正原因</option>
        <option value="score_mismatch">模型分数与画面不符</option>
        <option value="too_dark">画面过暗</option>
        <option value="too_bright">画面过曝</option>
        <option value="blurred">画面模糊 / 失焦</option>
        <option value="occluded">镜头被遮挡（我的观察）</option>
        <option value="wrong_scene">拍错场景 / 目标</option>
        <option value="other">其他（需填写说明）</option>
      </select>
      <div class="row"><textarea id="fbNote" maxlength="500" disabled
        placeholder="可选说明；选择“其他”时必填"></textarea></div>
      <div class="row"><button id="btnFeedback" class="primary" disabled type="button">保存反馈</button></div>
      <div id="fbMsg" class="hint"></div>
      <div id="fbHistory" class="block"><h3>已保存的反馈</h3>
        <div class="hint">尚未保存。</div></div>
      <div class="note">反馈来源标记为 <span class="mono">user_feedback</span>；
        “镜头被遮挡”只代表你的观察，不会由低分自动推断。</div>

      <h2>4 · 最近记录</h2>
      <div class="hint">点击一行可重新查看该次结果的对应关系。</div>
      <table class="rec"><thead><tr>
        <th>时间</th><th>记录 / 设备请求</th><th>分数</th><th>判定</th>
        <th>规则</th><th>人工反馈</th>
      </tr></thead><tbody id="recordsBody">
        <tr><td colspan="6" class="hint">尚未读取。</td></tr>
      </tbody></table>
    </section>
  </div>
</main>
<script>
'use strict';
var $=function(id){return document.getElementById(id);};
var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
var state={health:null,gallery:null,current:null,currentImg:null,records:{}};

function api(url,opts){
  return fetch(url,opts).then(function(r){
    return r.text().then(function(t){
      var d;
      try{d=JSON.parse(t);}catch(e){d={error:'bad_response',message:t.slice(0,200)};}
      if(!r.ok){
        var msg=d.error||('HTTP '+r.status);
        if(d.message){msg+=': '+d.message;}
        throw new Error(msg);
      }
      return d;
    });
  });
}
function fmtTime(iso){return iso?String(iso).replace('T',' '):'—';}
function fmtRel(ms){return (ms==null)?'—':((Number(ms)/1000).toFixed(1)+' s');}
function capLabel(st,reason){
  var labels={idle:'空闲',capturing:'采集中',ok:'采集成功',unusable:'画面不可用',failed:'采集失败'};
  var text=labels[st]||('状态 '+(st||'—'));
  if(st==='unusable'&&reason){text+='（'+reason+'）';}
  return text;
}

function loadHealth(){
  return api('/api/vision/health').then(function(h){
    state.health=h;
    var chips=[
      '模型版本 <b>'+esc(h.model.model_version)+'</b>',
      '模型文件 <b>'+(h.model.file_exists?'已找到':(h.model.path_configured?'缺失':'未配置'))+'</b>',
      '阈值 <b>'+(h.threshold.configured?esc(String(h.threshold.value)):'未配置')+'</b>',
      '设备 <b>'+(h.device.configured?esc(h.device.endpoint_masked||'已配置'):'未配置')+'</b>',
      '本地记录 <b>'+esc(h.storage.data_dir)+'</b>'
    ];
    $('chips').innerHTML=chips.map(function(t){return '<span class="chip">'+t+'</span>';}).join('');
    var banner=$('banner');
    if(!h.model.path_configured||!h.model.file_exists){
      banner.className='banner err';
      banner.innerHTML='模型未就绪：需要设置 <span class="mono">VISION_DBCNN_MODEL_PATH</span> '+
        '指向本地 <span class="mono">dbcnn_model.onnx</span>。推理失败时不会退回规则判断。';
    }else if(h.threshold.error){
      banner.className='banner err';
      banner.textContent='阈值配置非法：'+h.threshold.error;
    }else if(!h.threshold.configured){
      banner.className='banner';
      banner.innerHTML='<b>分数尚未校准</b>：没有正式阈值，因此所有结果标记为 '+
        '<span class="mono">needs_review</span>，不使用 0.5 之类的默认值。'+
        '分数是原始模型输出，<b>不代表准确率或正确概率</b>。';
    }else{
      banner.className='banner info';
      banner.textContent='阈值已显式配置为 '+h.threshold.value+'（仅表示已配置，不等同于校准完成）。';
    }
    return h;
  }).catch(function(e){
    $('banner').className='banner err';
    $('banner').textContent='无法读取服务状态：'+e.message;
  });
}

function loadGallery(silent){
  var msg=$('galMsg'),grid=$('galGrid');
  if(state.health&&!state.health.device.configured){
    msg.className='hint warn-text';
    msg.textContent='未配置设备地址（VISION_DEVICE_BASE_URL / --device-url）。'+
      '设备照片与重新采集不可用；可改用“本地图片”。';
    grid.innerHTML='';
    $('btnRecapture').disabled=true;
    return Promise.resolve(null);
  }
  if(!silent){msg.className='hint';msg.textContent='读取设备照片…';}
  return api('/api/vision/gallery').then(function(g){
    state.gallery=g;
    var photos=g.photos||[];
    if(!photos.length){
      if(!silent){msg.textContent='设备当前没有保留照片。';}
      grid.innerHTML='';
      return g;
    }
    if(!silent){
      msg.textContent='保留 '+(g.retained||photos.length)+' 张 / 上限 '+(g.retain_limit||'—')+
        '，累计释放 '+(g.evicted||0)+' 张。';
    }
    grid.innerHTML=photos.map(function(p){
      var t=Date.now();
      return '<div class="gal-card"><img src="/api/vision/photo?index='+encodeURIComponent(p.index)+
        '&_t='+t+'" alt="photo"><div class="gal-meta">index '+esc(p.index)+'<br>'+
        esc(p.request_id||'')+'<br>画面状态 '+esc(p.img_status||'—')+' · '+fmtRel(p.captured_ms)+
        '<br><button data-index="'+esc(p.index)+'">分析这张</button></div></div>';
    }).join('');
    return g;
  }).catch(function(e){
    if(!silent){
      msg.className='hint err-text';
      msg.textContent='设备不可用：'+e.message;
    }
    grid.innerHTML='';
    return null;
  });
}

function analyzeIndex(index){
  $('resultPane').innerHTML='<div class="hint">本地 CPU 推理中…（DBCNN）</div>';
  state.currentImg='/api/vision/photo?index='+encodeURIComponent(index)+'&_t='+Date.now();
  api('/api/vision/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({index:Number(index)})})
  .then(function(rec){renderResult(rec);})
  .catch(function(e){
    $('resultPane').innerHTML='<div class="hint err-text">分析失败：'+esc(e.message)+
      '<br>模型/设备错误会明确返回，不会用规则结果冒充模型结果。</div>';
  });
}

function analyzeLocal(){
  var input=$('fileInput');
  var file=input.files&&input.files[0];
  if(!file){$('localMsg').className='hint warn-text';$('localMsg').textContent='请先选择一张 JPEG 图片。';return;}
  if(file.type&&file.type!=='image/jpeg'){
    $('localMsg').className='hint err-text';
    $('localMsg').textContent='只接受 JPEG（当前 '+file.type+'）。';
    return;
  }
  var btn=$('btnLocalAnalyze');
  btn.disabled=true;
  $('localMsg').className='hint';
  $('localMsg').textContent='本地 CPU 推理中…（DBCNN）';
  $('resultPane').innerHTML='<div class="hint">本地 CPU 推理中…（DBCNN）</div>';
  api('/api/vision/analyze',{method:'POST',headers:{'Content-Type':'image/jpeg'},body:file})
  .then(function(rec){
    if(state.currentImg&&state.currentImg.indexOf('blob:')===0){URL.revokeObjectURL(state.currentImg);}
    state.currentImg=URL.createObjectURL(file);
    renderResult(rec);
    $('localMsg').className='hint ok-text';
    $('localMsg').textContent='已分析：'+rec.record_id;
  })
  .catch(function(e){
    $('localMsg').className='hint err-text';
    $('localMsg').textContent='分析失败：'+e.message;
    $('resultPane').innerHTML='<div class="hint err-text">分析失败：'+esc(e.message)+'</div>';
  })
  .then(function(){btn.disabled=false;});
}

function renderResult(rec){
  state.current=rec;
  var a=rec.analysis||{},rd=a.rule_diagnostics||{},dev=(rec.input||{}).device||{};
  var ref=(rec.input||{}).image_ref||{};
  var labels={ok:'可采纳',low_score:'低分（建议重采）',needs_review:'需要人工复核',unknown:'未知 / 拒识'};
  var dcls=(a.decision==='ok')?'ok':((a.decision==='low_score')?'err':((a.decision==='needs_review')?'warn':'dim'));
  var score=(a.score==null)?'—':String(a.score);
  var img=state.currentImg;
  if(!img&&dev.index!=null){img='/api/vision/photo?index='+encodeURIComponent(dev.index)+'&_t='+Date.now();}
  var html='';
  if(img){html+='<div class="shot"><img src="'+esc(img)+'" alt="输入图像"></div>';}
  html+='<div class="block"><h3>模型（DBCNN，仅分数）</h3>'+
    '<div class="row"><span class="score">'+esc(score)+'</span>'+
    '<span class="hint">原始质量分数，未四舍五入</span></div>'+
    '<div class="kv">'+
    '<div>model_version</div><div>'+esc(a.model_version||'—')+'</div>'+
    '<div>score_source</div><div>'+esc(a.score_source||'—')+'</div>'+
    '<div>score_kind</div><div>'+esc(a.score_kind||'—')+'</div>'+
    '<div>label</div><div>'+esc(a.label==null?'null（模型不输出类别）':a.label)+'</div>'+
    '<div>inference_ms</div><div>'+esc(a.inference_ms==null?'—':a.inference_ms)+'</div>'+
    '<div>权重 SHA-256</div><div class="mono">'+esc((a.model_sha256||'').slice(0,16)||'—')+'</div>'+
    '</div><div class="note">分数不代表准确率、正确概率或已校准的可用性。</div></div>';
  html+='<div class="block"><h3>判定状态（不是准确率）</h3>'+
    '<div class="row"><span class="badge '+dcls+'">'+esc(labels[a.decision]||a.decision||'—')+'</span>'+
    '<span class="hint">decision=<span class="mono">'+esc(a.decision||'—')+'</span>'+
    ' · reject_reason=<span class="mono">'+esc(a.reject_reason||'null')+'</span></span></div>'+
    '<div class="kv">'+
    '<div>threshold_status</div><div>'+esc(a.threshold_status||'—')+'</div>'+
    '<div>score_threshold</div><div>'+esc(a.score_threshold==null?'未配置（不设默认值）':a.score_threshold)+'</div>'+
    '<div>review_required</div><div>'+esc(a.review_required==null?'—':String(a.review_required))+'</div>'+
    '</div></div>';
  html+='<div class="block"><h3>Week 07 规则诊断（规则，不是模型输出）</h3>'+
    '<div class="kv">'+
    '<div>来源</div><div class="mono">'+esc(rd.source||'—')+'</div>'+
    '<div>available</div><div>'+esc(rd.available==null?'—':String(rd.available))+'</div>'+
    '<div>规则 label</div><div>'+esc(rd.available===false?'依赖不可用（不代表图像无效）':(rd.label||'—'))+'</div>'+
    '<div>mean_luma</div><div>'+esc((rd.metrics||{}).mean_luma==null?'—':(rd.metrics||{}).mean_luma)+'</div>'+
    '<div>contrast</div><div>'+esc((rd.metrics||{}).contrast==null?'—':(rd.metrics||{}).contrast)+'</div>'+
    '<div>detail</div><div>'+esc((rd.metrics||{}).detail==null?'—':(rd.metrics||{}).detail)+'</div>'+
    '</div><div class="note">规则 label 只描述亮度/对比度/细节，不能当作模型分类，也不等于遮挡结论。</div></div>';
  html+='<div class="block"><h3>输入与对应关系</h3><div class="kv">'+
    '<div>record_id</div><div class="mono">'+esc(rec.record_id)+'</div>'+
    '<div>输入来源</div><div>'+esc((rec.input||{}).source||'—')+'</div>'+
    '<div>设备 request_id</div><div class="mono">'+esc(dev.request_id||'—')+'</div>'+
    '<div>设备 index</div><div>'+esc(dev.index==null?'—':dev.index)+'</div>'+
    '<div>设备相对时间</div><div>'+fmtRel(dev.captured_ms)+'</div>'+
    '<div>图像 SHA-256</div><div class="mono">'+esc(ref.sha256||'—')+'</div>'+
    '<div>图像字节数</div><div>'+esc(ref.bytes_len==null?'—':ref.bytes_len)+'</div>'+
    '<div>记录时间 (UTC)</div><div>'+esc(fmtTime(rec.created_at))+'</div>'+
    '</div></div>';
  html+='<div class="block"><h3>阅读限制</h3><div class="note">'+
    '· DBCNN 只输出质量分数；分数尚未校准，不代表准确率或正确概率。<br>'+
    '· 未配置正式阈值时，判定恒为 <span class="mono">needs_review</span>，不会使用 0.5 作为生产默认值。<br>'+
    '· 低分不等于镜头遮挡；遮挡只能由你的人工反馈表达。<br>'+
    '· 人工反馈单独保存，不会改写模型分数或规则诊断。</div></div>';
  $('resultPane').innerHTML=html;
  enableFeedback(rec);
}

function enableFeedback(rec){
  $('fbTarget').innerHTML='当前记录：<span class="mono">'+esc(rec.record_id)+'</span>';
  $('fbReason').disabled=false;
  $('fbNote').disabled=false;
  $('btnFeedback').disabled=false;
  $('fbMsg').className='hint';
  $('fbMsg').textContent='';
  var history=(rec.feedback&&rec.feedback.history)||[];
  showHistory(history);
}

function showHistory(history){
  var box=$('fbHistory');
  if(!history||!history.length){
    box.innerHTML='<h3>已保存的反馈</h3><div class="hint">该记录尚无人工反馈。</div>';
    return;
  }
  box.innerHTML='<h3>已保存的反馈</h3>'+history.slice().reverse().map(function(h){
    return '<div class="hint">#'+esc(h.revision)+' · '+esc(fmtTime(h.submitted_at))+
      ' · <b>'+esc(h.verdict)+'</b>'+(h.reason?(' · '+esc(h.reason)):'')+
      (h.note?('<br>说明：'+esc(h.note)):'')+
      '<br>来源 <span class="mono">'+esc(h.label_source)+'</span></div>';
  }).join('');
}

function submitFeedback(){
  if(!state.current){$('fbMsg').textContent='请先分析一张图片。';return;}
  var checked=document.querySelector('input[name="verdict"]:checked');
  if(!checked){
    $('fbMsg').className='hint warn-text';
    $('fbMsg').textContent='请选择“可用 / 不可用 / 无法判断”。';
    return;
  }
  var btn=$('btnFeedback');
  btn.disabled=true;
  $('fbMsg').className='hint';
  $('fbMsg').textContent='保存中…';
  api('/api/vision/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({record_id:state.current.record_id,verdict:checked.value,
      reason:$('fbReason').value||null,note:$('fbNote').value||''})})
  .then(function(d){
    $('fbMsg').className='hint ok-text';
    $('fbMsg').textContent='已保存 revision '+d.feedback.revision+'（来源 '+d.feedback.label_source+'）。';
    $('fbNote').value='';
    loadRecords();
    var history=(state.current.feedback&&state.current.feedback.history)||[];
    history=history.concat([d.feedback]);
    state.current.feedback={latest:d.feedback,history:history};
    showHistory(history);
  })
  .catch(function(e){
    $('fbMsg').className='hint err-text';
    $('fbMsg').textContent='保存失败：'+e.message;
  })
  .then(function(){btn.disabled=false;});
}

function loadRecords(){
  api('/api/vision/records?limit=20').then(function(d){
    var rows=d.records||[];
    state.records={};
    if(!rows.length){
      $('recordsBody').innerHTML='<tr><td colspan="6" class="hint">暂无记录。</td></tr>';
      return;
    }
    $('recordsBody').innerHTML=rows.map(function(r){
      state.records[r.record_id]=r;
      var a=r.analysis||{},rd=a.rule_diagnostics||{},dev=(r.input||{}).device||{};
      var fb=r.feedback&&r.feedback.latest;
      var fbText=fb?(fb.verdict+(fb.reason?(' / '+fb.reason):'')):'—';
      return '<tr data-record="'+esc(r.record_id)+'">'+
        '<td>'+esc(fmtTime(r.created_at))+'</td>'+
        '<td class="mono">'+esc(r.record_id)+(dev.request_id?('<br>'+esc(dev.request_id)):'')+'</td>'+
        '<td>'+esc(a.score==null?'—':String(a.score))+'</td>'+
        '<td>'+esc(a.decision||'—')+'</td>'+
        '<td>'+esc(rd.available===false?'unavailable':(rd.label||'—'))+'</td>'+
        '<td>'+esc(fbText)+'</td></tr>';
    }).join('');
  }).catch(function(e){
    $('recordsBody').innerHTML='<tr><td colspan="6" class="hint err-text">读取失败：'+esc(e.message)+'</td></tr>';
  });
}

function recapture(){
  var btn=$('btnRecapture'),msg=$('galMsg');
  btn.disabled=true;
  msg.className='hint';
  msg.textContent='下发抓拍指令…';
  var before=(state.gallery&&state.gallery.total)||0;
  api('/api/vision/recapture',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
  .then(function(d){
    msg.textContent='已下发 request_id='+(d.request_id||'—')+'；等待设备回执…';
    var tries=0;
    var timer=setInterval(function(){
      tries++;
      api('/api/vision/status').then(function(st){
        msg.textContent='采集状态：'+capLabel(st.cap_task_status,st.last_img_status)+
          '（request_id='+(d.request_id||'—')+'）';
      }).catch(function(){});
      loadGallery(true).then(function(g){
        var done=(g&&g.total>before)||tries>=16;
        if(!done){return;}
        clearInterval(timer);
        btn.disabled=false;
        if(g&&g.total>before){
          msg.className='hint ok-text';
          msg.textContent='已收到新照片（total '+g.total+'），可点击“分析这张”。';
        }else{
          msg.className='hint warn-text';
          msg.textContent='8 秒内未观察到新照片；可手动刷新或查看设备状态。';
        }
      });
    },500);
  })
  .catch(function(e){
    btn.disabled=false;
    msg.className='hint err-text';
    msg.textContent='重新采集失败：'+e.message;
  });
}

function switchTab(local){
  $('tabDevice').className=local?'tab':'tab on';
  $('tabLocal').className=local?'tab on':'tab';
  $('devicePane').style.display=local?'none':'';
  $('localPane').style.display=local?'':'none';
}

function init(){
  $('tabDevice').addEventListener('click',function(){switchTab(false);});
  $('tabLocal').addEventListener('click',function(){switchTab(true);});
  $('btnRefreshGal').addEventListener('click',function(){loadGallery(false);});
  $('btnRecapture').addEventListener('click',recapture);
  $('btnLocalAnalyze').addEventListener('click',analyzeLocal);
  $('btnFeedback').addEventListener('click',submitFeedback);
  $('galGrid').addEventListener('click',function(ev){
    var btn=ev.target.closest('button[data-index]');
    if(btn){analyzeIndex(btn.getAttribute('data-index'));}
  });
  $('recordsBody').addEventListener('click',function(ev){
    var tr=ev.target.closest('tr[data-record]');
    if(!tr){return;}
    var rec=state.records[tr.getAttribute('data-record')];
    if(!rec){return;}
    state.currentImg=null;
    renderResult(rec);
  });
  loadHealth().then(function(){loadGallery(false);loadRecords();});
}

init();
</script>
</body></html>
"""




# ──────────────────────────────────────────────
# 启动入口（显式运行，不做常驻自动启动）
# ──────────────────────────────────────────────


def _configure_logging(log_file: Optional[str]) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """命令行入口：创建并前台运行服务，Ctrl+C 退出。"""
    parser = argparse.ArgumentParser(
        description=(
            "Week 08 W8-3 本地视觉反馈服务：DBCNN 真实推理 + 规则诊断 + 人工反馈"
        )
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="绑定地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="端口（默认 8090）")
    parser.add_argument(
        "--device-url",
        default=None,
        help=f"设备基地址，如 http://<device>；省略则读 {vf.ENV_DEVICE_BASE_URL}",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"本地记录目录；省略则读 {vf.ENV_DATA_DIR}，再回落到 .workbuddy/vision-feedback",
    )
    parser.add_argument(
        "--device-timeout",
        type=float,
        default=vf.DEVICE_TIMEOUT_SECONDS,
        help="设备 HTTP 超时秒数",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="等价于设置 VISION_DBCNN_MODEL_PATH（本地 .onnx 路径）",
    )
    parser.add_argument(
        "--model-version",
        default=None,
        help="等价于设置 VISION_DBCNN_MODEL_VERSION",
    )
    parser.add_argument("--log-file", default=None, help="额外写入的日志文件")
    args = parser.parse_args(argv)

    if not args.host or not str(args.host).strip():
        parser.error("--host 不能为空")
    if not (1 <= int(args.port) <= 65535):
        parser.error("--port 必须位于 1–65535")

    if args.model_path:
        os.environ[ENV_MODEL_PATH] = args.model_path
    if args.model_version:
        os.environ[ENV_MODEL_VERSION] = args.model_version

    _configure_logging(args.log_file)

    if not vf.is_loopback_host(args.host):
        LOGGER.warning(
            "绑定地址 %s 不是回环地址：局域网内其他设备可以访问本服务，"
            "且本服务没有认证机制。",
            args.host,
        )

    try:
        device = vf.DeviceClient(args.device_url, timeout=args.device_timeout)
    except ValueError as exc:
        LOGGER.error("设备地址非法：%s", exc)
        return 2

    service = vf.VisionFeedbackService(data_dir=args.data_dir, device=device)
    httpd = create_server(args.host, args.port, service)
    host, port = httpd.server_address[0], httpd.server_address[1]

    health = service.health()
    LOGGER.info("视觉反馈服务已启动: http://%s:%s/", host, port)
    LOGGER.info(
        "模型: path_configured=%s file_exists=%s model_version=%s",
        health["model"]["path_configured"],
        health["model"]["file_exists"],
        health["model"]["model_version"],
    )
    LOGGER.info(
        "阈值: %s（未配置时判定恒为 needs_review）",
        health["threshold"]["status"],
    )
    LOGGER.info(
        "设备: configured=%s endpoint=%s",
        health["device"]["configured"],
        health["device"]["endpoint_masked"],
    )
    LOGGER.info("记录目录: %s", health["storage"]["data_dir"])
    if not health["model"]["file_exists"]:
        LOGGER.warning(
            "模型文件未就绪：分析请求会返回 model_not_configured，不会退回规则判断。"
        )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("收到中断信号，正在停止服务")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI 入口
    raise SystemExit(main())


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "INDEX_HTML",
    "VisionFeedbackHandler",
    "create_server",
    "main",
    "map_exception",
]
