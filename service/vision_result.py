"""
vision_result.py — Week 08 W8-1: 结构化视觉结果契约与拒识语义

职责：
  把模型侧返回的「标签 + 分数」解析为一份可追踪、可审计的结构化结果，
  并明确区分三种决策：`ok` / `low_score` / `unknown`。

范围（仅 W8-1）：
  - 只定义契约与解析/拒识语义；不接入模型推理、不提供 HTTP API、
    不做持久化、不生成用户界面或反馈文案（留给后续任务）。
  - **不发明标签集合**：`label` 词汇表直接复用 Week 07
    `service/image_quality.py` 的 `IMAGE_STATUSES`（画面可用性/遮挡判断）。
  - **不发明分数阈值**：`score_threshold` 必须由调用方显式提供；W8-1
    未做任何校准，因此模块不设默认阈值。

字段语义：
  - `request_id`：输入（本次采集请求）与结果之间的关联键。
  - `label`：模型判断的原始标签；不受支持时也保留原始字符串作为证据，
    非字符串时为 None。
  - `score`：原始 0~1 相对可用性分数，**不是准确率、不是置信度**，
    不得解释为现实正确率；合法时原值保留，不做四舍五入或百分比换算。
  - `model_version`：产生该结果的算法版本标识，默认 `iq_v1`
    （Week 07 相对可用性算法，非训练模型）。
  - `decision`：该结果能否被采纳。
      `ok`        结构合法、标签受支持、分数达到门槛。
                  **不表示画面可用**——画面语义由 `label` 表达。
      `low_score` 分数低于调用方提供的门槛，应提示重新采集。
      `unknown`   无结果 / 解析失败 / 标签不受支持 / 分数非法 / 图像无效。
  - `reject_reason`：`decision != ok` 时的机器可读原因，否则为 None。
  - `created_at`：PC 端契约创建时间（UTC，ISO-8601 毫秒精度），
    `time_quality="pc_utc"`，不冒充设备时钟（设备时间见 D002）。

异常边界：
  - 模型侧的问题（空结果、坏 JSON、缺字段、非法分数、未知标签）一律
    映射为 `decision="unknown"` 并给出 `reject_reason`，不抛异常。
  - 调用方的编程错误（缺失 `request_id`、非法阈值、空 `model_version`）
    抛 `ValueError`，因为这类错误不应被伪装成一次模型结果。
"""

from __future__ import annotations

import enum
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

from .image_quality import IMAGE_STATUSES

# ──────────────────────────────────────────────
# 常量与枚举
# ──────────────────────────────────────────────

DEFAULT_MODEL_VERSION = "iq_v1"
"""Week 07 `image_quality` 相对可用性算法的版本标识；非训练模型。"""

SCORE_KIND = "relative_usability"
"""`score` 的语义标签，防止展示层把它写成准确率或置信度。"""

VISION_LABELS = frozenset(IMAGE_STATUSES)
"""受支持的标签集合（复用 Week 07 画面状态词表）。"""

IMAGE_INVALID_LABEL = "invalid"
"""Week 07 中表示「未能解码/无有效画面」的状态；不构成一次有效预测。"""


class DecisionEnum(str, enum.Enum):
    """视觉结果决策枚举（与课程 W8-1 要求一致）。"""

    OK = "ok"
    LOW_SCORE = "low_score"
    UNKNOWN = "unknown"

    @classmethod
    def all_values(cls) -> list[str]:
        return [member.value for member in cls]


class RejectReason(str, enum.Enum):
    """`decision != ok` 时的机器可读拒识原因。"""

    NO_RESULT = "no_result"
    PARSE_ERROR = "parse_error"
    MISSING_LABEL = "missing_label"
    UNSUPPORTED_LABEL = "unsupported_label"
    MISSING_SCORE = "missing_score"
    INVALID_SCORE = "invalid_score"
    IMAGE_INVALID = "image_invalid"
    SCORE_BELOW_THRESHOLD = "score_below_threshold"

    @classmethod
    def all_values(cls) -> list[str]:
        return [member.value for member in cls]


# ──────────────────────────────────────────────
# 内部校验
# ──────────────────────────────────────────────


def _require_request_id(request_id: Any) -> str:
    """校验关联键；缺失或空白属于调用方错误。"""
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id 必须是非空字符串")
    return request_id


def _require_model_version(model_version: Any) -> str:
    """校验模型版本标识；缺失会让结果无法归因。"""
    if not isinstance(model_version, str) or not model_version.strip():
        raise ValueError("model_version 必须是非空字符串")
    return model_version


def _require_threshold(score_threshold: Any) -> float:
    """校验调用方提供的分数门槛。

    W8-1 不设默认值、不自行校准；阈值必须落在 (0, 1] 内，
    避免 0 门槛静默关闭低分拒识。
    """
    if isinstance(score_threshold, bool) or not isinstance(score_threshold, (int, float)):
        raise ValueError("score_threshold 必须是 0~1 之间的数值")
    threshold = float(score_threshold)
    if not math.isfinite(threshold) or not (0.0 < threshold <= 1.0):
        raise ValueError("score_threshold 必须落在 (0, 1] 内")
    return threshold


def _decode_payload(raw_output: Any) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
    """把模型原始输出解码为映射对象。

    Returns:
        (payload, reject_reason)。成功时 reject_reason 为 None。
    """
    if raw_output is None:
        return None, RejectReason.NO_RESULT.value

    if isinstance(raw_output, (bytes, bytearray)):
        try:
            raw_output = bytes(raw_output).decode("utf-8")
        except UnicodeDecodeError:
            return None, RejectReason.PARSE_ERROR.value

    if isinstance(raw_output, str):
        try:
            raw_output = json.loads(raw_output)
        except (ValueError, TypeError):
            return None, RejectReason.PARSE_ERROR.value

    if not isinstance(raw_output, Mapping):
        return None, RejectReason.PARSE_ERROR.value

    return raw_output, None


def _normalise_score(value: Any) -> tuple[Optional[float], bool]:
    """校验原始分数。

    只接受有限数值且位于 [0, 1]。`bool` 是 `int` 的子类，必须显式排除。
    合法时原值返回（保留原始精度/类型）；非法时返回 (None, False)，
    因为 NaN/Inf 或非数值无法安全写入 JSON 结果。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, False

    score = float(value)
    if not math.isfinite(score) or not (0.0 <= score <= 1.0):
        return None, False

    return value, True


def _format_created_at(created_at: Optional[datetime]) -> str:
    """格式化为 UTC ISO-8601（毫秒精度，Z 后缀）。"""
    moment = created_at if created_at is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────


def build_vision_result(
    *,
    request_id: str,
    raw_output: Any,
    score_threshold: float,
    model_version: str = DEFAULT_MODEL_VERSION,
    created_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """把模型原始输出解析为结构化视觉结果契约。

    Args:
        request_id: 本次输入（图像采集请求）的关联键，必填。
        raw_output: 模型原始输出；可以是映射、JSON 字符串/字节，或 None。
        score_threshold: 低分门槛，调用方显式提供，范围 (0, 1]。
        model_version: 产生结果的算法版本，默认 `iq_v1`。
        created_at: 契约创建时间；默认当前 UTC 时间（测试可注入）。

    Returns:
        dict，始终包含 `request_id` / `label` / `score` / `model_version` /
        `decision` / `reject_reason` / `created_at`，并附带
        `time_quality` / `score_kind` / `score_threshold` 作为解释性元数据。

    Raises:
        ValueError: `request_id`、`model_version` 或 `score_threshold` 非法。
    """
    request_id = _require_request_id(request_id)
    model_version = _require_model_version(model_version)
    threshold = _require_threshold(score_threshold)

    payload, reject_reason = _decode_payload(raw_output)

    label: Optional[str] = None
    score: Optional[float] = None
    decision = DecisionEnum.UNKNOWN.value

    if payload is not None:
        raw_label = payload.get("label")
        if isinstance(raw_label, str):
            # 保留原始字符串（即使不受支持）作为可追溯证据
            label = raw_label

        has_score = "score" in payload
        score_valid = False
        if has_score:
            # 即使标签异常也先校验并保留合法分数，避免丢失原始证据
            score, score_valid = _normalise_score(payload.get("score"))

        if raw_label is None:
            reject_reason = RejectReason.MISSING_LABEL.value
        elif not isinstance(raw_label, str) or raw_label not in VISION_LABELS:
            reject_reason = RejectReason.UNSUPPORTED_LABEL.value
        elif not has_score:
            reject_reason = RejectReason.MISSING_SCORE.value
        elif not score_valid:
            reject_reason = RejectReason.INVALID_SCORE.value
        elif label == IMAGE_INVALID_LABEL:
            # 未能解码的图像不构成有效预测，不能当作低分结果
            reject_reason = RejectReason.IMAGE_INVALID.value
        elif score < threshold:
            decision = DecisionEnum.LOW_SCORE.value
            reject_reason = RejectReason.SCORE_BELOW_THRESHOLD.value
        else:
            decision = DecisionEnum.OK.value
            reject_reason = None

    return {
        "request_id": request_id,
        "label": label,
        "score": score,
        "model_version": model_version,
        "decision": decision,
        "reject_reason": reject_reason,
        "created_at": _format_created_at(created_at),
        "time_quality": "pc_utc",
        "score_kind": SCORE_KIND,
        "score_threshold": threshold,
    }


__all__ = [
    "build_vision_result",
    "DecisionEnum",
    "RejectReason",
    "VISION_LABELS",
    "DEFAULT_MODEL_VERSION",
    "SCORE_KIND",
    "IMAGE_INVALID_LABEL",
]
