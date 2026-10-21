"""Week 09 — 视觉事件与主动询问（PC 侧有限状态机）。

课程目标：把「视觉模型/规则结果」变成一次可解释、可纠正、可拒绝的主动询问，
并保证用户拒绝后进入冷却期、不反复打扰。

设计边界
--------
- 只使用标准库；不调用云端、不新增运行时依赖。
- 事件是**交互层**对象，不改变 DBCNN 分数 / 规则诊断 / 人工反馈的既有语义。
- `needs_review` 本身不是"画面有问题"的证据，只有规则诊断给出具体原因时才触发，
  避免阈值未校准时每张图都弹提示。
- 输入、结果、事件、用户响应通过 `record_id` / `task_ref` 关联。
- 拒绝（ignore）与稍后（snooze）按「来源 + 原因」进入冷却；确认（confirm）不产生冷却。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

EVENT_VERSION = "w9_vision_event_v1"
EVENT_FILE = "events.jsonl"
EVENT_STATES = ("pending", "confirmed", "ignored", "snoozed", "suppressed")
EVENT_ACTIONS = ("confirm", "ignore", "snooze")
PRIORITIES = ("high", "normal", "low")
PRIORITY_RANK = {"high": 3, "normal": 2, "low": 1}

DEFAULT_IGNORE_COOLDOWN_SECONDS = 600  # 10 分钟
DEFAULT_SNOOZE_SECONDS = 300  # 5 分钟
MAX_NOTE_LENGTH = 500
MAX_EVENTS_READ = 500

# 规则诊断的中文原因与优先级；未知值按原值回退，不猜测。
RULE_TRIGGERS: dict[str, tuple[str, str]] = {
    "invalid": ("high", "图像无效，是否重新采集？"),
    "too_dark": ("high", "画面过暗，可能影响判断，是否重拍？"),
    "too_bright": ("high", "画面过亮，可能影响判断，是否重拍？"),
    "low_contrast": ("normal", "画面对比度不足，是否重拍？"),
    "low_detail": ("normal", "画面细节不足，是否重拍？"),
}
DECISION_TRIGGERS: dict[str, tuple[str, str]] = {
    "unknown": ("high", "视觉结果无法判断，是否重拍或人工确认？"),
    "low_score": ("normal", "质量分数较低，是否重拍或人工确认？"),
}


class VisionEventError(RuntimeError):
    """事件层可预期错误基类。"""


class VisionEventValidationError(ValueError):
    """请求字段非法（400）。"""


class VisionEventNotFound(LookupError):
    """事件不存在（404）。"""


class VisionEventConflict(VisionEventError):
    """事件已结束或当前状态不允许该操作（409）。"""


class VisionEventStoreError(VisionEventError):
    """事件持久化失败（500）。"""


def utc_now_iso(now_ms: Optional[int] = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_to_ms(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def _run_id_or_none(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def derive_trigger(record: Any) -> Optional[dict[str, Any]]:
    """从一条分析记录推导主动询问触发条件；无明确证据时返回 None。

    `needs_review` 单独出现（阈值未配置）**不触发**；必须伴随具体规则原因，
    或 decision 为 unknown/low_score。
    """
    if not isinstance(record, dict):
        return None
    analysis = record.get("analysis")
    if not isinstance(analysis, dict):
        return None
    decision = str(analysis.get("decision") or "")
    rule = analysis.get("rule_diagnostics")
    rule_label = ""
    rule_available = False
    if isinstance(rule, dict):
        rule_available = bool(rule.get("available"))
        if rule_available and isinstance(rule.get("label"), str):
            rule_label = rule["label"]

    reason = ""
    priority = ""
    message = ""
    if decision == "unknown":
        reason, (priority, message) = "visual_result_unknown", DECISION_TRIGGERS["unknown"]
    elif decision == "low_score":
        reason, (priority, message) = "score_below_threshold", DECISION_TRIGGERS["low_score"]
    elif rule_available and rule_label in RULE_TRIGGERS:
        priority, message = RULE_TRIGGERS[rule_label]
        reason = rule_label

    if not reason:
        return None

    input_obj = record.get("input") if isinstance(record.get("input"), dict) else {}
    device = input_obj.get("device") if isinstance(input_obj.get("device"), dict) else {}
    score = analysis.get("score")
    return {
        "reason_code": reason,
        "priority": priority,
        "message": message,
        "fingerprint": f"{input_obj.get('source') or 'unknown'}:{reason}",
        "evidence": {
            "decision": decision or None,
            "label": analysis.get("label"),
            "score": score if isinstance(score, (int, float)) else None,
            "rule_label": rule_label or None,
            "rule_available": rule_available,
            "model_version": analysis.get("model_version"),
            "threshold_status": analysis.get("threshold_status"),
        },
        "task_ref": {
            "record_id": record.get("record_id"),
            "request_id": _run_id_or_none(device.get("request_id")),
            "device_index": device.get("index"),
            "source": input_obj.get("source"),
            "captured_ms": device.get("captured_ms"),
        },
    }


class VisionEventStore:
    """仅追加的 JSONL 事件存储；同一 event_id 的后续快照覆盖读取视图。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / EVENT_FILE
        self._lock = threading.RLock()

    def append(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise VisionEventStoreError(f"事件写入失败: {exc}") from exc

    def read_all(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return []
            except OSError as exc:
                raise VisionEventStoreError(f"事件读取失败: {exc}") from exc
        events: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("event_id"):
                events.append(item)
        return events

    def latest(self) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for event in self.read_all():
            by_id[event["event_id"]] = event
        return list(by_id.values())

    def get(self, event_id: str) -> Optional[dict[str, Any]]:
        for event in reversed(self.read_all()):
            if event.get("event_id") == event_id:
                return event
        return None


class VisionEventService:
    """事件生成、优先级、确认/忽略/稍后与冷却。"""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        store: Optional[VisionEventStore] = None,
        ignore_cooldown_seconds: int = DEFAULT_IGNORE_COOLDOWN_SECONDS,
        snooze_seconds: int = DEFAULT_SNOOZE_SECONDS,
    ) -> None:
        if ignore_cooldown_seconds < 0 or snooze_seconds < 0:
            raise ValueError("冷却时间不得为负数")
        self.store = store or VisionEventStore(data_dir)
        self.ignore_cooldown_seconds = int(ignore_cooldown_seconds)
        self.snooze_seconds = int(snooze_seconds)
        self._lock = threading.RLock()
        self._counter = 0

    def _now_ms(self, now_ms: Optional[int] = None) -> int:
        return int(time.time() * 1000) if now_ms is None else int(now_ms)

    def _new_event_id(self, now_ms: int) -> str:
        with self._lock:
            self._counter += 1
            return f"ve-{now_ms}-{self._counter:04d}"

    def _latest_by_id(self) -> list[dict[str, Any]]:
        return self.store.latest()

    def _find_by_record(self, record_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not record_id:
            return None
        for event in reversed(self._latest_by_id()):
            task_ref = event.get("task_ref") or {}
            if task_ref.get("record_id") == record_id:
                return event
        return None

    def _suppression_until(self, fingerprint: str, now_ms: int) -> Optional[int]:
        until: Optional[int] = None
        for event in self._latest_by_id():
            if event.get("fingerprint") != fingerprint:
                continue
            if event.get("state") not in ("ignored", "snoozed"):
                continue
            candidate = event.get("cooldown_until_ms")
            if isinstance(candidate, int) and candidate > now_ms:
                until = candidate if until is None else max(until, candidate)
        return until

    def on_record(self, record: dict[str, Any], *, now_ms: Optional[int] = None) -> Optional[dict[str, Any]]:
        """分析记录产生事件；同一 record_id 只生成一次。"""
        trigger = derive_trigger(record)
        if trigger is None:
            return None
        record_id = record.get("record_id")
        existing = self._find_by_record(record_id)
        if existing is not None:
            return existing

        now = self._now_ms(now_ms)
        suppression = self._suppression_until(trigger["fingerprint"], now)
        state = "suppressed" if suppression is not None else "pending"
        event = {
            "event_version": EVENT_VERSION,
            "event_id": self._new_event_id(now),
            "created_at": utc_now_iso(now),
            "updated_at": utc_now_iso(now),
            "state": state,
            "priority": trigger["priority"],
            "reason_code": trigger["reason_code"],
            "message": trigger["message"],
            "fingerprint": trigger["fingerprint"],
            "task_ref": trigger["task_ref"],
            "evidence": trigger["evidence"],
            "cooldown_until_ms": suppression,
            "cooldown_until": utc_now_iso(suppression) if suppression else None,
            "responses": [],
            "note": (
                "同类事件处于冷却期，本次未主动提示"
                if state == "suppressed"
                else None
            ),
        }
        self.store.append(event)
        return event

    def _sorted_latest(self) -> list[dict[str, Any]]:
        events = self._latest_by_id()
        events.sort(
            key=lambda item: (
                PRIORITY_RANK.get(str(item.get("priority")), 0),
                _iso_to_ms(item.get("created_at")) or 0,
            ),
            reverse=True,
        )
        return events

    def active(self, *, now_ms: Optional[int] = None) -> Optional[dict[str, Any]]:
        now = self._now_ms(now_ms)
        for event in self._sorted_latest():
            if event.get("state") != "pending":
                continue
            until = event.get("cooldown_until_ms")
            if isinstance(until, int) and until > now:
                continue
            return event
        return None

    def list_events(self, limit: int = 20, *, now_ms: Optional[int] = None) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise VisionEventValidationError("limit 必须位于 1–200")
        latest = self._latest_by_id()
        latest.sort(
            key=lambda item: _iso_to_ms(item.get("created_at")) or 0,
            reverse=True,
        )
        counts = {state: 0 for state in EVENT_STATES}
        for event in latest:
            state = event.get("state")
            if state in counts:
                counts[state] += 1
        return {
            "event_version": EVENT_VERSION,
            "active": self.active(now_ms=now_ms),
            "events": latest[:limit],
            "counts": counts,
            "config": {
                "actions": list(EVENT_ACTIONS),
                "states": list(EVENT_STATES),
                "priorities": list(PRIORITIES),
                "ignore_cooldown_seconds": self.ignore_cooldown_seconds,
                "snooze_seconds": self.snooze_seconds,
                "needs_review_alone_triggers": False,
            },
        }

    def health(self) -> dict[str, Any]:
        latest = self._latest_by_id()
        counts = {state: 0 for state in EVENT_STATES}
        for event in latest:
            if event.get("state") in counts:
                counts[event["state"]] += 1
        active = self.active()
        return {
            "event_version": EVENT_VERSION,
            "storage_file": str(self.store.path),
            "actions": list(EVENT_ACTIONS),
            "states": list(EVENT_STATES),
            "priorities": list(PRIORITIES),
            "ignore_cooldown_seconds": self.ignore_cooldown_seconds,
            "snooze_seconds": self.snooze_seconds,
            "needs_review_alone_triggers": False,
            "counts": counts,
            "active_event_id": active.get("event_id") if active else None,
        }

    @staticmethod
    def _normalize_response(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise VisionEventValidationError("请求体必须是 JSON 对象")
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise VisionEventValidationError("event_id 不能为空")
        action = payload.get("action")
        if not isinstance(action, str) or action not in EVENT_ACTIONS:
            raise VisionEventValidationError(
                f"action 必须是 {' / '.join(EVENT_ACTIONS)} 之一"
            )
        note = payload.get("note")
        if note is None:
            note_text = ""
        elif isinstance(note, str):
            note_text = note.strip()
        else:
            raise VisionEventValidationError("note 必须是字符串")
        if len(note_text) > MAX_NOTE_LENGTH:
            raise VisionEventValidationError(f"note 不得超过 {MAX_NOTE_LENGTH} 字")
        return {"event_id": event_id.strip(), "action": action, "note": note_text}

    def respond(
        self, payload: Any, *, now_ms: Optional[int] = None
    ) -> dict[str, Any]:
        fields = self._normalize_response(payload)
        now = self._now_ms(now_ms)
        with self._lock:
            latest = self.store.get(fields["event_id"])
            if latest is None:
                raise VisionEventNotFound(f"事件不存在: {fields['event_id']}")
            if latest.get("state") in ("confirmed", "suppressed"):
                raise VisionEventConflict(
                    f"事件状态为 {latest.get('state')}，不能再次响应"
                )
            if latest.get("state") not in ("pending", "ignored", "snoozed"):
                raise VisionEventConflict(
                    f"事件状态为 {latest.get('state')}，不能响应"
                )

            event = dict(latest)
            action = fields["action"]
            state = {
                "confirm": "confirmed",
                "ignore": "ignored",
                "snooze": "snoozed",
            }[action]
            cooldown_ms: Optional[int] = None
            if action == "ignore":
                cooldown_ms = now + self.ignore_cooldown_seconds * 1000
            elif action == "snooze":
                cooldown_ms = now + self.snooze_seconds * 1000

            event["state"] = state
            event["updated_at"] = utc_now_iso(now)
            event["cooldown_until_ms"] = cooldown_ms
            event["cooldown_until"] = utc_now_iso(cooldown_ms) if cooldown_ms else None
            responses = list(event.get("responses") or [])
            responses.append(
                {
                    "action": action,
                    "note": fields["note"] or None,
                    "at": event["updated_at"],
                }
            )
            event["responses"] = responses
            event["note"] = fields["note"] or None
            self.store.append(event)
            return event
