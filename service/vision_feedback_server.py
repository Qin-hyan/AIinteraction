"""
vision_feedback_server.py — Week 08 W8-3: 本地视觉反馈闭环 API 服务

作用：
  - 在 `127.0.0.1` 上提供一个**仅 API、无用户可见页面**的本地接口，
    由 ESP32 Dashboard 内嵌的「视觉反馈」面板调用（面板本身不在本服务）：
      GET  /api/vision/health        模型/阈值/设备/存储配置摘要（脱敏）
      GET  /api/vision/gallery       代理设备 `/api/camera/gallery`
      GET  /api/vision/status        代理设备 `/api/camera/status`
      GET  /api/vision/photo?index=N 代理设备照片 JPEG（不落盘）
      POST /api/vision/analyze       本地 JPEG（请求体）或设备照片（`{"index":N}`）
      POST /api/vision/recapture     代理设备 `/api/camera/capture`（按需抓拍）
      GET  /api/vision/records       最近分析记录 + 人工反馈历史
      GET  /api/vision/events        视觉事件列表 + 当前 active
      GET  /api/vision/events/active 当前最高优先级待响应事件
      POST /api/vision/events/respond 确认/忽略/稍后处理事件
      POST /api/vision/feedback      保存人工反馈（可用/不可用/无法判断 + 原因）

跨源：
  - 服务由浏览器从 ESP32 Dashboard 页面（非 127.0.0.1 源）调用，属跨源访问；
  - 通过 `allowed_origin()` 做有边界的 Origin 校验（仅回环/私网/链路本地
    IP 字面量 + 显式配置来源），命中时原样回显，**绝不返回 `*`**；
  - 预检（OPTIONS）同时处理 Chrome 的 Private Network Access，回显
    `Access-Control-Allow-Private-Network: true`（仅当请求声明时）。

边界：
  - 独立进程、独立端口（默认 8090），**不并入**语音服务（8080）；
  - 默认只监听 `127.0.0.1`；改用其他绑定地址时会在启动日志中明确告警；
  - 不修改固件、不修改 Week 07 定时抓拍策略、不自动启动常驻服务；
  - 设备地址必须由 `VISION_DEVICE_BASE_URL` 或 `--device-url` 显式提供，
    未配置时设备相关接口返回 `503 device_not_configured`（基本功能不依赖它，
    内嵌面板优先直接调用 Dashboard 同源设备 API）；
  - 图像不持久化；仅保存 SHA-256/字节数与设备侧引用；
  - 模型不可用/推理失败时返回明确错误，**不降级**为规则判断；
  - 不设生产阈值：未配置 `VISION_DBCNN_SCORE_THRESHOLD` 时判定恒为
    `needs_review`，面板明确提示分数尚未校准。

启动（使用已验证的隔离 venv，见 `docs/vision-feedback.md`）：
  <venv>\\Scripts\\python.exe -m service.vision_feedback_server --port 8090
"""

from __future__ import annotations

import argparse
import ipaddress
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
from . import vision_event as ve
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
    "/api/vision/events/respond",
}
GET_ROUTES = {
    "/api/vision/health",
    "/api/vision/gallery",
    "/api/vision/status",
    "/api/vision/photo",
    "/api/vision/records",
    "/api/vision/events",
    "/api/vision/events/active",
}

LOGGER = logging.getLogger("vision.feedback")


# ──────────────────────────────────────────────
# CORS / Private Network Access（跨源安全边界）
# ──────────────────────────────────────────────

CORS_ALLOW_METHODS = "GET, POST, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type"
CORS_PNA_HEADER = "Access-Control-Allow-Private-Network"
ENV_ALLOWED_ORIGINS = "VISION_ALLOWED_ORIGINS"


def _extra_allowed_origins() -> set[str]:
    """读取显式追加的来源（逗号分隔），默认空。"""
    raw = os.environ.get(ENV_ALLOWED_ORIGINS, "") or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _is_loopback_host(host: str) -> bool:
    """回环主机：localhost 或任意回环 IP（127.0.0.1、::1 等）。"""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_host(host: str) -> bool:
    """私网/链路本地 IP（局域网地址），构成浏览器来源的明确边界。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local


def allowed_origin(origin: Optional[str]) -> Optional[str]:
    """校验浏览器 `Origin` 是否落在允许边界内。

    允许：回环地址、私网/链路本地 IP 字面量，以及显式配置的来源；
    拒绝：`null`、公网地址、非 http(s) 协议、带路径/查询/片段的来源。
    命中时原样回显该 origin（绝不返回 `*`）；否则返回 `None`。
    """
    if not origin or origin == "null":
        return None
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.path not in ("", "/"):
        return None
    if parsed.query or parsed.fragment:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if _is_loopback_host(host) or _is_private_host(host):
        return origin
    if origin in _extra_allowed_origins():
        return origin
    return None


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
    if isinstance(exc, ve.VisionEventValidationError):
        return 400, "invalid_event_response", {}
    if isinstance(exc, ve.VisionEventNotFound):
        return 404, "event_not_found", {}
    if isinstance(exc, ve.VisionEventConflict):
        return 409, "event_state_conflict", {}
    if isinstance(exc, ve.VisionEventStoreError):
        return 500, "event_storage_error", {}
    if isinstance(exc, ve.VisionEventError):
        return 500, "vision_event_error", {}
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

    def _apply_cors_headers(self) -> None:
        """按明确边界回显允许的 Origin，并处理 Private Network Access 预检。"""
        allowed = allowed_origin(self.headers.get("Origin"))
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", CORS_ALLOW_METHODS)
        self.send_header("Access-Control-Allow-Headers", CORS_ALLOW_HEADERS)
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header(CORS_PNA_HEADER, "true")

    def do_OPTIONS(self) -> None:  # noqa: N802
        """处理跨源预检（含 Private Network Access）。"""
        self.send_response(204)
        self._common_headers()
        self._apply_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        self._apply_cors_headers()
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._common_headers()
        self._apply_cors_headers()
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
            if path == "/":
                self._send_json(
                    200,
                    {
                        "service": "vision_feedback_api",
                        "note": (
                            "API-only 服务：不提供用户可见页面，"
                            "仅供 ESP32 Dashboard 内嵌面板调用。"
                        ),
                        "endpoints": sorted(GET_ROUTES | JSON_ROUTES),
                    },
                )
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
            if path == "/api/vision/events":
                limit = self._query_int(query, "limit", default=20, maximum=200)
                self._send_json(200, self.service.events(limit=limit))
                return
            if path == "/api/vision/events/active":
                self._send_json(200, {"active": self.service.active_event()})
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
            if path == "/api/vision/events/respond":
                self._guard(self._post_event_response)
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

    def _post_event_response(self) -> None:
        payload = self._json_body()
        event = self.service.respond_event(payload)
        self._send_json(200, {"saved": True, "event": event})


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
    "allowed_origin",
    "VisionFeedbackHandler",
    "create_server",
    "main",
    "map_exception",
]
