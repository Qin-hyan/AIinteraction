"""
test_voice_control_server.py — Week 05 Step 2A 语音录音 HTTP 控制服务自动测试

测试策略：
  - 使用 Flask test client（``app.test_client()``）：不启动真实 HTTP 服务、不占端口、
    不改动任何网络/防火墙设置
  - 使用假采集器 FakeAudioCapture 替换真实麦克风：不打开任何录音设备、不写任何 WAV
  - 覆盖：start/stop/status、重复启动、未录音就停止、设备错误、录音模块抛错（含未预期
    异常）、JSON 响应与错误码、请求体校验、404/405 JSON 映射、输出目录保护、CLI 默认值

运行（使用仓库外隔离虚拟环境，不向系统全局 Python 装包）::

    <venv>\\Scripts\\python.exe -m pytest service/test_voice_control_server.py -v
"""

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional
from unittest.mock import patch

from . import audio_capture, voice_control_server
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
        self.app = voice_control_server.create_app(capture=self.capture)
        self.client = self.app.test_client()

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

    def test_stop_returns_wav_metadata(self):
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

        wav = data["wav"]
        self.assertEqual(Path(wav["path"]), self.output_dir / "fake.wav")
        self.assertEqual(wav["samplerate"], 16000)
        self.assertEqual(wav["channels"], 1)
        self.assertEqual(wav["sample_width_bytes"], 2)
        self.assertEqual(wav["frames"], 16000)
        self.assertEqual(wav["duration_seconds"], 1.0)
        self.assertEqual(wav["size_bytes"], 32044)
        self.assertEqual(wav["status_flags"], ["input overflow"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)