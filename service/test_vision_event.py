"""Week 09 视觉事件状态机测试（stdlib only，不访问设备/模型）。"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from . import vision_event as ve
except ImportError:  # pragma: no cover
    import vision_event as ve  # type: ignore[no-redef]


def make_record(
    record_id: str = "vision-1",
    *,
    decision: str = "needs_review",
    rule_label: str | None = "too_dark",
    rule_available: bool = True,
    score: float | None = 0.42,
    source: str = "local_upload",
    request_id: str | None = None,
) -> dict:
    rule = {
        "available": rule_available,
        "label": rule_label,
        "source": "week07_image_quality_rules",
    }
    return {
        "record_id": record_id,
        "input": {
            "source": source,
            "device": {"request_id": request_id} if request_id else None,
            "image_ref": {"sha256": "a" * 64, "bytes_len": 1234},
        },
        "analysis": {
            "request_id": record_id,
            "decision": decision,
            "label": None,
            "score": score,
            "model_version": "dbcnn_onnx_v1",
            "threshold_status": "not_configured",
            "rule_diagnostics": rule,
        },
    }


# ── 触发条件 ──────────────────────────────────


def test_needs_review_without_rule_evidence_does_not_trigger() -> None:
    record = make_record(decision="needs_review", rule_label="ok")
    assert ve.derive_trigger(record) is None


def test_needs_review_with_dark_rule_triggers_high() -> None:
    trigger = ve.derive_trigger(make_record(rule_label="too_dark"))
    assert trigger is not None
    assert trigger["reason_code"] == "too_dark"
    assert trigger["priority"] == "high"
    assert "过暗" in trigger["message"]


def test_unknown_decision_triggers_high() -> None:
    trigger = ve.derive_trigger(make_record(decision="unknown", rule_label="ok"))
    assert trigger is not None
    assert trigger["reason_code"] == "visual_result_unknown"
    assert trigger["priority"] == "high"


def test_low_score_triggers_normal() -> None:
    trigger = ve.derive_trigger(make_record(decision="low_score", rule_label="ok"))
    assert trigger is not None
    assert trigger["reason_code"] == "score_below_threshold"
    assert trigger["priority"] == "normal"


def test_low_detail_rule_triggers_normal() -> None:
    trigger = ve.derive_trigger(make_record(rule_label="low_detail"))
    assert trigger is not None
    assert trigger["priority"] == "normal"


def test_invalid_rule_triggers_high() -> None:
    trigger = ve.derive_trigger(make_record(rule_label="invalid"))
    assert trigger is not None
    assert trigger["priority"] == "high"


def test_unavailable_rule_cannot_be_used_as_evidence() -> None:
    trigger = ve.derive_trigger(
        make_record(decision="needs_review", rule_label="too_dark", rule_available=False)
    )
    assert trigger is None


def test_ok_result_with_problem_rule_still_triggers() -> None:
    trigger = ve.derive_trigger(make_record(decision="ok", rule_label="too_dark"))
    assert trigger is not None
    assert trigger["reason_code"] == "too_dark"


def test_trigger_keeps_record_and_request_ids() -> None:
    trigger = ve.derive_trigger(
        make_record(record_id="vision-9", source="device_gallery", request_id="req-7")
    )
    assert trigger is not None
    assert trigger["task_ref"] == {
        "record_id": "vision-9",
        "request_id": "req-7",
        "device_index": None,
        "source": "device_gallery",
        "captured_ms": None,
    }


# ── 事件生成 / 去重 / 持久化 ───────────────────


def test_on_record_creates_pending_event(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    event = service.on_record(make_record(), now_ms=1_000_000)
    assert event is not None
    assert event["state"] == "pending"
    assert event["priority"] == "high"
    assert event["event_id"].startswith("ve-1000000-")
    assert event["cooldown_until_ms"] is None
    assert service.active(now_ms=1_000_000)["event_id"] == event["event_id"]


def test_same_record_does_not_create_duplicate(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    first = service.on_record(make_record(), now_ms=1_000_000)
    second = service.on_record(make_record(), now_ms=1_000_001)
    assert first is not None and second is not None
    assert first["event_id"] == second["event_id"]
    assert len(service.list_events()["events"]) == 1


def test_event_persists_across_service_instances(tmp_path: Path) -> None:
    first = ve.VisionEventService(tmp_path)
    event = first.on_record(make_record(), now_ms=1_000_000)
    assert event is not None
    second = ve.VisionEventService(tmp_path)
    active = second.active(now_ms=1_000_001)
    assert active is not None
    assert active["event_id"] == event["event_id"]


def test_list_events_rejects_bad_limit(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    with pytest.raises(ve.VisionEventValidationError):
        service.list_events(limit=0)
    with pytest.raises(ve.VisionEventValidationError):
        service.list_events(limit=201)


def test_health_counts_and_config(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path, ignore_cooldown_seconds=60, snooze_seconds=30)
    service.on_record(make_record(), now_ms=1_000_000)
    health = service.health()
    assert health["ignore_cooldown_seconds"] == 60
    assert health["snooze_seconds"] == 30
    assert health["needs_review_alone_triggers"] is False
    assert health["counts"]["pending"] == 1
    assert health["active_event_id"]


# ── 确认 / 忽略 / 稍后 / 冷却 ─────────────────


def test_ignore_enters_cooldown_and_suppresses_same_reason(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path, ignore_cooldown_seconds=600)
    event = service.on_record(make_record(record_id="vision-1"), now_ms=1_000_000)
    assert event is not None
    ignored = service.respond(
        {"event_id": event["event_id"], "action": "ignore", "note": "环境太暗"},
        now_ms=1_000_100,
    )
    assert ignored["state"] == "ignored"
    assert ignored["cooldown_until_ms"] == 1_000_100 + 600_000
    assert ignored["responses"][0]["action"] == "ignore"
    assert service.active(now_ms=1_000_200) is None

    suppressed = service.on_record(
        make_record(record_id="vision-2", source="local_upload"),
        now_ms=1_000_200,
    )
    assert suppressed is not None
    assert suppressed["state"] == "suppressed"
    assert suppressed["cooldown_until_ms"] == ignored["cooldown_until_ms"]


def test_after_ignore_cooldown_new_event_is_pending(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path, ignore_cooldown_seconds=10)
    event = service.on_record(make_record(record_id="vision-1"), now_ms=1_000_000)
    assert event is not None
    service.respond(
        {"event_id": event["event_id"], "action": "ignore"}, now_ms=1_000_000
    )
    later = service.on_record(make_record(record_id="vision-2"), now_ms=1_010_001)
    assert later is not None
    assert later["state"] == "pending"


def test_snooze_uses_shorter_cooldown(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path, snooze_seconds=300)
    event = service.on_record(make_record(), now_ms=1_000_000)
    assert event is not None
    snoozed = service.respond(
        {"event_id": event["event_id"], "action": "snooze"}, now_ms=1_000_000
    )
    assert snoozed["state"] == "snoozed"
    assert snoozed["cooldown_until_ms"] == 1_300_000


def test_confirm_does_not_suppress_new_records(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path, ignore_cooldown_seconds=600)
    event = service.on_record(make_record(record_id="vision-1"), now_ms=1_000_000)
    assert event is not None
    confirmed = service.respond(
        {"event_id": event["event_id"], "action": "confirm"}, now_ms=1_000_000
    )
    assert confirmed["state"] == "confirmed"
    assert confirmed["cooldown_until_ms"] is None
    next_event = service.on_record(make_record(record_id="vision-2"), now_ms=1_000_001)
    assert next_event is not None
    assert next_event["state"] == "pending"


def test_active_prefers_high_priority_and_falls_back(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    low = service.on_record(
        make_record(record_id="vision-low", decision="low_score", rule_label="ok"),
        now_ms=1_000_000,
    )
    high = service.on_record(
        make_record(record_id="vision-high", decision="unknown", rule_label="ok"),
        now_ms=1_000_001,
    )
    assert low is not None and high is not None
    assert service.active(now_ms=1_000_002)["event_id"] == high["event_id"]
    service.respond({"event_id": high["event_id"], "action": "confirm"}, now_ms=1_000_003)
    assert service.active(now_ms=1_000_004)["event_id"] == low["event_id"]


# ── 响应校验 / 错误路径 ───────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event_id": "", "action": "confirm"},
        {"event_id": "ve-1", "action": "yes"},
        {"event_id": "ve-1", "action": "confirm", "note": 3},
        {"event_id": "ve-1", "action": "confirm", "note": "x" * 501},
        "not-an-object",
    ],
)
def test_invalid_response_payload(tmp_path: Path, payload) -> None:
    service = ve.VisionEventService(tmp_path)
    with pytest.raises(ve.VisionEventValidationError):
        service.respond(payload)


def test_unknown_event_returns_not_found(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    with pytest.raises(ve.VisionEventNotFound):
        service.respond({"event_id": "ve-nope", "action": "confirm"})


def test_confirmed_event_cannot_be_responded_again(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    event = service.on_record(make_record(), now_ms=1_000_000)
    assert event is not None
    service.respond({"event_id": event["event_id"], "action": "confirm"}, now_ms=1_000_000)
    with pytest.raises(ve.VisionEventConflict):
        service.respond(
            {"event_id": event["event_id"], "action": "ignore"}, now_ms=1_000_001
        )


def test_suppressed_event_cannot_be_responded(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path, ignore_cooldown_seconds=600)
    first = service.on_record(make_record(record_id="vision-1"), now_ms=1_000_000)
    assert first is not None
    service.respond({"event_id": first["event_id"], "action": "ignore"}, now_ms=1_000_000)
    suppressed = service.on_record(make_record(record_id="vision-2"), now_ms=1_000_001)
    assert suppressed is not None
    with pytest.raises(ve.VisionEventConflict):
        service.respond(
            {"event_id": suppressed["event_id"], "action": "confirm"}, now_ms=1_000_002
        )


def test_ignore_note_is_preserved_and_trimmed(tmp_path: Path) -> None:
    service = ve.VisionEventService(tmp_path)
    event = service.on_record(make_record(), now_ms=1_000_000)
    assert event is not None
    updated = service.respond(
        {"event_id": event["event_id"], "action": "ignore", "note": "  环境太暗  "},
        now_ms=1_000_000,
    )
    assert updated["note"] == "环境太暗"
    assert updated["responses"][0]["note"] == "环境太暗"
