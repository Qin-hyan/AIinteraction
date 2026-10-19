"""
vision_dbcnn.py — Week 08 W8-2: DBCNN ONNX 无参考质量分数适配器

职责：
  1. 从环境变量读取本地 DBCNN ONNX 模型路径，使用 ONNX Runtime CPU 加载；
  2. 按已验证的预处理约定（BGR→RGB、224×224、ImageNet mean/std、NCHW
     float32）对 JPEG 推理，返回**原始**质量分数；
  3. 模型不可用或推理失败时抛出明确异常，**不**静默退回规则判断，
     也不把规则标签伪装成模型输出。

不是：
  - 不是遮挡类别分类器：DBCNN 只输出一个通用质量分数，不输出标签；
  - 不做阈值判定：门槛由调用方/配置显式提供，适配器不设默认值；
  - 不把 0~1 输出乘以 100 或裁剪；模型卡标称 0~100 与实际 0~1 输出的
    差异记录为已知元数据不一致（见 `docs/vision-dbcnn.md`）。

配置（沿用项目环境变量约定）：
  - `VISION_DBCNN_MODEL_PATH`：必需，`.onnx` 文件路径；外部权重文件
    `.onnx.data` 必须与它同目录（ONNX Runtime 自动加载）。
  - `VISION_DBCNN_MODEL_VERSION`：可选，默认 `dbcnn_onnx_v1`，是本项目
    适配器/权重快照标识，不是上游模型语义版本。

依赖：
  - numpy、opencv-python-headless、onnxruntime（CPUExecutionProvider）。
  - 模块导入本身不加载 ONNX Runtime，便于在无依赖环境运行契约测试。
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

# ──────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────

ENV_MODEL_PATH = "VISION_DBCNN_MODEL_PATH"
"""模型 `.onnx` 路径的环境变量名；未设置时适配器拒绝初始化。"""

ENV_MODEL_VERSION = "VISION_DBCNN_MODEL_VERSION"
"""模型版本标识的环境变量名（可选）。"""

DEFAULT_MODEL_VERSION = "dbcnn_onnx_v1"
"""本适配器/权重快照的默认标识；不代表上游模型的语义版本。"""

SCORE_SOURCE = "dbcnn_onnx"
"""写入结果契约的分数来源标识，与 Week 07 规则诊断区分。"""

INPUT_SIZE = 224
"""模型卡要求的输入边长（224×224）。"""

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
"""与模型卡/已验证冒烟测试一致的 ImageNet 标准化参数。"""

JPEG_MAGIC = b"\xff\xd8"
"""JPEG SOI 魔数，用于快速拒绝非 JPEG 输入。"""

CPU_PROVIDER = "CPUExecutionProvider"
"""强制使用的 ONNX Runtime 执行提供器；不启用网络或云端推理。"""


# ──────────────────────────────────────────────
# 异常类型
# ──────────────────────────────────────────────


class DbcnnError(RuntimeError):
    """DBCNN 适配器的基类异常。"""


class DbcnnConfigError(DbcnnError):
    """模型路径/版本等配置缺失或非法。"""


class DbcnnDependencyError(DbcnnError):
    """numpy / OpenCV / ONNX Runtime 等运行依赖不可用。"""


class DbcnnModelError(DbcnnError):
    """模型文件不存在、加载失败或模型 I/O 不符合预期。"""


class DbcnnInferenceError(DbcnnError):
    """单张图像解码、预处理或前向推理失败。"""


# ──────────────────────────────────────────────
# 结果数据类
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class DbcnnScore:
    """一次 DBCNN 前向推理的原始结果。

    Fields:
        score: 模型原始输出（已验证为 0~1）；**不是**概率或准确率。
        model_version: 适配器/权重快照标识。
        model_sha256: `.onnx` 图文件 SHA-256（用于追溯本地权重）。
        model_data_sha256: 外部权重 `.onnx.data` 的 SHA-256；无外部数据时为 None。
        inference_ms: 前向推理耗时（毫秒，不含解码/预处理）。
        score_source: 固定为 `dbcnn_onnx`。
    """

    score: float
    model_version: str
    model_sha256: Optional[str] = None
    model_data_sha256: Optional[str] = None
    inference_ms: Optional[float] = None
    score_source: str = SCORE_SOURCE


# ──────────────────────────────────────────────
# 依赖与配置
# ──────────────────────────────────────────────


def _require_numpy() -> Any:
    try:
        import numpy as np  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 — 统一转为明确依赖错误
        raise DbcnnDependencyError(f"numpy 不可用: {type(exc).__name__}: {exc}") from exc
    return np


def _require_cv2() -> Any:
    try:
        import cv2  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 — 统一转为明确依赖错误
        raise DbcnnDependencyError(
            f"OpenCV 不可用: {type(exc).__name__}: {exc}"
        ) from exc
    return cv2


def _require_onnxruntime() -> Any:
    try:
        import onnxruntime as ort  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 — 统一转为明确依赖错误
        raise DbcnnDependencyError(
            f"onnxruntime 不可用: {type(exc).__name__}: {exc}"
        ) from exc
    return ort


def get_model_path() -> Optional[str]:
    """从环境变量读取模型路径；未设置/空白时返回 None。"""
    raw = os.environ.get(ENV_MODEL_PATH)
    if not raw or not raw.strip():
        return None
    return os.path.abspath(os.path.expanduser(raw.strip()))


def get_model_version() -> str:
    """从环境变量读取模型版本标识；未设置/空白时使用默认标识。"""
    raw = os.environ.get(ENV_MODEL_VERSION)
    if raw is None or not raw.strip():
        return DEFAULT_MODEL_VERSION
    return raw.strip()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ──────────────────────────────────────────────
# 解码、预处理与输出解析
# ──────────────────────────────────────────────


def decode_jpeg_bgr(jpeg_bytes: bytes) -> Any:
    """把 JPEG 字节解码为 BGR uint8 图像。

    Raises:
        DbcnnDependencyError: numpy/OpenCV 不可用。
        DbcnnInferenceError: 输入为空、非 JPEG 或解码失败。
    """
    np = _require_numpy()
    cv2 = _require_cv2()

    if not isinstance(jpeg_bytes, (bytes, bytearray)) or len(jpeg_bytes) < 2:
        raise DbcnnInferenceError("输入为空或不是 JPEG 字节")
    raw = bytes(jpeg_bytes)
    if raw[:2] != JPEG_MAGIC:
        raise DbcnnInferenceError("输入不是 JPEG 数据（缺少 SOI 魔数）")

    bgr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise DbcnnInferenceError("JPEG 解码失败")
    return bgr


def preprocess_bgr(bgr: Any, size: int = INPUT_SIZE) -> Any:
    """把 BGR uint8 图像转换为模型输入张量 (1, 3, size, size) float32。

    与已验证冒烟测试保持一致：BGR→RGB、INTER_CUBIC 缩放到 224×224、
    `/255.0` 后用 ImageNet mean/std 标准化，最后转 NCHW 并增加 batch 维。
    """
    np = _require_numpy()
    cv2 = _require_cv2()

    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("size 必须是正整数")

    array = np.asarray(bgr)
    if array.ndim != 3 or array.shape[2] != 3:
        raise DbcnnInferenceError(f"期望 3 通道 BGR 图像，实际 shape={array.shape}")
    if array.dtype != np.uint8:
        raise DbcnnInferenceError(f"期望 uint8 图像，实际 dtype={array.dtype}")

    rgb = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_CUBIC)

    scaled = resized.astype(np.float32) / np.float32(255.0)
    mean = np.asarray(IMAGENET_MEAN, dtype=np.float32)
    std = np.asarray(IMAGENET_STD, dtype=np.float32)
    normalised = (scaled - mean) / std

    return np.transpose(normalised, (2, 0, 1))[np.newaxis, ...].astype(np.float32)


def extract_single_score(outputs: Any) -> float:
    """从模型输出中提取唯一的有限分数。

    Raises:
        DbcnnInferenceError: 无输出、非数值、不是恰好一个值或非有限值。
    """
    np = _require_numpy()

    if outputs is None:
        raise DbcnnInferenceError("模型没有返回输出")

    if isinstance(outputs, (list, tuple)):
        if not outputs:
            raise DbcnnInferenceError("模型没有返回输出")
        arrays = [np.asarray(item) for item in outputs]
    else:
        arrays = [np.asarray(outputs)]

    try:
        flat = np.concatenate([array.reshape(-1) for array in arrays]).astype(np.float64)
    except Exception as exc:  # noqa: BLE001 — 输出结构异常
        raise DbcnnInferenceError(f"模型输出无法解析为数值数组: {exc}") from exc

    if flat.size != 1:
        raise DbcnnInferenceError(f"模型输出应为单一分数，实际 {flat.size} 个值")

    value = float(flat[0])
    if not math.isfinite(value):
        raise DbcnnInferenceError("模型输出不是有限数值")
    return value


# ──────────────────────────────────────────────
# 推理会话
# ──────────────────────────────────────────────


class DbcnnScorer:
    """加载本地 DBCNN ONNX 模型并返回原始质量分数。

    默认从环境变量读取模型路径；也可显式传入 `model_path`。
    `session` / `decoder` / `preprocessor` 仅用于测试注入；真实调用不需要。

    Raises:
        DbcnnConfigError: 模型路径缺失、模型版本为空。
        DbcnnModelError: 模型文件不存在、加载失败或模型 I/O 不符合预期。
        DbcnnDependencyError: 运行依赖不可用。
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        model_version: Optional[str] = None,
        *,
        session: Optional[Any] = None,
        decoder: Optional[Callable[[bytes], Any]] = None,
        preprocessor: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        raw_path = model_path if model_path is not None else get_model_path()
        if raw_path is None or not str(raw_path).strip():
            raise DbcnnConfigError(
                f"未配置 DBCNN 模型路径：请设置环境变量 {ENV_MODEL_PATH} "
                "指向 dbcnn_model.onnx"
            )

        resolved = os.path.abspath(os.path.expanduser(str(raw_path).strip()))
        if not os.path.isfile(resolved):
            raise DbcnnModelError(f"DBCNN 模型文件不存在: {resolved}")

        version = model_version if model_version is not None else get_model_version()
        if not isinstance(version, str) or not version.strip():
            raise DbcnnConfigError("DBCNN 模型版本标识不能为空")

        self.model_path = resolved
        self.model_version = version.strip()
        self._decoder = decoder or decode_jpeg_bgr
        self._preprocessor = preprocessor or preprocess_bgr

        # 记录本地权重指纹，便于把结果追溯到具体文件；不对上游版本作语义声明。
        self.model_sha256 = _sha256_file(resolved)
        data_path = f"{resolved}.data"
        self.model_data_path = data_path if os.path.isfile(data_path) else None
        self.model_data_sha256 = (
            _sha256_file(data_path) if self.model_data_path is not None else None
        )

        if session is None:
            session = self._create_session()
        self._session = session
        self._input_name: Optional[str] = None
        self._bind_session(session)

    # ── 内部 ──────────────────────────────────

    def _create_session(self) -> Any:
        """创建仅 CPU 的 ONNX Runtime 会话。"""
        ort = _require_onnxruntime()
        options = ort.SessionOptions()
        try:
            session = ort.InferenceSession(
                self.model_path,
                sess_options=options,
                providers=[CPU_PROVIDER],
            )
        except Exception as exc:  # noqa: BLE001 — 统一转为明确模型错误
            raise DbcnnModelError(
                f"DBCNN ONNX 模型加载失败: {type(exc).__name__}: {exc}"
                f"（外部权重 {self.model_path}.data 需与模型同目录）"
            ) from exc

        providers = list(session.get_providers())
        if providers != [CPU_PROVIDER]:
            raise DbcnnModelError(
                f"期望仅使用 {CPU_PROVIDER}，实际 providers={providers}"
            )
        return session

    def _bind_session(self, session: Any) -> None:
        """校验模型输入/输出元数据并记录输入名。"""
        try:
            inputs = list(session.get_inputs())
            outputs = list(session.get_outputs())
        except Exception as exc:  # noqa: BLE001 — 模型元数据异常
            raise DbcnnModelError(
                f"无法读取模型 I/O 元数据: {type(exc).__name__}: {exc}"
            ) from exc

        if len(inputs) != 1:
            raise DbcnnModelError(f"期望 1 个模型输入，实际 {len(inputs)} 个")
        if not outputs:
            raise DbcnnModelError("模型没有声明输出")

        input_meta = inputs[0]
        input_name = getattr(input_meta, "name", None)
        if not isinstance(input_name, str) or not input_name:
            raise DbcnnModelError("模型输入缺少名称")

        shape = list(getattr(input_meta, "shape", []) or [])
        if shape and len(shape) != 4:
            raise DbcnnModelError(f"期望 4D NCHW 输入，实际 shape={shape}")
        if len(shape) == 4:
            for index, expected in enumerate((3, INPUT_SIZE, INPUT_SIZE), start=1):
                actual = shape[index]
                if (
                    isinstance(actual, int)
                    and not isinstance(actual, bool)
                    and actual > 0
                    and actual != expected
                ):
                    raise DbcnnModelError(
                        f"模型输入第 {index} 维应为 {expected}，实际 {actual}"
                    )

        self._input_name = input_name

    # ── 公共入口 ──────────────────────────────

    def score_jpeg(self, jpeg_bytes: bytes) -> DbcnnScore:
        """对单张 JPEG 做一次 CPU 前向推理，返回模型原始分数。

        不缩放、不裁剪、不乘 100；也不做阈值判定（由结果契约负责）。

        Raises:
            DbcnnInferenceError: 解码、预处理或推理失败。
            DbcnnDependencyError: 运行依赖不可用。
        """
        if self._session is None or not self._input_name:
            raise DbcnnModelError("DBCNN 会话未初始化")

        try:
            bgr = self._decoder(jpeg_bytes)
            tensor = self._preprocessor(bgr)
        except DbcnnError:
            raise
        except Exception as exc:  # noqa: BLE001 — 统一转为明确推理错误
            raise DbcnnInferenceError(
                f"图像解码/预处理失败: {type(exc).__name__}: {exc}"
            ) from exc

        started = time.perf_counter()
        try:
            outputs = self._session.run(None, {self._input_name: tensor})
        except Exception as exc:  # noqa: BLE001 — 统一转为明确推理错误
            raise DbcnnInferenceError(
                f"DBCNN 推理失败: {type(exc).__name__}: {exc}"
            ) from exc
        inference_ms = (time.perf_counter() - started) * 1000.0

        return DbcnnScore(
            score=extract_single_score(outputs),
            model_version=self.model_version,
            model_sha256=self.model_sha256,
            model_data_sha256=self.model_data_sha256,
            inference_ms=round(inference_ms, 3),
            score_source=SCORE_SOURCE,
        )


__all__ = [
    "DbcnnScorer",
    "DbcnnScore",
    "DbcnnError",
    "DbcnnConfigError",
    "DbcnnDependencyError",
    "DbcnnModelError",
    "DbcnnInferenceError",
    "decode_jpeg_bgr",
    "preprocess_bgr",
    "extract_single_score",
    "get_model_path",
    "get_model_version",
    "ENV_MODEL_PATH",
    "ENV_MODEL_VERSION",
    "DEFAULT_MODEL_VERSION",
    "SCORE_SOURCE",
    "INPUT_SIZE",
    "IMAGENET_MEAN",
    "IMAGENET_STD",
    "CPU_PROVIDER",
]
