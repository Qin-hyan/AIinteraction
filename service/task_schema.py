"""
task_schema.py — Week 04 结构化任务契约定义

本模块使用纯 Python 标准库定义：
  - 允许的四种任务意图 (IntentEnum)
  - 任务契约数据类 (TaskContract)
  - 每类意图允许/必需的字段映射
  - JSON Schema 定义（用于结构性校验）

不接入语言模型，不调用设备接口。
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Dict, List, Optional


# ──────────────────────────────────────────────
# 1. 意图枚举
# ──────────────────────────────────────────────

class IntentEnum(str, enum.Enum):
    """课程目前允许的四种任务意图。

    - query_last:     查询最后一次观测数据
    - trigger_collect: 请求触发一次采集
    - clarify:        用户输入存在歧义，需澄清
    - unsupported:    不支持的意图
    """

    QUERY_LAST = "query_last"
    TRIGGER_COLLECT = "trigger_collect"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"

    @classmethod
    def all_values(cls) -> List[str]:
        return [m.value for m in cls]


# ──────────────────────────────────────────────
# 2. 每类意图允许的字段白名单
# ──────────────────────────────────────────────

# 每个意图允许的顶层 JSON 字段名
# 超出这些字段即视为"额外字段"→拒绝
INTENT_ALLOWED_FIELDS: Dict[str, set] = {
    IntentEnum.QUERY_LAST.value:      {"intent", "confidence", "original"},
    IntentEnum.TRIGGER_COLLECT.value: {"intent", "confidence", "original"},
    IntentEnum.CLARIFY.value:         {"intent", "confidence", "original", "question"},
    IntentEnum.UNSUPPORTED.value:     {"intent", "confidence", "original", "reason"},
}


# ──────────────────────────────────────────────
# 3. JSON Schema 定义
# ──────────────────────────────────────────────

# JSON Schema 草案-07 风格
TASK_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Week04 Task Contract",
    "description": "自然语言任务契约 — 结构化任务校验",
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": list(IntentEnum.all_values()),
            "description": "任务意图",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "模型置信度",
        },
        "original": {
            "type": "string",
            "minLength": 1,
            "description": "用户的原始输入文本",
        },
        "question": {
            "type": "string",
            "minLength": 1,
            "description": "澄清问题（仅 clarify 使用）",
        },
        "reason": {
            "type": "string",
            "minLength": 1,
            "description": "拒绝/无法处理原因（仅 unsupported 使用）",
        },
    },
    "required": ["intent", "confidence", "original"],
    "additionalProperties": False,
}


# ──────────────────────────────────────────────
# 4. 任务契约数据类
# ──────────────────────────────────────────────

@dataclass
class TaskContract:
    """通过校验后的任务契约——表示一个合法有效的任务。

    Fields:
        intent:     任务意图字符串
        confidence: 置信度 (0.0 ~ 1.0)
        original:   用户的原始输入文本
        question:   clarify 意图的澄清问题（可选）
        reason:     unsupported 意图的理由（可选）
    """

    intent: str
    confidence: float
    original: str
    question: Optional[str] = None
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        """类型与边界校验（防御性）。"""
        # 意图必须是已知值
        if self.intent not in IntentEnum.all_values():
            raise ValueError(
                f"未知意图: {self.intent!r}，允许: {IntentEnum.all_values()}"
            )
        # confidence 范围
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence 超出 [0.0, 1.0]: {self.confidence}"
            )
        # original 非空
        if not isinstance(self.original, str) or not self.original.strip():
            raise ValueError("original 必须是非空字符串")
        # clarify 必须有 question
        if self.intent == IntentEnum.CLARIFY.value:
            if not self.question:
                raise ValueError("clarify 意图必须提供非空的 question")
        # unsupported 必须有 reason
        if self.intent == IntentEnum.UNSUPPORTED.value:
            if not self.reason:
                raise ValueError("unsupported 意图必须提供非空的 reason")

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典，排除值为 None 的键。"""
        result: Dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is not None:
                result[f.name] = v
        return result

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskContract":
        """从字典构建（跳过 None 字段）。"""
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
        return cls(**kwargs)