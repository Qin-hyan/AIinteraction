"""
test_voice_control_server.py — Week 05 Step 3B 语音录音 HTTP 控制 + 后台 ASR 自动测试

测试策略：
  - 使用 Flask test client（``app.test_client()``）：不启动真实 HTTP 服务、不占端口、
    不改动任何网络/防火墙设置
  - 使用假采集器 FakeAudioCapture 替换真实麦克风：不打开任何录音设备、不写任何 WAV
  - ASR 部分使用假 whisper 模型（经真实 asr_whisper.Transcriber 注入）与脚本转录器：
    不下载模型权重、不调用外部语音服务、不打开真实麦克风
  - 覆盖：start/stop/status、自动 ASR 提交、后台状态转换（pending/running/success/failed）、
    识别成功/失败、重复请求与提交取代、轮询不重复推理、JSON 响应与错误码、请求体校验、
    404/405 JSON 映射、输出目录保护、CLI 默认值
  - **Step 3D（控制/查询接口解耦）**：断言 stop 是「紧凑控制回执」——UTF-8 字节数不超
    ESP32 固件 512B 响应缓冲（ESP32_RESP_BUFFER_BYTES）、不携带完整 wav/asr 调试信息；
    完整元数据与 ASR 文本仍由 GET /status 提供；stop 成功仍触发后台 ASR；失败响应同样
    保持小体积与既有错误兼容

运行（使用仓库外隔离虚拟环境，不向系统全局 Python 装包）::

    <venv>\\Scripts\\python.exe -m pytest service/test_voice_control_server.py -v
"""

import contextlib
import io
import json
import threading
import time
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional
from unittest.mock import patch

from . import asr_whisper, audio_capture, voice_control_server
from .audio_capture import (
    AudioCaptureError,
    AudioDeviceError,
    EmptyRecordingError,
    RecordingInfo,
    RecordingStateError,
)


def make_info(
    path: Any,
    frames: int = 16000,
    samplerate: int = 16000,
    channels: int = 1,
    size_bytes: int = 32044,
    flags: tuple = (),
) -> RecordingInfo:
    """构造真实的 ``audio_capture.RecordingInfo``。

    有意复用真实类型（而非自造 dict），这样被测代码走的 ``as_dict()`` 序列化路径
    与生产路径完全一致。
    """
    return RecordingInfo(
        path=Path(path),
        samplerate=samplerate,
        channels=channels,
        sample_width=audio_capture.SAMPLE_WIDTH_BYTES["int16"],
        frames=frames,
        duration_seconds=frames / samplerate,
        size_bytes=size_bytes,
        status_flags=tuple(flags),
    )


# ──────────────────────────────────────────────
# 假 whisper 模型 / 脚本转录器 / 轮询 helper（Week 05 Step 3B）
# 全部不接触真实 whisper 权重、真实麦克风与外部服务。
# ──────────────────────────────────────────────

ASR_TEST_POLL_INTERVAL = 0.01
ASR_TEST_POLL_TIMEOUT = 8.0
ASR_TEST_GATE_TIMEOUT = 3.0


def _poll_until(predicate, timeout=ASR_TEST_POLL_TIMEOUT):
    """轮询直到 predicate() 返回真值（阻塞至多 timeout 秒）。"""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(ASR_TEST_POLL_INTERVAL)


def _write_silent_wav(path, frames=8000, samplerate=16000) -> Path:
    """标准库 wave 写极短静音 PCM16/mono WAV（无 numpy/FFmpeg 依赖）。"""
    path = Path(path)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(audio_capture.SAMPLE_WIDTH_BYTES["int16"])
        wf.setframerate(samplerate)
        wf.writeframes(b"\x00\x00" * frames)
    return path


class FakeWhisperModel:
    """假 whisper 模型：记录调用、返回配置文本/异常，gate 可阻塞推理时机。

    经 ``asr_whisper.Transcriber(model=fake, ...)`` 注入真实 Transcriber，
    走真实 transcribe_wav → wave 解码 → model.transcribe 链路，但行为可控。
    """

    def __init__(self, text="查看最近的加速度数据", error=None, gate=None) -> None:
        self.text = text
        self.error = error
        self.gate = gate
        self.device = "cpu"
        self.transcribe_calls = 0

    def transcribe(self, audio, **kwargs):
        self.transcribe_calls += 1
        if self.gate is not None:
            self.gate.wait(ASR_TEST_GATE_TIMEOUT)
        if self.error is not None:
            raise self.error
        return {"text": self.text}

    def release(self) -> None:
        if self.gate is not None:
            self.gate.set()


class _ScriptedTranscriber:
    """脚本转录器：按调用序返回 AsrResult 或抛异常（不经文件解码）。"""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls = 0
        self.model_name = "base"
        self.language = "zh"
        self.task = "transcribe"
        self.device = "cpu"

    def transcribe_wav(self, path, language=None, task=None):
        self.calls += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        if item is None:
            return asr_whisper.AsrResult("", "zh", "transcribe", "base", "cpu", 0.0001)
        return item


class _GatedScriptedTranscriber(_ScriptedTranscriber):
    """可被 gate 阻塞的脚本转录器（复现「任务被新提交取代」的时序）。"""

    def __init__(self, script, gate) -> None:
        super().__init__(script)
        self.gate = gate

    def transcribe_wav(self, path, language=None, task=None):
        self.gate.wait(ASR_TEST_GATE_TIMEOUT)
        return super().transcribe_wav(path, language=language)


class FakeAudioCapture:
    """模拟 ``audio_capture.AudioCapture`` 的公开接口（不接触真实设备、不写文件）。

    接口与真实模块保持一致：``is_recording`` / ``frames_recorded`` /
    ``duration_seconds`` / ``start_recording()`` / ``stop_recording()``，并暴露
    配置属性供被测服务读取。
    """

    def __init__(
        self,
        output_dir: Any,
        samplerate: int = 16000,
        channels: int = 1,
        dtype: str = "int16",
        device: Any = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device

        self.start_calls = 0
        self.stop_calls = 0
        self._recording = False
        self._frames = 0

        # 异常/结果注入点（None 表示正常流程）
        self.start_error: Optional[Exception] = None
        self.stop_error: Optional[Exception] = None
        self.stop_result: Optional[RecordingInfo] = None

    # ── 与真实模块同名的只读属性 ─────────────

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def frames_recorded(self) -> int:
        return self._frames

    @property
    def duration_seconds(self) -> float:
        return self._frames / self.samplerate

    # ── 测试辅助 ────────────────────────────

    def simulate_frames(self, frames: int) -> None:
        """模拟「录音中已采集 N 帧」。"""
        self._frames = frames

    # ── 控制接口 ────────────────────────────

    def start_recording(self, output_path: Any = None) -> Path:
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self._recording = True
        self._frames = 0
        return (
            Path(output_path) if output_path is not None else self.output_dir / "fake.wav"
        )

    def stop_recording(self) -> RecordingInfo:
        self.stop_calls += 1
        # 与真实模块一致：失败路径也会把状态复位为 IDLE
        self._recording = False
        if self.stop_error is not None:
            raise self.stop_error
        if self.stop_result is not None:
            return self.stop_result
        return make_info(self.output_dir / "fake.wav")


class VoiceServerTestBase(unittest.TestCase):
    """公共装置：仓库外临时输出目录 + 假采集器 + Flask test client。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory(prefix="voice_srv_test_")
        self.addCleanup(self._tmp.cleanup)
        self.output_dir = Path(self._tmp.name)
        self.capture = FakeAudioCapture(self.output_dir)
        self.app = self.create_server_app()
        self.client = self.app.test_client()

    def create_server_app(self):
        """子类可覆盖以注入额外依赖（Step 3B: 假 ASR 转录器）。"""
        return voice_control_server.create_app(capture=self.capture)

    def post(self, path: str, payload: Optional[dict] = None, **kwargs):
        """POST 辅助：payload 为 None 时不带请求体（模拟 ESP32 极简请求）。"""
        if payload is None:
            return self.client.post(path, **kwargs)
        return self.client.post(path, json=payload, **kwargs)

    def assert_json(self, response, expected_status: int) -> dict:
        """断言状态码与 JSON 响应体，并返回解析后的 dict。"""
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, expected_status, body)
        self.assertTrue(response.is_json, f"响应不是 JSON: {body}")
        data = response.get_json()
        self.assertIsInstance(data, dict)
        return data


# ──────────────────────────────────────────────
# A. start / stop / status 正常路径
# ──────────────────────────────────────────────


class VoiceStartStopStatusTest(VoiceServerTestBase):
    """接口正常路径：状态读写与 WAV 元数据。"""

    def test_status_when_idle(self):
        data = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"], "idle")
        self.assertFalse(data["recording"])
        self.assertEqual(data["frames_recorded"], 0)
        self.assertEqual(data["elapsed_seconds"], 0.0)
        self.assertIsNone(data["target_path"])
        self.assertIsNone(data["last_recording"])
        self.assertEqual(data["config"]["samplerate"], 16000)
        self.assertEqual(data["config"]["channels"], 1)
        self.assertEqual(data["config"]["dtype"], "int16")
        self.assertEqual(Path(data["config"]["output_dir"]), self.output_dir)
        # 查询状态不得触发任何录音动作
        self.assertEqual((self.capture.start_calls, self.capture.stop_calls), (0, 0))

    def test_start_reports_recording_state_and_target_path(self):
        data = self.assert_json(self.post("/api/voice/start"), 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"], "recording")
        self.assertTrue(data["recording"])
        self.assertIn("录音已开始", data["message"])
        self.assertEqual(self.capture.start_calls, 1)
        self.assertEqual(Path(data["target_path"]), self.output_dir / "fake.wav")

    def test_start_with_empty_json_body_is_accepted(self):
        data = self.assert_json(self.post("/api/voice/start", payload={}), 200)
        self.assertTrue(data["recording"])

    def test_status_while_recording(self):
        self.post("/api/voice/start")
        self.capture.simulate_frames(8000)
        data = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual(data["state"], "recording")
        self.assertTrue(data["recording"])
        self.assertEqual(data["frames_recorded"], 8000)
        self.assertEqual(data["elapsed_seconds"], 0.5)
        self.assertEqual(Path(data["target_path"]), self.output_dir / "fake.wav")
        self.assertIsNone(data["last_recording"])

    def test_stop_returns_compact_ack(self):
        """Step 3D：stop 是「控制回执」，只含 ESP32 契约所需的最小关联信息。

        - 保留：ok / state / recording / wav.frames / wav.duration_seconds / asr.status
        - 不返回：完整 wav 元数据（path/samplerate/size_bytes/status_flags/...）、
          ASR 完整快照（text/wav_path/...）、message 长说明——调试信息由 GET /status 提供。
        """
        self.post("/api/voice/start")
        self.capture.stop_result = make_info(
            self.output_dir / "fake.wav",
            frames=16000,
            size_bytes=32044,
            flags=("input overflow",),
        )
        data = self.assert_json(self.post("/api/voice/stop"), 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"], "idle")
        self.assertFalse(data["recording"])
        self.assertEqual(self.capture.stop_calls, 1)

        # 极简关联信息：wav 只含固件契约需要的 frames / duration_seconds
        self.assertEqual(data["wav"], {"frames": 16000, "duration_seconds": 1.0})
        self.assertEqual(list(data["asr"].keys()), ["status"])
        self.assertIn(data["asr"]["status"], {"pending", "running", "success"})

        # 控制接口不得携带完整调试信息
        self.assertNotIn("message", data)
        for heavy_key in (
            "path",
            "samplerate",
            "channels",
            "sample_width_bytes",
            "size_bytes",
            "status_flags",
        ):
            self.assertNotIn(heavy_key, data["wav"])
        for heavy_key in (
            "text",
            "wav_path",
            "model",
            "language",
            "task",
            "device",
            "duration_seconds",
            "error",
        ):
            self.assertNotIn(heavy_key, data["asr"])

        # 完整元数据并未丢失：仍由 GET /status 的 last_recording 提供
        status = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual(status["last_recording"]["status_flags"], ["input overflow"])
        self.assertEqual(Path(status["last_recording"]["path"]), self.output_dir / "fake.wav")

    def test_status_after_stop_exposes_last_recording(self):
        self.post("/api/voice/start")
        self.post("/api/voice/stop")
        data = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual(data["state"], "idle")
        self.assertIsNone(data["target_path"])
        self.assertIsNotNone(data["last_recording"])
        self.assertEqual(data["last_recording"]["frames"], 16000)

    def test_new_start_clears_last_recording(self):
        self.post("/api/voice/start")
        self.post("/api/voice/stop")
        self.post("/api/voice/start")
        data = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual(data["state"], "recording")
        self.assertIsNone(data["last_recording"])

    def test_full_cycle_leaves_no_recording_files(self):
        """start → stop 全流程后，测试输出目录必须仍为空（不产生 WAV/音频文件）。"""
        self.post("/api/voice/start")
        self.capture.simulate_frames(4000)
        self.post("/api/voice/stop")
        self.assertEqual(list(self.output_dir.iterdir()), [])


# ──────────────────────────────────────────────
# B. 调用顺序错误（重复启动 / 未录音就停止）
# ──────────────────────────────────────────────


class VoiceStateOrderTest(VoiceServerTestBase):
    """状态顺序错误必须是可区分的 409，且不得重复触达底层采集器。"""

    def test_duplicate_start_returns_409(self):
        self.post("/api/voice/start")
        data = self.assert_json(self.post("/api/voice/start"), 409)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "already_recording")
        self.assertEqual(data["state"], "recording")
        self.assertEqual(self.capture.start_calls, 1)

    def test_stop_without_recording_returns_409(self):
        data = self.assert_json(self.post("/api/voice/stop"), 409)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "not_recording")
        self.assertEqual(data["state"], "idle")
        self.assertEqual(self.capture.stop_calls, 0)

    def test_second_stop_returns_409(self):
        self.post("/api/voice/start")
        self.post("/api/voice/stop")
        data = self.assert_json(self.post("/api/voice/stop"), 409)
        self.assertEqual(data["error"], "not_recording")
        self.assertEqual(self.capture.stop_calls, 1)


# ──────────────────────────────────────────────
# C. 录音模块抛错的映射
# ──────────────────────────────────────────────


class VoiceCaptureErrorTest(VoiceServerTestBase):
    """audio_capture 的异常族必须映射为稳定的错误码/状态码，且不回退为「假成功」。"""

    def test_device_error_on_start_returns_503_and_stays_idle(self):
        self.capture.start_error = AudioDeviceError("打开录音设备失败（模拟）")
        data = self.assert_json(self.post("/api/voice/start"), 503)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "audio_device_error")
        self.assertEqual(data["exception"], "AudioDeviceError")
        self.assertEqual(data["state"], "idle")
        self.assertFalse(self.capture.is_recording)
        # 失败后必须仍可正常再次 start（不残留半开状态）
        data2 = self.assert_json(self.post("/api/voice/start"), 503)
        self.assertEqual(data2["error"], "audio_device_error")

    def test_stop_after_device_error_is_not_recording(self):
        self.capture.start_error = AudioDeviceError("设备不可用（模拟）")
        self.post("/api/voice/start")
        data = self.assert_json(self.post("/api/voice/stop"), 409)
        self.assertEqual(data["error"], "not_recording")

    def test_empty_recording_on_stop_returns_422(self):
        self.post("/api/voice/start")
        self.capture.stop_error = EmptyRecordingError("录音为空（未采集到任何帧）")
        data = self.assert_json(self.post("/api/voice/stop"), 422)
        self.assertEqual(data["error"], "empty_recording")
        self.assertEqual(data["exception"], "EmptyRecordingError")
        self.assertEqual(data["state"], "idle")
        # 空录音不得被登记为 last_recording，也不得留下待写路径
        status = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertIsNone(status["target_path"])
        self.assertIsNone(status["last_recording"])

    def test_device_error_on_stop_returns_503(self):
        self.post("/api/voice/start")
        self.capture.stop_error = AudioDeviceError("停止录音设备时出错（模拟）")
        data = self.assert_json(self.post("/api/voice/stop"), 503)
        self.assertEqual(data["error"], "audio_device_error")
        self.assertEqual(data["state"], "idle")

    def test_generic_capture_error_on_stop_returns_500(self):
        self.post("/api/voice/start")
        self.capture.stop_error = AudioCaptureError("音频数据不完整（模拟）")
        data = self.assert_json(self.post("/api/voice/stop"), 500)
        self.assertEqual(data["error"], "audio_capture_error")
        self.assertEqual(data["exception"], "AudioCaptureError")

    def test_state_error_from_capture_maps_to_409(self):
        # 模拟竞态：服务预检查通过，但底层采集器拒绝
        self.capture.start_error = RecordingStateError("已有进行中的录音（模拟竞态）")
        data = self.assert_json(self.post("/api/voice/start"), 409)
        self.assertEqual(data["error"], "state_error")
        self.assertEqual(data["exception"], "RecordingStateError")

    def test_unexpected_exception_returns_500_internal_error(self):
        self.capture.start_error = RuntimeError("模拟未预期故障")
        data = self.assert_json(self.post("/api/voice/start"), 500)
        self.assertEqual(data["error"], "internal_error")
        self.assertEqual(data["exception"], "RuntimeError")
        self.assertIn("模拟未预期故障", data["message"])

    def test_status_read_failure_maps_to_500_with_state_none(self):
        with patch.object(
            type(self.capture), "is_recording", property(lambda _self: (_ for _ in ()).throw(RuntimeError("状态读取失败")))
        ):
            data = self.assert_json(self.client.get("/api/voice/status"), 500)
        self.assertEqual(data["error"], "internal_error")
        self.assertIsNone(data["state"])


# ──────────────────────────────────────────────
# D. HTTP/JSON 契约
# ──────────────────────────────────────────────


class VoiceHttpContractTest(VoiceServerTestBase):
    """响应格式契约：JSON、UTF-8 可读中文、请求体校验、404/405 也返回 JSON。"""

    def test_error_response_uses_readable_utf8_chinese(self):
        resp = self.post("/api/voice/stop")
        self.assertEqual(resp.status_code, 409)
        text = resp.get_data(as_text=True)
        self.assertIn("没有进行中的录音", text)
        self.assertNotIn("\\u", text)  # ensure_ascii=False：不转义中文
        self.assertIn("application/json", resp.headers.get("Content-Type", ""))

    def test_invalid_json_body_returns_400(self):
        resp = self.client.post(
            "/api/voice/start", data="{not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_request")
        self.assertEqual(self.capture.start_calls, 0)

    def test_non_object_json_body_returns_400(self):
        resp = self.client.post("/api/voice/start", json=[1, 2, 3])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_request")
        self.assertEqual(self.capture.start_calls, 0)

    def test_unknown_path_returns_json_404(self):
        resp = self.client.get("/api/voice/unknown")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.is_json)
        self.assertEqual(resp.get_json()["error"], "not_found")

    def test_wrong_method_returns_json_405(self):
        resp = self.client.get("/api/voice/start")
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(resp.is_json)
        self.assertEqual(resp.get_json()["error"], "method_not_allowed")

    def test_status_is_read_only_poll_safe(self):
        for _ in range(3):
            self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual((self.capture.start_calls, self.capture.stop_calls), (0, 0))


# ──────────────────────────────────────────────
# E. 默认配置 / 输出目录保护 / CLI
# ──────────────────────────────────────────────


class VoiceConfigAndCliTest(unittest.TestCase):
    """默认监听仅本机、默认输出到 %TEMP%/voice_task、拒绝把 WAV 写进仓库。"""

    def test_default_service_uses_real_audio_capture_and_temp_dir(self):
        app = voice_control_server.create_app()  # 不注入假实现（构造函数不做设备 I/O）
        service = app.extensions["voice_control"]
        self.assertIsInstance(service.capture, audio_capture.AudioCapture)
        self.assertEqual(
            Path(service.capture.output_dir), audio_capture.DEFAULT_OUTPUT_DIR
        )
        self.assertEqual(Path(service.capture.output_dir).name, "voice_task")
        self.assertEqual(service.config()["samplerate"], audio_capture.DEFAULT_SAMPLERATE)

    def test_default_output_dir_is_outside_repository(self):
        resolved = Path(audio_capture.DEFAULT_OUTPUT_DIR).resolve()
        repo = voice_control_server._REPO_ROOT
        self.assertNotEqual(resolved, repo)
        self.assertNotIn(repo, resolved.parents)

    def test_default_host_is_loopback(self):
        self.assertEqual(voice_control_server.DEFAULT_HOST, "127.0.0.1")
        self.assertIn(voice_control_server.DEFAULT_HOST, voice_control_server.LOOPBACK_HOSTS)
        self.assertNotIn("0.0.0.0", voice_control_server.LOOPBACK_HOSTS)
        self.assertEqual(voice_control_server.DEFAULT_PORT, 8080)

    def test_ensure_output_outside_repo_allows_temp_dir(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(
                voice_control_server.ensure_output_outside_repo(tmp), Path(tmp).resolve()
            )

    def test_output_dir_inside_repo_is_rejected(self):
        inside = voice_control_server._REPO_ROOT / "service"
        with self.assertRaises(voice_control_server.VoiceControlConfigError) as ctx:
            voice_control_server.create_app(output_dir=inside)
        self.assertIn("仓库内", str(ctx.exception))

    def test_repo_root_itself_is_rejected(self):
        with self.assertRaises(voice_control_server.VoiceControlConfigError):
            voice_control_server.ensure_output_outside_repo(voice_control_server._REPO_ROOT)

    def test_capture_and_output_dir_together_rejected(self):
        with TemporaryDirectory() as tmp:
            capture = FakeAudioCapture(tmp)
            with self.assertRaises(voice_control_server.VoiceControlConfigError):
                voice_control_server.create_app(capture=capture, output_dir=tmp)

    def test_injected_capture_output_dir_is_also_checked(self):
        with self.assertRaises(voice_control_server.VoiceControlConfigError):
            voice_control_server.create_app(
                capture=FakeAudioCapture(voice_control_server._REPO_ROOT / "service")
            )

    def test_cli_defaults_to_loopback_and_disables_reloader(self):
        with patch.object(voice_control_server.Flask, "run") as run_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = voice_control_server.main([])
        self.assertEqual(code, 0)
        self.assertEqual(run_mock.call_count, 1)
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 8080)
        self.assertFalse(kwargs["debug"])
        self.assertFalse(kwargs["use_reloader"])
        self.assertNotIn("警告", buf.getvalue())

    def test_cli_warns_when_host_is_not_loopback(self):
        with patch.object(voice_control_server.Flask, "run") as run_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                voice_control_server.main(["--host", "0.0.0.0", "--port", "8099"])
        self.assertEqual(run_mock.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run_mock.call_args.kwargs["port"], 8099)
        out = buf.getvalue()
        self.assertIn("警告", out)
        self.assertIn("0.0.0.0", out)

    def test_cli_converts_numeric_device_and_forwards_output_dir(self):
        created = {}
        real_cls = voice_control_server.audio_capture.AudioCapture

        def spy(**kwargs):
            created.update(kwargs)
            return real_cls(**kwargs)

        with TemporaryDirectory() as tmp:
            with patch.object(voice_control_server.audio_capture, "AudioCapture", spy):
                with patch.object(voice_control_server.Flask, "run"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        voice_control_server.main(
                            [
                                "--device",
                                "3",
                                "--samplerate",
                                "48000",
                                "--output-dir",
                                tmp,
                            ]
                        )
        self.assertEqual(created["device"], 3)
        self.assertEqual(created["samplerate"], 48000)
        self.assertEqual(Path(created["output_dir"]), Path(tmp).resolve())

    def test_cli_rejects_repo_output_dir(self):
        with self.assertRaises(voice_control_server.VoiceControlConfigError):
            with contextlib.redirect_stdout(io.StringIO()):
                voice_control_server.main(
                    ["--output-dir", str(voice_control_server._REPO_ROOT / "service")]
                )

    def test_cli_help_renders_without_crash(self):
        """回归保护：argparse 的 help 文本中字面量 % 必须转义为 %%（曾导致 --help 崩溃）。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                voice_control_server.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        out = buf.getvalue()
        self.assertIn("%TEMP%/voice_task/", out)
        self.assertIn("仅本机", out)

    def test_cli_does_not_open_flask_server_without_patching(self):
        """显式确认 main() 的一切网络行为都被测试屏蔽（不真实监听端口）。"""
        with patch.object(voice_control_server.Flask, "run") as run_mock:
            with contextlib.redirect_stdout(io.StringIO()):
                voice_control_server.main([])
        run_mock.assert_called_once()


# ──────────────────────────────────────────────
# Week 05 Step 3B — 后台 ASR 集成测试
# ──────────────────────────────────────────────


class VoiceAsrServerTestBase(VoiceServerTestBase):
    """服务级 ASR 测试基类：注入假模型包装的真实 Transcriber。"""

    default_asr_text = "查看最近的加速度数据"

    def make_model(self, text=None, error=None, gate=None) -> FakeWhisperModel:
        return FakeWhisperModel(
            text=text or self.default_asr_text, error=error, gate=gate
        )

    def build_transcriber(self, model) -> asr_whisper.Transcriber:
        return asr_whisper.Transcriber(
            model=model,
            model_name=asr_whisper.DEFAULT_MODEL,
            device="cpu",
            language=asr_whisper.DEFAULT_LANGUAGE,
            task=asr_whisper.DEFAULT_TASK,
        )

    def create_server_app(self):
        self._asr_model = self.make_model()
        return voice_control_server.create_app(
            capture=self.capture, asr=self.build_transcriber(self._asr_model)
        )

    @property
    def model(self) -> FakeWhisperModel:
        return self._asr_model

    def make_wav(self, name="rec.wav", frames=8000) -> Path:
        return _write_silent_wav(self.output_dir / name, frames=frames)

    def record_and_stop(self, wav_name="rec.wav", frames=8000):
        """start → 模拟采集 → stop，返回 (wav_path, stop_payload)。"""
        wav = self.make_wav(wav_name, frames=frames)
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.simulate_frames(frames)
        self.capture.stop_result = make_info(
            wav, frames=frames, size_bytes=frames * 2 + 44
        )
        stop_data = self.assert_json(self.post("/api/voice/stop"), 200)
        return wav, stop_data

    def status(self) -> dict:
        return self.assert_json(self.client.get("/api/voice/status"), 200)

    def poll_asr(self, predicate=None, timeout=ASR_TEST_POLL_TIMEOUT):
        """轮询 GET /status 的 asr 快照直到命中；返回快照 dict（超时返回 None）。"""
        if predicate is None:
            predicate = lambda a: True  # noqa: E731

        last = [None]

        def _probe():
            last[0] = self.status()["asr"]
            return last[0] if predicate(last[0]) else None

        return _poll_until(_probe, timeout=timeout)


class VoiceAsrAutoSubmitTest(VoiceAsrServerTestBase):
    """自动提交 + 后台状态转换 + 成功路径。"""

    def test_stop_auto_submits_and_status_reports_success(self):
        wav, stop_data = self.record_and_stop("auto.wav")
        self.assertTrue(stop_data["ok"])
        self.assertEqual(stop_data["state"], "idle")
        self.assertEqual(stop_data["recording"], False)
        # Step 3D：stop 回执只含 asr.status（不携带识别文本 / wav 路径）；
        # 任务与录音的关联（wav_path）改由 GET /status 的 asr 块查询
        self.assertIn(stop_data["asr"]["status"], {"pending", "running", "success"})
        self.assertEqual(list(stop_data["asr"].keys()), ["status"])
        self.assertEqual(self.status()["asr"]["wav_path"], str(wav))
        self.assertLessEqual(self.model.transcribe_calls, 1)

        asr = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(asr)
        self.assertEqual(asr["text"], self.default_asr_text)
        self.assertEqual(asr["model"], "base")
        self.assertEqual(asr["language"], "zh")
        self.assertEqual(asr["task"], "transcribe")
        self.assertEqual(asr["device"], "cpu")
        self.assertEqual(Path(asr["wav_path"]), wav)
        self.assertIsNotNone(asr["duration_seconds"])
        self.assertIsNone(asr["error"])
        self.assertEqual(self.model.transcribe_calls, 1)
        # 录音停止事实保持
        data = self.status()
        self.assertEqual(data["state"], "idle")
        self.assertIsNotNone(data["last_recording"])

    def test_stop_returns_immediately_while_model_is_blocked(self):
        gate = threading.Event()
        model = self.make_model(gate=gate)
        app = voice_control_server.create_app(
            capture=self.capture, asr=self.build_transcriber(model)
        )
        self.client = app.test_client()
        wav = self.make_wav("blocked.wav")
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.simulate_frames(8000)
        self.capture.stop_result = make_info(wav, frames=8000)

        started = time.monotonic()
        stop_data = self.assert_json(self.post("/api/voice/stop"), 200)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0, "POST /api/voice/stop 不得阻塞在模型加载/推理上")
        self.assertIn(stop_data["asr"]["status"], {"pending", "running"})

        # 模型阻塞在 gate 上：任务处于 running（后台线程持有），HTTP 已返回
        running = self.poll_asr(lambda a: a["status"] == "running")
        self.assertIsNotNone(running)
        model.release()
        final = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(final)
        self.assertEqual(final["text"], self.default_asr_text)
        self.assertEqual(model.transcribe_calls, 1)

    def test_repeated_status_poll_does_not_trigger_reinference(self):
        self.record_and_stop("poll.wav")
        asr = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(asr)
        for _ in range(5):
            data = self.status()
            self.assertEqual(data["asr"]["status"], "success")
            self.assertEqual(data["asr"]["text"], asr["text"])
        self.assertEqual(self.model.transcribe_calls, 1)


class VoiceAsrFailureTest(VoiceAsrServerTestBase):
    """识别失败时录音停止事实不受影响、如实报告 ASR 失败。"""

    def test_inference_failure_keeps_recording_stopped(self):
        model = self.make_model(error=RuntimeError("模拟推理异常"))
        app = voice_control_server.create_app(
            capture=self.capture, asr=self.build_transcriber(model)
        )
        self.client = app.test_client()

        wav, stop_data = self.record_and_stop("fail.wav")
        self.assertTrue(stop_data["ok"])
        self.assertEqual(stop_data["state"], "idle")
        # Step 3D：stop 回执不含 wav_path；关联信息由 status 的 asr 块提供
        self.assertEqual(list(stop_data["asr"].keys()), ["status"])
        self.assertEqual(self.status()["asr"]["wav_path"], str(wav))

        asr = self.poll_asr(lambda a: a["status"] == "failed")
        self.assertIsNotNone(asr)
        self.assertIsNone(asr["text"])
        self.assertIn("AsrInferenceError", asr["error"])
        self.assertIn("模拟推理异常", asr["error"])
        self.assertEqual(model.transcribe_calls, 1)
        # 录音停止成功不受 ASR 失败影响
        data = self.status()
        self.assertEqual(data["state"], "idle")
        self.assertFalse(data["recording"])
        self.assertIsNotNone(data["last_recording"])

    def test_model_load_failure_is_reported_as_failed(self):
        def failing_loader(name, device):
            raise asr_whisper.AsrModelError(f"模拟模型加载失败 {name}@{device}")

        transcriber = asr_whisper.Transcriber(
            model=None, model_name="base", device="cpu", model_loader=failing_loader
        )
        app = voice_control_server.create_app(capture=self.capture, asr=transcriber)
        self.client = app.test_client()
        self.record_and_stop("loadfail.wav")

        asr = self.poll_asr(lambda a: a["status"] == "failed")
        self.assertIsNotNone(asr)
        self.assertIn("AsrModelError", asr["error"])
        self.assertIn("模拟模型加载失败", asr["error"])
        self.assertIsNone(asr["text"])
        data = self.status()
        self.assertEqual(data["state"], "idle")
        self.assertIsNotNone(data["last_recording"])

    def test_missing_wav_is_reported_as_failed_before_model_load(self):
        # 真实默认 Transcriber：文件不存在在模型加载之前抛 AsrInputError，
        # 证明缺失文件不会触发模型权重加载/下载（测试不下载任何权重）。
        transcriber = asr_whisper.Transcriber()
        app = voice_control_server.create_app(capture=self.capture, asr=transcriber)
        self.client = app.test_client()
        self.post("/api/voice/start")
        self.capture.simulate_frames(8000)
        self.capture.stop_result = make_info(
            self.output_dir / "no_such.wav", frames=8000, size_bytes=16044
        )
        stop_data = self.assert_json(self.post("/api/voice/stop"), 200)
        self.assertTrue(stop_data["ok"])

        asr = self.poll_asr(lambda a: a["status"] == "failed")
        self.assertIsNotNone(asr)
        self.assertIn("AsrInputError", asr["error"])
        self.assertIn("不存在", asr["error"])
        self.assertIsNone(asr["text"])
        self.assertEqual(self.status()["state"], "idle")


class VoiceAsrDuplicateAndOrderTest(VoiceAsrServerTestBase):
    """重复请求、空录音不提交、新提交取代在途任务。"""

    def test_stop_without_recording_does_not_submit_asr(self):
        data = self.assert_json(self.post("/api/voice/stop"), 409)
        self.assertEqual(data["error"], "not_recording")
        self.assertEqual(self.model.transcribe_calls, 0)
        self.assertEqual(self.status()["asr"]["status"], "idle")

    def test_empty_recording_does_not_submit_asr(self):
        self.post("/api/voice/start")
        self.capture.stop_error = EmptyRecordingError("录音为空（模拟）")
        data = self.assert_json(self.post("/api/voice/stop"), 422)
        self.assertEqual(data["error"], "empty_recording")
        self.assertEqual(self.model.transcribe_calls, 0)
        self.assertEqual(self.status()["asr"]["status"], "idle")

    def test_second_stop_after_success_does_not_resubmit(self):
        self.record_and_stop("dup.wav")
        self.poll_asr(lambda a: a["status"] == "success")
        data = self.assert_json(self.post("/api/voice/stop"), 409)
        self.assertEqual(data["error"], "not_recording")
        self.assertEqual(self.model.transcribe_calls, 1)

    def test_new_stop_supersedes_previous_asr_task(self):
        gate = threading.Event()
        model = self.make_model(text="第二次录音的识别文本", gate=gate)
        app = voice_control_server.create_app(
            capture=self.capture, asr=self.build_transcriber(model)
        )
        self.client = app.test_client()

        wav1, _ = self.record_and_stop("first.wav")
        running = self.poll_asr(lambda a: a["status"] == "running")
        self.assertIsNotNone(running, "第一次任务应阻塞在推理中")

        wav2, stop2 = self.record_and_stop("second.wav")
        # 新任务取代旧任务：状态回到 pending；wav_path 改为查询 status（Step 3D）
        self.assertEqual(stop2["asr"]["status"], "pending")
        self.assertEqual(self.status()["asr"]["wav_path"], str(wav2))

        gate.set()
        final = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(final)
        self.assertEqual(Path(final["wav_path"]), wav2)
        self.assertEqual(final["text"], "第二次录音的识别文本")
        self.assertEqual(model.transcribe_calls, 2)


class VoiceAsrCompatibilityTest(VoiceAsrServerTestBase):
    """ASR 字段与既有录音状态兼容性。"""

    def test_idle_status_has_complete_asr_block(self):
        data = self.status()
        asr = data["asr"]
        self.assertEqual(asr["status"], "idle")
        for key in (
            "text",
            "wav_path",
            "model",
            "language",
            "task",
            "device",
            "duration_seconds",
            "error",
        ):
            self.assertIsNone(asr[key])
        # 既有录音字段仍在
        self.assertEqual(data["state"], "idle")
        self.assertIsNotNone(data["config"])
        self.assertIsNone(data["last_recording"])

    def test_default_app_uses_base_zh_transcribe(self):
        app = voice_control_server.create_app()
        service = app.extensions["voice_control"]
        tr = service.asr_runner.transcriber
        self.assertEqual(tr.model_name, asr_whisper.DEFAULT_MODEL)
        self.assertEqual(tr.language, asr_whisper.DEFAULT_LANGUAGE)
        self.assertEqual(tr.task, asr_whisper.DEFAULT_TASK)

    def test_asr_and_runner_are_mutually_exclusive(self):
        runner = voice_control_server.AsrTaskRunner(transcriber=None)
        with TemporaryDirectory() as tmp:
            capture = FakeAudioCapture(tmp)
            with self.assertRaises(voice_control_server.VoiceControlConfigError):
                voice_control_server.VoiceControlService(
                    capture=capture, asr=object(), asr_task_runner=runner
                )


class VoiceStopContractTest(VoiceAsrServerTestBase):
    """Step 3D — 控制/查询接口解耦：stop 是紧凑回执，完整信息由 status 提供。

    - 成功与失败响应的 UTF-8 字节数都不得超过 ESP32 固件 512B 响应缓冲
      （``voice_button.c`` 的 ``VOICE_RESP_BUF_SIZE``；Step 3C 实测 stop 携带完整
      asr 块后被截断为 ``len=511, truncated=1`` → 固件判定 RESPONSE_INVALID）。
    - stop 依旧触发后台 ASR；ASR 结果（text 等）通过 GET /status 查询。
    """

    @staticmethod
    def _raw_bytes(resp) -> bytes:
        """Flask test client 响应体的原始 UTF-8 字节（不含 HTTP 头）。"""
        return resp.get_data(as_text=False)

    def assert_fits_esp32_buffer(self, resp) -> None:
        self.assertLessEqual(
            len(self._raw_bytes(resp)),
            voice_control_server.ESP32_RESP_BUFFER_BYTES,
            f"响应 {len(self._raw_bytes(resp))}B 超过 ESP32 "
            f"{voice_control_server.ESP32_RESP_BUFFER_BYTES}B 响应缓冲",
        )

    def test_stop_success_response_fits_esp32_buffer(self):
        # 用真实联调量级（38.7s @16kHz ≈ 619200 帧）验证字节数上限
        wav = self.make_wav("size.wav", frames=619200)
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.simulate_frames(619200)
        self.capture.stop_result = make_info(
            wav, frames=619200, size_bytes=619200 * 2 + 44
        )
        resp = self.post("/api/voice/stop")
        data = self.assert_json(resp, 200)
        self.assertEqual(data["state"], "idle")
        self.assertEqual(data["wav"]["frames"], 619200)
        self.assert_fits_esp32_buffer(resp)
        # 等后台 ASR 读完该 WAV 再结束测试，避免 Windows 下临时目录清理竞争占用
        final = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(final, "stop 后后台 ASR 应正常执行完成")

    def test_stop_failure_responses_fit_esp32_buffer(self):
        # 409 not_recording：失败契约与旧版一致，且体积同样受控
        resp = self.post("/api/voice/stop")
        data = self.assert_json(resp, 409)
        self.assertEqual(data["error"], "not_recording")
        self.assert_fits_esp32_buffer(resp)

        # 422 empty_recording
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.stop_error = EmptyRecordingError("录音为空（模拟）")
        resp = self.post("/api/voice/stop")
        data = self.assert_json(resp, 422)
        self.assertEqual(data["error"], "empty_recording")
        self.assert_fits_esp32_buffer(resp)

    def test_stop_success_still_triggers_background_asr(self):
        # 解耦后 stop 仍触发后台 ASR；识别结果通过 GET /status 查询
        wav, stop_data = self.record_and_stop("trigger.wav")
        self.assertTrue(stop_data["ok"])
        self.assertIn(stop_data["asr"]["status"], {"pending", "running", "success"})
        self.assertEqual(list(stop_data["asr"].keys()), ["status"])

        asr = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(asr)
        self.assertEqual(asr["text"], self.default_asr_text)
        self.assertEqual(asr["wav_path"], str(wav))
        self.assertEqual(asr["model"], "base")
        self.assertEqual(asr["language"], "zh")
        self.assertGreaterEqual(self.model.transcribe_calls, 1)

    def test_status_after_stop_returns_full_debug_info(self):
        # GET /status 继续提供完整调试信息（wav 元数据 + ASR 全字段），不受解耦影响
        wav, stop_data = self.record_and_stop("full.wav", frames=16000)
        self.assertNotIn("message", stop_data)

        data = self.status()
        self.assertEqual(data["state"], "idle")
        lr = data["last_recording"]
        self.assertIsNotNone(lr)
        self.assertEqual(lr["frames"], 16000)
        self.assertEqual(lr["samplerate"], 16000)
        self.assertEqual(lr["channels"], 1)
        self.assertEqual(lr["size_bytes"], 16000 * 2 + 44)
        self.assertEqual(Path(lr["path"]), wav)

        asr = self.poll_asr(lambda a: a["status"] == "success")
        self.assertIsNotNone(asr)
        self.assertEqual(asr["text"], self.default_asr_text)
        self.assertIsInstance(asr["duration_seconds"], float)


class AsrTaskRunnerUnitTest(unittest.TestCase):
    """AsrTaskRunner 纯单元测试：脚本转录器直接驱动，不经 HTTP。"""

    def test_idle_snapshot_has_complete_shape(self):
        runner = voice_control_server.AsrTaskRunner(transcriber=None)
        snap = runner.snapshot()
        self.assertEqual(snap["status"], "idle")
        self.assertIsNone(snap["text"])
        for key in (
            "wav_path",
            "model",
            "language",
            "task",
            "device",
            "duration_seconds",
            "error",
        ):
            self.assertIsNone(snap[key])

    def test_success_publishes_text_and_meta(self):
        tr = _ScriptedTranscriber(
            [asr_whisper.AsrResult("你好世界", "zh", "transcribe", "base", "cpu", 0.25)]
        )
        runner = voice_control_server.AsrTaskRunner(transcriber=tr)
        runner.submit("dummy.wav")
        final = _poll_until(
            lambda: runner.snapshot()
            if runner.snapshot()["status"] == "success"
            else None
        )
        self.assertIsNotNone(final)
        self.assertEqual(final["text"], "你好世界")
        self.assertEqual(final["wav_path"], "dummy.wav")
        self.assertEqual(final["model"], "base")
        self.assertEqual(final["device"], "cpu")
        self.assertEqual(final["duration_seconds"], 0.25)
        self.assertIsNone(final["error"])
        self.assertEqual(tr.calls, 1)

    def test_failure_publishes_error_without_text(self):
        tr = _ScriptedTranscriber([asr_whisper.AsrInferenceError("模拟推理异常")])
        runner = voice_control_server.AsrTaskRunner(transcriber=tr)
        runner.submit("bad.wav")
        final = _poll_until(
            lambda: runner.snapshot()
            if runner.snapshot()["status"] == "failed"
            else None
        )
        self.assertIsNotNone(final)
        self.assertIsNone(final["text"])
        self.assertIn("AsrInferenceError", final["error"])
        self.assertEqual(tr.calls, 1)

    def test_dependency_error_is_reported_as_failed(self):
        tr = _ScriptedTranscriber([asr_whisper.AsrDependencyError("缺少 whisper 依赖")])
        runner = voice_control_server.AsrTaskRunner(transcriber=tr)
        runner.submit("dep.wav")
        final = _poll_until(
            lambda: runner.snapshot()
            if runner.snapshot()["status"] == "failed"
            else None
        )
        self.assertIsNotNone(final)
        self.assertIn("AsrDependencyError", final["error"])

    def test_latest_submit_wins_and_stale_result_is_discarded(self):
        gate = threading.Event()
        tr = _GatedScriptedTranscriber(
            [
                asr_whisper.AsrResult("旧任务文本", "zh", "transcribe", "base", "cpu", 0.1),
                asr_whisper.AsrInferenceError("新任务失败"),
            ],
            gate,
        )
        runner = voice_control_server.AsrTaskRunner(transcriber=tr)
        runner.submit("old.wav")
        running = _poll_until(
            lambda: runner.snapshot()
            if runner.snapshot()["status"] == "running"
            else None
        )
        self.assertIsNotNone(running, "旧任务应先进入 running 并被 gate 阻塞")

        runner.submit("new.wav")
        self.assertEqual(runner.snapshot()["status"], "pending")
        self.assertEqual(runner.snapshot()["wav_path"], "new.wav")
        gate.set()

        def _observe():
            snap = runner.snapshot()
            self.assertNotEqual(
                (snap["status"], snap["text"]),
                ("success", "旧任务文本"),
                "过期任务的成功结果不得被发布",
            )
            return snap if snap["status"] == "failed" else None

        final = _poll_until(_observe)
        self.assertIsNotNone(final)
        self.assertEqual(final["wav_path"], "new.wav")
        self.assertIn("新任务失败", final["error"])
        self.assertIsNone(final["text"])
        self.assertEqual(tr.calls, 2)

    def test_fail_submit_sets_failed_without_touching_recording(self):
        runner = voice_control_server.AsrTaskRunner(transcriber=None)
        runner.fail_submit("提交路径异常")
        snap = runner.snapshot()
        self.assertEqual(snap["status"], "failed")
        self.assertIsNone(snap["text"])
        self.assertIn("提交路径异常", snap["error"])

# ═══════════════════════════════════════════════════════════════
# Week 06: 任务上下文、取消、澄清与迟到结果隔离测试
# ═══════════════════════════════════════════════════════════════


class Week06TaskContextTest(VoiceServerTestBase):
    """Week 06: request_id / 状态机 / 状态查询。"""

    def test_status_includes_task_block_when_idle(self):
        data = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertIn("task", data)
        task = data["task"]
        self.assertIsNone(task["request_id"])
        self.assertEqual(task["state"], "idle")
        self.assertEqual(task["generation"], 0)
        self.assertIsNone(task["result"])

    def test_start_generates_request_id(self):
        data = self.assert_json(self.post("/api/voice/start"), 200)
        self.assertIn("request_id", data)
        self.assertTrue(data["request_id"].startswith("voice-"))
        self.assertEqual(data["task_state"], "recording")

    def test_stop_enters_processing_then_task_log(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.simulate_frames(8000)
        self.capture.stop_result = make_info(self.output_dir / "t1.wav", frames=8000)
        data = self.assert_json(self.post("/api/voice/stop"), 200)
        self.assertEqual(data["task_state"], "processing")
        s = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertGreaterEqual(len(s["task"]["log"]), 1)

    def test_request_id_unique_across_starts(self):
        d1 = self.assert_json(self.post("/api/voice/start"), 200)
        self.post("/api/voice/stop")
        d2 = self.assert_json(self.post("/api/voice/start"), 200)
        self.assertNotEqual(d1["request_id"], d2["request_id"])


class Week06CancelTest(VoiceServerTestBase):
    """Week 06: 取消测试。"""

    def test_cancel_no_task_returns_error(self):
        data = self.assert_json(self.post("/api/voice/cancel"), 409)
        self.assertEqual(data["error"], "no_active_task")

    def test_cancel_during_recording(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.simulate_frames(4000)
        data = self.assert_json(self.post("/api/voice/cancel"), 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"], "cancelled")
        self.assertTrue(data["cancelled_recording"])

    def test_double_cancel_idempotent(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        self.assert_json(self.post("/api/voice/cancel"), 200)
        self.assert_json(self.post("/api/voice/cancel"), 409)
class Week06StatusCompatibilityTest(VoiceServerTestBase):
    """Week 06: 状态接口兼容性。"""

    def test_existing_fields_preserved(self):
        data = self.assert_json(self.client.get("/api/voice/status"), 200)
        for key in ("ok", "state", "recording", "frames_recorded",
                     "elapsed_seconds", "config", "asr"):
            self.assertIn(key, data)

    def test_full_lifecycle_task_fields(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        self.capture.simulate_frames(8000)
        self.capture.stop_result = make_info(self.output_dir / "f1.wav", frames=8000)
        self.post("/api/voice/stop")
        s = self.assert_json(self.client.get("/api/voice/status"), 200)
        task = s["task"]
        self.assertIn("request_id", task)
        self.assertIn("generation", task)
        self.assertGreaterEqual(len(task["log"]), 1)


class Week06ClarifyTest(VoiceAsrServerTestBase):
    """Week 06: 澄清/重新输入。"""

    def test_clarify_no_task_returns_error(self):
        data = self.assert_json(self.post("/api/voice/clarify", {"text": "x"}), 409)
        self.assertEqual(data["error"], "no_active_task")

    def test_clarify_empty_text(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        self.post("/api/voice/stop")
        self.assert_json(self.post("/api/voice/clarify", {"text": "   "}), 400)

    def test_new_task_after_cancel(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        self.assert_json(self.post("/api/voice/cancel"), 200)
        data = self.assert_json(self.post("/api/voice/start"), 200)
        self.assertEqual(data["task_state"], "recording")

if __name__ == "__main__":
    unittest.main(verbosity=2)
class Week06LateResultIsolationTest(unittest.TestCase):
    """Week 06: 迟到结果隔离——取消后旧任务不能覆盖新任务。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.capture = FakeAudioCapture(Path(self.tmp.name))
        self._model = FakeWhisperModel(text="查看最近的加速度数据")
        tr = asr_whisper.Transcriber(model=self._model, model_name="base",
                                     language="zh", task="transcribe", device="cpu")
        self.svc = voice_control_server.VoiceControlService(
            capture=self.capture, asr=tr)

    def test_cancel_increments_generation(self):
        self.svc.start()
        g0 = self.svc._task_generation
        self.svc.cancel()
        self.assertGreater(self.svc._task_generation, g0)

    def test_new_task_after_cancel_has_clean_slate(self):
        self.svc.start()
        rid1 = self.svc._task_id
        self.svc.cancel()
        self.svc.start()
        rid2 = self.svc._task_id
        self.assertNotEqual(rid1, rid2)
        s = self.svc.status()
        self.assertEqual(s["task"]["state"], "recording")
        self.assertIsNone(s["task"]["context"])

    def test_cancelled_state_persists(self):
        self.svc.start()
        self.svc.cancel()
        s = self.svc.status()
        self.assertEqual(s["task"]["state"], "cancelled")

    def test_cancel_then_full_lifecycle(self):
        self.svc.start()
        self.svc.cancel()
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        self.capture.stop_result = make_info(Path(self.tmp.name) / "lt.wav", frames=8000)
        self.svc.stop()
        s = self.svc.status()
        self.assertEqual(s["task"]["request_id"], rid)
        self.assertNotEqual(s["task"]["state"], "cancelled")
# ──────────────────────────────────────────────
# H. Week 06 原子验收：慢响应 / 连续查询 / 迟到结果隔离
#    （全部 Mock + 隔离上下文 + 受控延迟；不启动麦克风、不触发真实 ASR、
#      不请求 ESP32、不产生真实 query_last/trigger_collect 副作用、不调用外部服务）
# ──────────────────────────────────────────────


class _StubPipeline:
    """受控的 ``task_pipeline.run_task`` 替身（Mock，不触达真实 dispatch/工具/HTTP）。

    序列项为 ``(delay_seconds, result)``；result 可为 dict / str / 异常。
    - 普通慢响应：给 ``delay_seconds>0``，并用 ``time.monotonic()`` 记录模拟耗时。
    - 迟到交错：传 ``gate=threading.Event()``，run_task 会先登记调用再等待 gate，
      由测试在「已取消 / 已开新任务」后再 set 释放 → 精确复现「旧结果晚到」。

    ``calls`` / ``elapsed_s`` 仅用于验证受控延迟与调用次数，属**模拟指标**，
    不代表真实语音体验数据（真实首反馈/完成计时在生产侧尚未实现）。
    """

    def __init__(self, sequence=None, gate=None, gate_timeout=20.0):
        self._seq = list(sequence or [])
        self._gate = gate
        self._gate_timeout = gate_timeout
        self.calls = []
        self.elapsed_s = []

    def run_task(self, raw):
        started = time.monotonic()
        # 先进门：确保「管线已进入、正在阻塞」可被观察，测试据此再做取消/新任务
        self.calls.append(raw)
        if self._gate is not None:
            self._gate.wait(self._gate_timeout)
        if self._seq:
            item = self._seq.pop(0)
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], (int, float))
            ):
                delay, result = item
            else:
                delay, result = 0.0, item
            if delay:
                time.sleep(delay)
        else:
            result = {"ok": False, "error": "unsupported"}
        self.elapsed_s.append(time.monotonic() - started)
        if isinstance(result, Exception):
            raise result
        return result


class Week06SlowAndLateResultAcceptanceTest(unittest.TestCase):
    """Week 06 原子验收：连续查询各自关联、迟到结果隔离、取消后丢弃、
    慢响应×取消交错的生命周期与日志关联、计时能力（模拟指标与生产事实分开）。

    验收对照：
      1. 连续两个任务：每个 ``request_id`` 正确关联各自结果；
      2. 前一个任务迟到返回：不覆盖新任务状态、不触发过期副作用；
      3. 用户取消处理中任务：迟到结果被安全丢弃；
      4. 慢响应与取消交错：状态机 / generation / 新任务生命周期保持一致；
      5. start / cancel / completed / 迟到丢弃日志关联正确 request_id 与 generation；
      6. 计时：生产侧实际存在的计时设施如实断言（task.log 的 wall-clock 毫秒 ts、
         ASR duration_seconds）；模拟慢响应用 ``time.monotonic()`` 度量并明确
         记作「模拟指标」，不冒充真实语音体验数据。
    """

    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="week06_accept_")
        self.addCleanup(self.tmp.cleanup)
        self.capture = FakeAudioCapture(Path(self.tmp.name))
        model = FakeWhisperModel(text="查看最近的加速度数据")
        tr = asr_whisper.Transcriber(
            model=model, model_name="base", language="zh",
            task="transcribe", device="cpu",
        )
        self.svc = voice_control_server.VoiceControlService(
            capture=self.capture, asr=tr
        )
        self.addCleanup(self._join_asr_worker)

    def _join_asr_worker(self):
        """结束前等待 ASR 工作线程退出，避免线程泄漏 / 临时目录清理竞争。"""
        runner = getattr(self.svc, "_asr_runner", None)
        thread = getattr(runner, "_thread", None) if runner is not None else None
        if thread is not None and thread.is_alive():
            thread.join(timeout=ASR_TEST_POLL_TIMEOUT)

    def _poll_state(self, svc, state, timeout=ASR_TEST_POLL_TIMEOUT):
        self.assertTrue(
            _poll_until(lambda: svc._task_state == state, timeout),
            f"等待任务状态 -> {state} 超时；当前 {svc._task_state}",
        )

    def _wait_discard(self, request_id, generation, svc=None):
        svc = self.svc if svc is None else svc

        def _found():
            return any(
                e.get("event") == "result_stale_discarded"
                and e.get("request_id") == request_id
                and e.get("generation") == generation
                for e in svc._task_log
            )

        return _poll_until(_found, ASR_TEST_POLL_TIMEOUT)

    def _full_task_once(self, result):
        """完整驱动一个任务：start → stop →（受控 run_task 返回 result）→ COMPLETED。

        返回 (request_id, _StubPipeline)。
        """
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline([(0.0, result)])
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(
                self.svc, voice_control_server.TASK_STATE_COMPLETED
            )
        return rid, pip
# ── 验收 1：连续查询各关联各自结果 ──────────────────────────

    def test_two_consecutive_tasks_results_bound_to_own_request_id(self):
        """连续两个任务：各自的 request_id ↔ 各自结果，互不复用 / 不串台。"""
        r1 = {"ok": True, "intent": "query_last",
              "sentence": "R1", "observation": "acc 12.3"}
        rid1, pip1 = self._full_task_once(r1)
        s1 = self.svc.status()["task"]
        self.assertEqual(s1["request_id"], rid1)
        self.assertEqual(s1["state"], voice_control_server.TASK_STATE_COMPLETED)
        self.assertEqual(s1["result"]["sentence"], "R1")
        self.assertEqual(s1["context"]["round"], 1)
        self.assertEqual(len(pip1.calls), 1, "管线只应收到任务1的一次调用")
        task1 = json.loads(pip1.calls[0])
        self.assertEqual(task1["original"], "查看最近的加速度数据")
        self.assertEqual(task1["intent"], "query_last")

        r2 = {"ok": True, "intent": "trigger_collect", "sentence": "R2", "done": True}
        rid2, pip2 = self._full_task_once(r2)
        self.assertNotEqual(rid2, rid1, "连续任务必须生成不同 request_id")
        s2 = self.svc.status()["task"]
        self.assertEqual(s2["request_id"], rid2)
        self.assertEqual(s2["state"], voice_control_server.TASK_STATE_COMPLETED)
        self.assertEqual(s2["result"]["sentence"], "R2")
        self.assertNotEqual(s2["result"], s1["result"], "结果不得串台")
        self.assertEqual(s2["context"]["round"], 1, "新任务上下文应独立清零")
        self.assertEqual(len(pip2.calls), 1)
        task2 = json.loads(pip2.calls[0])
        self.assertEqual(task2["original"], "查看最近的加速度数据")
        self.assertEqual(task2["intent"], "query_last")

    # ── 验收 2 + 4：慢响应×取消交错，旧结果不得覆盖新任务 ────────

    def test_old_task_late_result_does_not_overwrite_new_task(self):
        """旧任务迟到返回：只留 result_stale_discarded，不改写新任务状态/结果，
        且新任务仍能独立完成（生命周期一致）。"""
        gate = threading.Event()
        old_pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "LATE_RESULT"})],
            gate=gate,
        )
        with patch("service.task_pipeline.run_task", old_pip.run_task):
            # 任务1：慢响应（管线已被 gate 阻塞）
            self.svc.start()
            rid1 = self.svc._task_id
            gen1 = self.svc._task_generation
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / f"{rid1}.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / f"{rid1}.wav", frames=8000
            )
            self.svc.stop()
            self.assertTrue(
                _poll_until(lambda: len(old_pip.calls) >= 1, ASR_TEST_POLL_TIMEOUT),
                "旧任务应已进入管线（gate 阻塞中）",
            )
            self._poll_state(self.svc, voice_control_server.TASK_STATE_PROCESSING)

            # 用户取消慢响应任务
            self.svc.cancel()
            self.assertEqual(self.svc._task_state,
                             voice_control_server.TASK_STATE_CANCELLED)
            g_cancel = self.svc._task_generation
            self.assertGreater(g_cancel, gen1)

            # 紧接着发起新任务（全新生命周期）
            self.svc.start()
            rid2 = self.svc._task_id
            gen2 = self.svc._task_generation
            self.assertNotEqual(rid2, rid1)
            self.assertGreater(gen2, g_cancel)
            self._poll_state(self.svc, voice_control_server.TASK_STATE_RECORDING)

            # 释放 gate → 旧任务结果「迟到」到达
            gate.set()
            self.assertTrue(self._wait_discard(rid1, gen1),
                            "旧任务迟到结果应记录 result_stale_discarded")

            # 旧结果不得覆盖新任务任何状态
            new_state = self.svc.status()["task"]
            self.assertEqual(new_state["request_id"], rid2)
            self.assertEqual(new_state["state"],
                             voice_control_server.TASK_STATE_RECORDING)
            self.assertIsNone(new_state["result"])
            self.assertEqual(new_state["context"], None)
            self.assertEqual(self.svc._task_generation, gen2)

            # 新任务随后仍能正常完成（证明生命周期未被旧迟到结果破坏）
            new_pip = _StubPipeline(
                [(0.0, {"ok": True, "intent": "query_last", "sentence": "R2_OK"})]
            )
            with patch("service.task_pipeline.run_task", new_pip.run_task):
                self.capture.simulate_frames(8000)
                _write_silent_wav(Path(self.tmp.name) / f"{rid2}.wav", frames=8000)
                self.capture.stop_result = make_info(
                    Path(self.tmp.name) / f"{rid2}.wav", frames=8000
                )
                self.svc.stop()
                self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)
            final = self.svc.status()["task"]
            self.assertEqual(final["request_id"], rid2)
            self.assertEqual(final["state"], voice_control_server.TASK_STATE_COMPLETED)
            self.assertEqual(final["result"]["sentence"], "R2_OK")
            self.assertEqual(final["generation"], gen2)
# ── 验收 3：取消处理中任务后，迟到结果安全丢弃 ──────────────

    def test_cancel_in_progress_then_late_result_safely_discarded(self):
        """取消处理中任务 → 迟到结果安全丢弃：状态保持 CANCELLED，result 不写入。"""
        gate = threading.Event()
        old_pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last",
                    "sentence": "SHOULD_BE_DROPPED"})],
            gate=gate,
        )
        with patch("service.task_pipeline.run_task", old_pip.run_task):
            self.svc.start()
            rid1 = self.svc._task_id
            gen1 = self.svc._task_generation
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / f"{rid1}.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / f"{rid1}.wav", frames=8000
            )
            self.svc.stop()
            self.assertTrue(
                _poll_until(lambda: len(old_pip.calls) >= 1, ASR_TEST_POLL_TIMEOUT),
                "旧任务应已进入管线（gate 阻塞中）",
            )

            self.svc.cancel()
            cancel_gen = self.svc._task_generation
            self.assertEqual(self.svc._task_state,
                             voice_control_server.TASK_STATE_CANCELLED)
            self.assertEqual(cancel_gen, gen1 + 1, "取消必须单调递增代次")

            gate.set()  # 释放：旧任务「迟到」返回
            self.assertTrue(self._wait_discard(rid1, gen1))
            self.assertEqual(len(old_pip.calls), 1, "管线不得被重复调用")

            s = self.svc.status()["task"]
            self.assertEqual(s["request_id"], rid1)
            self.assertEqual(s["state"], voice_control_server.TASK_STATE_CANCELLED)
            self.assertIsNone(s["result"], "迟到结果不得写入 _task_result")
            self.assertEqual(s["generation"], cancel_gen)

    def test_cancel_before_pipeline_epoch_mismatch_discards_callback(self):
        """取消先于管线入口发生：ASR 回调经 epoch 隔离丢弃（第二道防线）。"""
        tr_gate = threading.Event()
        script_tr = _GatedScriptedTranscriber(
            [asr_whisper.AsrResult("查看最近的加速度数据", "zh", "transcribe",
                                   "base", "cpu", 0.001)],
            tr_gate,
        )
        new_runner = voice_control_server.AsrTaskRunner(
            transcriber=script_tr, on_complete=self.svc._on_asr_done
        )
        with patch.object(self.svc, "_asr_runner", new_runner), \
                patch("service.task_pipeline.run_task",
                      lambda raw: {"ok": True, "sentence": "NOPE"}):
            self.svc.start()
            rid1 = self.svc._task_id
            gen1 = self.svc._task_generation
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / f"{rid1}.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / f"{rid1}.wav", frames=8000
            )
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_PROCESSING)
            self.svc.cancel()  # 转录仍被 gate 阻塞 → 几乎没有进入管线的机会
            self.assertEqual(self.svc._task_state,
                             voice_control_server.TASK_STATE_CANCELLED)

            tr_gate.set()
            self.assertTrue(_poll_until(
                lambda: any(e.get("event") == "asr_stale_discarded"
                            for e in self.svc._task_log),
                ASR_TEST_POLL_TIMEOUT,
            ), "epoch 不匹配的 ASR 回调应被记录 asr_stale_discarded")
        s = self.svc.status()["task"]
        self.assertEqual(s["request_id"], rid1)
        self.assertEqual(s["state"], voice_control_server.TASK_STATE_CANCELLED)
        self.assertIsNone(s["result"])
        mismatch = [e for e in s["log"] if e.get("event") == "asr_stale_discarded"]
        self.assertTrue(mismatch)
        self.assertEqual(mismatch[-1]["reason"], "epoch_mismatch")
        self.assertEqual(mismatch[-1]["request_id"], rid1)
        self.assertGreaterEqual(mismatch[-1]["generation"], gen1)
# ── 验收 5：日志关联 request_id / generation ────────────────

    def test_log_events_replay_with_correct_request_id_and_generation(self):
        """慢响应×取消交错的完整事件序列可回放，且每个事件关联正确的代次。"""
        gate = threading.Event()
        pip = _StubPipeline([(0.0, {"ok": True, "sentence": "A"})], gate=gate)
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.start()
            rid1 = self.svc._task_id
            gen1 = self.svc._task_generation
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / f"{rid1}.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / f"{rid1}.wav", frames=8000
            )
            self.svc.stop()
            self.assertTrue(
                _poll_until(lambda: len(pip.calls) >= 1, ASR_TEST_POLL_TIMEOUT)
            )

            self.svc.cancel()
            gen_cancel = self.svc._task_generation
            self.svc.start()
            rid2 = self.svc._task_id
            gen2 = self.svc._task_generation
            self.assertLess(gen1, gen_cancel)
            self.assertLess(gen_cancel, gen2)

            gate.set()
            self.assertTrue(self._wait_discard(rid1, gen1))

        with self.svc._lock:
            log_snapshot = list(self.svc._task_log)
        replay = [(e.get("request_id"), e.get("generation"), e.get("event"))
                  for e in log_snapshot]

        # 每个环节都能关联到事件来源：start / 迟到丢弃
        self.assertIn((rid1, gen1, "state_change"), replay, "任务1 start 事件")
        self.assertIn((rid1, gen1, "result_stale_discarded"),
                      replay, "迟到丢弃必须关联旧任务代次")
        stale = [e for e in log_snapshot if e.get("event") == "result_stale_discarded"]
        self.assertEqual((stale[-1]["request_id"], stale[-1]["generation"]),
                         (rid1, gen1))
        # 新任务独立生命周期
        s = self.svc.status()["task"]
        self.assertEqual(s["request_id"], rid2)
        self.assertEqual(s["generation"], gen2)
        self.assertEqual(self.svc._task_state,
                         voice_control_server.TASK_STATE_RECORDING)

    # ── 验收 6：计时能力（生产事实 + 模拟指标明确区分） ──────────

    def test_task_log_timestamps_are_wall_clock_and_non_decreasing(self):
        """生产计时事实：task.log 每项的 ts 为 wall-clock 毫秒且随事件序非降。"""
        self._full_task_once({"ok": True, "intent": "query_last", "sentence": "OK"})
        log_snapshot = list(self.svc.status()["task"]["log"])
        self.assertTrue(log_snapshot)
        ts = [e["ts"] for e in log_snapshot]
        self.assertEqual(ts, sorted(ts), "task.log ts 必须按真实发生顺序非降")
        for e in log_snapshot:
            self.assertIsInstance(e["ts"], int)
            self.assertGreaterEqual(e["ts"], 1_000_000, "ts 应是 wall-clock 毫秒")
            self.assertIsNotNone(e["request_id"])
            self.assertIsInstance(e["generation"], int)

    def test_slow_response_latency_measurable_as_simulated_metric(self):
        """模拟慢响应的首反馈延迟可测（time.monotonic），并明确记为模拟指标。

        生产侧目前没有「首反馈 / 任务完成」的 monotonic 计时器（只有 task.log
        的 wall-clock 毫秒 ts 与 ASR duration_seconds），因此本测试验证的是
        **测试环境**能对受控延迟做可复现测量，并不冒充真实语音体验数据。
        """
        injected_delay = 0.15
        pip = _StubPipeline(
            [(injected_delay, {"ok": True, "intent": "query_last", "sentence": "OK"})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.start()
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / "slow.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / "slow.wav", frames=8000
            )
            t0 = time.monotonic()
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)
            simulated_first_feedback_s = time.monotonic() - t0

        # 模拟指标：受控注入的延迟应被如实测到
        self.assertGreaterEqual(simulated_first_feedback_s, injected_delay - 0.05)
        self.assertGreaterEqual(pip.elapsed_s[0], injected_delay - 0.05)
        self.assertLessEqual(simulated_first_feedback_s, injected_delay + 5.0)
        # 生产字段：ASR 推理耗时槽位存在（实际值由真实转录器填充，此处仅验收存在性）
        asr_snap = self.svc.status()["asr"]
        self.assertIn("duration_seconds", asr_snap)
# ============================================================
# Week 06 Production Timing Acceptance Tests
# ============================================================


class Week06ProductionTimingAcceptanceTest(unittest.TestCase):
    """Week 06 production timing: first-feedback / completion / failure / cancellation."""

    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="week06_timing_")
        self.addCleanup(self.tmp.cleanup)
        self.capture = FakeAudioCapture(Path(self.tmp.name))
        model = FakeWhisperModel(text="sensor data query")
        tr = asr_whisper.Transcriber(
            model=model, model_name="base", language="zh",
            task="transcribe", device="cpu",
        )
        self.svc = voice_control_server.VoiceControlService(
            capture=self.capture, asr=tr
        )
        self.addCleanup(self._join_asr_worker)

    def _join_asr_worker(self):
        runner = getattr(self.svc, "_asr_runner", None)
        thread = getattr(runner, "_thread", None) if runner is not None else None
        if thread is not None and thread.is_alive():
            thread.join(timeout=ASR_TEST_POLL_TIMEOUT)

    def _poll_state(self, svc, state, timeout=ASR_TEST_POLL_TIMEOUT):
        self.assertTrue(
            _poll_until(lambda: svc._task_state == state, timeout),
            f"wait for state {state} timed out; current {svc._task_state}",
        )
# Test 1: Normal completion timing
    def test_normal_timing_first_feedback_and_completion(self):
        """Normal task: latency_first_feedback_ms == latency_completion_ms > 0."""
        injected_delay = 0.12
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline(
            [(injected_delay, {"ok": True, "intent": "query_last",
                               "sentence": "OK"})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing, "Normal task should have timing block")
        fb_ms = timing.get("latency_first_feedback_ms")
        comp_ms = timing.get("latency_completion_ms")
        self.assertIsNotNone(fb_ms)
        self.assertGreater(fb_ms, 0)
        self.assertIsNotNone(comp_ms)
        self.assertGreater(comp_ms, 0)
        self.assertEqual(fb_ms, comp_ms)
        self.assertGreaterEqual(fb_ms, injected_delay * 500)
# Test 2: Failed termination timing
    def test_failed_task_timing_with_termination_reason(self):
        """Failed task: latency_termination_ms + termination_reason='failed'."""
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline([(0.0, RuntimeError("simulated crash"))])
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_FAILED)

        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertIsNotNone(timing.get("latency_first_feedback_ms"))
        self.assertIsNotNone(timing.get("latency_termination_ms"))
        self.assertEqual(timing.get("termination_reason"), "failed")
        self.assertTrue("latency_completion_ms" not in timing)
# Test 3: Cancelled during recording
    def test_cancelled_during_recording_timing(self):
        """Cancel during recording: produces cancelled termination timing."""
        self.svc.start()
        self.capture.simulate_frames(4000)
        result = self.svc.cancel()
        self.assertTrue(result["ok"])
        self.assertEqual(self.svc._task_state,
                         voice_control_server.TASK_STATE_CANCELLED)
        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertIsNotNone(timing.get("latency_first_feedback_ms"))
        self.assertIsNotNone(timing.get("latency_termination_ms"))
        self.assertEqual(timing.get("termination_reason"), "cancelled")

    # Test 4: Cancelled during processing
    def test_cancelled_during_processing_timing(self):
        """Cancel during processing: independent termination timing."""
        gate = threading.Event()
        pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "LATE"})],
            gate=gate,
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.start()
            rid = self.svc._task_id
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / f"{rid}.wav", frames=8000
            )
            self.svc.stop()
            _poll_until(lambda: len(pip.calls) > 0, ASR_TEST_GATE_TIMEOUT)
            self.svc.cancel()
            gate.set()
        self._poll_state(self.svc, voice_control_server.TASK_STATE_CANCELLED)
        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertEqual(timing.get("termination_reason"), "cancelled")

    # Test 5: request_id consistency
    def test_timing_events_share_request_id(self):
        """All state_change events share the same request_id."""
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "OK"})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        task = self.svc.status()["task"]
        self.assertEqual(task["request_id"], rid)
        for entry in task["log"]:
            if entry.get("event") == "state_change":
                self.assertEqual(entry["request_id"], rid)
# Test 6: Cross-task isolation
    def test_old_task_timing_does_not_contaminate_new_task(self):
        """Old task timing does not leak into new task."""
        self.svc.start()
        rid1 = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid1}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid1}.wav", frames=8000
        )
        pip1 = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "T1"})]
        )
        with patch("service.task_pipeline.run_task", pip1.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        self.svc.start()
        rid2 = self.svc._task_id
        self.assertNotEqual(rid1, rid2)
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid2}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid2}.wav", frames=8000
        )
        pip2 = _StubPipeline(
            [(0.0, {"ok": True, "intent": "trigger_collect", "sentence": "T2"})]
        )
        with patch("service.task_pipeline.run_task", pip2.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        task2 = self.svc.status()["task"]
        self.assertEqual(task2["request_id"], rid2)
        timing2 = task2["timing"]
        self.assertIsNotNone(timing2)
        self.assertGreaterEqual(timing2["latency_first_feedback_ms"], 0)
        self.assertGreaterEqual(timing2["latency_completion_ms"], 0)
        self.assertNotIn("latency_termination_ms", timing2)
# Test 7: Slow response + cancel interleaving
    def test_slow_response_cancel_interleaving_cancelled_timing(self):
        """Slow response cancelled: timing reflects cancelled, not completed."""
        gate = threading.Event()
        pip = _StubPipeline(
            [(0.0, {"ok": True, "sentence": "STALE"})], gate=gate
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.start()
            rid = self.svc._task_id
            gen_before = self.svc._task_generation
            self.capture.simulate_frames(8000)
            _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
            self.capture.stop_result = make_info(
                Path(self.tmp.name) / f"{rid}.wav", frames=8000
            )
            self.svc.stop()
            _poll_until(lambda: len(pip.calls) > 0, ASR_TEST_GATE_TIMEOUT)
            self.svc.cancel()
            gen_after = self.svc._task_generation
            gate.set()
        self._poll_state(self.svc, voice_control_server.TASK_STATE_CANCELLED)
        self.assertGreater(gen_after, gen_before)
        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertEqual(timing.get("termination_reason"), "cancelled")
        self.assertIsNotNone(timing.get("latency_termination_ms"))
        self.assertNotIn("latency_completion_ms", timing)
# Test 8: No timing before input end
    def test_timing_none_before_input_end(self):
        """Before input end: task.timing is None."""
        self.svc.start()
        timing = self.svc.status()["task"]["timing"]
        self.assertIsNone(timing)

    def test_timing_no_first_feedback_during_processing(self):
        """During processing: timing exists but no first_feedback yet."""
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        gate = threading.Event()
        pip = _StubPipeline(
            [(0.0, {"ok": True, "sentence": "A"})], gate=gate
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            _poll_until(lambda: len(pip.calls) > 0, ASR_TEST_GATE_TIMEOUT)
            timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertNotIn("latency_first_feedback_ms", timing)
        gate.set()
        self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)
# Test 9: Timing absence does not break state machine
    def test_timing_absence_does_not_break_state_machine(self):
        """Timing fields anomaly does not prevent state machine operation."""
        self.svc.start()
        self.svc._input_end_at = None
        self.svc._first_feedback_at = None
        self.svc._task_completed_at = None
        self.capture.simulate_frames(8000)
        rid = self.svc._task_id
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "OK"})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        self.assertEqual(self.svc._task_state,
                         voice_control_server.TASK_STATE_COMPLETED)
        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertIsNotNone(timing.get("latency_first_feedback_ms"))
        self.assertIsNotNone(timing.get("latency_completion_ms"))

    # Test 10: CLARIFYING has first feedback but not completion
    def test_clarifying_has_first_feedback_but_not_completion(self):
        """CLARIFYING: first feedback recorded, no completion/termination."""
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline(
            [(0.0, {"ok": False, "intent": "clarify",
                    "data": {"question": "Please repeat"}})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_CLARIFYING)

        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertIsNotNone(timing.get("latency_first_feedback_ms"))
        self.assertNotIn("latency_completion_ms", timing)
        self.assertNotIn("latency_termination_ms", timing)
        self.assertNotIn("termination_reason", timing)
# Test 11: Cancel-to-feedback-stop metric exists for cancelled tasks
    def test_latency_cancel_to_feedback_stop_present_for_cancelled(self):
        """Cancelled tasks: latency_cancel_to_feedback_stop_ms present and >=0."""
        self.svc.start()
        self.capture.simulate_frames(4000)
        self.svc.cancel()
        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        c2f = timing.get("latency_cancel_to_feedback_stop_ms")
        self.assertIsNotNone(c2f, "cancelled task should have cancel-to-feedback-stop")
        self.assertGreaterEqual(c2f, 0)
        # Also verify the two metrics coexist
        self.assertIsNotNone(timing.get("latency_termination_ms"))
        self.assertEqual(timing.get("termination_reason"), "cancelled")

    def test_latency_cancel_to_feedback_stop_absent_for_completed(self):
        """Completed tasks: latency_cancel_to_feedback_stop_ms absent."""
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "OK"})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        timing = self.svc.status()["task"]["timing"]
        self.assertIsNotNone(timing)
        self.assertNotIn("latency_cancel_to_feedback_stop_ms", timing,
                         "completed task should not have cancel-stop metric")

    def test_cancel_to_feedback_stop_previous_state_not_carry_over(self):
        """New task after cancel: cancel-stop metric cleared."""
        # Task 1: cancel
        self.svc.start()
        self.svc.cancel()
        timing1 = self.svc.status()["task"]["timing"]
        self.assertIn("latency_cancel_to_feedback_stop_ms", timing1)

        # Task 2: complete normally
        self.svc.start()
        rid = self.svc._task_id
        self.capture.simulate_frames(8000)
        _write_silent_wav(Path(self.tmp.name) / f"{rid}.wav", frames=8000)
        self.capture.stop_result = make_info(
            Path(self.tmp.name) / f"{rid}.wav", frames=8000
        )
        pip = _StubPipeline(
            [(0.0, {"ok": True, "intent": "query_last", "sentence": "OK"})]
        )
        with patch("service.task_pipeline.run_task", pip.run_task):
            self.svc.stop()
            self._poll_state(self.svc, voice_control_server.TASK_STATE_COMPLETED)

        timing2 = self.svc.status()["task"]["timing"]
        self.assertNotIn("latency_cancel_to_feedback_stop_ms", timing2,
                         "new completed task should not have old cancel metric")