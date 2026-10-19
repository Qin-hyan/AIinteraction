"""
test_vision_analysis.py — Week 08 W8-2: 本地视觉分析组合入口测试

覆盖：
  1. 未配置阈值 → needs_review / threshold_not_configured；
  2. 显式阈值 / 环境变量阈值 / 显式参数优先级；
  3. DBCNN 分数与 Week 07 规则诊断的来源分离；
  4. 规则依赖缺失时明确标记「未执行」，不冒充 invalid；
  5. 模型推理失败直接抛错，不退回规则判断；
  6. 结果可 JSON 序列化。

所有评分器/规则评估函数均为注入替身；真实模型冒烟见
`test_vision_dbcnn.py` 的可选集成测试。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from .vision_analysis import (
    ENV_SCORE_THRESHOLD,
    RULE_SOURCE,
    analyze_jpeg,
    get_configured_threshold,
)
from .vision_dbcnn import DbcnnInferenceError, DbcnnScore

REQUEST_ID = "req-w8-analysis-0001"
JPEG = b"fake-jpeg-bytes"


class _FakeScorer:
    def __init__(self, score: float = 0.7561, error: Exception | None = None) -> None:
        self.score = score
        self.error = error
        self.calls: list[bytes] = []

    def score_jpeg(self, payload: bytes) -> DbcnnScore:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return DbcnnScore(
            score=self.score,
            model_version="dbcnn_onnx_v1",
            model_sha256="a" * 64,
            model_data_sha256="b" * 64,
            inference_ms=12.5,
            score_source="dbcnn_onnx",
        )


def _fake_assessor(payload: bytes) -> dict[str, Any]:
    return {
        "usable": True,
        "status": "ok",
        "score": 0.9,
        "metrics": {"mean_luma": 100.0, "contrast": 50.0, "detail": 12.0},
        "bytes_len": len(payload),
    }


# ──────────────────────────────────────────────
# 1. 阈值配置
# ──────────────────────────────────────────────


def test_threshold_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)
    assert get_configured_threshold() is None


def test_threshold_is_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SCORE_THRESHOLD, " 0.42 ")
    assert get_configured_threshold() == pytest.approx(0.42)


@pytest.mark.parametrize("raw", ["abc", "1.5", "0", "-0.1", "nan", "inf"])
def test_invalid_env_threshold_raises(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SCORE_THRESHOLD, raw)
    with pytest.raises(ValueError, match=ENV_SCORE_THRESHOLD):
        get_configured_threshold()


# ──────────────────────────────────────────────
# 2. 无阈值 / 有阈值的判定
# ──────────────────────────────────────────────


def test_without_threshold_needs_review_and_sources_are_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)
    scorer = _FakeScorer(score=0.7561)

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=scorer,
        rule_assessor=_fake_assessor,
    )

    assert result["request_id"] == REQUEST_ID
    assert result["score"] == pytest.approx(0.7561)
    assert result["score_source"] == "dbcnn_onnx"
    assert result["score_kind"] == "relative_usability"
    assert result["model_version"] == "dbcnn_onnx_v1"
    assert result["decision"] == "needs_review"
    assert result["reject_reason"] == "threshold_not_configured"
    assert result["threshold_status"] == "not_configured"
    assert result["score_threshold"] is None
    assert result["review_required"] is True
    # DBCNN 未产生类别标签；规则标签只出现在 rule_diagnostics
    assert result["label"] is None
    assert result["label_source"] is None
    assert result["rule_diagnostics"]["source"] == RULE_SOURCE
    assert result["rule_diagnostics"]["kind"] == "rule_based_diagnostics"
    assert result["rule_diagnostics"]["label"] == "ok"
    assert result["rule_diagnostics"]["available"] is True
    assert scorer.calls == [JPEG]


def test_explicit_threshold_above_score_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(score=0.7561),
        score_threshold=0.5,
        rule_assessor=_fake_assessor,
    )

    assert result["decision"] == "ok"
    assert result["reject_reason"] is None
    assert result["threshold_status"] == "configured"
    assert result["score_threshold"] == 0.5
    assert result["review_required"] is False


def test_explicit_threshold_below_score_is_low_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(score=0.2581),
        score_threshold=0.5,
        rule_assessor=_fake_assessor,
    )

    assert result["score"] == pytest.approx(0.2581)
    assert result["decision"] == "low_score"
    assert result["reject_reason"] == "score_below_threshold"
    assert result["review_required"] is True


def test_env_threshold_is_used_when_argument_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_SCORE_THRESHOLD, "0.5")

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(score=0.7561),
        rule_assessor=_fake_assessor,
    )

    assert result["decision"] == "ok"
    assert result["score_threshold"] == pytest.approx(0.5)


def test_explicit_threshold_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SCORE_THRESHOLD, "0.9")

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(score=0.7561),
        score_threshold=0.5,
        rule_assessor=_fake_assessor,
    )

    assert result["decision"] == "ok"
    assert result["score_threshold"] == 0.5


def test_invalid_env_threshold_fails_before_model_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_SCORE_THRESHOLD, "not-a-number")
    scorer = _FakeScorer()

    with pytest.raises(ValueError, match=ENV_SCORE_THRESHOLD):
        analyze_jpeg(
            request_id=REQUEST_ID,
            jpeg_bytes=JPEG,
            scorer=scorer,
            rule_assessor=_fake_assessor,
        )

    assert scorer.calls == []


# ──────────────────────────────────────────────
# 3. 规则诊断与失败边界
# ──────────────────────────────────────────────


def test_rule_diagnostics_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(),
        include_rule_diagnostics=False,
    )

    assert result["rule_diagnostics"] is None
    assert result["decision"] == "needs_review"


def test_unavailable_rule_dependencies_are_marked_not_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)
    monkeypatch.setattr(
        "service.vision_analysis.image_quality.is_available", lambda: False
    )

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(),
    )

    diagnostics = result["rule_diagnostics"]
    assert diagnostics["available"] is False
    assert diagnostics["label"] is None
    assert diagnostics["usable"] is None
    assert "未执行" in diagnostics["note"]
    # DBCNN 结果仍然有效，不能被规则依赖缺失影响
    assert result["score"] == pytest.approx(0.7561)
    assert result["decision"] == "needs_review"


def test_dbcnn_failure_does_not_fall_back_to_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)
    assessor_calls: list[bytes] = []

    def _assessor(payload: bytes) -> dict[str, Any]:
        assessor_calls.append(payload)
        return _fake_assessor(payload)

    with pytest.raises(DbcnnInferenceError, match="坏图"):
        analyze_jpeg(
            request_id=REQUEST_ID,
            jpeg_bytes=JPEG,
            scorer=_FakeScorer(error=DbcnnInferenceError("坏图")),
            rule_assessor=_assessor,
        )

    assert assessor_calls == []


def test_jpeg_bytes_type_is_validated() -> None:
    with pytest.raises(TypeError, match="bytes"):
        analyze_jpeg(
            request_id=REQUEST_ID,
            jpeg_bytes="not-bytes",  # type: ignore[arg-type]
            scorer=_FakeScorer(),
        )


def test_result_is_json_serialisable_and_records_input_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SCORE_THRESHOLD, raising=False)

    result = analyze_jpeg(
        request_id=REQUEST_ID,
        jpeg_bytes=JPEG,
        scorer=_FakeScorer(),
        rule_assessor=_fake_assessor,
    )

    assert result["input_bytes"] == len(JPEG)
    assert result["inference_ms"] == pytest.approx(12.5)
    assert result["model_sha256"] == "a" * 64
    assert result["model_data_sha256"] == "b" * 64
    json.dumps(result, ensure_ascii=False, allow_nan=False)
