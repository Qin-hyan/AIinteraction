"""
image_quality.py — Week 07 Step 3: PC 端画面可用性 / 遮挡识别基线模块

职责：
  1. `assess_image(jpeg_bytes)`：对 ESP32 摄像头返回的 JPEG 图像做快速、
     无状态的画面可用性评估。
  2. 输出 `usable / status / score / metrics / bytes_len`，支持课程要求的
     "采集中 / 采集成功 / 镜头遮挡 / 画面不可用 / 采集失败" 等反馈映射。
  3. 只做画面可用性 / 遮挡识别；不推断精确位置、人身风险或现实正确率。

算法要点：
  - Pillow 解码为灰度图（L 模式），长边超过 `MAX_LONG_EDGE` 时 Lanczos 降采样。
  - 指标：mean_luma（亮度均值）、contrast（亮度标准差）、detail（相邻像素
    梯度能量均值，采用前向差分近似 Sobel，速度快且对镜头遮挡 / 失焦敏感）。
  - 判定顺序：invalid → too_dark → too_bright → low_contrast → low_detail → ok。
  - 所有阈值均为模块级具名常量，便于课堂现场调整与审计。

约束：
  - `score` 仅作相对比较，不得解释为现实正确率。
  - 输入无效（空字节、非 JPEG 魔数、Pillow 解码失败）均返回 `status="invalid"`，
    不抛异常，避免调用方因单张坏图崩溃。
"""

from __future__ import annotations

import io
from typing import Any, Dict

# ──────────────────────────────────────────────
# 依赖加载（允许在无 Pillow/numpy 的环境中导入本模块）
# ──────────────────────────────────────────────

try:
    import numpy as np  # type: ignore[import-untyped]

    NUMPY_IMPORT_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 — 保留原因，稍后转为明确错误信息
    np = None  # type: ignore[assignment]
    NUMPY_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

try:
    from PIL import Image  # type: ignore[import-untyped]

    PIL_IMPORT_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 — 同上
    Image = None  # type: ignore[assignment]
    PIL_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

# ──────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────

JPEG_MAGIC = b"\xff\xd8"
"""JPEG 文件起始魔数（SOI marker），用于快速拒绝非 JPEG 输入。"""

MAX_LONG_EDGE = 640
"""降采样后长边最大像素数，在速度与精度间取得平衡；ESP32 常见分辨率 320×240。"""

LUMA_DARK = 25.0
"""平均亮度低于此值视为过暗（画面几乎无可用信息）。"""

LUMA_BRIGHT = 230.0
"""平均亮度高于此值视为过亮 / 曝光过度（高光溢出）。"""

CONTRAST_LOW = 8.0
"""亮度标准差低于此值视为低对比度（疑似镜头遮挡、纯色纸/布覆盖）。"""

DETAIL_LOW = 8.0
"""相邻像素梯度能量均值低于此值视为低细节（疑似失焦、严重模糊）。"""

# ──────────────────────────────────────────────
# 内部工具函数
# ──────────────────────────────────────────────


def _require_numpy() -> Any:
    """返回 numpy 模块；不可用时抛出包含原因的 RuntimeError。"""
    if np is None:
        raise RuntimeError(f"numpy 不可用: {NUMPY_IMPORT_ERROR}")
    return np


def _require_pil() -> Any:
    """返回 PIL.Image 模块；不可用时抛出包含原因的 RuntimeError。"""
    if Image is None:
        raise RuntimeError(f"Pillow 不可用: {PIL_IMPORT_ERROR}")
    return Image


def _decode_gray(jpeg_bytes: bytes) -> Any:
    """把 JPEG 字节解码为灰度图，并在需要时降采样。

    Raises:
        ValueError: 输入非 JPEG、格式不符或 Pillow 解码失败。
    """
    Image_ = _require_pil()

    if not jpeg_bytes or len(jpeg_bytes) < 2 or jpeg_bytes[:2] != JPEG_MAGIC:
        raise ValueError("输入为空或非 JPEG 数据")

    try:
        with io.BytesIO(jpeg_bytes) as bio:
            img = Image_.open(bio)
            if img.format != "JPEG":
                raise ValueError(f"输入格式为 {img.format!r}，需要 JPEG")
            gray = img.convert("L")
    except Exception as exc:  # noqa: BLE001 — 统一转为 invalid
        raise ValueError(f"JPEG 解码失败: {exc}") from exc

    # 降采样：保持画面内容不变，仅减少后续矩阵运算量
    width, height = gray.size
    if max(width, height) > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / max(width, height)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        gray = gray.resize(new_size, Image_.Resampling.LANCZOS)

    return gray


def _compute_metrics(gray_img: Any) -> Dict[str, float]:
    """从灰度图计算画面质量指标。

    指标：
      - mean_luma：灰度均值（0~255）。
      - contrast：灰度标准差。
      - detail：相邻像素梯度能量均值。采用前向差分计算梯度幅值，
        与 Sobel 相比计算量更小，且对镜头遮挡 / 失焦导致的边缘丧失同样敏感。
      - width / height：降采样后的图像尺寸。
    """
    np_ = _require_numpy()
    arr = np_.ascontiguousarray(gray_img, dtype=np_.float32)

    mean_luma = float(arr.mean())
    contrast = float(arr.std())

    # 前向差分梯度幅值：保留主要边缘能量，同时避免 Sobel 核的额外开销
    dx = arr[:, 1:] - arr[:, :-1]
    dy = arr[1:, :] - arr[:-1, :]
    # 取两个梯度场的公共有效区域，避免行列数不一致
    grad_mag = np_.sqrt(dx[:-1, :] * dx[:-1, :] + dy[:, :-1] * dy[:, :-1])
    detail = float(grad_mag.mean())

    height, width = arr.shape
    return {
        "mean_luma": mean_luma,
        "contrast": contrast,
        "detail": detail,
        "width": width,
        "height": height,
    }


def _score_from_metrics(mean_luma: float, contrast: float, detail: float) -> float:
    """由指标计算 0~1 的相对可用性分数（越高越好，仅作相对比较）。

    分数为亮度、对比度、细节三个子分数的乘积：
      - 亮度越靠近中间区域，luma_score 越接近 1；越靠近纯黑 / 纯白越接近 0。
      - contrast / CONTRAST_LOW 与 detail / DETAIL_LOW 上限为 1。
    """
    dark_penalty = max(0.0, (LUMA_DARK - mean_luma) / LUMA_DARK)
    bright_penalty = max(0.0, (mean_luma - LUMA_BRIGHT) / (255.0 - LUMA_BRIGHT))
    luma_score = 1.0 - max(dark_penalty, bright_penalty)

    contrast_score = min(1.0, contrast / CONTRAST_LOW)
    detail_score = min(1.0, detail / DETAIL_LOW)

    raw_score = luma_score * contrast_score * detail_score
    return float(max(0.0, min(1.0, raw_score)))


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────


def assess_image(jpeg_bytes: bytes) -> Dict[str, Any]:
    """评估单张 JPEG 图像的画面可用性。

    Args:
        jpeg_bytes: 来自 ESP32 `/api/camera/photo` 的 JPEG 二进制数据。

    Returns:
        dict: 至少包含以下键：
          - usable (bool): 画面是否可用。
          - status (str): 状态，取值
            {"ok", "too_dark", "too_bright", "low_contrast", "low_detail", "invalid"}。
          - score (float): 0~1 的相对可用性分数。
          - metrics (dict): 含 mean_luma / contrast / detail / width / height。
          - bytes_len (int): 输入字节数。
    """
    bytes_len = 0 if jpeg_bytes is None else len(jpeg_bytes)

    # 依赖缺失：与课程环境解耦，模块可导入但不可用
    if np is None or Image is None:
        return {
            "usable": False,
            "status": "invalid",
            "score": 0.0,
            "metrics": {},
            "bytes_len": bytes_len,
        }

    try:
        gray = _decode_gray(jpeg_bytes)
    except Exception:  # noqa: BLE001 — 输入错误统一映射为 invalid
        return {
            "usable": False,
            "status": "invalid",
            "score": 0.0,
            "metrics": {},
            "bytes_len": bytes_len,
        }

    metrics = _compute_metrics(gray)
    mean_luma = metrics["mean_luma"]
    contrast = metrics["contrast"]
    detail = metrics["detail"]

    # 判定顺序：invalid → too_dark → too_bright → low_contrast → low_detail → ok
    status = "ok"
    if mean_luma < LUMA_DARK:
        status = "too_dark"
    elif mean_luma > LUMA_BRIGHT:
        status = "too_bright"
    elif contrast < CONTRAST_LOW:
        status = "low_contrast"
    elif detail < DETAIL_LOW:
        status = "low_detail"

    score = _score_from_metrics(mean_luma, contrast, detail)

    return {
        "usable": status == "ok",
        "status": status,
        "score": score,
        "metrics": metrics,
        "bytes_len": bytes_len,
    }


__all__ = [
    "assess_image",
    "JPEG_MAGIC",
    "MAX_LONG_EDGE",
    "LUMA_DARK",
    "LUMA_BRIGHT",
    "CONTRAST_LOW",
    "DETAIL_LOW",
]
