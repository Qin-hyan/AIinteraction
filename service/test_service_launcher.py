"""Unit/integration tests for the local service launcher (stdlib only).

覆盖：
  1. 幂等启动、重复 ensure 不重复起进程；
  2. 已由其他终端启动的服务标记为 running_external，拒绝被停止/重启；
  3. 端口冲突、解释器缺失、未知服务等错误路径；
  4. HTTP API：预检/CORS/PNA、Origin 边界、Content-Type、请求体上限；
  5. 配置解析与回环绑定约束。

真实进程使用一个临时 Python 健康检查服务，不接触语音/视觉模型，也不占 8080/8090。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

try:
    from . import service_launcher as sl
except ImportError:  # pragma: no cover - 直接运行 service/ 下测试
    import service_launcher as sl  # type: ignore[no-redef]


FAKE_SERVICE = """
import http.server
import json
import sys

PORT = int(sys.argv[1])
MODE = sys.argv[2] if len(sys.argv) > 2 else ""
UNHEALTHY = MODE == "unhealthy"

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" and not UNHEALTHY:
            payload = {"ok": True, "port": PORT}
            if MODE == "no-model":
                payload["model"] = {"file_exists": False}
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass

http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"port {port} did not open")


@pytest.fixture(scope="module")
def fake_script(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("fake_service") / "fake_service.py"
    path.write_text(FAKE_SERVICE, encoding="utf-8")
    return path


def make_spec(
    tmp_path: Path,
    script: Path,
    port: int,
    *,
    name: str = "probe",
    python: str = sys.executable,
    start_timeout: float = 10.0,
    mode: str | None = None,
) -> sl.ServiceSpec:
    command: tuple[str, ...] = (python, str(script), str(port))
    if mode:
        command += (mode,)
    return sl.ServiceSpec(
        name=name,
        module="(test)",
        health_path="/health",
        host="127.0.0.1",
        port=port,
        python=python,
        log_file=tmp_path / f"{name}-{port}.log",
        command_override=command,
        start_timeout=start_timeout,
    )


def start_external(script: Path, port: int) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    wait_port(port)
    return proc


def request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(base + path, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


# ──────────────────────────────────────────────
# 进程管理
# ──────────────────────────────────────────────


def test_ensure_starts_once_and_is_idempotent(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    try:
        first = launcher.ensure("probe")
        assert first["state"] == "running"
        assert first["managed"] is True
        pid = first["pid"]
        assert pid and pid > 0

        second = launcher.ensure("probe")
        assert second["state"] == "running"
        assert second["pid"] == pid
    finally:
        launcher.stop("probe")

    assert not sl.port_open("127.0.0.1", port)


def test_external_service_is_detected_and_not_owned(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    external = start_external(fake_script, port)
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    try:
        status = launcher.ensure("probe")
        assert status["state"] == "running_external"
        assert status["managed"] is False
        assert status["pid"] is None

        with pytest.raises(sl.ExternalServiceError):
            launcher.stop("probe")
        with pytest.raises(sl.ExternalServiceError):
            launcher.restart("probe")
    finally:
        external.terminate()
        external.wait(timeout=8)


def test_vision_health_marks_degraded_when_model_missing(
    tmp_path: Path, fake_script: Path
) -> None:
    port = free_port()
    spec = make_spec(
        tmp_path, fake_script, port, name="vision", mode="no-model"
    )
    launcher = sl.Launcher({"vision": spec})
    try:
        status = launcher.ensure("vision")
        assert status["state"] == "running"
        assert status["degraded"] is True
        assert "模型文件未就绪" in status["detail"]
    finally:
        launcher.stop("vision")


def test_stop_managed_service(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    started = launcher.ensure("probe")
    assert started["state"] == "running"

    stopped = launcher.stop("probe")
    assert stopped["state"] == "stopped"
    assert stopped["pid"] is None
    assert not sl.port_open("127.0.0.1", port)


def test_start_timeout_terminates_process_and_reports_failure(
    tmp_path: Path, fake_script: Path
) -> None:
    port = free_port()
    spec = make_spec(
        tmp_path, fake_script, port, start_timeout=1.2, mode="unhealthy"
    )
    launcher = sl.Launcher({"probe": spec})
    status = launcher.ensure("probe")
    assert status["state"] == "failed"
    assert "启动超时" in status["detail"]
    assert not sl.port_open("127.0.0.1", port)


def test_stop_reports_unhealthy_if_port_survives(
    tmp_path: Path, fake_script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = free_port()
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    assert launcher.ensure("probe")["state"] == "running"
    proc = launcher._procs["probe"]
    monkeypatch.setattr(sl, "_terminate_tree", lambda _proc: None)
    try:
        status = launcher.stop("probe")
        assert status["state"] == "unhealthy"
        assert "仍被占用" in status["detail"]
    finally:
        proc.kill()
        proc.wait(timeout=8)


def test_restart_managed_service_changes_pid(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    try:
        first = launcher.ensure("probe")
        second = launcher.restart("probe")
        assert second["state"] == "running"
        assert second["pid"] != first["pid"]
    finally:
        launcher.stop("probe")


def test_port_conflict_is_reported_without_spawning(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", port))
        blocker.listen(1)
        launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
        status = launcher.ensure("probe")
        assert status["state"] == "port_conflict"
        assert status["managed"] is False


def test_missing_python_is_reported(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    missing = str(tmp_path / "no-such-python.exe")
    spec = make_spec(tmp_path, fake_script, port, python=missing)
    launcher = sl.Launcher({"probe": spec})
    status = launcher.ensure("probe")
    assert status["state"] == "missing_python"
    assert "Python 解释器不存在" in status["detail"]


def test_startup_exit_reports_exit_code_and_log_tail(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    spec = make_spec(tmp_path, fake_script, port, start_timeout=4)
    spec = sl.ServiceSpec(
        name=spec.name,
        module=spec.module,
        health_path=spec.health_path,
        host=spec.host,
        port=spec.port,
        python=sys.executable,
        log_file=spec.log_file,
        command_override=(sys.executable, "-c", "raise SystemExit(3)"),
        start_timeout=4,
    )
    launcher = sl.Launcher({"probe": spec})
    status = launcher.ensure("probe")
    assert status["state"] == "failed"
    assert "exit=3" in status["detail"]


def test_unknown_service_rejected(tmp_path: Path, fake_script: Path) -> None:
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, free_port())})
    with pytest.raises(sl.UnknownServiceError):
        launcher.ensure("nope")
    with pytest.raises(sl.UnknownServiceError):
        launcher.status("nope")


def test_status_without_side_effects(tmp_path: Path, fake_script: Path) -> None:
    port = free_port()
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    status = launcher.status("probe")
    assert status["state"] == "stopped"
    assert not sl.port_open("127.0.0.1", port)


# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────


def test_default_specs_use_expected_ports(tmp_path: Path) -> None:
    specs = sl.build_specs({})
    assert specs["voice"].port == 8080
    assert specs["vision"].port == 8090
    assert specs["voice"].host == "127.0.0.1"
    assert specs["vision"].health_path == "/api/vision/health"


def test_config_overrides_host_port_and_args(tmp_path: Path) -> None:
    specs = sl.build_specs(
        {
            "services": {
                "voice": {"host": "0.0.0.0", "port": 18080, "args": ["--device", "1"]},
                "vision": {"port": 18090, "log_file": str(tmp_path / "v.log")},
            }
        }
    )
    assert specs["voice"].host == "0.0.0.0"
    assert specs["voice"].port == 18080
    assert specs["voice"].command()[-2:] == ["--device", "1"]
    assert specs["vision"].port == 18090
    assert specs["vision"].log_file == tmp_path / "v.log"


def test_config_env_is_carried_to_spec(tmp_path: Path) -> None:
    specs = sl.build_specs(
        {
            "services": {
                "voice": {
                    "env": {"ESP32_BASE_URL": "http://device"},
                }
            }
        }
    )
    assert dict(specs["voice"].env) == {"ESP32_BASE_URL": "http://device"}


def test_invalid_config_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        sl.load_config(bad)
    with pytest.raises(ValueError):
        sl.build_specs({"services": {"voice": {"port": 70000}}})


def test_check_host_follows_bind_host(tmp_path: Path, fake_script: Path) -> None:
    spec = make_spec(tmp_path, fake_script, free_port())
    assert spec.check_host == "127.0.0.1"
    wildcard = replace(spec, host="0.0.0.0")
    assert wildcard.check_host == "127.0.0.1"
    lan = replace(spec, host="192.168.1.10")
    assert lan.check_host == "192.168.1.10"


def test_resolve_listen_address_prefers_cli_then_config() -> None:
    assert sl.resolve_listen_address({}) == ("127.0.0.1", 8079)
    assert sl.resolve_listen_address({"host": "127.0.0.1", "port": 18079}) == (
        "127.0.0.1",
        18079,
    )
    assert sl.resolve_listen_address(
        {"host": "127.0.0.1", "port": 18079}, port=19079
    ) == ("127.0.0.1", 19079)
    assert sl.resolve_listen_address({"port": 18079}, host="localhost") == (
        "localhost",
        18079,
    )
    with pytest.raises(ValueError):
        sl.resolve_listen_address({"host": "0.0.0.0"})


def test_launcher_must_bind_loopback() -> None:
    with pytest.raises(ValueError):
        sl.create_server("0.0.0.0", 0, None)  # type: ignore[arg-type]


# ──────────────────────────────────────────────
# HTTP API / CORS / PNA
# ──────────────────────────────────────────────


@pytest.fixture()
def http_launcher(tmp_path: Path, fake_script: Path):
    port = free_port()
    launcher = sl.Launcher({"probe": make_spec(tmp_path, fake_script, port)})
    server = sl.create_server("127.0.0.1", 0, launcher)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, launcher, port
    finally:
        server.shutdown()
        server.server_close()
        try:
            launcher.stop("probe")
        except sl.ExternalServiceError:
            pass


def test_get_health_and_services(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, headers, body = request(base, "/api/launcher/health")
    assert status == 200
    assert json.loads(body)["service"] == "service_launcher"

    status, _headers, body = request(base, "/api/services")
    assert status == 200
    payload = json.loads(body)
    assert payload["services"][0]["name"] == "probe"
    assert payload["services"][0]["state"] == "stopped"


def test_console_page_is_served(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, headers, body = request(base, "/")
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert "本地启动器" in body.decode("utf-8")


def test_preflight_echoes_private_origin_and_pna(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, headers, _body = request(
        base,
        "/api/services/ensure",
        method="OPTIONS",
        headers={
            "Origin": "http://192.168.1.50",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == "http://192.168.1.50"
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert headers["Access-Control-Allow-Origin"] != "*"


def test_ensure_via_http_starts_service(http_launcher) -> None:
    base, launcher, port = http_launcher
    status, _headers, body = request(
        base,
        "/api/services/ensure",
        method="POST",
        body=json.dumps({"services": ["probe"]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:8080",
        },
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["results"][0]["state"] == "running"
    assert sl.port_open("127.0.0.1", port)
    assert launcher.status("probe")["managed"] is True
    launcher.stop("probe")


def test_public_origin_is_rejected(http_launcher) -> None:
    base, launcher, port = http_launcher
    status, _headers, body = request(
        base,
        "/api/services/ensure",
        method="POST",
        body=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": "https://example.com",
        },
    )
    assert status == 403
    assert json.loads(body)["error"] == "origin_not_allowed"
    assert not sl.port_open("127.0.0.1", port)

    status, headers, _body = request(
        base,
        "/api/services/ensure",
        method="OPTIONS",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert status == 204
    assert "Access-Control-Allow-Origin" not in headers
    assert "Access-Control-Allow-Private-Network" not in headers


def test_private_origin_status_redacts_paths_and_detail(http_launcher) -> None:
    base, launcher, _port = http_launcher
    try:
        assert launcher.ensure("probe")["state"] == "running"
        status, _headers, body = request(
            base,
            "/api/services",
            headers={"Origin": "http://192.168.1.50"},
        )
        assert status == 200
        service = json.loads(body)["services"][0]
        assert service["log_file"].endswith(".log")
        assert "\\" not in service["log_file"]
        assert service["detail"] == ""
    finally:
        launcher.stop("probe")


def test_non_json_content_type_is_rejected(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, _headers, body = request(
        base,
        "/api/services/ensure",
        method="POST",
        body=b"services=voice",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 415
    assert json.loads(body)["error"] == "unsupported_media_type"


def test_oversized_body_is_rejected(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, _headers, body = request(
        base,
        "/api/services/ensure",
        method="POST",
        body=b"{" + b" " * (sl.MAX_BODY_BYTES + 10) + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert status == 413
    assert json.loads(body)["error"] == "payload_too_large"


def test_invalid_json_and_unknown_service(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, _headers, _body = request(
        base,
        "/api/services/ensure",
        method="POST",
        body=b"{",
        headers={"Content-Type": "application/json"},
    )
    assert status == 400

    status, _headers, body = request(base, "/api/services/nope")
    assert status == 404
    assert json.loads(body)["error"] == "unknown_service"

    status, _headers, _body = request(
        base,
        "/api/services/nope/start",
        method="POST",
        body=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert status == 404


def test_external_stop_returns_conflict(http_launcher, fake_script: Path) -> None:
    base, launcher, port = http_launcher
    launcher.stop("probe")
    external = start_external(fake_script, port)
    try:
        status, _headers, body = request(
            base,
            "/api/services/probe/stop",
            method="POST",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert status == 409
        assert json.loads(body)["error"] == "external_service"
    finally:
        external.terminate()
        external.wait(timeout=8)


def test_stop_from_private_origin_is_rejected(http_launcher) -> None:
    base, _launcher, _port = http_launcher
    status, _headers, body = request(
        base,
        "/api/services/probe/stop",
        method="POST",
        body=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": "http://192.168.1.50",
        },
    )
    assert status == 403
    assert json.loads(body)["error"] == "origin_not_allowed"


def test_ensure_from_private_origin_is_allowed(http_launcher) -> None:
    base, launcher, _port = http_launcher
    try:
        status, _headers, body = request(
            base,
            "/api/services/ensure",
            method="POST",
            body=json.dumps({"services": ["probe"]}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://192.168.1.50",
            },
        )
        assert status == 200
        assert json.loads(body)["ok"] is True
    finally:
        launcher.stop("probe")


def test_allowed_origin_boundary() -> None:
    assert sl.allowed_origin("http://127.0.0.1:8079") == "http://127.0.0.1:8079"
    assert sl.allowed_origin("http://localhost:3000") == "http://localhost:3000"
    assert sl.allowed_origin("http://192.168.1.10") == "http://192.168.1.10"
    assert sl.allowed_origin("http://10.0.0.5:8080") == "http://10.0.0.5:8080"
    assert sl.allowed_origin("http://169.254.1.1") == "http://169.254.1.1"
    assert sl.allowed_origin("https://example.com") is None
    assert sl.allowed_origin("http://127.0.0.1/path") is None
    assert sl.allowed_origin("http://127.0.0.1?q=1") is None
    assert sl.allowed_origin("null") is None
    assert sl.allowed_origin(None) is None
