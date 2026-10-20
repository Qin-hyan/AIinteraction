"""
test_vision_feedback_server.py — Week 08 W8-3: 本地反馈服务 HTTP 接口测试

覆盖：
  1. 页面与 health（无外部资源、阈值未配置提示）；
  2. 本地 JPEG / 设备照片两种分析入口；
  3. 模型缺失、推理失败、设备未配置/404、请求体非法、过大、方法错误；
  4. 反馈保存、重复冲突、无效输入、记录不存在、保存失败；
  5. 设备代理（gallery/photo/status/recapture）；
  6. 可选真实 DBCNN 推理冒烟（隔离 venv + 本地权重）。

单元测试全部注入替身，不访问真实设备；HTTP 服务绑定 127.0.0.1 临时端口。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from . import vision_analysis
from . import vision_feedback as vf
from . import vision_feedback_server as server
from .vision_dbcnn import DbcnnConfigError, DbcnnInferenceError, DbcnnScore

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 96
REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ENV = "VISION_DBCNN_RUN_INTEGRATION"
DEFAULT_MODEL_PATH = (
    REPO_ROOT / ".workbuddy" / "artifacts" / "dbcnn-iqa-test" / "weights" / "dbcnn_model.onnx"
)
OK_IMAGE = REPO_ROOT / ".workbuddy" / "week07_acceptance" / "ok.jpg"


class FakeScorer:
    def __init__(self, score: float = 0.7561, error: Exception | None = None) -> None:
        self.score = score
        self.error = error
        self.calls: list[bytes] = []

    def score_jpeg(self, jpeg_bytes: bytes) -> DbcnnScore:
        self.calls.append(jpeg_bytes)
        if self.error is not None:
            raise self.error
        return DbcnnScore(
            score=self.score,
            model_version="dbcnn_onnx_v1",
            model_sha256="a" * 64,
            model_data_sha256="b" * 64,
            inference_ms=1.0,
        )


def _rules(jpeg_bytes: bytes) -> dict[str, Any]:
    return {
        "usable": True,
        "status": "ok",
        "score": 0.9,
        "metrics": {"mean_luma": 118.5, "contrast": 63.4, "detail": 8.3},
        "bytes_len": len(jpeg_bytes),
    }


def make_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    device: Any = None,
    score: float = 0.7561,
    scorer_error: Exception | None = None,
    scorer_factory: Any = None,
) -> vf.VisionFeedbackService:
    monkeypatch.delenv(vision_analysis.ENV_SCORE_THRESHOLD, raising=False)
    monkeypatch.delenv(vf.ENV_DEVICE_BASE_URL, raising=False)
    scorer = FakeScorer(score=score, error=scorer_error)

    def _analyzer(**kwargs: Any) -> dict[str, Any]:
        return vision_analysis.analyze_jpeg(rule_assessor=_rules, **kwargs)

    return vf.VisionFeedbackService(
        data_dir=tmp_path / "data",
        device=device,
        scorer_factory=scorer_factory or (lambda: scorer),
        analyzer=_analyzer,
    )


class DeviceStub:
    configured = True
    base_url = "http://device-stub:80"

    def __init__(
        self,
        photos: list[dict[str, Any]] | None = None,
        *,
        photo_error: Exception | None = None,
    ) -> None:
        self.photos = photos if photos is not None else []
        self.photo_error = photo_error
        self.photo_calls: list[int] = []
        self.capture_calls = 0

    def gallery(self) -> dict[str, Any]:
        return {
            "total": len(self.photos),
            "retain_limit": 3,
            "retained": len(self.photos),
            "photos": self.photos,
        }

    def photo(self, index: int) -> bytes:
        self.photo_calls.append(index)
        if self.photo_error is not None:
            raise self.photo_error
        return JPEG

    def capture(self) -> dict[str, Any]:
        self.capture_calls += 1
        return {
            "request_id": "req-1-0002",
            "status": "submitted",
            "cap_task_status": "capturing",
        }

    def status(self) -> dict[str, Any]:
        return {"cap_task_status": "ok", "last_img_status": "ok"}


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """在 127.0.0.1 临时端口启动服务；测试结束统一关闭。"""
    service = make_service(tmp_path, monkeypatch)
    httpd = server.create_server("127.0.0.1", 0, service)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], service
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def start_server(service: vf.VisionFeedbackService) -> tuple[Any, int, threading.Thread]:
    httpd = server.create_server("127.0.0.1", 0, service)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1], thread


def stop_server(httpd: Any, thread: threading.Thread) -> None:
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def request(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def request_json(
    port: int,
    method: str,
    path: str,
    payload: Any = None,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = None
    actual_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        actual_headers["Content-Type"] = "application/json"
    status, _, data = request(port, method, path, body=body, headers=actual_headers)
    return status, json.loads(data.decode("utf-8"))


# ──────────────────────────────────────────────
# 1. 页面与健康状态
# ──────────────────────────────────────────────


def test_index_page_states_uncalibrated_and_separated_sources(live) -> None:
    port, _ = live
    status, headers, data = request(port, "GET", "/")
    html = data.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert html.count("<script>") == 1
    assert "cdn." not in html and "https://" not in html
    assert "needs_review" in html
    assert "不代表准确率或正确概率" in html
    assert "rule_diagnostics" not in html or "规则诊断" in html
    assert "无法判断" in html
    assert "user_feedback" in html


def test_health_endpoint_reports_config_without_secrets(live) -> None:
    port, _ = live
    status, payload = request_json(port, "GET", "/api/vision/health")

    assert status == 200
    assert payload["model"]["model_version"] == "dbcnn_onnx_v1"
    assert payload["threshold"]["configured"] is False
    assert payload["threshold"]["status"] == "not_configured"
    assert payload["device"]["configured"] is False
    assert payload["feedback"]["verdicts"] == list(vf.FEEDBACK_VERDICTS)
    assert "score_is_raw_model_output_not_accuracy" in payload["disclaimers"]


# ──────────────────────────────────────────────
# 2. 分析接口
# ──────────────────────────────────────────────


def test_analyze_local_jpeg_returns_record_and_persists(live) -> None:
    port, service = live
    status, headers, data = request(
        port, "POST", "/api/vision/analyze", body=JPEG,
        headers={"Content-Type": "image/jpeg"},
    )
    record = json.loads(data.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert record["input"]["source"] == vf.SOURCE_LOCAL_UPLOAD
    assert record["analysis"]["decision"] == "needs_review"
    assert record["analysis"]["label"] is None
    assert record["analysis"]["rule_diagnostics"]["label"] == "ok"
    assert record["input"]["image_ref"]["sha256"] == vf.sha256_hex(JPEG)

    status, listed = request_json(port, "GET", "/api/vision/records")
    assert status == 200
    assert listed["count"] == 1
    assert listed["records"][0]["record_id"] == record["record_id"]


def test_analyze_device_photo_links_request_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = DeviceStub(
        photos=[{"index": 0, "request_id": "req-777-0001", "img_status": "ok"}]
    )
    service = make_service(tmp_path, monkeypatch, device=device)
    httpd, port, thread = start_server(service)
    try:
        status, record = request_json(port, "POST", "/api/vision/analyze", {"index": 0})
        assert status == 200
        assert record["input"]["source"] == vf.SOURCE_DEVICE_GALLERY
        assert record["input"]["device"]["request_id"] == "req-777-0001"
        assert device.photo_calls == [0]
    finally:
        stop_server(httpd, thread)


def test_analyze_requires_configured_device(live) -> None:
    port, _ = live
    status, payload = request_json(port, "POST", "/api/vision/analyze", {"index": 0})

    assert status == 503
    assert payload["error"] == "device_not_configured"


def test_analyze_device_photo_not_in_gallery_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = DeviceStub(photos=[{"index": 0, "request_id": "req-1"}])
    service = make_service(tmp_path, monkeypatch, device=device)
    httpd, port, thread = start_server(service)
    try:
        status, payload = request_json(port, "POST", "/api/vision/analyze", {"index": 3})
        assert status == 404
        assert payload["error"] == "photo_not_found"
        assert device.photo_calls == []
    finally:
        stop_server(httpd, thread)


def test_analyze_device_http_error_is_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = DeviceStub(
        photos=[{"index": 0, "request_id": "req-1"}],
        photo_error=vf.DeviceHttpError(404, "设备返回 HTTP 404"),
    )
    service = make_service(tmp_path, monkeypatch, device=device)
    httpd, port, thread = start_server(service)
    try:
        status, payload = request_json(port, "POST", "/api/vision/analyze", {"index": 0})
        assert status == 502
        assert payload["error"] == "device_http_error"
        assert payload["device_status"] == 404
    finally:
        stop_server(httpd, thread)


def test_analyze_rejects_bad_json(live) -> None:
    port, _ = live
    status, _, data = request(
        port, "POST", "/api/vision/analyze", body=b"{not-json",
        headers={"Content-Type": "application/json"},
    )

    assert status == 400
    assert json.loads(data.decode("utf-8"))["error"] == "invalid_request"


def test_analyze_rejects_non_jpeg_body(live) -> None:
    port, service = live
    status, _, data = request(
        port, "POST", "/api/vision/analyze", body=b"not an image",
        headers={"Content-Type": "image/jpeg"},
    )

    assert status == 400
    assert json.loads(data.decode("utf-8"))["error"] == "invalid_request"
    assert service.store.records() == []


def test_analyze_rejects_unsupported_media_type(live) -> None:
    port, _ = live
    status, _, data = request(
        port, "POST", "/api/vision/analyze", body=b"x",
        headers={"Content-Type": "text/plain"},
    )

    assert status == 415
    assert json.loads(data.decode("utf-8"))["error"] == "unsupported_media_type"


def test_analyze_model_config_error_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> Any:
        raise DbcnnConfigError("model path missing")

    service = make_service(tmp_path, monkeypatch, scorer_factory=_raise)
    httpd, port, thread = start_server(service)
    try:
        status, _, data = request(
            port, "POST", "/api/vision/analyze", body=JPEG,
            headers={"Content-Type": "image/jpeg"},
        )
        payload = json.loads(data.decode("utf-8"))
        assert status == 503
        assert payload["error"] == "model_not_configured"
        assert service.store.records() == []
    finally:
        stop_server(httpd, thread)


def test_analyze_inference_error_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(
        tmp_path, monkeypatch, scorer_error=DbcnnInferenceError("boom")
    )
    httpd, port, thread = start_server(service)
    try:
        status, _, data = request(
            port, "POST", "/api/vision/analyze", body=JPEG,
            headers={"Content-Type": "image/jpeg"},
        )
        payload = json.loads(data.decode("utf-8"))
        assert status == 502
        assert payload["error"] == "inference_failed"
        assert service.store.records() == []
    finally:
        stop_server(httpd, thread)



# ──────────────────────────────────────────────
# 3. 反馈接口
# ──────────────────────────────────────────────


def _analyze_via_http(port: int) -> dict[str, Any]:
    status, _, data = request(
        port, "POST", "/api/vision/analyze", body=JPEG,
        headers={"Content-Type": "image/jpeg"},
    )
    assert status == 200
    return json.loads(data.decode("utf-8"))


def test_feedback_roundtrip_and_duplicate_conflict(live) -> None:
    port, _ = live
    record = _analyze_via_http(port)
    payload = {
        "record_id": record["record_id"],
        "verdict": "unusable",
        "reason": "blurred",
        "note": "画面糊了",
    }

    status, saved = request_json(port, "POST", "/api/vision/feedback", payload)
    assert status == 201
    assert saved["saved"] is True
    assert saved["feedback"]["revision"] == 1
    assert saved["feedback"]["label_source"] == vf.FEEDBACK_SOURCE

    status, duplicate = request_json(port, "POST", "/api/vision/feedback", payload)
    assert status == 409
    assert duplicate["error"] == "duplicate_feedback"

    status, listed = request_json(port, "GET", "/api/vision/records")
    assert listed["records"][0]["feedback"]["latest"]["verdict"] == "unusable"
    assert listed["records"][0]["feedback"]["latest"]["note"] == "画面糊了"
    assert len(listed["records"][0]["feedback"]["history"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "usable"},
        {"record_id": "rec-x", "verdict": "maybe"},
        {"record_id": "rec-x", "verdict": "usable", "reason": "noise_drift"},
    ],
)
def test_feedback_invalid_payload_is_400(live, payload: dict[str, Any]) -> None:
    port, _ = live
    status, body = request_json(port, "POST", "/api/vision/feedback", payload)

    assert status == 400
    assert body["error"] == "invalid_feedback"


def test_feedback_unknown_record_is_404(live) -> None:
    port, _ = live
    status, body = request_json(
        port,
        "POST",
        "/api/vision/feedback",
        {"record_id": "vision-0000000000000-0001", "verdict": "usable"},
    )

    assert status == 404
    assert body["error"] == "record_not_found"


def test_feedback_save_failure_is_500(live, monkeypatch: pytest.MonkeyPatch) -> None:
    port, service = live
    record = _analyze_via_http(port)

    def _fail(_event: dict[str, Any]) -> None:
        raise vf.FeedbackStoreError("disk full")

    monkeypatch.setattr(service.store, "append_feedback", _fail)

    status, body = request_json(
        port,
        "POST",
        "/api/vision/feedback",
        {"record_id": record["record_id"], "verdict": "usable"},
    )

    assert status == 500
    assert body["error"] == "feedback_save_failed"


# ──────────────────────────────────────────────
# 4. 设备代理与路由
# ──────────────────────────────────────────────

DEVICE_PHOTOS = [{"index": 0, "request_id": "req-1-0001", "img_status": "ok"}]


@pytest.fixture
def live_with_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    device = DeviceStub(photos=DEVICE_PHOTOS)
    service = make_service(tmp_path, monkeypatch, device=device)
    httpd, port, thread = start_server(service)
    try:
        yield port, service, device
    finally:
        stop_server(httpd, thread)


def test_gallery_requires_device(live) -> None:
    port, _ = live
    status, body = request_json(port, "GET", "/api/vision/gallery")

    assert status == 503
    assert body["error"] == "device_not_configured"


def test_gallery_proxies_device(live_with_device) -> None:
    port, _, _ = live_with_device
    status, body = request_json(port, "GET", "/api/vision/gallery")

    assert status == 200
    assert body["photos"][0]["request_id"] == "req-1-0001"


def test_photo_proxy_returns_jpeg(live_with_device) -> None:
    port, _, device = live_with_device
    status, headers, data = request(port, "GET", "/api/vision/photo?index=0")

    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert data == JPEG
    assert device.photo_calls == [0]


def test_photo_proxy_maps_device_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = DeviceStub(
        photos=DEVICE_PHOTOS, photo_error=vf.DeviceHttpError(404, "设备返回 HTTP 404")
    )
    service = make_service(tmp_path, monkeypatch, device=device)
    httpd, port, thread = start_server(service)
    try:
        status, _, data = request(port, "GET", "/api/vision/photo?index=0")
        payload = json.loads(data.decode("utf-8"))
        assert status == 502
        assert payload["device_status"] == 404
    finally:
        stop_server(httpd, thread)


def test_photo_requires_index(live_with_device) -> None:
    port, _, _ = live_with_device
    status, body = request_json(port, "GET", "/api/vision/photo")

    assert status == 400
    assert body["error"] == "invalid_request"


def test_status_proxy(live_with_device) -> None:
    port, _, _ = live_with_device
    status, body = request_json(port, "GET", "/api/vision/status")

    assert status == 200
    assert body["cap_task_status"] == "ok"


def test_recapture_uses_existing_capture_endpoint(live_with_device) -> None:
    port, _, device = live_with_device
    status, body = request_json(port, "POST", "/api/vision/recapture", {})

    assert status == 200
    assert body["request_id"] == "req-1-0002"
    assert body["cap_task_status"] == "capturing"
    assert device.capture_calls == 1


def test_method_not_allowed(live) -> None:
    port, _ = live
    status, headers, data = request(port, "GET", "/api/vision/analyze")
    assert status == 405
    assert headers.get("Allow") == "POST"
    assert json.loads(data.decode("utf-8"))["error"] == "method_not_allowed"

    status, headers, data = request(port, "POST", "/api/vision/health", body=b"")
    assert status == 405
    assert headers.get("Allow") == "GET"


def test_unknown_path_is_json_404(live) -> None:
    port, _ = live
    status, body = request_json(port, "GET", "/api/vision/unknown")

    assert status == 404
    assert body["error"] == "not_found"


@pytest.mark.parametrize("query", ["limit=abc", "limit=1000", "limit=-1"])
def test_records_limit_validation(live, query: str) -> None:
    port, _ = live
    status, body = request_json(port, "GET", f"/api/vision/records?{query}")

    assert status == 400
    assert body["error"] == "invalid_request"


def test_payload_too_large_is_413(live, monkeypatch: pytest.MonkeyPatch) -> None:
    """只发送头部声明超大 Content-Length：服务端必须在读体前拒绝。

    用原始 socket 而非 `http.client`，避免客户端写入大请求体时
    与服务端提前关闭连接产生竞争（曾导致偶发失败）。
    """
    import socket

    port, _ = live
    monkeypatch.setattr(server, "MAX_BODY_BYTES", 32)

    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        sock.sendall(
            b"POST /api/vision/analyze HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: 4096\r\n\r\n"
        )
        received = b""
        while b"\r\n\r\n" not in received:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
        head, _, body = received.partition(b"\r\n\r\n")
        declared = 0
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                declared = int(line.split(b":", 1)[1].strip())
        while len(body) < declared:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
    finally:
        sock.close()

    assert b" 413 " in head.split(b"\r\n")[0]
    assert json.loads(body.decode("utf-8"))["error"] == "payload_too_large"


# ──────────────────────────────────────────────
# 5. 可选真实模型冒烟（隔离 venv + 本地权重）
# ──────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get(INTEGRATION_ENV) != "1",
    reason=(
        "真实 ONNX 冒烟默认跳过；设置 VISION_DBCNN_RUN_INTEGRATION=1 "
        "并在安装 onnxruntime/opencv 的本地环境运行"
    ),
)
def test_real_dbcnn_feedback_loop_over_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .vision_dbcnn import ENV_MODEL_PATH

    monkeypatch.delenv(vision_analysis.ENV_SCORE_THRESHOLD, raising=False)
    monkeypatch.delenv(vf.ENV_DEVICE_BASE_URL, raising=False)
    # 无 ONNX Runtime/OpenCV 的环境跳过，而不是把依赖错误当成模型失败。
    pytest.importorskip("onnxruntime")
    pytest.importorskip("cv2")

    model_path = os.environ.get(ENV_MODEL_PATH) or str(DEFAULT_MODEL_PATH)
    if not os.path.isfile(model_path):
        pytest.skip(f"本地模型不存在: {model_path}")
    if not OK_IMAGE.is_file():
        pytest.skip("缺少本地样例图 .workbuddy/week07_acceptance/ok.jpg")
    monkeypatch.setenv(ENV_MODEL_PATH, model_path)

    service = vf.VisionFeedbackService(data_dir=tmp_path / "data")
    httpd, port, thread = start_server(service)
    try:
        jpeg = OK_IMAGE.read_bytes()
        status, _, data = request(
            port, "POST", "/api/vision/analyze", body=jpeg,
            headers={"Content-Type": "image/jpeg"},
        )
        assert status == 200
        record = json.loads(data.decode("utf-8"))
        analysis = record["analysis"]

        # 已验证值：ok.jpg ≈ 0.7561（未乘 100，未校准）
        assert analysis["score"] == pytest.approx(0.7561, abs=1e-4)
        assert analysis["score_source"] == "dbcnn_onnx"
        assert analysis["model_version"] == "dbcnn_onnx_v1"
        assert analysis["decision"] == "needs_review"
        assert analysis["reject_reason"] == "threshold_not_configured"
        assert analysis["label"] is None
        assert analysis["rule_diagnostics"]["source"] == vision_analysis.RULE_SOURCE
        assert analysis["rule_diagnostics"]["label"] == "ok"
        assert record["input"]["image_ref"]["sha256"] == vf.sha256_hex(jpeg)

        status, saved = request_json(
            port,
            "POST",
            "/api/vision/feedback",
            {"record_id": record["record_id"], "verdict": "undetermined"},
        )
        assert status == 201
        assert saved["feedback"]["label_source"] == vf.FEEDBACK_SOURCE

        status, listed = request_json(port, "GET", "/api/vision/records")
        assert listed["records"][0]["feedback"]["latest"]["verdict"] == "undetermined"
    finally:
        stop_server(httpd, thread)
