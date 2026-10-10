"""
validator.py — Week 04 严格校验函数

职责：
  1. 解析 JSON 字符串 → Python 对象
  2. 用 JSON Schema 做结构性校验
  3. 按意图做契约级校验（无设备地址、澄清必带 question 等）
  4. 返回标准化的 ValidationResult

不接入语言模型，不调用设备接口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .task_schema import (
    INTENT_ALLOWED_FIELDS,
    IntentEnum,
    TaskContract,
)


# ──────────────────────────────────────────────
# 校验结果
# ──────────────────────────────────────────────


@dataclass
class ValidationResult:
    """一次校验的结果。

    Fields:
        valid:     是否通过全部校验
        contract:  通过校验时的任务契约（None 表示未通过）
        errors:    错误信息列表
    """

    valid: bool
    contract: Optional[TaskContract] = None
    errors: List[str] = field(default_factory=list)


# ──────────────────────────────────────────────
# 解析后的中间表示
# ──────────────────────────────────────────────


@dataclass
class ParsedTask:
    """已成功解析 JSON 但尚未通过契约校验的任务。"""

    raw: Dict[str, Any]
    intent: str
    confidence: float
    original: str


# ──────────────────────────────────────────────
# JSON Schema 校验（纯标准库实现）
# ──────────────────────────────────────────────


def _validate_type(value: Any, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float))
    if expected == "string":
        return isinstance(value, str)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _validate_schema(data: Any) -> Optional[List[str]]:
    """对字典执行 JSON Schema 校验。严格模式：禁止额外字段。"""
    errors: List[str] = []

    if not isinstance(data, dict):
        return ["顶层必须是 JSON 对象"]

    required = ["intent", "confidence", "original"]
    for r in required:
        if r not in data:
            errors.append(f"缺少必需字段: {r!r}")
    if errors:
        return errors

    # intent
    if not _validate_type(data["intent"], "string"):
        errors.append("intent 必须为字符串")
    elif data["intent"] not in IntentEnum.all_values():
        errors.append(
            f"未知意图: {data['intent']!r}，允许: {IntentEnum.all_values()}"
        )

    # confidence
    if not _validate_type(data["confidence"], "number"):
        errors.append("confidence 必须为数值")
    elif not (0.0 <= data["confidence"] <= 1.0):
        errors.append(f"confidence 超出 [0.0, 1.0]: {data['confidence']}")

    # original
    if not _validate_type(data["original"], "string"):
        errors.append("original 必须为字符串")
    elif len(data["original"]) == 0:
        errors.append("original 不能为空字符串")

    # question / reason (if present)
    if "question" in data:
        if not _validate_type(data["question"], "string"):
            errors.append("question 必须为字符串")
        elif len(data["question"]) == 0:
            errors.append("question 不能为空字符串")
    if "reason" in data:
        if not _validate_type(data["reason"], "string"):
            errors.append("reason 必须为字符串")
        elif len(data["reason"]) == 0:
            errors.append("reason 不能为空字符串")

    # additionalProperties: false
    known_fields = {"intent", "confidence", "original", "question", "reason"}
    for k in data:
        if k not in known_fields:
            errors.append(f"不允许的额外字段: {k!r}")

    return errors if errors else None
# ──────────────────────────────────────────────
# 意图级契约校验
# ──────────────────────────────────────────────


def _validate_intent_contract(
    intent: str,
    confidence: float,
    original: str,
    raw: Dict[str, Any],
) -> Optional[List[str]]:
    """按意图执行额外契约约束。

    规则：
      - query_last / trigger_collect: 不得带除白名单外的任何字段
      - clarify: 必须带非空 question
      - unsupported: 必须带非空 reason
    """
    errors: List[str] = []

    allowed = INTENT_ALLOWED_FIELDS.get(intent, set())
    extra = [k for k in raw if k not in allowed]
    if extra:
        errors.append(
            f"意图 {intent!r} 不允许字段: {extra} "
            f"(允许: {sorted(allowed)})"
        )

    # clarify 必须提供 question
    if intent == IntentEnum.CLARIFY.value:
        question = raw.get("question")
        if not question:
            errors.append("clarify 意图必须提供非空的 question")
        elif not isinstance(question, str):
            errors.append("question 必须为字符串")

    # unsupported 必须提供 reason
    if intent == IntentEnum.UNSUPPORTED.value:
        reason = raw.get("reason")
        if not reason:
            errors.append("unsupported 意图必须提供非空的 reason")
        elif not isinstance(reason, str):
            errors.append("reason 必须为字符串")

    return errors if errors else None


# ──────────────────────────────────────────────
# 主校验入口
# ──────────────────────────────────────────────


def parse_and_validate(raw_input: str) -> ValidationResult:
    """解析并严格校验一条原始输入。

    Args:
        raw_input: 用户原始输入字符串（预期为 JSON 格式）

    Returns:
        ValidationResult — valid=True 表示通过，
        此时 contract 为 TaskContract 实例。
    """
    # Step 1: 解析 JSON
    try:
        data: Dict[str, Any] = json.loads(raw_input)
    except json.JSONDecodeError as e:
        return ValidationResult(
            valid=False,
            errors=[f"非法 JSON: {e}"],
        )

    # Step 2: Schema 校验
    schema_errors = _validate_schema(data)
    if schema_errors is not None:
        return ValidationResult(valid=False, errors=schema_errors)

    # Step 3: 意图级契约校验
    intent = str(data["intent"])
    confidence = float(data["confidence"])
    original = str(data["original"])

    contract_errors = _validate_intent_contract(
        intent, confidence, original, data
    )
    if contract_errors is not None:
        return ValidationResult(valid=False, errors=contract_errors)

    # Step 4: 构建 TaskContract
    try:
        contract = TaskContract(
            intent=intent,
            confidence=confidence,
            original=original,
            question=data.get("question"),
            reason=data.get("reason"),
        )
    except (ValueError, TypeError) as e:
        return ValidationResult(
            valid=False,
            errors=[f"契约构建失败: {e}"],
        )

    return ValidationResult(valid=True, contract=contract)