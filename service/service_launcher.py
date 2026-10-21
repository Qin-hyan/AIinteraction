"""PC-side local service launcher for the AIinteraction prototype.

为什么需要它
------------
ESP32 Dashboard 是浏览器页面。浏览器出于安全限制，**不能**直接启动电脑上的
Python 进程。因此本模块提供一个仅监听 ``127.0.0.1`` 的本地启动器：

* Dashboard 打开时调用 ``POST /api/services/ensure``；
* 启动器检查语音服务（默认 8080）和视觉反馈服务（默认 8090）；
* 未运行时以固定命令启动，已运行/由其他终端启动时保持不动（幂等）；
* 页面随后轮询 ``GET /api/services`` 显示真实状态。

安全边界
--------
* 启动器只允许绑定回环地址，不对局域网暴露"可启动进程"的能力；
* POST 必须是 ``application/json``，且浏览器 ``Origin`` 必须落在回环/私网边界内；
* 只接受已知服务名与配置中的固定命令，不从 HTTP 请求读取任意命令；
* 不读取、不记录任何凭据；日志只写本地 ``.workbuddy/logs``。

它不替代语音/视觉服务，也不修改其 API、模型或存储格式。
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8079
DEFAULT_CONFIG_PATH = REPO_ROOT / "service" / "launcher.config.json"
ENV_CONFIG = "AI_LAUNCHER_CONFIG"
ENV_ALLOWED_ORIGINS = "LAUNCHER_ALLOWED_ORIGINS"
ENV_MODEL_PATH = "VISION_DBCNN_MODEL_PATH"
ENV_DEVICE_URL = "VISION_DEVICE_BASE_URL"

MAX_BODY_BYTES = 8192
HTTP_TIMEOUT = 1.2
START_TIMEOUT = 20.0
STOP_TIMEOUT = 5.0

CORS_ALLOW_METHODS = "GET, POST, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type"
CORS_PNA_HEADER = "Access-Control-Allow-Private-Network"

LOGGER = logging.getLogger("launcher")

# 状态取值（API 与页面共用；不得把"端口被占用"写成"服务已启动"）
STATE_RUNNING = "running"
STATE_RUNNING_EXTERNAL = "running_external"
STATE_STARTING = "starting"
STATE_STOPPED = "stopped"
STATE_FAILED = "failed"
STATE_PORT_CONFLICT = "port_conflict"
STATE_MISSING_PYTHON = "missing_python"
STATE_UNHEALTHY = "unhealthy"

KNOWN_SERVICES: dict[str, dict[str, Any]] = {
    "voice": {
        "module": "service.voice_control_server",
        "health_path": "/api/voice/status",
        "port": 8080,
        "log_name": "voice-8080.log",
    },
    "vision": {
        "module": "service.vision_feedback_server",
        "health_path": "/api/vision/health",
        "port": 8090,
        "log_name": "vision-8090.log",
    },
}


# ──────────────────────────────────────────────
# CORS / Private Network Access（与视觉服务保持同一安全边界）
# ──────────────────────────────────────────────


def _extra_allowed_origins() -> set[str]:
    raw = os.environ.get(ENV_ALLOWED_ORIGINS, "") or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local


def allowed_origin(origin: Optional[str]) -> Optional[str]:
    """允许回环/私网/链路本地来源；命中时原样回显，绝不返回 ``*``。"""
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
# 配置与进程规格
# ──────────────────────────────────────────────


class LauncherError(Exception):
    """启动器可预期的错误基类。"""


class UnknownServiceError(LauncherError):
    def __init__(self, name: str) -> None:
        super().__init__(f"未知服务: {name}")
        self.name = name


class ExternalServiceError(LauncherError):
    """端口上的服务不是本启动器启动的，拒绝停止/重启。"""


class PayloadTooLargeError(LauncherError):
    pass


class InvalidContentTypeError(LauncherError):
    pass


@dataclass(frozen=True)
class ServiceSpec:
    """一个受管服务的固定启动描述（命令只能来自本地配置）。"""

    name: str
    module: str
    health_path: str
    host: str
    port: int
    python: str
    log_file: Path
    extra_args: tuple[str, ...] = ()
    command_override: Optional[tuple[str, ...]] = None
    start_timeout: float = START_TIMEOUT
    check_host: str = "127.0.0.1"

    def __post_init__(self) -> None:
        # 探测地址必须与实际监听地址一致：0.0.0.0/:: 用回环探测，
        # 具体 LAN IP 则直接探测该地址，避免"端口仍被占用却报告 stopped"。
        if self.check_host == "127.0.0.1":
            host = (self.host or "").strip()
            if host in ("", "0.0.0.0", "::") or _is_loopback_host(host):
                check = "127.0.0.1"
            else:
                check = host
            object.__setattr__(self, "check_host", check)

    def command(self) -> list[str]:
        if self.command_override is not None:
            return list(self.command_override)
        return [
            self.python,
            "-m",
            self.module,
            "--host",
            self.host,
            "--port",
            str(self.port),
            *self.extra_args,
        ]


def _coerce_port(value: Any, name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} 端口必须位于 1–65535")
    return port


def _coerce_args(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} 的 args 必须是字符串数组")
    return tuple(value)


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """读取可选 JSON 配置；不存在时返回空配置（使用安全默认值）。"""
    config_path = Path(
        path or os.environ.get(ENV_CONFIG) or DEFAULT_CONFIG_PATH
    ).expanduser()
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取启动器配置 {config_path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("启动器配置必须是 JSON 对象")
    return data


def build_specs(config: dict[str, Any]) -> dict[str, ServiceSpec]:
    """由配置构造两个固定服务的启动规格。

    默认只监听回环；如果 Dashboard 的语音地址是局域网地址，需在本地配置中把
    ``services.voice.host`` 显式设为 ``0.0.0.0``（会暴露到局域网，见 README）。
    """
    services_cfg = config.get("services", {})
    if services_cfg is None:
        services_cfg = {}
    if not isinstance(services_cfg, dict):
        raise ValueError("配置字段 services 必须是 JSON 对象")

    log_dir = REPO_ROOT / ".workbuddy" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    specs: dict[str, ServiceSpec] = {}
    for name, defaults in KNOWN_SERVICES.items():
        raw = services_cfg.get(name, {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(f"services.{name} 必须是 JSON 对象")

        host = str(raw.get("host", DEFAULT_HOST)).strip()
        if not host:
            raise ValueError(f"services.{name}.host 不能为空")
        port = _coerce_port(raw.get("port", defaults["port"]), f"services.{name}")
        python = str(raw.get("python") or sys.executable)
        extra_args = list(_coerce_args(raw.get("args"), f"services.{name}"))

        if name == "vision":
            model_path = os.environ.get(ENV_MODEL_PATH, "") or ""
            if model_path and "--model-path" not in extra_args:
                extra_args += ["--model-path", model_path]
            device_url = os.environ.get(ENV_DEVICE_URL, "") or ""
            if device_url and "--device-url" not in extra_args:
                extra_args += ["--device-url", device_url]

        log_file = Path(
            raw.get("log_file") or (log_dir / defaults["log_name"])
        ).expanduser()

        specs[name] = ServiceSpec(
            name=name,
            module=defaults["module"],
            health_path=defaults["health_path"],
            host=host,
            port=port,
            python=python,
            log_file=log_file,
            extra_args=tuple(extra_args),
            start_timeout=float(raw.get("start_timeout", START_TIMEOUT)),
        )

    return specs


# ──────────────────────────────────────────────
# 探测与进程管理
# ──────────────────────────────────────────────


def port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_health(host: str, port: int, path: str, timeout: float = HTTP_TIMEOUT) -> Optional[dict[str, Any]]:
    """GET 健康端点；仅 200 + JSON 对象视为健康，避免把任意占用端口的进程当服务。"""
    conn = HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Accept": "application/json"})
        response = conn.getresponse()
        body = response.read(65536)
        if response.status != 200:
            return None
        payload = json.loads(body.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        conn.close()


def _tail(path: Path, limit: int = 1200) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = data[-limit:].decode("utf-8", errors="replace")
    return text.strip()


def _creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    """终止受管进程及其子进程。

    Windows 上部分虚拟环境的 ``python.exe`` 会再启动真实解释器（父子进程），
    只终止父进程可能留下仍占用端口的子进程，因此使用 ``taskkill /T``。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=STOP_TIMEOUT)


class Launcher:
    """幂等服务管理器；实例只由本地 HTTP 处理器调用。"""

    def __init__(self, specs: dict[str, ServiceSpec]) -> None:
        if not specs:
            raise ValueError("至少需要一个服务规格")
        self._specs = dict(specs)
        self._locks = {name: threading.Lock() for name in specs}
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._logs: dict[str, Any] = {}
        self._states: dict[str, str] = {name: STATE_STOPPED for name in specs}
        self._last_detail: dict[str, str] = {}
        self._ensure_slots = threading.BoundedSemaphore(2)

    # ── 状态 ──────────────────────────────────

    def _status(self, name: str, *, probe: bool = True) -> dict[str, Any]:
        spec = self._specs[name]
        proc = self._procs.get(name)
        pid = proc.pid if proc is not None and proc.poll() is None else None
        payload: dict[str, Any] = {
            "name": name,
            "state": self._states.get(name, STATE_STOPPED),
            "managed": pid is not None,
            "pid": pid,
            "host": spec.host,
            "port": spec.port,
            "health_path": spec.health_path,
            "log_file": str(spec.log_file),
            "detail": "",
            "degraded": False,
        }
        if not probe:
            payload["detail"] = self._last_detail.get(name, "")
            return payload

        is_open = port_open(spec.check_host, spec.port)
        if not is_open:
            if pid is not None:
                payload["state"] = STATE_STARTING
                payload["detail"] = "进程已启动，等待端口/健康检查"
            elif self._states.get(name) in (
                STATE_FAILED,
                STATE_PORT_CONFLICT,
                STATE_MISSING_PYTHON,
            ):
                payload["state"] = self._states[name]
                payload["detail"] = self._last_detail.get(name, "")
            else:
                payload["state"] = STATE_STOPPED
                payload["detail"] = "未运行"
            return payload

        healthy = http_health(spec.check_host, spec.port, spec.health_path)
        if healthy is not None:
            payload["state"] = STATE_RUNNING if pid is not None else STATE_RUNNING_EXTERNAL
            payload["detail"] = (
                "健康检查通过（由本启动器启动）"
                if pid is not None
                else "健康检查通过（由其他终端启动，启动器不会停止它）"
            )
            if healthy.get("ok") is False:
                payload["degraded"] = True
                payload["detail"] += "；健康检查报告 ok=false"
            if name == "vision":
                model = healthy.get("model")
                if isinstance(model, dict) and model.get("file_exists") is False:
                    payload["degraded"] = True
                    payload["detail"] += "；模型文件未就绪，分析请求会失败"
        elif pid is not None:
            payload["state"] = STATE_STARTING
            payload["detail"] = "进程已启动，等待健康检查"
        else:
            payload["state"] = STATE_UNHEALTHY
            payload["detail"] = "端口已被占用，但健康检查未通过"
        return payload

    def status(self, name: Optional[str] = None) -> dict[str, Any] | list[dict[str, Any]]:
        names = self._resolve_names([name] if name else None)
        results = [self._status(item) for item in names]
        return results[0] if name else results

    # ── 启动 ──────────────────────────────────

    def ensure(self, name: str) -> dict[str, Any]:
        self._require_name(name)
        if not self._ensure_slots.acquire(blocking=False):
            return self._status(name, probe=False) | {
                "state": STATE_STARTING,
                "detail": "已有启动检查正在进行；请稍后刷新状态",
            }
        try:
            return self._ensure_impl(name)
        finally:
            self._ensure_slots.release()

    def _ensure_impl(self, name: str) -> dict[str, Any]:
        with self._locks[name]:
            spec = self._specs[name]

            proc = self._procs.get(name)
            if proc is not None and proc.poll() is None:
                self._states[name] = STATE_STARTING
                return self._wait_until_healthy(name, spec, proc)
            if proc is not None:
                # 上一次进程已退出：关闭旧日志句柄，允许重新启动。
                self._close_log(name)
                self._procs.pop(name, None)

            if port_open(spec.check_host, spec.port):
                healthy = http_health(spec.check_host, spec.port, spec.health_path)
                if healthy is not None:
                    self._states[name] = STATE_RUNNING
                    return self._status(name)
                self._states[name] = STATE_PORT_CONFLICT
                self._set_detail(name, "端口已被其他进程占用；为避免误杀，启动器不会启动")
                return self._status(name, probe=False) | {
                    "state": STATE_PORT_CONFLICT,
                    "detail": "端口已被其他进程占用；请先确认占用进程",
                }

            python_path = Path(spec.python)
            if (python_path.is_absolute() or os.sep in spec.python) and not python_path.exists():
                self._states[name] = STATE_MISSING_PYTHON
                self._set_detail(name, f"Python 解释器不存在: {spec.python}")
                return self._status(name, probe=False)

            spec.log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(spec.log_file, "ab", buffering=0)
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            existing_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                str(REPO_ROOT) + (os.pathsep + existing_path if existing_path else "")
            )
            try:
                proc = subprocess.Popen(
                    spec.command(),
                    cwd=str(REPO_ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env=env,
                    creationflags=_creation_flags(),
                    start_new_session=(os.name != "nt"),
                )
            except OSError as exc:
                log_handle.close()
                self._states[name] = STATE_FAILED
                self._set_detail(name, f"无法启动进程: {exc}")
                return self._status(name, probe=False)

            self._procs[name] = proc
            self._logs[name] = log_handle
            self._states[name] = STATE_STARTING
            self._set_detail(name, "进程已启动，等待健康检查")
            LOGGER.info("启动服务 %s: pid=%s port=%s", name, proc.pid, spec.port)
            return self._wait_until_healthy(name, spec, proc)

    def _wait_until_healthy(
        self, name: str, spec: ServiceSpec, proc: subprocess.Popen[bytes]
    ) -> dict[str, Any]:
        deadline = time.monotonic() + spec.start_timeout
        while time.monotonic() < deadline:
            exit_code = proc.poll()
            if exit_code is not None:
                self._states[name] = STATE_FAILED
                self._set_detail(
                    name,
                    f"进程已退出（exit={exit_code}）；日志尾部: {_tail(spec.log_file)}",
                )
                self._close_log(name)
                return self._status(name, probe=False)
            if http_health(spec.check_host, spec.port, spec.health_path) is not None:
                self._states[name] = STATE_RUNNING
                self._set_detail(name, "")
                return self._status(name)
            time.sleep(0.2)

        # 超时后终止半启动进程，避免留下占用端口的孤儿进程。
        if proc.poll() is None:
            _terminate_tree(proc)
        self._states[name] = STATE_FAILED
        if not self._wait_port_closed(spec):
            detail = f"启动超时且端口仍被占用；日志尾部: {_tail(spec.log_file)}"
            self._states[name] = STATE_UNHEALTHY
            self._set_detail(name, detail)
            return self._status(name, probe=False) | {
                "state": STATE_UNHEALTHY,
                "detail": detail,
            }
        detail = f"启动超时；日志尾部: {_tail(spec.log_file)}"
        self._set_detail(name, detail)
        return self._status(name, probe=False) | {"state": STATE_FAILED, "detail": detail}

    def _wait_port_closed(self, spec: ServiceSpec, attempts: int = 15) -> bool:
        for _ in range(attempts):
            if not port_open(spec.check_host, spec.port):
                return True
            time.sleep(0.2)
        return not port_open(spec.check_host, spec.port)

    def ensure_all(self, names: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
        resolved = self._resolve_names(names)
        if len(resolved) == 1:
            return [self.ensure(resolved[0])]
        with ThreadPoolExecutor(max_workers=len(resolved)) as executor:
            return list(executor.map(self.ensure, resolved))

    # ── 停止 / 重启 ───────────────────────────

    def stop(self, name: str) -> dict[str, Any]:
        self._require_name(name)
        with self._locks[name]:
            proc = self._procs.get(name)
            if proc is None or proc.poll() is not None:
                if port_open(self._specs[name].check_host, self._specs[name].port):
                    raise ExternalServiceError(
                        f"{name} 不是由本启动器启动，拒绝停止（请回到原终端处理）"
                    )
                self._states[name] = STATE_STOPPED
                self._close_log(name)
                return self._status(name, probe=False)

            _terminate_tree(proc)
            if not self._wait_port_closed(self._specs[name]):
                self._states[name] = STATE_UNHEALTHY
                detail = "停止后端口仍被占用；可能有子进程未被终止"
                self._set_detail(name, detail)
                self._close_log(name)
                return self._status(name, probe=False) | {
                    "state": STATE_UNHEALTHY,
                    "detail": detail,
                }
            self._states[name] = STATE_STOPPED
            self._set_detail(name, "")
            self._close_log(name)
            self._procs.pop(name, None)
            LOGGER.info("已停止服务 %s", name)
            return self._status(name, probe=False)

    def restart(self, name: str) -> dict[str, Any]:
        self._require_name(name)
        proc = self._procs.get(name)
        if (proc is None or proc.poll() is not None) and port_open(
            self._specs[name].check_host, self._specs[name].port
        ):
            raise ExternalServiceError(f"{name} 不是由本启动器启动，拒绝重启")
        try:
            self.stop(name)
        except ExternalServiceError:
            raise
        return self.ensure(name)

    # ── 工具 ──────────────────────────────────

    def _resolve_names(self, names: Optional[Iterable[str]]) -> list[str]:
        if names is None:
            return list(self._specs)
        resolved = list(dict.fromkeys(names))
        if not resolved:
            raise UnknownServiceError("(empty)")
        for name in resolved:
            self._require_name(name)
        return resolved

    def _require_name(self, name: str) -> None:
        if name not in self._specs:
            raise UnknownServiceError(name)

    def _set_detail(self, name: str, detail: str) -> None:
        self._last_detail[name] = detail

    def _close_log(self, name: str) -> None:
        handle = self._logs.pop(name, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


# ──────────────────────────────────────────────
# HTTP 处理器
# ──────────────────────────────────────────────


class LauncherHandler(BaseHTTPRequestHandler):
    server_version = "AIinteractionLauncher/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def launcher(self) -> Launcher:
        return self.server.launcher  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _apply_cors_headers(self) -> None:
        allowed = allowed_origin(self.headers.get("Origin"))
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")
            # PNA 只对允许来源声明，避免把拒绝来源也标记为可访问本机网络。
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header(CORS_PNA_HEADER, "true")
        self.send_header("Access-Control-Allow-Methods", CORS_ALLOW_METHODS)
        self.send_header("Access-Control-Allow-Headers", CORS_ALLOW_HEADERS)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._apply_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._apply_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"ok": False, "error": code, "message": message})

    def _check_post_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # 本机 curl/脚本
        if allowed_origin(origin) is None:
            self._discard_small_body()
            self._send_error_json(403, "origin_not_allowed", "来源不在允许边界内")
            return False
        return True

    def _origin_is_loopback(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # 本机脚本/curl
        try:
            host = (urlsplit(origin).hostname or "").lower()
        except ValueError:
            return False
        return _is_loopback_host(host)

    def _read_json_body(self) -> dict[str, Any]:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._discard_small_body()
            raise InvalidContentTypeError("POST 必须使用 application/json")
        length_raw = self.headers.get("Content-Length")
        try:
            length = int(length_raw or "0")
        except ValueError as exc:
            raise LauncherError("Content-Length 非法") from exc
        if length < 0:
            raise LauncherError("Content-Length 非法")
        if length > MAX_BODY_BYTES:
            # 先读掉可接受范围内的小请求体，避免响应 413 前因未读数据导致 RST。
            self._discard_small_body()
            raise PayloadTooLargeError("请求体过大")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LauncherError("请求体不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise LauncherError("请求体必须是 JSON 对象")
        return payload

    def _discard_small_body(self) -> None:
        """丢弃小型请求体，避免服务器在客户端仍发送时关闭连接导致 RST。"""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return
        if 0 < length <= MAX_BODY_BYTES:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._apply_cors_headers()
        self.end_headers()

    def _public_service(self, status: dict[str, Any]) -> dict[str, Any]:
        """跨源响应脱敏：私网页面不返回绝对日志路径与日志尾部。"""
        out = dict(status)
        if out.get("log_file"):
            out["log_file"] = Path(str(out["log_file"])).name
        if not self._origin_is_loopback():
            out["detail"] = (
                ""
                if out.get("state") in (STATE_RUNNING, STATE_RUNNING_EXTERNAL)
                else "详情请在本机启动器控制台查看"
            )
        return out

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send_html(200, _console_html(self.launcher, self.server.server_address[1]))
                return
            if path == "/api/launcher/health":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "service": "service_launcher",
                        "version": "week08_autostart_v1",
                        "host": DEFAULT_HOST,
                        "port": self.server.server_address[1],
                    },
                )
                return
            if path == "/api/services":
                services = [self._public_service(item) for item in self.launcher.status()]
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "services": services,
                        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                )
                return
            prefix = "/api/services/"
            if path.startswith(prefix):
                name = path[len(prefix) :].strip("/")
                status = self.launcher.status(name)
                self._send_json(200, {"ok": True, "service": self._public_service(status)})
                return
            self._send_error_json(404, "not_found", "未知路径")
        except UnknownServiceError as exc:
            self._send_error_json(404, "unknown_service", str(exc))
        except LauncherError as exc:
            self._send_error_json(400, "invalid_request", str(exc))
        except Exception:  # noqa: BLE001 - 兜底不泄露堆栈
            LOGGER.exception("GET %s 失败", path)
            self._send_error_json(500, "internal_error", "启动器内部错误")

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if not self._check_post_origin():
                return
            payload = self._read_json_body()

            if path == "/api/services/ensure":
                names = payload.get("services")
                if names is not None and (
                    not isinstance(names, list) or not all(isinstance(n, str) for n in names)
                ):
                    raise LauncherError("services 必须是字符串数组")
                results = self.launcher.ensure_all(names)
                all_running = all(
                    result.get("state") in (STATE_RUNNING, STATE_RUNNING_EXTERNAL)
                    for result in results
                )
                public_results = [self._public_service(item) for item in results]
                self._send_json(
                    200 if all_running else 503,
                    {
                        "ok": all_running,
                        "results": public_results,
                        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                )
                return

            prefix = "/api/services/"
            if path.startswith(prefix):
                parts = path[len(prefix) :].strip("/").split("/")
                if len(parts) != 2:
                    self._send_error_json(404, "not_found", "未知路径")
                    return
                name, action = parts
                if action in ("stop", "restart") and not self._origin_is_loopback():
                    self._send_error_json(
                        403,
                        "origin_not_allowed",
                        "stop/restart 只允许来自本机回环页面",
                    )
                    return
                if action == "start":
                    result = self.launcher.ensure(name)
                    self._send_json(
                        200 if result.get("state") in (STATE_RUNNING, STATE_RUNNING_EXTERNAL) else 503,
                        {"ok": result.get("state") == STATE_RUNNING, "service": self._public_service(result)},
                    )
                    return
                if action == "stop":
                    result = self.launcher.stop(name)
                    self._send_json(200, {"ok": True, "service": self._public_service(result)})
                    return
                if action == "restart":
                    result = self.launcher.restart(name)
                    self._send_json(
                        200 if result.get("state") in (STATE_RUNNING, STATE_RUNNING_EXTERNAL) else 503,
                        {"ok": result.get("state") == STATE_RUNNING, "service": self._public_service(result)},
                    )
                    return
                self._send_error_json(404, "not_found", "未知操作")
                return

            self._send_error_json(404, "not_found", "未知路径")
        except UnknownServiceError as exc:
            self._send_error_json(404, "unknown_service", str(exc))
        except ExternalServiceError as exc:
            self._send_error_json(409, "external_service", str(exc))
        except InvalidContentTypeError as exc:
            self._send_error_json(415, "unsupported_media_type", str(exc))
        except PayloadTooLargeError as exc:
            self.close_connection = True
            self._send_error_json(413, "payload_too_large", str(exc))
        except LauncherError as exc:
            self._send_error_json(400, "invalid_request", str(exc))
        except Exception:  # noqa: BLE001
            LOGGER.exception("POST %s 失败", path)
            self._send_error_json(500, "internal_error", "启动器内部错误")


def create_server(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, launcher: Optional[Launcher] = None
) -> ThreadingHTTPServer:
    """创建启动器 HTTP 服务；只允许绑定回环地址。"""
    if not _is_loopback_host(host):
        raise ValueError("启动器会拉起本机进程，只能绑定回环地址（127.0.0.1/localhost）")
    if launcher is None:
        launcher = Launcher(build_specs(load_config()))
    httpd = ThreadingHTTPServer((host, port), LauncherHandler)
    bound_host = httpd.server_address[0]
    if not _is_loopback_host(bound_host):
        httpd.server_close()
        raise ValueError(
            f"启动器解析到非回环地址 {bound_host!r}，拒绝启动（请检查 hosts 配置）"
        )
    httpd.daemon_threads = True
    httpd.launcher = launcher  # type: ignore[attr-defined]
    return httpd


# ──────────────────────────────────────────────
# 本地控制台页面
# ──────────────────────────────────────────────


def _console_html(launcher: Launcher, port: int) -> str:
    rows = "".join(
        f"<li><b>{html.escape(name)}</b> — 端口 {spec.port}</li>"
        for name, spec in launcher._specs.items()
    )
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>AIinteraction 本地启动器</title><style>"
        "body{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;"
        "max-width:760px;margin:0 auto;padding:32px 20px;line-height:1.7}"
        "h1{color:#58a6ff;font-size:1.4em;margin-bottom:6px}"
        ".sub{color:#8b949e;font-size:.86em;margin-bottom:22px}"
        ".card{background:#161b22;border:1px solid #30363d;border-radius:10px;"
        "padding:18px 22px;margin-bottom:16px}"
        ".kv{display:flex;justify-content:space-between;padding:7px 0;"
        "border-bottom:1px dashed #21262d;font-size:.9em}"
        ".kv:last-child{border-bottom:none}.kv span:last-child{font-family:monospace}"
        ".ok{color:#7ee787}.warn{color:#d29922}.err{color:#f85149}"
        "button{background:#238636;color:#fff;border:none;border-radius:6px;"
        "padding:9px 18px;font-size:.9em;cursor:pointer;margin:4px 6px 4px 0}"
        "button.ghost{background:#21262d;color:#c9d1d9}"
        "code{background:#0d1117;padding:2px 6px;border-radius:4px}"
        "</style></head><body>"
        "<h1>AIinteraction 本地启动器</h1>"
        "<div class=\"sub\">仅监听 127.0.0.1 · 打开 ESP32 仪表盘时会自动调用本服务</div>"
        "<div class=\"card\"><div class=\"kv\"><span>语音服务</span>"
        "<span id=\"voice\">检查中…</span></div>"
        "<div class=\"kv\"><span>视觉反馈服务</span>"
        "<span id=\"vision\">检查中…</span></div>"
        "<div class=\"kv\"><span>启动器端口</span><span>" + str(port) + "</span></div></div>"
        "<div class=\"card\"><b>受管服务</b><ul>" + rows + "</ul>"
        "<button onclick=\"act('ensure')\">启动/检查全部</button>"
        "<button class=\"ghost\" onclick=\"act('restart')\">重启全部</button>"
        "<div id=\"msg\" class=\"sub\" style=\"margin-top:10px\"></div></div>"
        "<div class=\"card\"><b>说明</b><p class=\"sub\" style=\"margin:8px 0 0\">"
        "本页不是 ESP32 仪表盘。启动器只负责拉起本机 Python 服务；"
        "语音与视觉功能仍在原有面板中使用。关闭启动器不会自动停止已启动的服务。"
        "</p></div>"
        "<script>"
        "function label(s){if(s==='running'||s==='running_external')return ['运行中','ok'];"
        "if(s==='starting')return ['启动中','warn'];if(s==='stopped')return ['未运行','warn'];"
        "if(s==='port_conflict')return ['端口冲突','err'];return ['异常: '+s,'err']}"
        "function render(list){var m={};for(var i=0;i<list.length;i++)m[list[i].name]=list[i];"
        "['voice','vision'].forEach(function(n){var e=document.getElementById(n);if(!e)return;"
        "var r=m[n]||{};var l=label(r.state);e.textContent=l[0];e.className=l[1];});}"
        "function refresh(){fetch('/api/services').then(function(r){return r.json();})"
        ".then(function(d){render(d.services||[]);}).catch(function(){});}"
        "function act(what){var reqs=[];"
        "if(what==='ensure'){reqs.push(fetch('/api/services/ensure',{method:'POST',"
        "headers:{'Content-Type':'application/json'},body:'{}'}));}"
        "else{['voice','vision'].forEach(function(n){reqs.push(fetch('/api/services/'+n+'/'+what,"
        "{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}));});}"
        "Promise.all(reqs).then(function(){refresh();})"
        ".catch(function(e){document.getElementById('msg').textContent='操作失败: '+e.message;});}"
        "refresh();setInterval(refresh,5000);"
        "</script></body></html>"
    )


# ──────────────────────────────────────────────
# CLI
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


def resolve_listen_address(
    config: dict[str, Any], host: Optional[str] = None, port: Optional[int] = None
) -> tuple[str, int]:
    """解析启动器监听地址：CLI 显式值优先，其次配置文件，最后安全默认值。"""
    resolved_host = str(host or config.get("host", DEFAULT_HOST)).strip()
    if not resolved_host:
        raise ValueError("启动器 host 不能为空")
    if not _is_loopback_host(resolved_host):
        raise ValueError("启动器会拉起本机进程，只能绑定回环地址（127.0.0.1/localhost）")
    resolved_port = port if port is not None else config.get("port", DEFAULT_PORT)
    return resolved_host, _coerce_port(resolved_port, "启动器")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="AIinteraction 本地启动器：打开 Dashboard 时幂等启动语音/视觉服务"
    )
    parser.add_argument("--host", default=None, help="绑定地址（只能是回环；默认读配置/127.0.0.1）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认读配置/8079）")
    parser.add_argument("--config", default=None, help=f"配置文件（默认 {DEFAULT_CONFIG_PATH.name}）")
    parser.add_argument(
        "--no-ensure-on-start",
        action="store_true",
        help="启动器启动时不预热服务（仅响应页面请求）",
    )
    parser.add_argument("--log-file", default=None, help="额外写入的日志文件")
    args = parser.parse_args(argv)

    _configure_logging(args.log_file)

    try:
        config = load_config(Path(args.config) if args.config else None)
        specs = build_specs(config)
        host, port = resolve_listen_address(config, args.host, args.port)
    except ValueError as exc:
        LOGGER.error("配置错误：%s", exc)
        return 2

    launcher = Launcher(specs)
    httpd = create_server(host, port, launcher)
    host, port = httpd.server_address[0], httpd.server_address[1]
    LOGGER.info("本地启动器已启动: http://%s:%s/", host, port)
    LOGGER.info("受管服务: %s", ", ".join(f"{n}:{s.port}" for n, s in specs.items()))

    if not args.no_ensure_on_start:
        threading.Thread(target=launcher.ensure_all, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("收到中断信号，正在停止启动器（已启动的服务保持运行）")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "KNOWN_SERVICES",
    "Launcher",
    "LauncherError",
    "ServiceSpec",
    "allowed_origin",
    "build_specs",
    "create_server",
    "load_config",
    "resolve_listen_address",
]
