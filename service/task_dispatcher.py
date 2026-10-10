"""
task_dispatcher.py — Week 04 受限任务分发模块

职责：
  1. 接收结构化任务 JSON 字符串
  2. 调用 parse_and_validate() 完成检验
  3. 按四种意图分发：
     - query_last:      调用 query_last() 工具
     - trigger_collect: 调用 trigger_collect() 工具
     - clarify:         返回澄清问题（不调设备工具）
     - unsupported:     返回拒绝原因（不调设备工具）
  4. 非法 JSON / 校验失败 / 未知意图 — 失败关闭，不执行任何工具

约束：
  - 纯 Python 标准库，零外部依赖
  - 不接入语言模型，不修改 ESP32 业务代码
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .task_schema import IntentEnum, TaskContract
from .validator import parse_and_validate
from .query_last import query_last as _query_last
from .trigger_collect import trigger_collect as _trigger_collect


# ──────────────────────────────────────────────
# 分发结果
# ──────────────────────────────────────────────


@dataclass
class DispatchResult:
    """受限任务分发器的一次执行结果。

    Fields:
        success:    True=任务成功完成（含 query_last/trigger_collect 工具成功）
                   False=校验失败、工具失败、或意图无需调用设备工具
        intent:     实际处理的意图字符串

        # 校验失败 / 工具失败时有效
        error:      错误描述（success=True 的 clarify/unsupported 也有描述）

        # query_last 成功时：
        observation:  设备返回的观测数据字典

        # trigger_collect 成功时：
        request_id:            设备分配的请求 ID
        collect_status:        最终采集状态字符串
        collect_observation:   本次采集的有效观测字典

        # clarify 成功（非工具调用）时：
        question:  需要向用户澄清的问题

        # unsupported 成功（非工具调用）时：
        reason:    拒绝/不支持的原因

        # 校验失败时保留原始字段
        validation_errors:  校验错误列表
    """

    success: bool
    intent: str
    error: Optional[str] = None

    # query_last
    observation: Optional[Dict[str, Any]] = None

    # trigger_collect
    request_id: Optional[str] = None
    collect_status: str = ""
    collect_observation: Optional[Dict[str, Any]] = None

    # clarify
    question: Optional[str] = None

    # unsupported
    reason: Optional[str] = None

    # 校验失败信息
    validation_errors: Any = None


class _SkipDispatch(Exception):
    """内部信号：意图不需要调用设备工具，已在 result 中设置好返回内容。"""
    def __init__(self, result: DispatchResult):
        self.result = result


# ──────────────────────────────────────────────
# 意图分发函数
# ──────────────────────────────────────────────


def _dispatch_query_last(contract: TaskContract) -> DispatchResult:
    """分发 query_last 意图：调用 query_last 工具。"""
    result_raw = _query_last()

    # 提取观测数据（成功时 data 可能为 None，代表无有效记录）
    observation = result_raw.data if result_raw.success else None

    # query_last 工具可能成功但无有效记录 (valid=false, source=none)
    # 此时仍算工具调用成功，但 observation 为 None
    return DispatchResult(
        success=result_raw.success,
        intent=IntentEnum.QUERY_LAST.value,
        error=result_raw.error,
        observation=observation,
    )


def _dispatch_trigger_collect(contract: TaskContract) -> DispatchResult:
    """分发 trigger_collect 意图：调用 trigger_collect 工具。"""
    result_raw = _trigger_collect()

    return DispatchResult(
        success=result_raw.success,
        intent=IntentEnum.TRIGGER_COLLECT.value,
        error=result_raw.error,
        request_id=result_raw.request_id,
        collect_status=result_raw.status,
        collect_observation=result_raw.observation,
    )


def _dispatch_clarify(contract: TaskContract) -> DispatchResult:
    """分发 clarify 意图：返回澄清问题，不调用设备工具。"""
    return DispatchResult(
        success=True,
        intent=IntentEnum.CLARIFY.value,
        question=contract.question,
        error=None,
    )


def _dispatch_unsupported(contract: TaskContract) -> DispatchResult:
    """分发 unsupported 意图：返回拒绝原因，不调用设备工具。"""
    return DispatchResult(
        success=True,
        intent=IntentEnum.UNSUPPORTED.value,
        reason=contract.reason,
        error=None,
    )


# 意图到分发函数的映射
_INTENT_DISPATCH_MAP = {
    IntentEnum.QUERY_LAST.value:      _dispatch_query_last,
    IntentEnum.TRIGGER_COLLECT.value: _dispatch_trigger_collect,
    IntentEnum.CLARIFY.value:         _dispatch_clarify,
    IntentEnum.UNSUPPORTED.value:     _dispatch_unsupported,
}


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────


def dispatch_task(raw_input: str) -> DispatchResult:
    """受限任务分发唯一入口。

    流程：
      1. 调用 parse_and_validate(raw_input) 校验
      2. 校验失败 → 返回 DispatchResult(success=False, ...)，不调任何工具
      3. 校验成功 → 按意图分发：
         - query_last:      真实调用 query_last()
         - trigger_collect: 真实调用 trigger_collect()
         - clarify:         返回澄清问题，不调用设备
         - unsupported:     返回拒绝原因，不调用设备
      4. 工具内部异常 → 捕获并返回失败

    Args:
        raw_input: 用户原始输入字符串（预期为 JSON 格式）。

    Returns:
        DispatchResult:
          - success=True:  任务受理并执行完成
          - success=False: 校验失败、工具失败或未知错误
    """
    # Step 1: 校验
    validation = parse_and_validate(raw_input)

    if not validation.valid:
        return DispatchResult(
            success=False,
            intent="",
            error="; ".join(validation.errors) if validation.errors else "校验失败",
            validation_errors=validation.errors,
        )

    contract = validation.contract
    if contract is None:
        return DispatchResult(
            success=False,
            intent="",
            error="校验通过但未生成契约",
            validation_errors=["契约为空"],
        )

    # Step 2: 按意图分发
    intent = contract.intent
    dispatch_fn = _INTENT_DISPATCH_MAP.get(intent)

    if dispatch_fn is None:
        # 原则上不会到达这里（parse_and_validate 已校验意图合法性）
        return DispatchResult(
            success=False,
            intent=intent,
            error=f"未知意图: {intent!r}，无法分发",
        )

    try:
        return dispatch_fn(contract)
    except Exception as e:
        return DispatchResult(
            success=False,
            intent=intent,
            error=f"工具执行异常: {type(e).__name__}: {e}",
        )