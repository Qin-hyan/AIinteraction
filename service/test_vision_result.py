"""
test_vision_result.py — Week 08 W8-1: 结构化视觉结果契约与拒识语义测试

覆盖：
  1. 契约字段完整性与类型（request_id / label / score / model_version /
     decision / reject_reason / created_at）
  2. 正常结果、阈值边界、低分拒绝
  3. 无结果、解析失败、缺失字段
  4. 非法分数（越界 / 非数值 / bool / NaN / Inf）
  5. 不受支持标签、非字符串标签、无效图像
  6. 标签词汇表复用 `image_quality.IMAGE_STATUSES`，未另造标签
  7. `score` 语义不得表述为准确率/置信度
  8. 调用方错误（request_id / 阈值 / model_version）
  9. `created_at` 为 UTC ISO-8601

说明：测试中的 `0.5` 仅用于演示契约行为，**不代表任何校准结果或生产阈值**。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from .image_quality import IMAGE_STATUSES
from .vision_result import (
    DEFAULT_MODEL_VERSION,
    DecisionEnum,
    IMAGE_INVALID_LABEL,
    RejectReason,
    SCORE_KIND,
    THRESHOLD_STATUS_CONFIGURED,
    THRESHOLD_STATUS_NOT_CONFIGURED,
    VISION_LABELS,
    build_score_vision_result,
    build_vision_result,
)

REQUEST_ID = "req-123456-0001"
THRESHOLD = 0.5  # 测试示例阈值，非校准结果


def _build(raw_output: Any, **overrides: Any) -> dict:
    """以固定 request_id 与示例阈值构造结果契约。"""
    params: dict[str, Any] = {
        "request_id": REQUEST_ID,
        "raw_output": raw_output,
        "score_threshold": THRESHOLD,
    }
    params.update(overrides)
    return build_vision_result(**params)


# ──────────────────────────────────────────────
# 1. 契约字段
# ──────────────────────────────────────────────


def test_contract_contains_required_fields() -> None:
    """正常结果必须包含 W8-1 要求的全部字段。"""
    result = _build({"label": "ok", "score": 0.87})

    required = {
        "request_id",
        "label",
        "score",
        "model_version",
        "decision",
        "reject_reason",
        "created_at",
    }
    assert required <= set(result)

    assert result["request_id"] == REQUEST_ID
    assert result["label"] == "ok"
    assert result["score"] == 0.87
    assert result["model_version"] == DEFAULT_MODEL_VERSION
    assert result["decision"] == DecisionEnum.OK.value
    assert result["reject_reason"] is None
    assert isinstance(result["created_at"], str)


def test_decision_enum_has_exactly_required_values() -> None:
    """decision 取值固定；needs_review 表示分数合法但阈值未配置。"""
    assert DecisionEnum.all_values() == [
        "ok",
        "low_score",
        "needs_review",
        "unknown",
    ]


def test_model_version_and_threshold_are_recorded() -> None:
    """结果记录算法版本与实际使用的门槛，便于追溯判定依据。"""
    result = _build(
        {"label": "ok", "score": 0.9},
        model_version="iq_v2-test",
        score_threshold=0.4,
    )

    assert result["model_version"] == "iq_v2-test"
    assert result["score_threshold"] == 0.4


# ──────────────────────────────────────────────
# 2. 正常 / 低分
# ──────────────────────────────────────────────


def test_normal_result_is_ok() -> None:
    """分数达到门槛的正常结果被采纳。"""
    result = _build({"label": "ok", "score": 0.87})

    assert result["decision"] == DecisionEnum.OK.value
    assert result["reject_reason"] is None


def test_score_equal_to_threshold_is_ok() -> None:
    """分数恰好等于门槛时视为达到门槛（闭区间）。"""
    result = _build({"label": "ok", "score": THRESHOLD})

    assert result["decision"] == DecisionEnum.OK.value
    assert result["reject_reason"] is None


def test_low_score_is_rejected() -> None:
    """低于门槛的结果标记 low_score，并保留原始分数。"""
    result = _build({"label": "ok", "score": 0.2})

    assert result["decision"] == DecisionEnum.LOW_SCORE.value
    assert result["reject_reason"] == "score_below_threshold"
    assert result["score"] == 0.2


def test_unsupported_label_high_score_still_not_low_score() -> None:
    """未知标签属于 unknown，不应被误报为 low_score。"""
    result = _build({"label": "person", "score": 0.1})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "unsupported_label"


def test_invalid_image_is_unknown_not_low_score() -> None:
    """无法解码的图像（label=invalid）不构成有效预测，不能降级为低分。"""
    result = _build({"label": IMAGE_INVALID_LABEL, "score": 0.0})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "image_invalid"
    assert result["label"] == IMAGE_INVALID_LABEL


def test_accepted_result_does_not_claim_image_usable() -> None:
    """decision=ok 只表示结果可采纳，不表示画面可用。"""
    result = _build({"label": "too_dark", "score": 0.9})

    assert result["decision"] == DecisionEnum.OK.value
    assert result["label"] == "too_dark"
    # 画面可用性由 label 表达，契约不额外伪造 usable 字段
    assert "usable" not in result


def test_json_text_output_is_parsed() -> None:
    """JSON 文本输出可被解析，并保留原始分数。"""
    result = _build('{"label": "too_dark", "score": 0.02}')

    assert result["label"] == "too_dark"
    assert result["score"] == 0.02
    assert result["decision"] == DecisionEnum.LOW_SCORE.value


# ──────────────────────────────────────────────
# 3. 无结果 / 解析失败 / 缺字段
# ──────────────────────────────────────────────


def test_no_result_is_unknown() -> None:
    """完全无输出时标记 unknown/no_result，不伪造标签或分数。"""
    result = _build(None)

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "no_result"
    assert result["label"] is None
    assert result["score"] is None


@pytest.mark.parametrize(
    "raw_output",
    [
        "not-json",
        b"\xff\xfe\x00\x01",
        "[1, 2, 3]",
        42,
        ["ok", 0.9],
    ],
    ids=["bad-json", "bad-utf8", "json-array", "json-number", "python-list"],
)
def test_parse_failures_are_unknown(raw_output: Any) -> None:
    """无法解析为对象时标记 unknown/parse_error。"""
    result = _build(raw_output)

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "parse_error"
    assert result["label"] is None
    assert result["score"] is None


def test_missing_label_is_unknown_but_keeps_score() -> None:
    """缺少标签时标记 missing_label，同时保留合法分数作为证据。"""
    result = _build({"score": 0.8})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "missing_label"
    assert result["label"] is None
    assert result["score"] == 0.8


def test_missing_score_is_unknown() -> None:
    """缺少分数时标记 missing_score，不猜测分数。"""
    result = _build({"label": "ok"})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "missing_score"
    assert result["score"] is None


# ──────────────────────────────────────────────
# 4. 非法分数
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "score",
    [1.5, -0.1, "0.9", True, False, None, float("nan"), float("inf")],
    ids=["above-1", "below-0", "string", "true", "false", "null", "nan", "inf"],
)
def test_invalid_scores_are_unknown(score: Any) -> None:
    """越界、非数值、bool、NaN/Inf 一律 unknown/invalid_score。"""
    result = _build({"label": "ok", "score": score})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "invalid_score"
    # 不可安全表示的值不保留，避免污染 JSON 结果
    assert result["score"] is None


# ──────────────────────────────────────────────
# 5. 标签
# ──────────────────────────────────────────────


def test_unsupported_label_keeps_original_text() -> None:
    """不受支持的标签保留原始字符串，便于定位模型输出问题。"""
    result = _build({"label": "cat", "score": 0.99})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "unsupported_label"
    assert result["label"] == "cat"
    assert result["score"] == 0.99


def test_non_string_label_is_unsupported() -> None:
    """非字符串标签不受支持，label 字段置 None。"""
    result = _build({"label": 123, "score": 0.99})

    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == "unsupported_label"
    assert result["label"] is None


def test_label_vocabulary_reuses_image_quality_statuses() -> None:
    """标签词汇表必须复用 Week 07 状态集合，不得另造标签。"""
    assert VISION_LABELS == frozenset(IMAGE_STATUSES)
    assert IMAGE_INVALID_LABEL in VISION_LABELS


# ──────────────────────────────────────────────
# 6. 分数语义
# ──────────────────────────────────────────────


def test_score_is_not_reported_as_accuracy() -> None:
    """分数不得以准确率/置信度等名义暴露。"""
    result = _build({"label": "ok", "score": 0.9})

    for forbidden in ("accuracy", "confidence", "probability", "correct_rate"):
        assert forbidden not in result
    assert result["score_kind"] == SCORE_KIND


def test_raw_score_is_not_rounded_or_rescaled() -> None:
    """合法分数原样保留，不四舍五入、不换算为百分比。"""
    result = _build({"label": "ok", "score": 0.87654321})

    assert result["score"] == 0.87654321


def test_results_are_json_serialisable_including_invalid_scores() -> None:
    """含 NaN/Inf 或未知标签的结果也必须能安全序列化为 JSON。"""
    cases: list[Any] = [
        {"label": "ok", "score": 0.9},
        {"label": "ok", "score": 0.1},
        {"label": "ok", "score": float("nan")},
        {"label": "cat", "score": 0.9},
        None,
    ]
    for case in cases:
        serialised = json.dumps(_build(case), ensure_ascii=False, allow_nan=False)
        assert isinstance(serialised, str)


# ──────────────────────────────────────────────
# 7. 调用方错误
# ──────────────────────────────────────────────


@pytest.mark.parametrize("request_id", ["", "   ", None, 123])
def test_invalid_request_id_raises(request_id: Any) -> None:
    """缺少关联键属于调用方错误，不能被伪装成模型结果。"""
    with pytest.raises(ValueError):
        build_vision_result(
            request_id=request_id,
            raw_output={"label": "ok", "score": 0.9},
            score_threshold=THRESHOLD,
        )


@pytest.mark.parametrize(
    "threshold",
    [0, 0.0, 1.5, -0.1, float("nan"), float("inf"), True, None, "0.5"],
    ids=["zero-int", "zero-float", "above-1", "negative", "nan", "inf", "bool", "null", "string"],
)
def test_invalid_threshold_raises(threshold: Any) -> None:
    """阈值必须显式提供且位于 (0, 1]，避免静默关闭低分拒识。"""
    with pytest.raises(ValueError):
        build_vision_result(
            request_id=REQUEST_ID,
            raw_output={"label": "ok", "score": 0.9},
            score_threshold=threshold,
        )


def test_missing_threshold_raises() -> None:
    """score_threshold 无默认值，调用方必须显式声明。"""
    with pytest.raises(TypeError):
        build_vision_result(  # type: ignore[call-arg]
            request_id=REQUEST_ID,
            raw_output={"label": "ok", "score": 0.9},
        )


@pytest.mark.parametrize("model_version", ["", "   ", None])
def test_invalid_model_version_raises(model_version: Any) -> None:
    """版本标识缺失会导致结果无法归因。"""
    with pytest.raises(ValueError):
        build_vision_result(
            request_id=REQUEST_ID,
            raw_output={"label": "ok", "score": 0.9},
            score_threshold=THRESHOLD,
            model_version=model_version,
        )


# ──────────────────────────────────────────────
# 8. created_at
# ──────────────────────────────────────────────


def test_created_at_is_utc_iso8601() -> None:
    """注入时间后输出稳定的 UTC ISO-8601（毫秒精度）。"""
    moment = datetime(2026, 10, 19, 8, 30, 0, 123000, tzinfo=timezone.utc)
    result = _build({"label": "ok", "score": 0.9}, created_at=moment)

    assert result["created_at"] == "2026-10-19T08:30:00.123Z"
    assert result["time_quality"] == "pc_utc"


def test_naive_created_at_is_treated_as_utc() -> None:
    """无时区时间按 UTC 解释，不静默套用本地时区。"""
    result = _build({"label": "ok", "score": 0.9}, created_at=datetime(2026, 10, 19, 8, 30))

    assert result["created_at"] == "2026-10-19T08:30:00.000Z"


def test_default_created_at_is_recent_utc() -> None:
    """默认时间戳为当前 UTC 时间，可被解析且带时区。"""
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    result = _build({"label": "ok", "score": 0.9})
    parsed = datetime.fromisoformat(result["created_at"].replace("Z", "+00:00"))
    after = datetime.now(timezone.utc) + timedelta(seconds=1)

    assert parsed.tzinfo is not None
    assert before <= parsed <= after


# ──────────────────────────────────────────────
# 9. 仅分数模型（DBCNN）：来源分离与阈值未配置
# ──────────────────────────────────────────────


def _build_score(**overrides: Any) -> dict:
    """构造仅分数结果；默认不配置阈值（W8-2 生产默认状态）。"""
    params: dict[str, Any] = {
        "request_id": REQUEST_ID,
        "score": 0.7561315298080444,
        "model_version": "dbcnn_onnx_v1",
        "score_threshold": None,
        "score_source": "dbcnn_onnx",
    }
    params.update(overrides)
    return build_score_vision_result(**params)


def test_score_only_without_threshold_needs_review() -> None:
    """未配置阈值时不得自造门槛，也不得标记 ok。"""
    result = _build_score()

    assert result["score"] == 0.7561315298080444
    assert result["model_version"] == "dbcnn_onnx_v1"
    assert result["score_source"] == "dbcnn_onnx"
    assert result["score_kind"] == SCORE_KIND
    assert result["decision"] == DecisionEnum.NEEDS_REVIEW.value
    assert result["reject_reason"] == RejectReason.THRESHOLD_NOT_CONFIGURED.value
    assert result["threshold_status"] == THRESHOLD_STATUS_NOT_CONFIGURED
    assert result["score_threshold"] is None
    assert result["review_required"] is True


def test_score_only_never_invents_a_label() -> None:
    """DBCNN 不产生类别标签；规则标签不得写入该字段。"""
    result = _build_score()

    assert result["label"] is None
    assert result["label_source"] is None


def test_score_only_threshold_configured_is_ok() -> None:
    """调用方显式提供阈值且分数达标时，才允许 decision=ok。"""
    result = _build_score(score=0.7561, score_threshold=0.5)

    assert result["decision"] == DecisionEnum.OK.value
    assert result["reject_reason"] is None
    assert result["threshold_status"] == THRESHOLD_STATUS_CONFIGURED
    assert result["score_threshold"] == 0.5
    assert result["review_required"] is False


def test_score_only_below_threshold_is_low_score() -> None:
    """低于显式阈值时保留原始分数并标记 low_score。"""
    result = _build_score(score=0.2581, score_threshold=0.5)

    assert result["score"] == 0.2581
    assert result["decision"] == DecisionEnum.LOW_SCORE.value
    assert result["reject_reason"] == RejectReason.SCORE_BELOW_THRESHOLD.value
    assert result["review_required"] is True


def test_score_only_equal_to_threshold_is_ok() -> None:
    result = _build_score(score=0.5, score_threshold=0.5)
    assert result["decision"] == DecisionEnum.OK.value


def test_score_is_not_rescaled_or_rounded() -> None:
    """保持模型原始 0~1 数值；不乘 100、不四舍五入。"""
    result = _build_score(score=0.7561315298080444, score_threshold=0.5)

    assert result["score"] == 0.7561315298080444
    assert result["score"] < 1.0


@pytest.mark.parametrize(
    "score",
    [float("nan"), float("inf"), -0.1, 1.1, True, "0.5", None],
    ids=["nan", "inf", "negative", "above-1", "bool", "string", "null"],
)
def test_score_only_invalid_score_is_unknown(score: Any) -> None:
    result = _build_score(score=score)

    assert result["score"] is None
    assert result["decision"] == DecisionEnum.UNKNOWN.value
    assert result["reject_reason"] == RejectReason.INVALID_SCORE.value
    assert result["label"] is None


@pytest.mark.parametrize(
    "threshold",
    [0, 0.0, 1.5, -0.1, float("nan"), float("inf"), True, "0.5"],
    ids=["zero-int", "zero-float", "above-1", "negative", "nan", "inf", "bool", "string"],
)
def test_score_only_invalid_threshold_raises(threshold: Any) -> None:
    with pytest.raises(ValueError):
        _build_score(score_threshold=threshold)


@pytest.mark.parametrize("source", ["", "   ", None])
def test_score_only_invalid_source_raises(source: Any) -> None:
    with pytest.raises(ValueError):
        _build_score(score_source=source)


def test_score_only_created_at_and_json_serialisable() -> None:
    moment = datetime(2026, 10, 19, 8, 30, 0, 123000, tzinfo=timezone.utc)
    result = _build_score(created_at=moment)

    assert result["created_at"] == "2026-10-19T08:30:00.123Z"
    assert result["time_quality"] == "pc_utc"
    json.dumps(result, ensure_ascii=False, allow_nan=False)
