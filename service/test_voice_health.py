"""Week 05/06 语音服务健康检查 + NL→JSON 接线的最小测试。"""

from __future__ import annotations

from pathlib import Path

from . import voice_control_server as vc


class FakeCapture:
    is_recording = False
    frames_recorded = 0
    duration_seconds = 0.0
    samplerate = 16000
    channels = 1
    dtype = "int16"
    device = None

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def start_recording(self):
        raise AssertionError("health 不应启动录音")

    def stop_recording(self):
        raise AssertionError("health 不应停止录音")


class FakeTranscriber:
    model_name = "base"
    language = "zh"
    task = "transcribe"
    device = "cpu"


def make_app(tmp_path: Path):
    capture = FakeCapture(tmp_path / "wav")
    return vc.create_app(capture=capture, asr=FakeTranscriber())


def test_voice_health_reports_service_model_and_audio(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    client = app.test_client()
    response = client.get("/api/voice/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["service"] == "voice_control"
    assert payload["asr"]["model"] == "base"
    assert payload["asr"]["language"] == "zh"
    assert payload["audio"]["samplerate"] == 16000
    assert payload["pipeline"]["nl_to_task"] is True
    assert payload["pipeline"]["tts"] is False


def test_voice_status_still_available(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    client = app.test_client()
    response = client.get("/api/voice/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["state"] == "idle"


def test_voice_cors_allows_private_origin_and_pna(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    client = app.test_client()
    response = client.options(
        "/api/voice/health",
        headers={
            "Origin": "http://192.168.1.50",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert response.headers["Access-Control-Allow-Origin"] == "http://192.168.1.50"
    assert response.headers["Access-Control-Allow-Private-Network"] == "true"
    assert response.headers["Access-Control-Allow-Origin"] != "*"


def test_voice_cors_rejects_public_origin(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    client = app.test_client()
    response = client.get(
        "/api/voice/health", headers={"Origin": "https://example.com"}
    )
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
