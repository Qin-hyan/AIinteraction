"""nl_to_task.py — Week 04–06 语音文本 → 结构化任务契约（确定性映射）。

背景：ASR 输出是自然语言文本，而 ``task_pipeline.run_task()`` 要求结构化 JSON。
本模块补上这条缺失的接口，不调用云端语言模型、不猜造设备地址。

范围（与 Week 04 契约一致）：
  - query_last：查看上次/最近/已存观测
  - trigger_collect：重新采集/再采集一次
  - clarify：输入含糊或要求取消（取消应使用界面按钮）
  - unsupported：超出教学白名单的动作

无法确定时返回 ``clarify``，绝不为了"能执行"而猜测意图。
"""

from __future__ import annotations

import json
import re
from typing import Any

_TASK_VERBS = ("查看", "查询", "看看", "显示", "读取", "读一下", "报一下")
_DATA_NOUNS = ("数据", "观测", "数值", "值", "状态", "结果", "加速度", "传感器")
_LAST_MARKERS = ("上次", "上一次", "最近", "刚才", "已存", "保存的", "历史")
_COLLECT_PATTERNS = (
    "重新采集",
    "再采集",
    "采集一次",
    "采样一次",
    "重新采样",
    "再采样",
    "立刻采集",
    "马上采集",
    "现在采集",
    "新采集",
    "采集最新",
)
_UNSUPPORTED_VERBS = (
    "打开",
    "关闭",
    "播放",
    "暂停音乐",
    "发消息",
    "打电话",
    "导航",
    "设置",
    "调高",
    "调低",
    "开灯",
    "关灯",
)
_CANCEL_WORDS = ("取消", "停止任务", "别录了", "结束任务")


def _contract(intent: str, original: str, confidence: float, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "intent": intent,
        "confidence": confidence,
        "original": original,
    }
    payload.update(extra)
    return payload


def classify_text(text: Any) -> dict[str, Any]:
    """把 ASR 文本分类为 TaskContract 字典；不抛异常（空/非字符串→clarify）。"""
    original = text.strip() if isinstance(text, str) else ""
    normalized = re.sub(r"\s+", "", original).lower()
    if not normalized:
        return _contract(
            "clarify",
            original or "（空输入）",
            0.2,
            question="请说明要查看上次数据，还是重新采集一次。",
        )

    if any(word in normalized for word in _CANCEL_WORDS):
        return _contract(
            "clarify",
            original,
            0.6,
            question="如需取消当前任务，请点击界面上的「取消任务」按钮，或重新说一次查询/采集。",
        )

    has_data_noun = any(noun in normalized for noun in _DATA_NOUNS)
    has_last_marker = any(marker in normalized for marker in _LAST_MARKERS)
    has_query_verb = any(verb in normalized for verb in _TASK_VERBS)
    has_collect_pattern = any(pattern in normalized for pattern in _COLLECT_PATTERNS)
    has_collect_word = "采集" in normalized or "采样" in normalized

    # “查看上次采集的数据”优先按查询处理，不能被“采集”抢走。
    query_score = 0
    if has_data_noun and has_last_marker:
        query_score += 2
    if has_data_noun and has_query_verb:
        query_score += 1
    if has_last_marker and has_query_verb:
        query_score += 1

    if query_score >= 2 and not has_collect_pattern:
        return _contract("query_last", original, 0.9)

    if has_collect_pattern:
        return _contract("trigger_collect", original, 0.9)

    if has_collect_word and has_data_noun and has_last_marker:
        return _contract("query_last", original, 0.7)

    if has_collect_word and not has_query_verb:
        return _contract("trigger_collect", original, 0.65)

    if query_score >= 1:
        return _contract("query_last", original, 0.6)

    if any(verb in normalized for verb in _UNSUPPORTED_VERBS):
        return _contract(
            "unsupported",
            original,
            0.2,
            reason="该动作不在 Week 04–06 教学白名单内；目前只支持查询上次数据或重新采集一次。",
        )

    return _contract(
        "clarify",
        original,
        0.3,
        question="我没有听清要查询上次数据，还是重新采集一次。",
    )


def to_json(text: Any) -> str:
    """返回可直接传给 ``task_pipeline.run_task()`` 的 JSON 字符串。"""
    return json.dumps(classify_text(text), ensure_ascii=False)


__all__ = ["classify_text", "to_json"]
