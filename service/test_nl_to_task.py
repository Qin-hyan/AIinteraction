"""Week 04–06 语音文本 → 任务契约 → 管道 的确定性映射测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from . import nl_to_task
from .task_pipeline import run_task


def test_query_last_from_view_last_data() -> None:
    task = nl_to_task.classify_text("查看上次的数据")
    assert task["intent"] == "query_last"
    assert task["original"] == "查看上次的数据"
    assert 0 <= task["confidence"] <= 1


def test_query_last_from_recent_observation() -> None:
    assert nl_to_task.classify_text("最近的观测值是多少").get("intent") == "query_last"


def test_query_beats_collect_in_last_capture_phrase() -> None:
    task = nl_to_task.classify_text("查看上次采集的数据")
    assert task["intent"] == "query_last"


def test_trigger_collect_from_recapture() -> None:
    assert nl_to_task.classify_text("重新采集一次").get("intent") == "trigger_collect"


def test_trigger_collect_from_collect_once() -> None:
    assert nl_to_task.classify_text("采集一次最新数据").get("intent") == "trigger_collect"


def test_trigger_collect_from_plain_collect() -> None:
    assert nl_to_task.classify_text("采集").get("intent") == "trigger_collect"


def test_cancel_returns_clarify_with_button_hint() -> None:
    task = nl_to_task.classify_text("取消")
    assert task["intent"] == "clarify"
    assert "取消任务" in task["question"]


def test_empty_text_returns_clarify() -> None:
    task = nl_to_task.classify_text("   ")
    assert task["intent"] == "clarify"
    assert task["question"]


def test_unsupported_action_is_rejected() -> None:
    task = nl_to_task.classify_text("打开灯")
    assert task["intent"] == "unsupported"
    assert "白名单" in task["reason"]


def test_unknown_text_returns_clarify_not_guess() -> None:
    task = nl_to_task.classify_text("今天天气怎么样")
    assert task["intent"] == "clarify"


@pytest.mark.parametrize(
    "text",
    ["查看上次数据", "重新采集一次", "取消", "打开灯", ""],
)
def test_to_json_is_valid_task_contract(text: str) -> None:
    from .validator import parse_and_validate

    parsed = json.loads(nl_to_task.to_json(text))
    result = parse_and_validate(nl_to_task.to_json(text))
    assert result.valid, result.errors
    assert parsed["original"] == (text or "（空输入）")


def test_query_command_reaches_tool_via_pipeline() -> None:
    observation = {
        "valid": True,
        "record_id": "obs-1",
        "source": "live",
        "seq": 7,
        "accel_x": 1.0,
        "accel_y": 2.0,
        "accel_z": 3.0,
        "button": "none",
        "observed_ms": 1000,
    }
    with mock.patch(
        "service.task_dispatcher._query_last",
        return_value=SimpleNamespace(success=True, data=observation, error=None),
    ):
        output = run_task(nl_to_task.to_json("查看上次数据"))
    assert "加速度" in output
    assert "观测序号" in output


def test_collect_command_reaches_tool_via_pipeline() -> None:
    observation = {
        "valid": True,
        "record_id": "obs-2",
        "source": "live",
        "seq": 8,
        "accel_x": 4.0,
        "accel_y": 5.0,
        "accel_z": 6.0,
        "button": "none",
        "observed_ms": 2000,
    }
    with mock.patch(
        "service.task_dispatcher._trigger_collect",
        return_value=SimpleNamespace(
            success=True,
            error=None,
            request_id="req-1",
            status="completed",
            observation=observation,
        ),
    ):
        output = run_task(nl_to_task.to_json("重新采集一次"))
    assert "req-1" in output or "采集" in output


def test_clarify_command_does_not_call_device_tool() -> None:
    with mock.patch("service.task_dispatcher._query_last") as q, mock.patch(
        "service.task_dispatcher._trigger_collect"
    ) as c:
        output = run_task(nl_to_task.to_json("取消"))
    q.assert_not_called()
    c.assert_not_called()
    assert "取消任务" in output


def test_unsupported_command_does_not_call_device_tool() -> None:
    with mock.patch("service.task_dispatcher._query_last") as q, mock.patch(
        "service.task_dispatcher._trigger_collect"
    ) as c:
        output = run_task(nl_to_task.to_json("打开灯"))
    q.assert_not_called()
    c.assert_not_called()
    assert "白名单" in output
