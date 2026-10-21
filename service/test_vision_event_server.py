"""Week 09 视觉事件 HTTP 集成测试（临时端口，不访问真实模型/设备）。"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from . import vision_feedback as vf
from . import vision_feedback_server as server

JPEG = b"\xff\xd8\xff\xe0" + b"week09-event-test" * 8


def _analyzer(**kwargs: Any) -> dict[str, Any]:
    return {
        "request_id": kwargs["request_id"],
        "created_at": "2026-10-22T00:00:00.000Z",
        "decision": "needs_review",
        "label": None,
        "score": 0.21,
        "model_version": "test_dbcnn",
        "threshold_status": "not_configured",
        "rule_diagnostics": {
            "available": True,
            "label": "too_dark",
            "source": "week07_image_quality_rules",
        },
    }


def make_service(tmp_path: Path) -> vf.VisionFeedbackService:
    return vf.VisionFeedbackService(
        data_dir=tmp_path / "data",
        scorer_factory=lambda: object(),
        analyzer=_analyzer,
    )


def start_server(service: vf.VisionFeedbackService):
    httpd = server.create_server("127.0.0.1", 0, service)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1], thread


def stop_server(httpd, thread) -> None:
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


@pytest.fixture()
def live(tmp_path: Path):
    service = make_service(tmp_path)
    httpd, port, thread = start_server(service)
    try:
        yield port, service
    finally:
        stop_server(httpd, thread)


def _analyze(port: int) -> dict[str, Any]:
    status, _headers, body = request(
        port,
        "POST",
        "/api/vision/analyze",
        body=JPEG,
        headers={"Content-Type": "image/jpeg"},
    )
    assert status == 200, body
    return json.loads(body)


def test_analyze_creates_pending_event_and_lists_it(live) -> None:
    port, _service = live
    record = _analyze(port)
    assert record["event"]["state"] == "pending"
    assert record["event"]["priority"] == "high"
    assert record["event"]["task_ref"]["record_id"] == record["record_id"]

    status, _headers, body = request(port, "GET", "/api/vision/events")
    assert status == 200
    payload = json.loads(body)
    assert payload["active"]["event_id"] == record["event"]["event_id"]
    assert payload["counts"]["pending"] == 1
    assert payload["config"]["needs_review_alone_triggers"] is False


def test_ignore_then_next_event_is_suppressed(live) -> None:
    port, _service = live
    first = _analyze(port)
    status, _headers, body = request(
        port,
        "POST",
        "/api/vision/events/respond",
        body=json.dumps(
            {"event_id": first["event"]["event_id"], "action": "ignore", "note": "环境暗"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert status == 200, body
    assert json.loads(body)["event"]["state"] == "ignored"

    second = _analyze(port)
    assert second["event"]["state"] == "suppressed"
    status, _headers, body = request(port, "GET", "/api/vision/events/active")
    assert status == 200
    assert json.loads(body)["active"] is None


def test_confirm_clears_active_without_suppression(live) -> None:
    port, _service = live
    first = _analyze(port)
    status, _headers, _body = request(
        port,
        "POST",
        "/api/vision/events/respond",
        body=json.dumps(
            {"event_id": first["event"]["event_id"], "action": "confirm"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    second = _analyze(port)
    assert second["event"]["state"] == "pending"


def test_invalid_event_action_is_400(live) -> None:
    port, _service = live
    first = _analyze(port)
    status, _headers, body = request(
        port,
        "POST",
        "/api/vision/events/respond",
        body=json.dumps(
            {"event_id": first["event"]["event_id"], "action": "accept"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert status == 400
    assert json.loads(body)["error"] == "invalid_event_response"


def test_unknown_event_is_404(live) -> None:
    port, _service = live
    status, _headers, body = request(
        port,
        "POST",
        "/api/vision/events/respond",
        body=json.dumps({"event_id": "ve-nope", "action": "confirm"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert status == 404
    assert json.loads(body)["error"] == "event_not_found"


def test_confirmed_event_is_409(live) -> None:
    port, _service = live
    first = _analyze(port)
    payload = json.dumps(
        {"event_id": first["event"]["event_id"], "action": "confirm"}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    assert request(port, "POST", "/api/vision/events/respond", payload, headers)[0] == 200
    status, _headers, body = request(
        port, "POST", "/api/vision/events/respond", payload, headers
    )
    assert status == 409
    assert json.loads(body)["error"] == "event_state_conflict"


def test_health_reports_event_config(live) -> None:
    port, _service = live
    status, _headers, body = request(port, "GET", "/api/vision/health")
    assert status == 200
    events = json.loads(body)["events"]
    assert events["event_version"] == "w9_vision_event_v1"
    assert events["needs_review_alone_triggers"] is False
    assert events["ignore_cooldown_seconds"] > 0


def test_events_limit_is_validated(live) -> None:
    port, _service = live
    status, _headers, body = request(port, "GET", "/api/vision/events?limit=0")
    assert status == 400
    assert json.loads(body)["error"] in ("invalid_request", "invalid_event_response")
