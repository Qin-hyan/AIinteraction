"""
test_vision_feedback.py — Week 08 W8-3: 视觉反馈闭环领域层测试

覆盖：
  1. 真实推理链路（注入评分器）→ 记录输入引用/模型版本/分数/规则诊断；
  2. 未配置阈值 → needs_review；显式阈值仅为契约行为，不是生产默认值；
  3. 模型缺失/推理失败 → 明确异常，不写记录、不回退规则；
  4. 输入校验（空字节/非 JPEG/超大）；
  5. 人工反馈：可用/不可用/无法判断、原因词表、重复冲突、存储失败；
  6. 设备照片联动（request_id 对应）与设备错误；
  7. 存储仅追加、容忍残行、不保存图像字节；
  8. 健康状态的脱敏与阈值/模型状态。

不访问真实设备、不加载真实模型；真实模型冒烟见
`test_vision_feedback_server.py` 的集成用例。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from . import vision_analysis
from . import vision_feedback as vf
from .vision_dbcnn import DbcnnConfigError, DbcnnInferenceError, DbcnnScore

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 96
RULE_SOURCE = vision_analysis.RULE_SOURCE


class FakeScorer:
    """返回固定原始分数的替身；只实现 `score_jpeg`。"""

    def __init__(self, score: float = 0.7561, error: Exception | None = None) -> None:
        self.score = score
        self.error = error
        self.calls: list[bytes] = []

    def score_jpeg(self, jpeg_bytes: bytes) -> DbcnnScore:
        self.calls.append(jpeg_bytes)
        if self.error is not None:
            raise self.error
        return DbcnnScore(
            score=self.score,
            model_version="dbcnn_onnx_v1",
            model_sha256="a" * 64,
            model_data_sha256="b" * 64,
            inference_ms=1.25,
        )


def fake_rules(jpeg_bytes: bytes) -> dict[str, Any]:
    """Week 07 规则诊断替身；与模型分数完全无关。"""
    return {
        "usable": True,
        "status": "ok",
        "score": 0.9,
        "metrics": {"mean_luma": 118.5, "contrast": 63.4, "detail": 8.3},
        "bytes_len": len(jpeg_bytes),
    }


def make_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    score: float = 0.7561,
    threshold: float | None = None,
    scorer_error: Exception | None = None,
    device: Any = None,
    rule_calls: list[bytes] | None = None,
) -> tuple[vf.VisionFeedbackService, FakeScorer]:
    """构造注入替身的服务；阈值只通过显式环境变量配置。"""
    if threshold is None:
        monkeypatch.delenv(vision_analysis.ENV_SCORE_THRESHOLD, raising=False)
    else:
        monkeypatch.setenv(vision_analysis.ENV_SCORE_THRESHOLD, str(threshold))

    scorer = FakeScorer(score=score, error=scorer_error)

    def _rules(jpeg_bytes: bytes) -> dict[str, Any]:
        if rule_calls is not None:
            rule_calls.append(jpeg_bytes)
        return fake_rules(jpeg_bytes)

    def _analyzer(**kwargs: Any) -> dict[str, Any]:
        return vision_analysis.analyze_jpeg(rule_assessor=_rules, **kwargs)

    service = vf.VisionFeedbackService(
        data_dir=tmp_path / "data",
        device=device,
        scorer_factory=lambda: scorer,
        analyzer=_analyzer,
    )
    return service, scorer


# ──────────────────────────────────────────────
# 1. 分析记录与来源分离
# ──────────────────────────────────────────────


def test_analyze_without_threshold_stays_needs_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, scorer = make_service(tmp_path, monkeypatch)

    record = service.analyze(JPEG)

    analysis = record["analysis"]
    assert scorer.calls == [JPEG]
    assert analysis["decision"] == "needs_review"
    assert analysis["reject_reason"] == "threshold_not_configured"
    assert analysis["threshold_status"] == "not_configured"
    assert analysis["score_threshold"] is None
    assert analysis["review_required"] is True
    # 仅分数模型：不得伪造标签，规则诊断必须单独标注来源
    assert analysis["label"] is None
    assert analysis["label_source"] is None
    assert analysis["score_source"] == vf.LABEL_SOURCE_MODEL
    assert analysis["score"] == pytest.approx(0.7561)
    assert analysis["rule_diagnostics"]["source"] == RULE_SOURCE
    assert analysis["rule_diagnostics"]["label"] == "ok"
    assert analysis["rule_diagnostics"]["label"] != analysis["label"]
    assert "threshold_not_configured" in record["disclaimers"]
    assert record["record_version"] == vf.RECORD_VERSION


def test_analyze_records_input_reference_without_image_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)

    record = service.analyze(JPEG)

    ref = record["input"]["image_ref"]
    assert record["input"]["source"] == vf.SOURCE_LOCAL_UPLOAD
    assert ref["sha256"] == vf.sha256_hex(JPEG)
    assert ref["bytes_len"] == len(JPEG)
    assert ref["format"] == "jpeg"
    assert record["input"]["device"] is None
    assert record["created_at"] == record["analysis"]["created_at"]

    raw = service.store.records_path.read_bytes()
    assert JPEG not in raw
    assert b"\xff\xd8" not in raw
    assert ref["sha256"].encode() in raw


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.75, "ok"), (0.25, "low_score")],
)
def test_explicit_threshold_is_contract_behaviour_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, score: float, expected: str
) -> None:
    # 0.5 仅为契约行为用例，不是校准后的生产默认值。
    service, _ = make_service(tmp_path, monkeypatch, score=score, threshold=0.5)

    analysis = service.analyze(JPEG)["analysis"]

    assert analysis["decision"] == expected
    assert analysis["score_threshold"] == 0.5
    assert analysis["threshold_status"] == "configured"


def test_invalid_threshold_env_is_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, scorer = make_service(tmp_path, monkeypatch)
    monkeypatch.setenv(vision_analysis.ENV_SCORE_THRESHOLD, "not-a-number")

    with pytest.raises(vf.VisionConfigError):
        service.analyze(JPEG)

    assert scorer.calls == []
    assert service.store.records() == []


def test_records_are_append_only_and_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)

    first = service.analyze(JPEG)
    second = service.analyze(JPEG)
    listed = service.records(limit=10)["records"]

    assert first["record_id"] != second["record_id"]
    assert [row["record_id"] for row in listed] == [
        second["record_id"],
        first["record_id"],
    ]
    assert service.records(limit=1)["count"] == 1
    assert service.store.get_record(first["record_id"]) is not None


@pytest.mark.parametrize("payload", [b"", b"not-a-jpeg", b"\x00\x01\x02"])
def test_analyze_rejects_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    service, scorer = make_service(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        service.analyze(payload)

    assert scorer.calls == []
    assert service.store.records() == []


def test_analyze_rejects_oversize_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, scorer = make_service(tmp_path, monkeypatch)
    oversize = b"\xff\xd8" + b"0" * vf.MAX_INPUT_BYTES

    with pytest.raises(vf.PayloadTooLargeError):
        service.analyze(oversize)

    assert scorer.calls == []
    assert service.store.records() == []


def test_model_config_error_propagates_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(vision_analysis.ENV_SCORE_THRESHOLD, raising=False)

    def _raise() -> Any:
        raise DbcnnConfigError("model path missing")

    service = vf.VisionFeedbackService(
        data_dir=tmp_path / "data", scorer_factory=_raise
    )

    with pytest.raises(DbcnnConfigError):
        service.analyze(JPEG)
    assert service.store.records() == []


def test_inference_error_does_not_fall_back_to_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_calls: list[bytes] = []
    service, _ = make_service(
        tmp_path,
        monkeypatch,
        scorer_error=DbcnnInferenceError("boom"),
        rule_calls=rule_calls,
    )

    with pytest.raises(DbcnnInferenceError):
        service.analyze(JPEG)

    assert rule_calls == []
    assert service.store.records() == []



# ──────────────────────────────────────────────
# 2. 人工反馈（与模型/规则严格分离）
# ──────────────────────────────────────────────


def _analyzed(service: vf.VisionFeedbackService) -> dict[str, Any]:
    return service.analyze(JPEG)


def test_feedback_is_stored_separately_from_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)
    record = _analyzed(service)

    event = service.submit_feedback(
        {
            "record_id": record["record_id"],
            "verdict": "usable",
            "reason": None,
            "note": "",
        }
    )

    assert event["revision"] == 1
    assert event["label_source"] == vf.FEEDBACK_SOURCE
    assert event["time_quality"] == "pc_utc"
    assert service.store.feedback_events(record["record_id"]) == [event]

    stored = service.store.get_record(record["record_id"])
    assert stored is not None
    assert "feedback" not in stored
    assert stored["analysis"] == record["analysis"]

    listed = service.records()["records"][0]
    assert listed["feedback"]["latest"]["verdict"] == "usable"
    assert len(listed["feedback"]["history"]) == 1


def test_feedback_correction_increments_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)
    record_id = _analyzed(service)["record_id"]

    service.submit_feedback(
        {"record_id": record_id, "verdict": "usable", "reason": "score_mismatch"}
    )
    second = service.submit_feedback(
        {
            "record_id": record_id,
            "verdict": "unusable",
            "reason": "occluded",
            "note": "镜头被纸挡住（人工观察）",
        }
    )

    assert second["revision"] == 2
    assert service.store.latest_feedback(record_id)["verdict"] == "unusable"
    assert len(service.store.feedback_events(record_id)) == 2


def test_duplicate_feedback_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)
    record_id = _analyzed(service)["record_id"]
    payload = {"record_id": record_id, "verdict": "undetermined", "reason": None}

    service.submit_feedback(payload)
    with pytest.raises(vf.FeedbackConflictError):
        service.submit_feedback(dict(payload))

    assert len(service.store.feedback_events(record_id)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"verdict": "usable"},
        {"record_id": "   ", "verdict": "usable"},
        {"record_id": "rec-test", "verdict": "maybe"},
        {"record_id": "rec-test", "verdict": "usable", "reason": "noise_drift"},
        {"record_id": "rec-test", "verdict": "unusable", "reason": "other"},
        {"record_id": "rec-test", "verdict": "usable", "note": "x" * 501},
        {"record_id": "rec-test", "verdict": "usable", "score": 0.9},
    ],
)
def test_feedback_validation_rejects_invalid_payload(payload: Any) -> None:
    with pytest.raises(vf.FeedbackValidationError):
        vf.normalize_feedback_payload(payload)


def test_feedback_for_unknown_record_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)

    with pytest.raises(vf.FeedbackNotFoundError):
        service.submit_feedback(
            {"record_id": "vision-0000000000000-0001", "verdict": "usable"}
        )


def test_feedback_save_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch)
    record_id = _analyzed(service)["record_id"]

    def _fail(_event: dict[str, Any]) -> None:
        raise vf.FeedbackStoreError("disk full")

    monkeypatch.setattr(service.store, "append_feedback", _fail)

    with pytest.raises(vf.FeedbackStoreError):
        service.submit_feedback({"record_id": record_id, "verdict": "usable"})



# ──────────────────────────────────────────────
# 3. 存储与配置
# ──────────────────────────────────────────────


def test_store_reports_write_failure_when_data_dir_is_file(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    store = vf.VisionFeedbackStore(blocker)

    with pytest.raises(vf.FeedbackStoreError):
        store.append_record({"record_id": "vision-x"})


def test_store_ignores_truncated_trailing_line(tmp_path: Path) -> None:
    store = vf.VisionFeedbackStore(tmp_path / "data")
    store.append_record({"record_id": "vision-1"})
    with store.records_path.open("a", encoding="utf-8") as handle:
        handle.write('{"record_id": "vision-2"')

    assert [row["record_id"] for row in store.records()] == ["vision-1"]


def test_health_reports_configuration_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .vision_dbcnn import ENV_MODEL_PATH

    monkeypatch.delenv(ENV_MODEL_PATH, raising=False)
    monkeypatch.delenv(vision_analysis.ENV_SCORE_THRESHOLD, raising=False)
    monkeypatch.delenv(vf.ENV_DEVICE_BASE_URL, raising=False)

    service = vf.VisionFeedbackService(
        data_dir=tmp_path / "data", device=vf.DeviceClient(base_url=None)
    )
    health = service.health()

    assert health["model"]["path_configured"] is False
    assert health["model"]["model_version"] == "dbcnn_onnx_v1"
    assert health["threshold"]["configured"] is False
    assert health["threshold"]["status"] == "not_configured"
    assert health["device"]["configured"] is False
    assert health["feedback"]["verdicts"] == list(vf.FEEDBACK_VERDICTS)
    assert health["feedback"]["reasons"] == list(vf.FEEDBACK_REASONS)
    assert health["limits"]["max_input_bytes"] == vf.MAX_INPUT_BYTES


def test_health_masks_device_endpoint(tmp_path: Path) -> None:
    service = vf.VisionFeedbackService(
        data_dir=tmp_path / "data",
        device=vf.DeviceClient(base_url="http://198.51.100.20:80"),
    )

    health = service.health()

    assert health["device"]["configured"] is True
    assert health["device"]["endpoint_masked"] == "http://198.51.*.*:80"
    assert "198.51.100.20" not in str(health)


def test_health_reports_invalid_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(vision_analysis.ENV_SCORE_THRESHOLD, "2")
    service = vf.VisionFeedbackService(data_dir=tmp_path / "data")

    health = service.health()

    assert health["threshold"]["configured"] is False
    assert health["threshold"]["error"]


def test_record_id_format_and_uniqueness() -> None:
    first = vf.make_record_id(1700000000000)
    second = vf.make_record_id(1700000000000)

    assert re.fullmatch(r"vision-\d{13}-\d{4}", first)
    assert first != second


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://198.51.100.10:80/", "http://198.51.100.10:80"),
        ("https://device.local", "https://device.local"),
    ],
)
def test_normalize_base_url_accepts_valid(raw: str, expected: str) -> None:
    assert vf.normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["ftp://device", "http://", "http://device:80/path", "http://user:pw@device"],
)
def test_normalize_base_url_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        vf.normalize_base_url(raw)


def test_normalize_base_url_requires_value() -> None:
    with pytest.raises(vf.DeviceNotConfigured):
        vf.normalize_base_url("  ")



# ──────────────────────────────────────────────
# 4. 设备客户端与设备照片联动
# ──────────────────────────────────────────────


class RecordingOpener:
    """记录请求的替身 opener，可注入响应或异常。"""

    def __init__(
        self,
        payload: bytes = b"{}",
        content_type: str = "application/json",
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.content_type = content_type
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float) -> tuple[int, bytes, str]:
        self.calls.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "timeout": timeout,
                "body": request.data,
            }
        )
        if self.error is not None:
            raise self.error
        return 200, self.payload, self.content_type


def test_device_gallery_uses_existing_endpoint() -> None:
    opener = RecordingOpener(
        payload=b'{"total": 1, "retain_limit": 3, "photos": [{"index": 0}]}'
    )
    client = vf.DeviceClient("http://device:80", opener=opener)

    gallery = client.gallery()

    assert gallery["photos"] == [{"index": 0}]
    assert opener.calls[0]["method"] == "GET"
    assert opener.calls[0]["url"] == "http://device:80/api/camera/gallery"


def test_device_photo_returns_jpeg_bytes() -> None:
    opener = RecordingOpener(payload=JPEG, content_type="image/jpeg")
    client = vf.DeviceClient("http://device:80", opener=opener)

    assert client.photo(2) == JPEG
    assert opener.calls[0]["url"] == "http://device:80/api/camera/photo?index=2"


def test_device_photo_rejects_non_jpeg() -> None:
    opener = RecordingOpener(payload=b"<html>error</html>", content_type="text/html")
    client = vf.DeviceClient("http://device:80", opener=opener)

    with pytest.raises(vf.DeviceProtocolError):
        client.photo(0)


def test_device_capture_posts_to_existing_endpoint() -> None:
    opener = RecordingOpener(payload=b'{"request_id": "req-1-0001"}')
    client = vf.DeviceClient("http://device:80", opener=opener)

    assert client.capture()["request_id"] == "req-1-0001"
    assert opener.calls[0]["method"] == "POST"
    assert opener.calls[0]["url"] == "http://device:80/api/camera/capture"


def test_device_gallery_rejects_missing_photos_array() -> None:
    opener = RecordingOpener(payload=b'{"total": 0}')
    client = vf.DeviceClient("http://device:80", opener=opener)

    with pytest.raises(vf.DeviceProtocolError):
        client.gallery()


def test_device_gallery_rejects_bad_json() -> None:
    opener = RecordingOpener(payload=b"not-json")
    client = vf.DeviceClient("http://device:80", opener=opener)

    with pytest.raises(vf.DeviceProtocolError):
        client.gallery()


def test_device_http_error_preserves_status() -> None:
    opener = RecordingOpener(error=vf.DeviceHttpError(404, "设备返回 HTTP 404"))
    client = vf.DeviceClient("http://device:80", opener=opener)

    with pytest.raises(vf.DeviceHttpError) as excinfo:
        client.photo(5)
    assert excinfo.value.status == 404


def test_device_client_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(vf.ENV_DEVICE_BASE_URL, raising=False)
    client = vf.DeviceClient(base_url=None)

    assert client.configured is False
    with pytest.raises(vf.DeviceNotConfigured):
        client.gallery()



class StubDevice:
    """服务层测试用的设备替身。"""

    configured = True
    base_url = "http://device-stub:80"

    def __init__(self, photos: list[dict[str, Any]] | None = None) -> None:
        self.photos = photos if photos is not None else []
        self.photo_calls: list[int] = []
        self.capture_calls = 0

    def gallery(self) -> dict[str, Any]:
        return {
            "total": len(self.photos),
            "retain_limit": 3,
            "retained": len(self.photos),
            "photos": self.photos,
        }

    def photo(self, index: int) -> bytes:
        self.photo_calls.append(index)
        return JPEG

    def capture(self) -> dict[str, Any]:
        self.capture_calls += 1
        return {"request_id": "req-1-0002", "cap_task_status": "capturing"}

    def status(self) -> dict[str, Any]:
        return {"cap_task_status": "idle"}


def test_analyze_device_photo_keeps_request_id_linkage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = StubDevice(
        photos=[
            {
                "index": 0,
                "request_id": "req-777-0001",
                "captured_ms": 123456,
                "img_status": "ok",
                "usable": True,
                "file_name": "cap-0.jpg",
                "width": 320,
                "height": 240,
                "size_bytes": len(JPEG),
            }
        ]
    )
    service, scorer = make_service(tmp_path, monkeypatch, device=device)

    record = service.analyze_device_photo(0)

    assert scorer.calls == [JPEG]
    assert device.photo_calls == [0]
    assert record["input"]["source"] == vf.SOURCE_DEVICE_GALLERY
    assert record["input"]["device"]["request_id"] == "req-777-0001"
    assert record["input"]["device"]["index"] == 0
    assert record["input"]["device"]["img_status"] == "ok"
    # 分析记录 ID 与设备 request_id 不同：两者都必须保留以便追踪
    assert record["record_id"] != "req-777-0001"
    assert record["analysis"]["request_id"] == record["record_id"]


def test_analyze_device_photo_missing_index_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = StubDevice(photos=[{"index": 0, "request_id": "req-1"}])
    service, _ = make_service(tmp_path, monkeypatch, device=device)

    with pytest.raises(vf.DevicePhotoNotFound):
        service.analyze_device_photo(3)
    assert device.photo_calls == []


def test_analyze_device_photo_rejects_invalid_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path, monkeypatch, device=StubDevice())

    for invalid in (True, -1, "0", 1.5):
        with pytest.raises(ValueError):
            service.analyze_device_photo(invalid)  # type: ignore[arg-type]
