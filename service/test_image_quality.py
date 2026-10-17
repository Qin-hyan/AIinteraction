"""
test_image_quality.py — Week 07 Step 3: 画面可用性 / 遮挡识别模块自动化测试

覆盖场景：
  1. 空输入 / 非 JPEG 垃圾数据 → invalid
  2. 全黑图 → too_dark
  3. 全白图 → too_bright
  4. 均匀灰图 → low_contrast（遮挡场景）
  5. 高斯模糊后的图 → low_detail（失焦场景）
  6. 有结构的正常图（棋盘格） → ok
  7. metrics 字段齐全且类型正确
  8. score 在 0~1 范围内

全部用 Pillow 现场合成图像，不依赖真实摄像头硬件。
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageFilter  # type: ignore[import-untyped]

from .image_quality import (
    CONTRAST_LOW,
    DETAIL_LOW,
    LUMA_BRIGHT,
    LUMA_DARK,
    assess_image,
)


# ──────────────────────────────────────────────
# 图像构造 helper
# ──────────────────────────────────────────────


def _encode_jpeg(arr: np.ndarray, quality: int = 85) -> bytes:
    """把灰度 numpy 数组编码为 JPEG 字节。"""
    img = Image.fromarray(arr.astype(np.uint8), mode="L")
    bio = io.BytesIO()
    img.save(bio, format="JPEG", quality=quality)
    return bio.getvalue()


def _make_checkerboard(size: int = 256, block: int = 8) -> np.ndarray:
    """生成黑白棋盘格。"""
    x = np.arange(size)
    y = np.arange(size)
    xx, yy = np.meshgrid(x, y)
    return ((xx // block + yy // block) % 2 * 255).astype(np.uint8)


def _make_blurred_checkerboard(size: int = 256, block: int = 8, radius: float = 5.0) -> np.ndarray:
    """生成高斯模糊后的棋盘格，保留亮度分布但丢失边缘细节。"""
    sharp = _make_checkerboard(size, block)
    img = Image.fromarray(sharp, mode="L")
    blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(blurred)


# ──────────────────────────────────────────────
# 1. 输入无效
# ──────────────────────────────────────────────


def test_invalid_empty_bytes() -> None:
    """空字节输入应返回 invalid。"""
    result = assess_image(b"")
    assert result["status"] == "invalid"
    assert result["usable"] is False
    assert result["score"] == 0.0
    assert result["bytes_len"] == 0


def test_invalid_garbage_data() -> None:
    """非 JPEG 垃圾数据应返回 invalid。"""
    result = assess_image(b"this is definitely not a jpeg image")
    assert result["status"] == "invalid"
    assert result["usable"] is False
    assert result["score"] == 0.0
    assert result["bytes_len"] == 35


# ──────────────────────────────────────────────
# 2. 全黑图
# ──────────────────────────────────────────────


def test_all_black_too_dark() -> None:
    """纯黑图像应判定为过暗。"""
    arr = np.zeros((256, 256), dtype=np.uint8)
    result = assess_image(_encode_jpeg(arr))

    assert result["status"] == "too_dark"
    assert result["usable"] is False
    assert result["metrics"]["mean_luma"] < LUMA_DARK


# ──────────────────────────────────────────────
# 3. 全白图
# ──────────────────────────────────────────────


def test_all_white_too_bright() -> None:
    """纯白图像应判定为过亮 / 曝光过度。"""
    arr = np.full((256, 256), 255, dtype=np.uint8)
    result = assess_image(_encode_jpeg(arr))

    assert result["status"] == "too_bright"
    assert result["usable"] is False
    assert result["metrics"]["mean_luma"] > LUMA_BRIGHT


# ──────────────────────────────────────────────
# 4. 均匀灰图（遮挡场景）
# ──────────────────────────────────────────────


def test_uniform_gray_low_contrast() -> None:
    """均匀灰图应判定为低对比度（疑似镜头遮挡 / 纯色覆盖）。"""
    arr = np.full((256, 256), 128, dtype=np.uint8)
    result = assess_image(_encode_jpeg(arr))

    assert result["status"] == "low_contrast"
    assert result["usable"] is False
    assert result["metrics"]["contrast"] < CONTRAST_LOW


# ──────────────────────────────────────────────
# 5. 高斯模糊后的图（失焦场景）
# ──────────────────────────────────────────────


def test_blurred_checkerboard_low_detail() -> None:
    """模糊后的棋盘格应判定为低细节（疑似失焦）。"""
    # 块尺寸较大时，即使高斯模糊后对比度仍保留，但边缘细节显著下降
    arr = _make_blurred_checkerboard(size=256, block=16, radius=8.0)
    result = assess_image(_encode_jpeg(arr))

    assert result["status"] == "low_detail"
    assert result["usable"] is False
    assert result["metrics"]["detail"] < DETAIL_LOW
    # 亮度分布仍保留一定对比度，因此不应被低对比度分支截获
    assert result["metrics"]["contrast"] >= CONTRAST_LOW


# ──────────────────────────────────────────────
# 6. 有结构的正常图
# ──────────────────────────────────────────────


def test_checkerboard_ok() -> None:
    """清晰棋盘格应判定为画面可用。"""
    arr = _make_checkerboard(size=256, block=8)
    result = assess_image(_encode_jpeg(arr))

    assert result["status"] == "ok"
    assert result["usable"] is True
    assert result["score"] > 0.5
    assert result["metrics"]["mean_luma"] >= LUMA_DARK
    assert result["metrics"]["mean_luma"] <= LUMA_BRIGHT
    assert result["metrics"]["contrast"] >= CONTRAST_LOW
    assert result["metrics"]["detail"] >= DETAIL_LOW


# ──────────────────────────────────────────────
# 7. metrics 字段齐全且类型正确
# ──────────────────────────────────────────────


def test_metrics_fields_complete_for_ok_image() -> None:
    """正常图像的 metrics 应包含全部必需字段且类型正确。"""
    arr = _make_checkerboard(size=256, block=8)
    result = assess_image(_encode_jpeg(arr))
    metrics = result["metrics"]

    assert isinstance(metrics, dict)
    assert "mean_luma" in metrics
    assert "contrast" in metrics
    assert "detail" in metrics
    assert "width" in metrics
    assert "height" in metrics

    assert isinstance(metrics["mean_luma"], (int, float))
    assert isinstance(metrics["contrast"], (int, float))
    assert isinstance(metrics["detail"], (int, float))
    assert isinstance(metrics["width"], int)
    assert isinstance(metrics["height"], int)

    assert metrics["width"] > 0
    assert metrics["height"] > 0


def test_metrics_empty_for_invalid_input() -> None:
    """无效输入的 metrics 应为空字典，避免暴露不完整指标。"""
    result = assess_image(b"garbage")
    assert result["metrics"] == {}


# ──────────────────────────────────────────────
# 8. score 范围
# ──────────────────────────────────────────────


def test_score_within_unit_interval() -> None:
    """所有返回的 score 都应在 [0, 1] 闭区间内。"""
    cases: list[Any] = [
        b"",
        b"not jpeg",
        np.zeros((256, 256), dtype=np.uint8),
        np.full((256, 256), 255, dtype=np.uint8),
        np.full((256, 256), 128, dtype=np.uint8),
        _make_blurred_checkerboard(size=256, block=8, radius=5.0),
        _make_checkerboard(size=256, block=8),
    ]
    for case in cases:
        if isinstance(case, bytes):
            jpeg = case
        else:
            jpeg = _encode_jpeg(case)
        result = assess_image(jpeg)
        assert 0.0 <= result["score"] <= 1.0
