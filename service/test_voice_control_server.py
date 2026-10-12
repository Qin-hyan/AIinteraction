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
# D2. 网页语音控制台（Week 05 网页入口）
# ──────────────────────────────────────────────


class WebUiContractTest(VoiceServerTestBase):
    """Week 05 网页（Step 4B 整合）：``GET /`` 统一仪表盘 + ``GET /voice`` 语音控制台回退入口。

    - 统一页 ``GET /``（``service/web/dashboard.html``）：以 HTML 返回；同一页面内嵌
      「原设备仪表盘」iframe（地址由 ``create_app(esp32_base_url=...)`` 或环境变量
      ``ESP32_BASE_URL`` 注入，占位符替换后不泄漏；未配置时显示配置提示）与
      「语音控制台」iframe（指向 ``GET /voice``）；
    - 语音控制台 ``GET /voice``（原 ``service/web/index.html``）：真实 API、fetch、
      请求失败处理，按钮由服务端录音状态驱动 —— 与 ESP32 物理按键共用同一状态来源；
    - 通过 test client 复现网页操作序列（轮询 → start → 轮询 → stop → 轮询），
      验证网页所依赖的 API 联动与「重复点击不双重启动」契约。
    """

    def _fresh_app(self, esp32_base_url=None):
        return voice_control_server.create_app(
            capture=self.capture, esp32_base_url=esp32_base_url
        )

    def test_root_serves_unified_dashboard_as_html(self):
        app = self._fresh_app(esp32_base_url="http://10.1.2.3")
        resp = app.test_client().get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        body = resp.get_data(as_text=True)
        self.assertIn("统一入口", body)
        self.assertIn("dashFrame", body)
        self.assertIn("voiceFrame", body)
        self.assertIn("原设备仪表盘", body)
        self.assertIn("语音控制台", body)

    def test_root_embeds_original_dashboard_via_injected_base_url(self):
        app = self._fresh_app(esp32_base_url="http://10.1.2.3:8080/")
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('data-esp32-base="http://10.1.2.3:8080"', body)
        self.assertEqual(app.config["ESP32_BASE_URL"], "http://10.1.2.3:8080")
        self.assertNotIn(voice_control_server.ESP32_BASE_URL_PLACEHOLDER, body)

    def test_root_resolves_esp32_base_url_from_env(self):
        with patch.object(
            voice_control_server, "get_base_url", return_value="http://10.9.8.7"
        ):
            app = voice_control_server.create_app(capture=self.capture)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('data-esp32-base="http://10.9.8.7"', body)
        self.assertEqual(app.config["ESP32_BASE_URL"], "http://10.9.8.7")

    def test_root_without_esp32_base_url_shows_config_notice(self):
        app = self._fresh_app(esp32_base_url="")
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("esp32ConfigNotice", body)
        self.assertIn("未配置", body)
        self.assertNotIn(voice_control_server.ESP32_BASE_URL_PLACEHOLDER, body)
        self.assertEqual(app.config["ESP32_BASE_URL"], "")

    def test_voice_console_served_at_fallback_route(self):
        resp = self.client.get(voice_control_server.WEB_VOICE_ROUTE)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        body = resp.get_data(as_text=True)
        self.assertIn("Week 05 语音控制台", body)
        self.assertIn("开始录音", body)
        self.assertIn("停止录音", body)
        self.assertIn("getElementById", body)

    def test_voice_console_references_real_api_endpoints(self):
        body = self.client.get(voice_control_server.WEB_VOICE_ROUTE).get_data(
            as_text=True
        )
        for endpoint in ("/api/voice/start", "/api/voice/stop", "/api/voice/status"):
            self.assertIn(endpoint, body)

    def test_voice_console_uses_real_fetch_and_shared_state(self):
        body = self.client.get(voice_control_server.WEB_VOICE_ROUTE).get_data(
            as_text=True
        )
        self.assertIn("fetch(", body)
        self.assertIn(".json()", body)
        self.assertIn('setBanner("error"', body)
        self.assertIn("state.recording", body)

    def test_index_post_method_not_allowed_returns_json_405(self):
        resp = self.client.post("/", data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(resp.is_json)
        self.assertEqual(resp.get_json()["error"], "method_not_allowed")

    def test_web_driven_full_cycle_matches_page_flow(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)

        idle = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual(idle["state"], "idle")
        self.assertFalse(idle["recording"])

        started = self.assert_json(self.post("/api/voice/start"), 200)
        self.assertEqual(started["state"], "recording")

        recording = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertTrue(recording["recording"])

        stopped = self.assert_json(self.post("/api/voice/stop"), 200)
        self.assertEqual(stopped["state"], "idle")

        final = self.assert_json(self.client.get("/api/voice/status"), 200)
        self.assertEqual(final["state"], "idle")
        self.assertFalse(final["recording"])

    def test_web_double_start_is_rejected_with_409(self):
        self.assert_json(self.post("/api/voice/start"), 200)
        dup = self.assert_json(self.post("/api/voice/start"), 409)
        self.assertEqual(dup["error"], "already_recording")
        self.assertEqual(self.capture.start_calls, 1)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)