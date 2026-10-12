"""
audio_capture.py — Week 05 Step 1: PC 端麦克风音频采集模块

职责：
  1. 用 sounddevice.RawInputStream 采集原始 PCM（不使用 NumPy）
  2. 用标准库 wave 写出 WAV（默认 单声道 / 16-bit PCM / 16kHz，采样率可配置）
  3. 提供按键式半双工录音所需的明确 API：
       start_recording() → stop_recording()
  4. 状态管理 + 线程安全 + 资源清理 + 明确异常反馈：
       重复启动、未启动就停止、空录音、依赖缺失、设备打开失败、
       设备实际采样率与请求不一致 —— 一律抛出明确异常，不静默伪装成功

范围约束（Week 05 Step 1）：
  - 只做本地采集与 WAV 落盘；不上传、不播放、不做识别（ASR/LLM/TTS 不属于本模块）
  - 录音文件只写本机（默认 %TEMP%/voice_task/），不提交仓库
  - 不使用 NumPy：RawInputStream 回调直接给出可 bytes() 的缓冲对象

依赖：
  sounddevice（见仓库根 requirements-audio.txt）。未安装或 PortAudio 不可用时，
  本模块仍可导入，但调用录音/设备查询 API 会抛出 AudioDeviceError。

线程模型：
  PortAudio 回调在自己的线程中执行，只做「追加数据」；
  状态变更与缓冲区快照由 threading.Lock 保护，锁不会跨阻塞式 I/O 持有。
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ──────────────────────────────────────────────
# 依赖加载（允许在无 sounddevice 的环境中导入本模块）
# ──────────────────────────────────────────────

try:
    # sounddevice 是 CFFI 绑定：PortAudio 动态库缺失时抛 OSError 而非 ImportError，
    # 因此这里有意捕获全部导入期异常，并保留原因，稍后转为明确错误信息。
    import sounddevice as sd  # type: ignore[import-untyped]

    SOUNDDEVICE_IMPORT_ERROR: Optional[str] = None
except Exception as _exc:  # noqa: BLE001
    sd = None  # type: ignore[assignment]
    SOUNDDEVICE_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


# ──────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────

DEFAULT_SAMPLERATE = 16_000
"""默认采样率 (Hz)。Week 05 架构约定 16kHz。"""

DEFAULT_CHANNELS = 1
"""默认声道数（单声道）。"""

DEFAULT_DTYPE = "int16"
"""默认采样格式（16-bit PCM）。"""

SAMPLE_WIDTH_BYTES: Dict[str, int] = {"int16": 2}
"""支持的 dtype → WAV 每采样字节数。当前仅支持 int16（16-bit PCM）。"""

DEFAULT_BLOCKSIZE = 1_600
"""默认回调块大小 (frames)。16kHz 下约 100ms，兼顾回调频率与延迟。"""

DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "voice_task"
"""默认输出目录（架构 §7.4：录音只存本机临时目录，不提交仓库）。"""

_SAMPLERATE_TOLERANCE_HZ = 0.5
"""设备实际采样率与请求采样率允许的最大偏差；超出即报错，不静默接受。"""


# ──────────────────────────────────────────────
# 异常与结果类型
# ──────────────────────────────────────────────


class AudioCaptureError(Exception):
    """音频采集模块的基类异常。"""


class AudioDeviceError(AudioCaptureError):
    """依赖缺失、设备不可用、设备不支持请求参数等硬件/驱动层错误。"""


class RecordingStateError(AudioCaptureError):
    """调用顺序/状态错误：重复启动、未启动就停止。"""


class EmptyRecordingError(AudioCaptureError):
    """录音为空（未采集到任何帧），不得视为成功。"""


@dataclass(frozen=True)
class RecordingInfo:
    """一次录音（或一个 WAV 文件）的可核对参数。

    Fields:
        path:               WAV 文件路径
        samplerate:         采样率 (Hz)
        channels:           声道数
        sample_width:       每采样字节数（int16 → 2）
        frames:             每声道帧数
        duration_seconds:   frames / samplerate
        size_bytes:         文件实际字节数（含 WAV 头）
        status_flags:       PortAudio 回调上报的状态标志（如 input overflow）
    """

    path: Path
    samplerate: int
    channels: int
    sample_width: int
    frames: int
    duration_seconds: float
    size_bytes: int
    status_flags: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        """转为便于打印/记录的可序列化字典。"""
        return {
            "path": str(self.path),
            "samplerate": self.samplerate,
            "channels": self.channels,
            "sample_width_bytes": self.sample_width,
            "frames": self.frames,
            "duration_seconds": round(self.duration_seconds, 4),
            "size_bytes": self.size_bytes,
            "status_flags": list(self.status_flags),
        }


def read_wav_info(
    path: os.PathLike[str] | str,
    status_flags: Sequence[str] = (),
) -> RecordingInfo:
    """读取 WAV 文件的真实参数（标准库 wave，不依赖 sounddevice）。

    用于核对落盘文件是否与预期一致；文件不存在或不是合法 WAV 时抛
    AudioCaptureError（不做任何猜测或降级）。

    Args:
        path:         WAV 文件路径。
        status_flags: 录音时收集到的回调状态标志，原样带入结果。

    Returns:
        RecordingInfo — 参数取自文件头，不取自调用方记录值。
    """
    target = Path(path)
    if not target.is_file():
        raise AudioCaptureError(f"WAV 文件不存在: {target}")

    try:
        with wave.open(str(target), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            samplerate = wf.getframerate()
            frames = wf.getnframes()
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioCaptureError(
            f"不是合法的 WAV 文件: {target} ({type(exc).__name__}: {exc})"
        ) from exc

    return RecordingInfo(
        path=target,
        samplerate=samplerate,
        channels=channels,
        sample_width=sample_width,
        frames=frames,
        duration_seconds=(frames / samplerate) if samplerate else 0.0,
        size_bytes=target.stat().st_size,
        status_flags=tuple(status_flags),
    )


# ──────────────────────────────────────────────
# 依赖与设备查询
# ──────────────────────────────────────────────


def _require_sounddevice() -> Any:
    """返回 sounddevice 模块；不可用时抛 AudioDeviceError（含原因与安装提示）。"""
    if sd is None:
        raise AudioDeviceError(
            "sounddevice 不可用，无法进行音频采集"
            f"（导入错误: {SOUNDDEVICE_IMPORT_ERROR}）；"
            "请安装依赖: pip install -r requirements-audio.txt"
        )
    return sd


def query_input_devices() -> List[Dict[str, Any]]:
    """列出系统识别到的输入设备（max_input_channels > 0）。

    Returns:
        设备信息字典列表，每项额外带 ``index``（在 PortAudio 设备表中的下标）。
    """
    module = _require_sounddevice()
    try:
        devices = module.query_devices()
    except Exception as exc:  # noqa: BLE001 — 统一转为明确错误
        raise AudioDeviceError(
            f"查询音频设备失败: {type(exc).__name__}: {exc}"
        ) from exc

    result: List[Dict[str, Any]] = []
    for index, device in enumerate(devices):
        info = dict(device)
        if int(info.get("max_input_channels", 0)) > 0:
            info["index"] = index
            result.append(info)
    return result


def default_input_device() -> Dict[str, Any]:
    """返回默认输入设备信息；无可用输入设备时抛 AudioDeviceError。"""
    module = _require_sounddevice()
    try:
        info = dict(module.query_devices(kind="input"))
        default = getattr(module.default, "device", None)
    except Exception as exc:  # noqa: BLE001 — 统一转为明确错误
        raise AudioDeviceError(
            f"未找到可用的默认输入设备: {type(exc).__name__}: {exc}"
        ) from exc

    info["index"] = default[0] if isinstance(default, (list, tuple)) and default else None
    return info


# ──────────────────────────────────────────────
# 录音状态
# ──────────────────────────────────────────────


class RecordingState(str, Enum):
    """录音模块状态（按键式半双工的 IDLE ↔ RECORDING 两态）。"""

    IDLE = "idle"
    RECORDING = "recording"


# ──────────────────────────────────────────────
# 采集器
# ──────────────────────────────────────────────


class AudioCapture:
    """按键式半双工录音：``start_recording()`` 开始，``stop_recording()`` 停止并落盘。

    典型用法（后续 Step 2 的按键事件直接映射到这两个调用）::

        capture = AudioCapture()
        capture.start_recording()        # 按键按下
        info = capture.stop_recording()  # 按键松开 → 得到 WAV 参数

    线程安全：状态与缓冲区由内部锁保护；PortAudio 回调只追加数据。
    资源清理：任何失败路径都会关闭已打开的音频流；stop 之后状态必然回到 IDLE。
    """

    def __init__(
        self,
        samplerate: int = DEFAULT_SAMPLERATE,
        channels: int = DEFAULT_CHANNELS,
        dtype: str = DEFAULT_DTYPE,
        device: Optional[Any] = None,
        output_dir: Optional[os.PathLike[str] | str] = None,
        blocksize: int = DEFAULT_BLOCKSIZE,
    ) -> None:
        """配置一次录音会话的参数（不做任何设备 I/O）。

        Args:
            samplerate: 采样率 (Hz)，默认 16000。
            channels:   声道数，默认 1（单声道）。
            dtype:      采样格式，当前仅支持 ``"int16"``（16-bit PCM）。
            device:     PortAudio 设备下标或名称；None 表示默认输入设备。
            output_dir: WAV 输出目录；None 表示 ``DEFAULT_OUTPUT_DIR``。
            blocksize:  回调块大小 (frames)，必须为正整数。

        Raises:
            AudioCaptureError: 参数非法（非正采样率/声道数/块大小、不支持的 dtype）。
        """
        if not isinstance(samplerate, int) or isinstance(samplerate, bool) or samplerate <= 0:
            raise AudioCaptureError(f"采样率必须是正整数 (Hz): {samplerate!r}")
        if not isinstance(channels, int) or isinstance(channels, bool) or channels <= 0:
            raise AudioCaptureError(f"声道数必须是正整数: {channels!r}")
        if dtype not in SAMPLE_WIDTH_BYTES:
            raise AudioCaptureError(
                f"不支持的采样格式: {dtype!r}，当前仅支持 {sorted(SAMPLE_WIDTH_BYTES)}"
            )
        if not isinstance(blocksize, int) or isinstance(blocksize, bool) or blocksize <= 0:
            raise AudioCaptureError(f"块大小必须是正整数: {blocksize!r}")

        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device
        self.output_dir = (
            Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
        )
        self.blocksize = blocksize

        self._lock = threading.Lock()
        self._state = RecordingState.IDLE
        self._stream: Optional[Any] = None
        self._target_path: Optional[Path] = None
        self._chunks: List[bytes] = []
        self._frames = 0
        self._status_flags: List[str] = []

    # ── 只读属性 ─────────────────────────────

    @property
    def sample_width(self) -> int:
        """每采样字节数（int16 → 2）。"""
        return SAMPLE_WIDTH_BYTES[self.dtype]

    @property
    def state(self) -> RecordingState:
        """当前状态。"""
        with self._lock:
            return self._state

    @property
    def is_recording(self) -> bool:
        """是否正在录音。"""
        return self.state is RecordingState.RECORDING

    @property
    def frames_recorded(self) -> int:
        """本次录音已采集的每声道帧数。"""
        with self._lock:
            return self._frames

    @property
    def duration_seconds(self) -> float:
        """本次录音已采集的时长（秒）。"""
        return self.frames_recorded / self.samplerate

    # ── 开始录音 ─────────────────────────────

    def start_recording(
        self, output_path: Optional[os.PathLike[str] | str] = None
    ) -> Path:
        """开始录音（按键按下时调用），返回目标 WAV 路径。

        Args:
            output_path: 目标 WAV 路径；None 表示自动生成
                         ``<output_dir>/voice_<时间戳>.wav``。

        Returns:
            目标 WAV 文件路径（此刻尚未写入，停止时才落盘）。

        Raises:
            RecordingStateError: 已有进行中的录音（禁止重复启动）。
            AudioDeviceError:    sounddevice 不可用 / 设备打开或启动失败 /
                                 设备实际采样率与请求不一致。
            AudioCaptureError:   输出目录无法创建。
        """
        # 1) 状态占位：先置 RECORDING，阻止并发重复启动
        with self._lock:
            if self._state is not RecordingState.IDLE:
                raise RecordingStateError(
                    "已有进行中的录音，禁止重复启动（请先 stop_recording()）"
                )
            self._state = RecordingState.RECORDING
            self._chunks = []
            self._frames = 0
            self._status_flags = []
            self._target_path = None

        # 2) 锁外执行可能阻塞的 I/O；任何失败都必须回滚状态并关闭资源
        module = sd
        stream: Optional[Any] = None
        try:
            if module is None:
                _require_sounddevice()  # 抛 AudioDeviceError（含安装提示）
            target = self._resolve_output_path(output_path)
            stream = self._open_stream(module)
            stream.start()
        except AudioCaptureError:
            self._close_stream_quietly(stream)
            self._reset_to_idle()
            raise
        except Exception as exc:  # noqa: BLE001 — 兜底：不留下半开状态
            self._close_stream_quietly(stream)
            self._reset_to_idle()
            raise AudioDeviceError(
                f"启动录音失败 ({type(exc).__name__}: {exc})；"
                f"请求参数 device={self.device!r} "
                f"{self.samplerate}Hz/{self.channels}ch/{self.dtype}"
            ) from exc

        with self._lock:
            self._stream = stream
            self._target_path = target
        return target

    # ── 停止录音 ─────────────────────────────

    def stop_recording(self) -> RecordingInfo:
        """停止录音（按键松开时调用），把音频写入 WAV 并返回真实文件参数。

        Returns:
            RecordingInfo — 取自落盘文件的真实参数（采样率/声道/位深/帧数/时长/大小）。

        Raises:
            RecordingStateError: 当前没有进行中的录音。
            EmptyRecordingError: 未采集到任何帧（不生成空 WAV，不视为成功）。
            AudioDeviceError:    停止/关闭设备时报错。
            AudioCaptureError:   数据不完整或写盘/落盘校验失败。
        """
        with self._lock:
            if self._state is not RecordingState.RECORDING:
                raise RecordingStateError("当前没有进行中的录音，无法停止")
            stream = self._stream

        # 先停流再取数据：关闭后回调不再运行，缓冲区即为最终内容
        stop_error: Optional[Exception] = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:  # noqa: BLE001
                stop_error = exc
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001
                stop_error = stop_error or exc

        data, frames, flags, target = self._collect_and_reset()

        if stop_error is not None:
            raise AudioDeviceError(
                f"停止录音设备时出错 ({type(stop_error).__name__}: {stop_error})"
            ) from stop_error

        if target is None:
            raise AudioCaptureError("内部错误：缺少目标输出路径")

        if frames <= 0 or not data:
            self._remove_partial_file(target)
            raise EmptyRecordingError(
                "录音为空（未采集到任何帧）：不生成 WAV，也不视为成功"
            )

        expected_bytes = frames * self.channels * self.sample_width
        if len(data) != expected_bytes:
            self._remove_partial_file(target)
            raise AudioCaptureError(
                f"音频数据不完整: 收到 {len(data)} 字节，按 {frames} 帧 × "
                f"{self.channels} 声道 × {self.sample_width} 字节应为 {expected_bytes} 字节"
                "（状态已复位，不生成文件）"
            )

        self._write_wav(target, data)

        info = read_wav_info(target, status_flags=flags)
        if (
            info.frames != frames
            or info.channels != self.channels
            or info.samplerate != self.samplerate
        ):
            self._remove_partial_file(target)
            raise AudioCaptureError(
                f"WAV 落盘校验失败: 期望 {self.samplerate}Hz/{self.channels}ch/{frames} 帧，"
                f"实际 {info.samplerate}Hz/{info.channels}ch/{info.frames} 帧"
            )
        return info

    # ── 内部实现 ─────────────────────────────

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """PortAudio 回调（独立线程）：只做追加，不做阻塞式工作。

        停止后到达的块会被直接丢弃，避免污染下一次录音。
        """
        with self._lock:
            if self._state is not RecordingState.RECORDING:
                return
            if status:
                self._status_flags.append(str(status))
            self._chunks.append(bytes(indata))
            self._frames += frames

    def _open_stream(self, module: Any) -> Any:
        """打开 RawInputStream 并校验设备实际采样率（不做静默降级）。"""
        try:
            stream = module.RawInputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                device=self.device,
                channels=self.channels,
                dtype=self.dtype,
                callback=self._callback,
            )
        except Exception as exc:  # noqa: BLE001 — PortAudioError 等统一转为明确错误
            raise AudioDeviceError(
                f"打开录音设备失败 (device={self.device!r}, "
                f"{self.samplerate}Hz/{self.channels}ch/{self.dtype}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        actual_rate = float(getattr(stream, "samplerate", self.samplerate) or 0.0)
        if abs(actual_rate - self.samplerate) > _SAMPLERATE_TOLERANCE_HZ:
            self._close_stream_quietly(stream)
            raise AudioDeviceError(
                f"录音设备实际采样率 {actual_rate:g}Hz 与请求 {self.samplerate}Hz 不一致，"
                "已中止（不静默改写参数）"
            )
        return stream

    def _resolve_output_path(
        self, output_path: Optional[os.PathLike[str] | str]
    ) -> Path:
        """决定目标 WAV 路径并确保父目录存在。"""
        target = Path(output_path) if output_path is not None else (
            self.output_dir / _default_file_name()
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AudioCaptureError(
                f"无法创建输出目录 {target.parent}: {exc}"
            ) from exc
        return target

    def _write_wav(self, path: Path, data: bytes) -> None:
        """把裸 PCM 写为标准 WAV（标准库 wave，无第三方依赖）。"""
        try:
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.sample_width)
                wf.setframerate(self.samplerate)
                wf.writeframes(data)
        except (OSError, wave.Error, ValueError) as exc:
            self._remove_partial_file(path)
            raise AudioCaptureError(
                f"写出 WAV 失败: {path} ({type(exc).__name__}: {exc})"
            ) from exc

    def _collect_and_reset(
        self,
    ) -> Tuple[bytes, int, Tuple[str, ...], Optional[Path]]:
        """在锁内取出缓冲区快照，并把状态/资源引用复位为 IDLE。"""
        with self._lock:
            chunks = self._chunks
            frames = self._frames
            flags = tuple(self._status_flags)
            path = self._target_path

            self._chunks = []
            self._frames = 0
            self._status_flags = []
            self._stream = None
            self._target_path = None
            self._state = RecordingState.IDLE
        return b"".join(chunks), frames, flags, path

    def _reset_to_idle(self) -> None:
        """启动失败后的状态回滚（保证不会卡在 RECORDING）。"""
        with self._lock:
            self._state = RecordingState.IDLE
            self._stream = None
            self._target_path = None
            self._chunks = []
            self._frames = 0
            self._status_flags = []

    @staticmethod
    def _close_stream_quietly(stream: Optional[Any]) -> None:
        """尽力关闭流：清理路径上的次生错误不掩盖原始异常。"""
        if stream is None:
            return
        for action in ("stop", "close"):
            func = getattr(stream, action, None)
            if callable(func):
                try:
                    func()
                except Exception:  # noqa: BLE001 — 清理路径忽略次生错误
                    pass

    @staticmethod
    def _remove_partial_file(path: Path) -> None:
        """删除失败路径产生的半成品/校验失败文件（尽力而为）。"""
        try:
            path.unlink()
        except OSError:
            pass


def _default_file_name() -> str:
    """生成带毫秒时间戳的文件名，避免同一秒内互相覆盖。"""
    now = time.time()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    return f"voice_{stamp}_{int(now * 1000) % 1000:03d}.wav"