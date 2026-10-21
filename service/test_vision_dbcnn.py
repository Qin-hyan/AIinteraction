"""
test_vision_dbcnn.py — Week 08 W8-2: DBCNN ONNX 适配器测试

覆盖：
  1. 配置错误：模型路径缺失/文件不存在/版本为空；
  2. 注入会话的评分路径、权重指纹与推理错误；
  3. 预处理形状/ImageNet 标准化/非法输入；
  4. 输出解析：单值、多值、非有限值；
  5. 可选真实集成冒烟（需显式开启，避免在无 ONNX Runtime 环境崩溃）。

真实集成测试运行方式（隔离 venv，本地权重）：
  $env:VISION_DBCNN_RUN_INTEGRATION = "1"
  $env:VISION_DBCNN_MODEL_PATH = "<...>\\dbcnn_model.onnx"
  <venv>\\python.exe -m pytest service/test_vision_dbcnn.py -q
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from .vision_dbcnn import (
    DEFAULT_MODEL_VERSION,
    ENV_MODEL_PATH,
    ENV_MODEL_VERSION,
    DbcnnConfigError,
    DbcnnInferenceError,
    DbcnnModelError,
    DbcnnScorer,
    extract_single_score,
    get_model_version,
    preprocess_bgr,
)


# ──────────────────────────────────────────────
# 测试替身
# ──────────────────────────────────────────────


class _FakeInput:
    name = "input"
    shape = ["batch", 3, 224, 224]
    type = "tensor(float)"


class _FakeSession:
    def __init__(self, outputs: Any = None, run_error: Exception | None = None) -> None:
        self.outputs = (
            outputs
            if outputs is not None
            else [np.array([[0.5]], dtype=np.float32)]
        )
        self.run_error = run_error
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def get_inputs(self) -> list[_FakeInput]:
        return [_FakeInput()]

    def get_outputs(self) -> list[object]:
        return [object()]

    def run(self, output_names: Any, feed: dict[str, Any]) -> Any:
        self.calls.append((output_names, feed))
        if self.run_error is not None:
            raise self.run_error
        return self.outputs


def _write_fake_model(tmp_path: Path, with_data: bool = True) -> Path:
    model_path = tmp_path / "dbcnn_model.onnx"
    model_path.write_bytes(b"fake-onnx-graph")
    if with_data:
        (tmp_path / "dbcnn_model.onnx.data").write_bytes(b"fake-onnx-weights")
    return model_path


def _fake_scorer(
    tmp_path: Path, outputs: Any = None
) -> tuple[DbcnnScorer, _FakeSession]:
    session = _FakeSession(outputs=outputs)
    scorer = DbcnnScorer(
        model_path=str(_write_fake_model(tmp_path)),
        model_version="test-v1",
        session=session,
        decoder=lambda raw: object(),
        preprocessor=lambda bgr: np.zeros((1, 3, 224, 224), dtype=np.float32),
    )
    return scorer, session


# ──────────────────────────────────────────────
# 1. 配置错误
# ──────────────────────────────────────────────


def test_missing_model_path_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_MODEL_PATH, raising=False)

    with pytest.raises(DbcnnConfigError, match=ENV_MODEL_PATH):
        DbcnnScorer(session=_FakeSession())


def test_missing_model_file_raises_model_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.onnx"
    monkeypatch.setenv(ENV_MODEL_PATH, str(missing))

    with pytest.raises(DbcnnModelError, match="不存在"):
        DbcnnScorer(session=_FakeSession())


def test_blank_model_version_raises_config_error(tmp_path: Path) -> None:
    model_path = _write_fake_model(tmp_path)

    with pytest.raises(DbcnnConfigError, match="版本"):
        DbcnnScorer(
            model_path=str(model_path),
            model_version="   ",
            session=_FakeSession(),
        )


def test_model_version_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_MODEL_VERSION, raising=False)
    assert get_model_version() == DEFAULT_MODEL_VERSION

    monkeypatch.setenv(ENV_MODEL_VERSION, "  snapshot-2026-10-19  ")
    assert get_model_version() == "snapshot-2026-10-19"


# ──────────────────────────────────────────────
# 2. 注入会话的评分路径
# ──────────────────────────────────────────────


def test_injected_session_returns_raw_score_and_hashes(tmp_path: Path) -> None:
    scorer, session = _fake_scorer(tmp_path)

    result = scorer.score_jpeg(b"jpeg-bytes")

    assert result.score == pytest.approx(0.5)
    assert result.model_version == "test-v1"
    assert result.score_source == "dbcnn_onnx"
    assert result.model_sha256 == hashlib.sha256(b"fake-onnx-graph").hexdigest()
    assert result.model_data_sha256 == hashlib.sha256(b"fake-onnx-weights").hexdigest()
    assert result.inference_ms is not None and result.inference_ms >= 0.0
    assert len(session.calls) == 1
    assert session.calls[0][1]["input"].shape == (1, 3, 224, 224)


def test_model_without_external_data_has_no_data_hash(tmp_path: Path) -> None:
    model_path = _write_fake_model(tmp_path, with_data=False)
    scorer = DbcnnScorer(
        model_path=str(model_path),
        model_version="test-v1",
        session=_FakeSession(),
        decoder=lambda raw: object(),
        preprocessor=lambda bgr: np.zeros((1, 3, 224, 224), dtype=np.float32),
    )

    assert scorer.model_data_sha256 is None


def test_multiple_output_values_raise_inference_error(tmp_path: Path) -> None:
    scorer, _ = _fake_scorer(
        tmp_path, outputs=[np.array([[0.1, 0.2]], dtype=np.float32)]
    )

    with pytest.raises(DbcnnInferenceError, match="单一分数"):
        scorer.score_jpeg(b"jpeg-bytes")


def test_non_finite_output_raises_inference_error(tmp_path: Path) -> None:
    scorer, _ = _fake_scorer(
        tmp_path, outputs=[np.array([[float("nan")]], dtype=np.float32)]
    )

    with pytest.raises(DbcnnInferenceError, match="有限"):
        scorer.score_jpeg(b"jpeg-bytes")


def test_session_run_error_is_wrapped(tmp_path: Path) -> None:
    scorer = DbcnnScorer(
        model_path=str(_write_fake_model(tmp_path)),
        model_version="test-v1",
        session=_FakeSession(run_error=RuntimeError("boom")),
        decoder=lambda raw: object(),
        preprocessor=lambda bgr: np.zeros((1, 3, 224, 224), dtype=np.float32),
    )

    with pytest.raises(DbcnnInferenceError, match="推理失败"):
        scorer.score_jpeg(b"jpeg-bytes")


def test_decoder_error_propagates_without_rule_fallback(tmp_path: Path) -> None:
    def _bad_decoder(raw: bytes) -> Any:
        raise DbcnnInferenceError("坏图")

    scorer = DbcnnScorer(
        model_path=str(_write_fake_model(tmp_path)),
        model_version="test-v1",
        session=_FakeSession(),
        decoder=_bad_decoder,
        preprocessor=lambda bgr: np.zeros((1, 3, 224, 224), dtype=np.float32),
    )

    with pytest.raises(DbcnnInferenceError, match="坏图"):
        scorer.score_jpeg(b"jpeg-bytes")


# ──────────────────────────────────────────────
# 3. 预处理与输出解析
# ──────────────────────────────────────────────


def test_preprocess_bgr_shape_and_imagenet_normalisation() -> None:
    pytest.importorskip("cv2")
    bgr = np.zeros((10, 20, 3), dtype=np.uint8)

    tensor = preprocess_bgr(bgr)

    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    # 黑色像素：RGB 均为 0，标准化后为 (0 - mean) / std
    assert tensor[0, 0, 0, 0] == pytest.approx((0.0 - 0.485) / 0.229, rel=1e-5)


def test_preprocess_rejects_non_three_channel() -> None:
    pytest.importorskip("cv2")
    gray = np.zeros((10, 20), dtype=np.uint8)

    with pytest.raises(DbcnnInferenceError, match="3 通道"):
        preprocess_bgr(gray)


def test_preprocess_rejects_non_uint8() -> None:
    pytest.importorskip("cv2")
    floats = np.zeros((10, 20, 3), dtype=np.float32)

    with pytest.raises(DbcnnInferenceError, match="uint8"):
        preprocess_bgr(floats)


def test_extract_single_score_accepts_single_value() -> None:
    assert extract_single_score([np.array([[0.7561]], dtype=np.float32)]) == pytest.approx(
        0.7561, rel=1e-6
    )


def test_extract_single_score_rejects_multiple_values() -> None:
    with pytest.raises(DbcnnInferenceError, match="单一分数"):
        extract_single_score([np.array([[0.1, 0.2]], dtype=np.float32)])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_extract_single_score_rejects_non_finite(value: float) -> None:
    with pytest.raises(DbcnnInferenceError, match="有限"):
        extract_single_score([np.array([[value]], dtype=np.float64)])


# ──────────────────────────────────────────────
# 4. 可选真实集成冒烟（本地权重 + 两张既有样例）
# ──────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = (
    REPO_ROOT / ".workbuddy" / "artifacts" / "dbcnn-iqa-test" / "weights" / "dbcnn_model.onnx"
)
DEFAULT_IMAGE_DIR = REPO_ROOT / ".workbuddy" / "week07_acceptance"
INTEGRATION_ENV = "VISION_DBCNN_RUN_INTEGRATION"


@pytest.mark.skipif(
    os.environ.get(INTEGRATION_ENV) != "1",
    reason=(
        "真实 ONNX 冒烟默认跳过；设置 VISION_DBCNN_RUN_INTEGRATION=1 "
        "并在安装 onnxruntime/opencv 的本地环境运行"
    ),
)
def test_real_dbcnn_smoke_on_two_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 集成冒烟固定验证「未配置阈值」路径；避免外部 shell 残留阈值影响断言。
    monkeypatch.delenv("VISION_DBCNN_SCORE_THRESHOLD", raising=False)
    # 无 ONNX Runtime/OpenCV 的环境跳过，而不是把依赖错误当成模型失败。
    pytest.importorskip("onnxruntime")
    pytest.importorskip("cv2")

    model_path = os.environ.get(ENV_MODEL_PATH) or str(DEFAULT_MODEL_PATH)
    if not os.path.isfile(model_path):
        pytest.skip(f"本地模型不存在: {model_path}")

    ok_path = DEFAULT_IMAGE_DIR / "ok.jpg"
    occluded_path = DEFAULT_IMAGE_DIR / "occluded.jpg"
    if not ok_path.is_file() or not occluded_path.is_file():
        pytest.skip("缺少本地样例图 .workbuddy/week07_acceptance/{ok,occluded}.jpg")

    from .vision_analysis import RULE_SOURCE, analyze_jpeg

    scorer = DbcnnScorer(model_path=model_path, model_version="dbcnn_onnx_v1")
    ok_score = scorer.score_jpeg(ok_path.read_bytes())
    occluded_score = scorer.score_jpeg(occluded_path.read_bytes())

    # 已验证值：ok ≈ 0.7561，occluded ≈ 0.2581（不乘 100）。
    assert ok_score.score == pytest.approx(0.7561, abs=1e-4)
    assert occluded_score.score == pytest.approx(0.2581, abs=1e-4)
    assert ok_score.score > occluded_score.score
    assert ok_score.model_version == "dbcnn_onnx_v1"
    assert ok_score.model_sha256
    assert ok_score.model_data_sha256
    assert ok_score.inference_ms is not None and ok_score.inference_ms >= 0.0

    result = analyze_jpeg(
        request_id="req-w8-smoke-0001",
        jpeg_bytes=ok_path.read_bytes(),
        scorer=scorer,
    )

    assert result["decision"] == "needs_review"
    assert result["reject_reason"] == "threshold_not_configured"
    assert result["threshold_status"] == "not_configured"
    assert result["score_threshold"] is None
    assert result["score"] == pytest.approx(ok_score.score)
    assert result["model_version"] == "dbcnn_onnx_v1"
    assert result["label"] is None
    assert result["label_source"] is None

    diagnostics = result["rule_diagnostics"]
    assert diagnostics["source"] == RULE_SOURCE
    assert set(diagnostics) >= {"source", "kind", "available", "label", "note"}
    if diagnostics["available"]:
        from .image_quality import IMAGE_STATUSES

        assert diagnostics["label"] in IMAGE_STATUSES
        assert diagnostics["usable"] is not None
