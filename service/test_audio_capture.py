"""
test_audio_capture.py — Week 05 Step 1 音频采集模块自动测试

测试策略：
  - 全部用假 sounddevice 模块（FakeSoundDevice）替换 audio_capture.sd，
    不打开真实麦克风、不依赖任何音频硬件、不播放录音
  - 覆盖：开始/停止、WAV 头信息、状态转换、异常路径、并发启动、设备能力不足
  - 文件的真实 WAV 参数用标准库 wave 独立复核（不信任被测代码的自述）

运行（使用仓库外隔离虚拟环境，不向系统全局 Python 装包）::

    <venv>\\Scripts\\python.exe -m pytest service/test_audio_capture.py -v
"""

import threading
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from . import audio_capture
from .audio_capture import (
    SAMPLE_WIDTH_BYTES,
    AudioCapture,
    AudioCaptureError,
    AudioDeviceError,
    EmptyRecordingError,
    RecordingInfo,
    RecordingState,
    RecordingStateError,
    read_wav_info,
)

# ──────────────────────────────────────────────
# 假设备：完全替代真实 PortAudio / 麦克风
# ──────────────────────────────────────────────

DEFAULT_FAKE_DEVICES = [
    {
        "name": "Fake Mic",
        "max_input_channels": 1,
        "max_output_channels": 0,
        "default_samplerate": 16000.0,
    },
    {
        "name": "Fake Speakers",
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    },
]

SAMPLE_BYTES = b"\x10\x27"  # 10000 (int16 小端) 的 2 字节采样


class FakePortAudioError(Exception):
    """模拟 sounddevice.PortAudioError。"""


class FakeRawInputStream:
    """模拟 sounddevice.RawInputStream：只记录调用，不接触真实设备。"""

    def __init__(self, owner, kwargs):
        self.owner = owner
        self.samplerate = owner.reported_samplerate or kwargs["samplerate"]
        self.channels = kwargs["channels"]
        self.dtype = kwargs["dtype"]
        self.blocksize = kwargs["blocksize"]
        self.device = kwargs["device"]
        self.callback = kwargs["callback"]
        self.started = False
        self.start_calls = 0
        self.stopped = False
        self.closed = False

    def start(self):
        if self.owner.start_error is not None:
            raise self.owner.start_error
        self.started = True
        self.start_calls += 1

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def feed(self, frames=1600, channels=None, status=None):
        """按 PortAudio 回调签名推送一块 PCM 数据。"""
        channel_count = channels or self.channels
        data = SAMPLE_BYTES * (frames * channel_count)
        self.callback(data, frames, None, status)
        return data


class FakeSoundDevice:
    """模拟 sounddevice 模块中本模块用到的表面。"""

    PortAudioError = FakePortAudioError

    def __init__(
        self,
        supported_rates=(16000,),
        reported_samplerate=None,
        open_error=None,
        start_error=None,
        devices=None,
        default_device=(1, 4),
    ):
        self.supported_rates = tuple(supported_rates)
        self.reported_samplerate = reported_samplerate
        self.open_error = open_error
        self.start_error = start_error
        self.devices = devices if devices is not None else DEFAULT_FAKE_DEVICES
        self.default = SimpleNamespace(device=default_device)
        self.streams = []
        self.open_calls = []

    def RawInputStream(self, **kwargs):
        self.open_calls.append(dict(kwargs))
        if self.open_error is not None:
            raise self.open_error
        if kwargs["samplerate"] not in self.supported_rates:
            raise FakePortAudioError(f"Invalid sample rate [{kwargs['samplerate']}]")
        stream = FakeRawInputStream(self, kwargs)
        self.streams.append(stream)
        return stream

    def query_devices(self, device=None, kind=None):
        if kind == "input":
            for item in self.devices:
                if int(item["max_input_channels"]) > 0:
                    return dict(item)
            raise FakePortAudioError("No input device available")
        return [dict(item) for item in self.devices]


# ──────────────────────────────────────────────
# 公共测试装置
# ──────────────────────────────────────────────


class AudioCaptureTestBase(unittest.TestCase):
    """临时输出目录 + 受控假设备，绝不使用真实麦克风。"""

    def setUp(self):
        tmp = TemporaryDirectory(prefix="week05_audio_test_")
        self.addCleanup(tmp.cleanup)
        self.output_dir = Path(tmp.name)

    def install_fake(self, fake):
        """把假 sounddevice 安装到被测模块（测试结束自动还原）。"""
        patcher = patch.object(audio_capture, "sd", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def make_capture(self, fake=None, **kwargs):
        fake = fake if fake is not None else FakeSoundDevice()
        self.install_fake(fake)
        kwargs.setdefault("output_dir", self.output_dir)
        return AudioCapture(**kwargs)

    def out_files(self):
        return sorted(p.name for p in self.output_dir.iterdir())


# ──────────────────────────────────────────────
# 配置与默认值
# ──────────────────────────────────────────────


class TestConfiguration(AudioCaptureTestBase):
    """默认参数必须符合 Week 05 约定（16kHz / 16bit / mono）。"""

    def test_default_configuration_matches_week05_contract(self):
        capture = self.make_capture(output_dir=None)
        self.assertEqual(capture.samplerate, 16_000)
        self.assertEqual(capture.channels, 1)
        self.assertEqual(capture.dtype, "int16")
        self.assertEqual(capture.sample_width, SAMPLE_WIDTH_BYTES["int16"])
        self.assertEqual(capture.output_dir, audio_capture.DEFAULT_OUTPUT_DIR)
        self.assertEqual(capture.state, RecordingState.IDLE)
        self.assertFalse(capture.is_recording)

    def test_configuration_is_overridable(self):
        capture = self.make_capture(samplerate=8_000, channels=2, blocksize=400)
        self.assertEqual(capture.samplerate, 8_000)
        self.assertEqual(capture.channels, 2)
        self.assertEqual(capture.blocksize, 400)

    def test_invalid_constructor_arguments_are_rejected(self):
        cases = (
            {"samplerate": 0},
            {"samplerate": -1},
            {"channels": 0},
            {"channels": -2},
            {"dtype": "float32"},
            {"blocksize": 0},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AudioCaptureError):
                    AudioCapture(**kwargs)

    def test_open_stream_receives_requested_parameters(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake, blocksize=800, device=7)
        capture.start_recording()
        call = fake.open_calls[0]
        self.assertEqual(call["samplerate"], 16_000)
        self.assertEqual(call["channels"], 1)
        self.assertEqual(call["dtype"], "int16")
        self.assertEqual(call["blocksize"], 800)
        self.assertEqual(call["device"], 7)
        self.assertTrue(callable(call["callback"]))


# ──────────────────────────────────────────────
# 正常路径：开始 → 采集 → 停止 → WAV 参数
# ──────────────────────────────────────────────


class TestStartStopRecording(AudioCaptureTestBase):
    """开始/停止录音与 WAV 头信息。"""

    def test_start_and_stop_writes_wav_and_returns_real_parameters(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)

        path = capture.start_recording()
        stream = fake.streams[0]
        self.assertTrue(stream.started)
        self.assertEqual(capture.state, RecordingState.RECORDING)
        self.assertTrue(capture.is_recording)

        stream.feed(frames=1600)
        stream.feed(frames=1600)
        self.assertEqual(capture.frames_recorded, 3200)
        self.assertAlmostEqual(capture.duration_seconds, 0.2, places=6)

        info = capture.stop_recording()

        self.assertIsInstance(info, RecordingInfo)
        self.assertEqual(info.path, path)
        self.assertTrue(path.is_file())
        self.assertEqual(info.samplerate, 16_000)
        self.assertEqual(info.channels, 1)
        self.assertEqual(info.sample_width, 2)
        self.assertEqual(info.frames, 3200)
        self.assertAlmostEqual(info.duration_seconds, 0.2, places=6)
        self.assertEqual(info.size_bytes, 44 + 3200 * 2)

        # 状态与资源复位
        self.assertEqual(capture.state, RecordingState.IDLE)
        self.assertEqual(capture.frames_recorded, 0)
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)

        # 用标准库 wave 独立复核文件头
        with wave.open(str(path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16_000)
            self.assertEqual(wf.getnframes(), 3200)
            self.assertEqual(wf.getcomptype(), "NONE")

    def test_auto_generated_file_name_and_location(self):
        capture = self.make_capture()
        path = capture.start_recording()
        self.assertEqual(path.parent, self.output_dir)
        self.assertTrue(path.name.startswith("voice_"))
        self.assertEqual(path.suffix, ".wav")

    def test_explicit_output_path_is_honoured(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        target = self.output_dir / "nested" / "custom.wav"

        path = capture.start_recording(target)
        self.assertEqual(path, target)
        self.assertTrue(target.parent.is_dir())

        fake.streams[0].feed(frames=160)
        info = capture.stop_recording()
        self.assertTrue(target.is_file())
        self.assertEqual(info.frames, 160)

    def test_stereo_configuration_writes_stereo_header(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake, channels=2)
        capture.start_recording()
        fake.streams[0].feed(frames=1600, channels=2)
        info = capture.stop_recording()
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.frames, 1600)
        self.assertEqual(info.size_bytes, 44 + 1600 * 2 * 2)

    def test_callback_status_flags_are_surfaced(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        capture.start_recording()
        fake.streams[0].feed(frames=800, status="input overflow")
        info = capture.stop_recording()
        self.assertIn("input overflow", info.status_flags)

    def test_state_transitions_allow_repeated_recordings(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        for index in range(3):
            path = capture.start_recording()
            self.assertEqual(capture.state, RecordingState.RECORDING)
            fake.streams[index].feed(frames=160)
            info = capture.stop_recording()
            self.assertEqual(capture.state, RecordingState.IDLE)
            self.assertEqual(info.frames, 160)
            self.assertTrue(path.is_file())

        self.assertEqual(len(fake.streams), 3)
        self.assertTrue(all(s.closed for s in fake.streams))
        self.assertEqual(len([n for n in self.out_files() if n.endswith(".wav")]), 3)

    def test_callback_after_stop_is_discarded(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)

        capture.start_recording()
        first_stream = fake.streams[0]
        first_stream.feed(frames=800)
        first = capture.stop_recording()
        self.assertEqual(first.frames, 800)

        # 停止后迟到的回调不得影响状态或下一次录音
        first_stream.feed(frames=800)
        self.assertEqual(capture.frames_recorded, 0)

        capture.start_recording()
        fake.streams[1].feed(frames=400)
        second = capture.stop_recording()
        self.assertEqual(second.frames, 400)
        self.assertNotEqual(first.path, second.path)


# ──────────────────────────────────────────────
# 状态错误与空录音
# ──────────────────────────────────────────────


class TestStateErrors(AudioCaptureTestBase):
    """重复启动、错误停止、空录音都必须明确失败。"""

    def test_duplicate_start_is_rejected(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        capture.start_recording()
        with self.assertRaises(RecordingStateError) as ctx:
            capture.start_recording()
        self.assertIn("禁止重复启动", str(ctx.exception))
        # 第一次录音未被破坏
        self.assertEqual(capture.state, RecordingState.RECORDING)
        self.assertEqual(len(fake.streams), 1)
        self.assertEqual(fake.streams[0].start_calls, 1)

    def test_stop_without_start_is_rejected(self):
        capture = self.make_capture()
        with self.assertRaises(RecordingStateError) as ctx:
            capture.stop_recording()
        self.assertIn("当前没有进行中的录音", str(ctx.exception))
        self.assertEqual(capture.state, RecordingState.IDLE)
        self.assertEqual(self.out_files(), [])

    def test_double_stop_is_rejected(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        capture.start_recording()
        fake.streams[0].feed(frames=160)
        capture.stop_recording()
        with self.assertRaises(RecordingStateError):
            capture.stop_recording()

    def test_empty_recording_is_rejected_and_no_file_written(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        path = capture.start_recording()
        with self.assertRaises(EmptyRecordingError) as ctx:
            capture.stop_recording()
        self.assertIn("录音为空", str(ctx.exception))
        self.assertFalse(path.exists())
        self.assertEqual(self.out_files(), [])
        self.assertEqual(capture.state, RecordingState.IDLE)
        self.assertTrue(fake.streams[0].closed)

    def test_zero_frame_callback_is_treated_as_empty(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        path = capture.start_recording()
        fake.streams[0].feed(frames=0)
        self.assertEqual(capture.frames_recorded, 0)
        with self.assertRaises(EmptyRecordingError):
            capture.stop_recording()
        self.assertFalse(path.exists())

    def test_incomplete_pcm_data_is_rejected_not_silently_written(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        path = capture.start_recording()
        # 声称 1600 帧，但只给出 3 字节（非 3200 字节，也不是采样整数倍）
        fake.streams[0].callback(b"\x01\x02\x03", 1600, None, None)
        with self.assertRaises(AudioCaptureError) as ctx:
            capture.stop_recording()
        self.assertIn("音频数据不完整", str(ctx.exception))
        self.assertFalse(path.exists())
        self.assertEqual(capture.state, RecordingState.IDLE)

    def test_recover_after_empty_recording(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        capture.start_recording()
        with self.assertRaises(EmptyRecordingError):
            capture.stop_recording()
        # 空录音失败后仍可正常开始下一次录音
        capture.start_recording()
        fake.streams[1].feed(frames=320)
        info = capture.stop_recording()
        self.assertEqual(info.frames, 320)


# ──────────────────────────────────────────────
# 设备/依赖失败路径
# ──────────────────────────────────────────────


class TestDeviceFailures(AudioCaptureTestBase):
    """设备不支持、打开失败、依赖缺失都必须给出清晰错误且不伪装成功。"""

    def test_unsupported_samplerate_reports_clear_error_and_stays_idle(self):
        fake = FakeSoundDevice(supported_rates=(48000,))
        capture = self.make_capture(fake)
        with self.assertRaises(AudioDeviceError) as ctx:
            capture.start_recording()
        message = str(ctx.exception)
        self.assertIn("打开录音设备失败", message)
        self.assertIn("16000Hz", message)
        self.assertIn("Invalid sample rate", message)
        self.assertEqual(capture.state, RecordingState.IDLE)
        self.assertEqual(fake.streams, [])
        self.assertEqual(self.out_files(), [])

    def test_reported_samplerate_mismatch_is_rejected_not_silently_accepted(self):
        fake = FakeSoundDevice(reported_samplerate=48000.0)
        capture = self.make_capture(fake)
        with self.assertRaises(AudioDeviceError) as ctx:
            capture.start_recording()
        self.assertIn("不一致", str(ctx.exception))
        self.assertEqual(capture.state, RecordingState.IDLE)
        stream = fake.streams[0]
        self.assertTrue(stream.closed)
        self.assertFalse(stream.started)

    def test_device_open_failure_is_recovered_on_retry(self):
        fake = FakeSoundDevice(open_error=FakePortAudioError("Error opening InputStream"))
        capture = self.make_capture(fake)
        with self.assertRaises(AudioDeviceError) as ctx:
            capture.start_recording()
        self.assertIn("打开录音设备失败", str(ctx.exception))
        self.assertEqual(capture.state, RecordingState.IDLE)

        # 设备恢复后（例如被其它进程释放）应能再次启动
        fake.open_error = None
        path = capture.start_recording()
        fake.streams[0].feed(frames=800)
        info = capture.stop_recording()
        self.assertTrue(path.is_file())
        self.assertEqual(info.frames, 800)

    def test_stream_start_failure_closes_stream_and_resets_state(self):
        fake = FakeSoundDevice(start_error=FakePortAudioError("Device unavailable"))
        capture = self.make_capture(fake)
        with self.assertRaises(AudioDeviceError) as ctx:
            capture.start_recording()
        self.assertIn("启动录音失败", str(ctx.exception))
        self.assertTrue(fake.streams[0].closed)
        self.assertEqual(capture.state, RecordingState.IDLE)
        self.assertEqual(self.out_files(), [])

    def test_missing_sounddevice_dependency_reports_install_hint(self):
        capture = AudioCapture(output_dir=self.output_dir)
        with patch.object(audio_capture, "sd", None), patch.object(
            audio_capture,
            "SOUNDDEVICE_IMPORT_ERROR",
            "ModuleNotFoundError: No module named 'sounddevice'",
        ):
            with self.assertRaises(AudioDeviceError) as ctx:
                capture.start_recording()
            self.assertIn("sounddevice 不可用", str(ctx.exception))
            self.assertIn("requirements-audio.txt", str(ctx.exception))
            with self.assertRaises(AudioDeviceError):
                audio_capture.query_input_devices()
        self.assertEqual(capture.state, RecordingState.IDLE)


# ──────────────────────────────────────────────
# WAV 参数读取与设备枚举
# ──────────────────────────────────────────────


class TestWavInspection(AudioCaptureTestBase):
    """read_wav_info 必须读取文件头真实参数，并对非法文件明确报错。"""

    def test_read_wav_info_reads_header_not_expectation(self):
        manual = self.output_dir / "manual_8k.wav"
        with wave.open(str(manual), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8_000)
            wf.writeframes(SAMPLE_BYTES * 800)

        info = read_wav_info(manual)
        self.assertEqual(info.samplerate, 8_000)
        self.assertEqual(info.channels, 1)
        self.assertEqual(info.sample_width, 2)
        self.assertEqual(info.frames, 800)
        self.assertAlmostEqual(info.duration_seconds, 0.1, places=6)
        self.assertEqual(info.size_bytes, 44 + 800 * 2)

    def test_read_wav_info_missing_file_raises(self):
        with self.assertRaises(AudioCaptureError) as ctx:
            read_wav_info(self.output_dir / "nope.wav")
        self.assertIn("不存在", str(ctx.exception))

    def test_read_wav_info_invalid_file_raises(self):
        bad = self.output_dir / "bad.wav"
        bad.write_text("this is not a wav file", encoding="utf-8")
        with self.assertRaises(AudioCaptureError) as ctx:
            read_wav_info(bad)
        self.assertIn("不是合法的 WAV", str(ctx.exception))

    def test_recording_info_as_dict_is_reportable(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)
        capture.start_recording()
        fake.streams[0].feed(frames=1600)
        info = capture.stop_recording()

        payload = info.as_dict()
        self.assertEqual(payload["samplerate"], 16_000)
        self.assertEqual(payload["channels"], 1)
        self.assertEqual(payload["sample_width_bytes"], 2)
        self.assertEqual(payload["frames"], 1600)
        self.assertEqual(payload["duration_seconds"], 0.1)
        self.assertEqual(payload["size_bytes"], 44 + 1600 * 2)
        self.assertEqual(payload["status_flags"], [])
        self.assertTrue(payload["path"].endswith(".wav"))


class TestDeviceQueries(AudioCaptureTestBase):
    """设备枚举辅助函数（真实验证脚本也复用它们）。"""

    def test_query_input_devices_filters_output_only_devices(self):
        self.install_fake(FakeSoundDevice())
        devices = audio_capture.query_input_devices()
        self.assertEqual([d["index"] for d in devices], [0])
        self.assertEqual(devices[0]["name"], "Fake Mic")
        self.assertEqual(devices[0]["max_input_channels"], 1)

    def test_default_input_device_reports_index(self):
        self.install_fake(FakeSoundDevice(default_device=(7, 9)))
        info = audio_capture.default_input_device()
        self.assertEqual(info["index"], 7)
        self.assertEqual(info["max_input_channels"], 1)

    def test_default_input_device_error_when_none_available(self):
        fake = FakeSoundDevice(
            devices=[
                {
                    "name": "Speakers only",
                    "max_input_channels": 0,
                    "max_output_channels": 2,
                    "default_samplerate": 48000.0,
                }
            ]
        )
        self.install_fake(fake)
        with self.assertRaises(AudioDeviceError) as ctx:
            audio_capture.default_input_device()
        self.assertIn("未找到可用的默认输入设备", str(ctx.exception))


# ──────────────────────────────────────────────
# 并发安全
# ──────────────────────────────────────────────


class TestConcurrency(AudioCaptureTestBase):
    """并发调用 start_recording 时只允许一个成功。"""

    def test_concurrent_start_only_one_succeeds(self):
        fake = FakeSoundDevice()
        capture = self.make_capture(fake)

        paths = []
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            try:
                paths.append(capture.start_recording())
            except Exception as exc:  # noqa: BLE001 — 测试需捕获全部异常
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

        self.assertEqual(len(paths), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RecordingStateError)
        self.assertEqual(len(fake.streams), 1)
        self.assertEqual(capture.state, RecordingState.RECORDING)

        fake.streams[0].feed(frames=160)
        info = capture.stop_recording()
        self.assertEqual(info.frames, 160)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()