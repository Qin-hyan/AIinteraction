"""
test_asr_whisper.py — Week 05 Step 3A: 本地 Whisper ASR 模块自动化测试

覆盖：
  - 正常识别（假模型注入，验证文本 + 模型/设备/耗时元数据 + 传给模型的语言/任务/采样率）
  - 文件不存在 / 无效 WAV / 空 WAV / 不支持的采样宽度 / 超过支持声道数
  - 立体声下混、非 16kHz 重采样
  - 空识别结果
  - 模型加载失败 / 推理异常 / 依赖缺失
  - 模型加载器注入与模型复用

全部使用假模型，不依赖真实麦克风、网络语音服务或模型权重。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from . import asr_whisper as awh
from .asr_whisper import (
    AsrDependencyError,
    AsrError,
    AsrInferenceError,
    AsrInputError,
    AsrModelError,
    AsrResult,
    Transcriber,
)


# ──────────────────────────────────────────────
# 假模型与 WAV 构造 helper（不接触真实 whisper/模型权重）
# ──────────────────────────────────────────────


class FakeModel:
    """最小假模型：记录 transcribe 调用参数，返回可配置文本或抛异常。"""

    def __init__(self, text="查看最近的加速度数据", device="cpu", raise_exc=None) -> None:
        self.text = text
        self.device = device
        self.raise_exc = raise_exc
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"audio": np.asarray(audio), "kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"text": self.text, "language": kwargs.get("language"), "task": kwargs.get("task")}


def _write_wav(
    path: Path,
    *,
    samplerate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
    frames: int | None = None,
    data: np.ndarray | None = None,
) -> Path:
    """用标准库 wave 写测试 WAV（默认 1 秒 16kHz/mono/int16 确定性信号）。"""
    if data is None:
        if frames is None:
            frames = samplerate
        arr = (np.arange(frames * channels) % 1000).astype(np.int16)
    else:
        arr = np.asarray(data, dtype=np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(samplerate)
        wf.writeframes(arr.tobytes())
    return path


# ──────────────────────────────────────────────
# 正常识别
# ──────────────────────────────────────────────


def test_transcribe_wav_success_returns_text_and_metadata(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav", samplerate=16000, channels=1, frames=16000)
    fake = FakeModel(text="查看最近的加速度数据")

    result = Transcriber(model=fake).transcribe_wav(wav)

    assert isinstance(result, AsrResult)
    assert result.text == "查看最近的加速度数据"
    assert result.language == "zh"
    assert result.task == "transcribe"
    assert result.model == "base"
    assert result.device == "cpu"
    assert result.duration_seconds >= 0
    assert result.is_empty is False

    call = fake.calls[0]
    assert call["audio"].dtype == np.float32
    assert len(call["audio"]) == 16000  # 1s @ 16kHz 原样传入
    assert call["kwargs"]["language"] == "zh"
    assert call["kwargs"]["task"] == "transcribe"
    assert call["kwargs"]["fp16"] is False


# ──────────────────────────────────────────────
# 输入错误
# ──────────────────────────────────────────────


def test_transcribe_wav_file_not_found_raises() -> None:
    with pytest.raises(AsrInputError, match="不存在"):
        Transcriber(model=FakeModel()).transcribe_wav(Path("no_such_file_anywhere.wav"))


def test_transcribe_wav_invalid_wav_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"this is definitely not a wave file at all")
    with pytest.raises(AsrInputError, match="WAV"):
        Transcriber(model=FakeModel()).transcribe_wav(bad)


def test_transcribe_wav_empty_wav_raises(tmp_path: Path) -> None:
    empty = _write_wav(tmp_path / "empty.wav", frames=0)
    with pytest.raises(AsrInputError, match="空"):
        Transcriber(model=FakeModel()).transcribe_wav(empty)


def test_transcribe_wav_unsupported_sample_width_raises(tmp_path: Path) -> None:
    wav8 = _write_wav(tmp_path / "u8.wav", samplerate=16000, sample_width=1, frames=160)
    with pytest.raises(AsrInputError, match="16-bit"):
        Transcriber(model=FakeModel()).transcribe_wav(wav8)


def test_transcribe_wav_too_many_channels_raises(tmp_path: Path) -> None:
    wav4 = _write_wav(tmp_path / "quad.wav", samplerate=16000, channels=4, frames=400)
    with pytest.raises(AsrInputError, match="声道数"):
        Transcriber(model=FakeModel()).transcribe_wav(wav4)


# ──────────────────────────────────────────────
# 立体声下混 / 非 16kHz 重采样
# ──────────────────────────────────────────────


def test_stereo_wav_downmixed_to_mono_before_transcribe(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "stereo.wav", samplerate=16000, channels=2, frames=2000)
    fake = FakeModel()

    result = Transcriber(model=fake).transcribe_wav(wav)

    assert result.text == "查看最近的加速度数据"
    assert len(fake.calls[0]["audio"]) == 2000  # 2ch(4000 点) → mono(2000 帧)
# ──────────────────────────────────────────────
# 模型加载失败 / 模型加载器注入
# ──────────────────────────────────────────────


def test_model_load_failure_raises_model_error(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav")

    def _failing_loader(model_name: str, device: str):
        raise AsrModelError(f"模型下载失败 (model={model_name}, device={device})")

    with pytest.raises(AsrModelError, match="模型下载失败"):
        Transcriber(model=None, model_name="base", model_loader=_failing_loader).transcribe_wav(wav)


def test_model_loader_receives_requested_name_and_device(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav")
    fake = FakeModel(text="ok")
    captured: list[tuple[str, str]] = []

    def _recording_loader(model_name: str, device: str):
        captured.append((model_name, device))
        return fake

    Transcriber(model=None, model_name="tiny", device="cpu", model_loader=_recording_loader).transcribe_wav(wav)

    assert captured == [("tiny", "cpu")]


# ──────────────────────────────────────────────
# 推理异常
# ──────────────────────────────────────────────


def test_inference_exception_wrapped_as_inference_error(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav")
    fake = FakeModel(raise_exc=RuntimeError("boom in decoder"))

    with pytest.raises(AsrInferenceError) as excinfo:
        Transcriber(model=fake).transcribe_wav(wav)

    message = str(excinfo.value)
    assert "boom in decoder" in message
    assert "RuntimeError" in message


# ──────────────────────────────────────────────
# 依赖缺失
# ──────────────────────────────────────────────


def test_numpy_missing_raises_dependency_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wav = _write_wav(tmp_path / "voice.wav")
    monkeypatch.setattr(awh, "np", None)

    with pytest.raises(AsrDependencyError, match="numpy"):
        Transcriber(model=FakeModel()).transcribe_wav(wav)


def test_whisper_missing_on_default_loader_raises_dependency_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = _write_wav(tmp_path / "voice.wav")

    def _no_whisper():
        raise AsrDependencyError("openai-whisper 不可用")

    monkeypatch.setattr(awh, "_require_whisper", _no_whisper)

    with pytest.raises(AsrDependencyError, match="openai-whisper"):
        Transcriber().transcribe_wav(wav)


# ──────────────────────────────────────────────
# 构造参数与默认值
# ──────────────────────────────────────────────


def test_constructor_rejects_bad_args() -> None:
    with pytest.raises(AsrError):
        Transcriber(model_name="")
    with pytest.raises(AsrError):
        Transcriber(language="")
    with pytest.raises(AsrError):
        Transcriber(task="")
    with pytest.raises(AsrError):
        Transcriber(model_name=None)  # type: ignore[arg-type]


def test_defaults_match_step3a_spec() -> None:
    assert awh.DEFAULT_MODEL == "base"
    assert awh.DEFAULT_LANGUAGE == "zh"
    assert awh.DEFAULT_TASK == "transcribe"


def test_48k_wav_resampled_to_16k_before_transcribe(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "hi48k.wav", samplerate=48000, channels=1, frames=24000)
    fake = FakeModel()

    Transcriber(model=fake).transcribe_wav(wav)

    got = fake.calls[0]["audio"]
    assert abs(len(got) - 8000) <= 1  # 24000 帧 @48kHz(0.5s) → 8000 帧 @16kHz


# ──────────────────────────────────────────────
# 空识别结果
# ──────────────────────────────────────────────


def test_empty_recognition_result_not_treated_as_success(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav")
    fake = FakeModel(text="    ")

    result = Transcriber(model=fake).transcribe_wav(wav)

    assert result.text == ""
    assert result.is_empty is True
    assert result.duration_seconds >= 0
def test_module_level_helper_passes_model_name_language_task_override(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav")
    fake = FakeModel(text="hello")

    result = awh.transcribe_wav(
        wav,
        model=fake,
        model_name="tiny",
        language="en",
        task="translate",
    )

    assert result.text == "hello"
    assert result.model == "tiny"
    assert result.language == "en"
    assert result.task == "translate"
    assert fake.calls[0]["kwargs"]["language"] == "en"
    assert fake.calls[0]["kwargs"]["task"] == "translate"


def test_provided_model_skips_model_loader(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "voice.wav")
    fake = FakeModel()

    def _should_never_be_called(model_name: str, device: str) -> None:
        raise AssertionError("已注入模型时不得调用 model_loader")

    result = Transcriber(model=fake, model_loader=_should_never_be_called).transcribe_wav(wav)
    assert result.text == "查看最近的加速度数据"