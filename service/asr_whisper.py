"""
asr_whisper.py — Week 05 Step 3A: PC 端本地 Whisper 语音识别（ASR）模块

职责：
  1. `transcribe_wav(path)`：把 WAV 音频文件转成中文文本（本地推理）,
     返回文本 + 模型 / 设备 / 耗时等必要元数据。
  2. 使用官方 `openai-whisper`（Python 包 `whisper`）在**本地**加载并推理，
     不使用 ASR API Key，不调用任何云端语音识别服务。
  3. 默认选择多语言 `base` 模型，语言固定 `zh`（中文），任务固定
     `transcribe`（转录，不做翻译）。

WAV 解码策略（与 ffmpeg 的关系）：
  - 本项目录音产物是标准 PCM WAV（`service/audio_capture.py`：16kHz/16bit/mono）。
    Whisper 推理输入应为 16kHz 单声道 float32 数组，因此本模块用标准库
    `wave` 解码 PCM → numpy float32，直接交给 `model.transcribe(array, ...)`，
    绕开 `whisper.load_audio`（后者依赖系统 ffmpeg）。
  - 非标准用途（mp3/m4a/压缩 WAV 等需要外部解码器的输入）会得到明确的
    `AsrInputError` / 依赖提示；本模块不擅自修改系统 PATH 或全局环境。

设计约束：
  - 可注入模型或模型加载器（`model=...` / `model_loader=...`），单元测试
    使用假模型，不下载权重、不碰真实麦克风或网络语音服务。
  - whisper/torch 为惰性导入：未安装时模块仍可导入，调用推理才抛
    `AsrDependencyError`（与 `audio_capture.py` 的依赖策略一致）。
  - 明确异常类型：文件不存在 / 无效 WAV / 依赖缺失 / 模型加载失败 / 推理失败
    各归其类，不做静默降级。

范围约束（Week 05 Step 3A）：
  - 只做 WAV → 中文文本；不接 LLM、TTS、不上传音频、不接 ESP32 联调。
"""

from __future__ import annotations

import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union


# ──────────────────────────────────────────────
# 依赖加载（允许在无 numpy/whisper 的环境中导入本模块）
# ──────────────────────────────────────────────

try:
    import numpy as np  # type: ignore[import-untyped]

    NUMPY_IMPORT_ERROR: Optional[str] = None
except Exception as _exc:  # noqa: BLE001 — 保留原因，稍后转为明确错误信息
    np = None  # type: ignore[assignment]
    NUMPY_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


# ──────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────

DEFAULT_MODEL = "base"
"""默认 Whisper 模型：`base`（多语言，适合首轮中文验证，体积/速度均衡）。"""

DEFAULT_LANGUAGE = "zh"
"""固定识别语言：中文。"""

DEFAULT_TASK = "transcribe"
"""固定任务：转录（不做翻译）。"""

WHISPER_TARGET_SAMPLERATE = 16_000
"""Whisper 推理所需的输入采样率 (Hz)。"""

SUPPORTED_SAMPLE_WIDTH = 2
"""支持 WAV 采样宽度（字节）：2 = 16-bit PCM（与 audio_capture.py 一致）。"""

MAX_SUPPORTED_CHANNELS = 2
"""最多支持 2 声道（立体声按均值下混为单声道）。"""

# ──────────────────────────────────────────────
# 异常类型
# ──────────────────────────────────────────────


class AsrError(Exception):
    """ASR 模块的基类异常。"""


class AsrDependencyError(AsrError):
    """依赖缺失：numpy / openai-whisper 未安装或导入失败。"""


class AsrInputError(AsrError):
    """输入错误：文件不存在、不是合法 PCM WAV、参数超出支持范围、WAV 为空。"""


class AsrModelError(AsrError):
    """模型加载失败：权重下载失败、模型名无效、设备不可用等。"""


class AsrInferenceError(AsrError):
    """推理失败：whisper 解码器执行阶段抛出的异常。"""


# ──────────────────────────────────────────────
# 结果类型
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class AsrResult:
    """一次本地 ASR 的识别结果与必要元数据。

    Fields:
        text:             识别出的文本（可能为空字符串）。
        language:         固定识别语言（默认 zh）。
        task:             固定任务（默认 transcribe）。
        model:            实际使用的模型名。
        device:           推理设备（如 "cpu" / "cuda:0"）。
        duration_seconds: 纯推理耗时（秒）。
    """

    text: str
    language: str
    task: str
    model: str
    device: str
    duration_seconds: float

    @property
    def is_empty(self) -> bool:
        """识别结果是否为空（无有效文本）。"""
        return not self.text.strip()


# ──────────────────────────────────────────────
# 依赖与设备查询
# ──────────────────────────────────────────────


def _require_numpy() -> Any:
    """返回 numpy 模块；不可用时抛 AsrDependencyError（含原因与安装提示）。"""
    if np is None:
        raise AsrDependencyError(
            "numpy 不可用，无法进行语音识别"
            f"（导入错误: {NUMPY_IMPORT_ERROR}）；"
            "请安装依赖: pip install -r requirements-asr.txt"
        )
    return np


def _require_whisper() -> Any:
    """惰性导入 `whisper`（openai-whisper）；不可用时抛 AsrDependencyError。"""
    try:
        import whisper  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 — 统一转为明确错误
        raise AsrDependencyError(
            "openai-whisper（import whisper）不可用，无法本地加载模型"
            f"（导入错误: {type(exc).__name__}: {exc}）；"
            "请安装依赖: pip install -r requirements-asr.txt"
        ) from exc
    return whisper


def _resolve_device(requested: Optional[str]) -> str:
    """将 device 参数解析为实际设备名。

    显式指定则原样返回；`"auto"` 时优先 CUDA（GPU 可用时），否则 CPU。
    不为了 GPU 加速盲目安装大型 CUDA 依赖——torch 未安装时按 CPU 处理。
    """
    if requested and requested != "auto":
        return requested
    try:
        import torch  # type: ignore[import-untyped]

        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 — torch 未安装/不可用 → 回退 CPU
        pass
    return "cpu"


def _device_from_model(model: Any, fallback: str) -> str:
    """从已加载模型的 `device` 属性取设备名（兼容 torch.device 与字符串）。"""
    dev = getattr(model, "device", None)
    if dev is not None:
        return str(dev)
    return fallback

# ──────────────────────────────────────────────
# 模型加载
# ──────────────────────────────────────────────


def _default_model_loader(model_name: str, device: str) -> Any:
    """默认模型加载器：whisper.load_model(name, device=device)。

    任何加载阶段异常统一包装为 AsrModelError（含模型名与设备信息）。
    首次加载需联网下载模型权重（base ≈ 140 MB，缓存在
    ~/.cache/whisper）；失败不无限重试，由调用方决定。
    """
    whisper = _require_whisper()
    try:
        return whisper.load_model(model_name, device=device)
    except AsrDependencyError:
        raise
    except Exception as exc:  # noqa: BLE001 — 统一转为明确错误
        raise AsrModelError(
            f"Whisper 模型加载失败 (model={model_name!r}, device={device!r}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc


# ──────────────────────────────────────────────
# PCM WAV 解码（标准库 wave，不依赖 ffmpeg）
# ──────────────────────────────────────────────


def _resample_linear(
    audio: Any, src_rate: int, dst_rate: int = WHISPER_TARGET_SAMPLERATE
) -> Any:
    """numpy 线性插值重采样到目标采样率（纯内存，无 ffmpeg）。"""
    np_ = _require_numpy()
    if src_rate == dst_rate:
        return audio
    n_src = len(audio)
    n_out = max(1, int(round(n_src * dst_rate / src_rate)))
    x_src = np_.linspace(0.0, 1.0, n_src, dtype=np_.float64)
    x_dst = np_.linspace(0.0, 1.0, n_out, dtype=np_.float64)
    return np_.interp(x_dst, x_src, audio.astype(np_.float64)).astype(np_.float32)


def _decode_wav_to_float32(path: Path) -> tuple[Any, int]:
    """把标准 PCM WAV 解码为 16kHz 单声道 float32 数组。

    返回 (audio_array, 源采样率)。非标准 PCM（压缩编解码）抛 AsrInputError；
    文件缺失由调用方前置判断。
    """
    np_ = _require_numpy()
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            samplerate = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise AsrInputError(
            f"不是合法的 WAV 文件（需要标准 PCM）: {path} "
            f"({type(exc).__name__}: {exc})"
        ) from exc

    if not isinstance(channels, int) or channels <= 0 or samplerate <= 0:
        raise AsrInputError(f"WAV 声道数/采样率非法 (channels={channels}, rate={samplerate}): {path}")
    if sample_width != SUPPORTED_SAMPLE_WIDTH:
        raise AsrInputError(
            f"不支持的采样宽度 {sample_width} 字节，仅支持 16-bit PCM "
            f"(请用 audio_capture.py 等以 int16 PCM 导出): {path}"
        )
    if channels > MAX_SUPPORTED_CHANNELS:
        raise AsrInputError(
            f"声道数 {channels} 超出支持范围（最多 {MAX_SUPPORTED_CHANNELS}）: {path}"
        )
    if frames == 0 or not raw:
        raise AsrInputError(f"WAV 内容为空（0 帧），无有效语音: {path}")

    data = np_.frombuffer(raw, dtype=np_.int16)
    if channels == 2:
        left = data[0::2].astype(np_.float32)
        right = data[1::2].astype(np_.float32)
        audio = (left + right) * 0.5
    else:
        audio = data.astype(np_.float32)
    audio = audio / 32768.0

    if samplerate != WHISPER_TARGET_SAMPLERATE:
        audio = _resample_linear(audio, samplerate, WHISPER_TARGET_SAMPLERATE)
    return np_.ascontiguousarray(audio, dtype=np_.float32), samplerate


# ──────────────────────────────────────────────
# 推理结果提取
# ──────────────────────────────────────────────


def _extract_text(result: Any) -> str:
    """从 whisper 返回结果中提取文本（dict `text` 字段 / 对象属性）。"""
    if isinstance(result, dict):
        text = result.get("text", "")
    else:
        text = getattr(result, "text", "") or ""
    return str(text).strip()

# ──────────────────────────────────────────────
# 转录器
# ──────────────────────────────────────────────


class Transcriber:
    """本地 Whisper 转录器（可注入模型或模型加载器）。

    用法::

        tr = Transcriber()                    # 默认 base / zh / transcribe
        result = tr.transcribe_wav("a.wav")

    单元测试注入假模型::

        tr = Transcriber(model=FakeModel())
        assert tr.transcribe_wav("a.wav").text == "…"

    Args:
        model:        已加载的模型对象；None 时由 model_loader 加载。
        model_name:   模型名（默认 `base`），用于加载与元数据。
        device:       "auto" | "cpu" | "cuda[:n]"；默认 "auto" 自动选择。
        model_loader: 可注入加载器 `(model_name, device) -> model`。
        language:     固定识别语言，默认 "zh"。
        task:         固定任务，默认 "transcribe"。
    """

    def __init__(
        self,
        model: Any = None,
        model_name: str = DEFAULT_MODEL,
        device: Union[str, None] = "auto",
        model_loader: Optional[Callable[[str, str], Any]] = None,
        language: str = DEFAULT_LANGUAGE,
        task: str = DEFAULT_TASK,
    ) -> None:
        if not isinstance(model_name, str) or not model_name:
            raise AsrError(f"模型名必须是非空字符串: {model_name!r}")
        if not isinstance(language, str) or not language:
            raise AsrError(f"语言必须是非空字符串: {language!r}")
        if not isinstance(task, str) or not task:
            raise AsrError(f"任务必须是非空字符串: {task!r}")

        self._model = model
        self._model_name = model_name
        self._requested_device = device
        self._model_loader = model_loader or _default_model_loader
        self.language = language
        self.task = task

    # ── 只读属性 ─────────────────────────────

    @property
    def model(self) -> Any:
        """当前模型对象（未加载前为 None）。"""
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str:
        """解析后的设备名；未加载模型时为自动解析结果（不做设备 I/O）。"""
        if self._model is not None:
            return _device_from_model(self._model, _resolve_device(self._requested_device))
        return _resolve_device(self._requested_device)

    # ── 模型加载 ─────────────────────────────

    def load(self) -> Any:
        """确保模型已加载；返回模型对象。失败抛 AsrModelError。"""
        if self._model is None:
            device = _resolve_device(self._requested_device)
            self._model = self._model_loader(self._model_name, device)
        return self._model

    # ── 转录 ─────────────────────────────────

    def transcribe_wav(
        self,
        path: Any,
        language: Optional[str] = None,
        task: Optional[str] = None,
    ) -> AsrResult:
        """转录 WAV 文件，返回 AsrResult（文本 + 模型/设备/耗时元数据）。

        Args:
            path:     WAV 文件路径。
            language: 覆盖固定语言；None 表示用构造时的 language。
            task:     覆盖固定任务；None 表示用构造时的 task。

        Raises:
            AsrInputError:       文件不存在 / 不是合法 PCM WAV / WAV 为空。
            AsrDependencyError:  numpy / openai-whisper 未安装。
            AsrModelError:       模型加载失败（未注入已加载模型时）。
            AsrInferenceError:   whisper 推理阶段失败。
        """
        target = Path(path)
        if not target.is_file():
            raise AsrInputError(f"WAV 文件不存在: {target}")

        audio, _src_rate = _decode_wav_to_float32(target)
        model = self.load()  # 可能抛 AsrModelError
        lang = language or self.language
        task_ = task or self.task
        device = _device_from_model(model, _resolve_device(self._requested_device))

        try:
            started = time.perf_counter()
            raw_result = model.transcribe(
                audio,
                language=lang,
                task=task_,
                fp16=False,
            )
            elapsed = time.perf_counter() - started
        except Exception as exc:  # noqa: BLE001 — 统一包装为明确错误
            raise AsrInferenceError(
                f"Whisper 推理失败 (model={self._model_name!r}, device={device!r}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        return AsrResult(
            text=_extract_text(raw_result),
            language=lang,
            task=task_,
            model=self._model_name,
            device=device,
            duration_seconds=elapsed,
        )


# ──────────────────────────────────────────────
# 模块级便捷函数
# ──────────────────────────────────────────────


def transcribe_wav(
    path: Any,
    *,
    model: Any = None,
    model_name: str = DEFAULT_MODEL,
    device: Union[str, None] = "auto",
    language: str = DEFAULT_LANGUAGE,
    task: str = DEFAULT_TASK,
    model_loader: Optional[Callable[[str, str], Any]] = None,
) -> AsrResult:
    """便捷入口：转录一个 WAV 文件，返回 AsrResult。

    全部参数与 `Transcriber` 构造一致；反复调用可复用显式传入的
    已加载 `model` 以避免重复加载。
    """
    return Transcriber(
        model=model,
        model_name=model_name,
        device=device,
        model_loader=model_loader,
        language=language,
        task=task,
    ).transcribe_wav(path)


__all__ = [
    "AsrError",
    "AsrDependencyError",
    "AsrInputError",
    "AsrModelError",
    "AsrInferenceError",
    "AsrResult",
    "Transcriber",
    "transcribe_wav",
    "DEFAULT_MODEL",
    "DEFAULT_LANGUAGE",
    "DEFAULT_TASK",
]

