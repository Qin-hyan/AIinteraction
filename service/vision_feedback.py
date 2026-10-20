"""
vision_feedback.py — Week 08 W8-3: 视觉反馈闭环的本地领域层

职责：
  1. 对一张 JPEG 发起**真实本地 DBCNN 推理**（复用 `vision_analysis.analyze_jpeg`），
     并保留输入引用、`request_id`、模型版本、原始分数、Week 07 规则诊断
     与时间戳之间的对应关系；
  2. 以仅追加（append-only）的本地 JSONL 记录分析与人工反馈，便于审计；
  3. 通过设备既有 HTTP API（`/api/camera/gallery`、`/api/camera/photo`、
     `/api/camera/capture`）读取与重新采集照片，**不改动** Week 07 的
     定时抓拍策略；
  4. 校验并保存人工反馈（`usable` / `unusable` / `undetermined`）。

来源分离（强制）：
  - `analysis.score` / `model_version` 来自 DBCNN，**只有分数**；
  - `analysis.rule_diagnostics` 来自 Week 07 规则，**不是模型输出**；
  - 反馈事件 `label_source="user_feedback"`，始终单独存放，**不写回**
    `analysis`，也不把三者合成一个"模型分类结果"；
  - 低分**不得**被解释为遮挡类别；`occluded` 只能作为人工原因出现。

边界：
  - 不上传图片、不调用云端推理、不启动常驻服务（HTTP 层见
    `service/vision_feedback_server.py`）；
  - 模型不可用时抛明确 `DbcnnError`，**不回退**规则判断；
  - 不设生产阈值：阈值仍由 `VISION_DBCNN_SCORE_THRESHOLD` 显式配置，
    未配置时结果恒为 `needs_review`；
  - 图像字节不做持久化，只保存 SHA-256、字节数与设备侧引用。

配置：
  - `VISION_DEVICE_BASE_URL`：设备基地址（如 `http://<device>:80`），可选；
    未配置时设备照片/重新采集不可用，本地图片分析仍可用；
  - `VISION_FEEDBACK_DATA_DIR`：本地记录目录，默认
    `<repo>/.workbuddy/vision-feedback`（已被 `.gitignore` 忽略）；
  - 其余模型配置沿用 `vision_dbcnn` / `vision_analysis`。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import vision_analysis
from .vision_dbcnn import (
    DEFAULT_MODEL_VERSION,
    ENV_MODEL_PATH,
    ENV_MODEL_VERSION,
    SCORE_SOURCE,
)

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

ENV_DEVICE_BASE_URL = "VISION_DEVICE_BASE_URL"
"""设备基地址环境变量；未配置时设备相关功能明确不可用，不猜测局域网地址。"""

ENV_DATA_DIR = "VISION_FEEDBACK_DATA_DIR"
"""本地记录目录环境变量；默认仓库内被忽略的 `.workbuddy/vision-feedback`。"""

DEFAULT_DATA_SUBDIR = (".workbuddy", "vision-feedback")
"""默认数据目录（相对仓库根），已被 `.gitignore` 中的 `.workbuddy/` 覆盖。"""

RECORDS_FILE = "records.jsonl"
FEEDBACK_FILE = "feedback.jsonl"

RECORD_VERSION = "w8_3_feedback_v1"
"""记录结构版本标识，便于后续迁移。"""

FEEDBACK_SOURCE = "user_feedback"
"""人工反馈来源标识；不得与模型或规则来源混用。"""

LABEL_SOURCE_MODEL = SCORE_SOURCE
LABEL_SOURCE_RULES = vision_analysis.RULE_SOURCE

FEEDBACK_VERDICTS = ("usable", "unusable", "undetermined")
"""人工判定：可用 / 不可用 / 无法判断。"""

FEEDBACK_REASONS = (
    "score_mismatch",
    "too_dark",
    "too_bright",
    "blurred",
    "occluded",
    "wrong_scene",
    "other",
)
"""人工纠正原因词表。

`occluded` 仅代表**用户自己的观察**，不能由低分或 `noise_drift` 自动推导。
"""

REASON_NEEDS_NOTE = "other"
MAX_NOTE_LENGTH = 500
MAX_INPUT_BYTES = 4 * 1024 * 1024
"""单张输入 JPEG 上限（4 MiB）；超过即拒绝，不写入记录。"""

JPEG_MAGIC = b"\xff\xd8"

DEVICE_TIMEOUT_SECONDS = 5.0
DEVICE_GALLERY_PATH = "/api/camera/gallery"
DEVICE_PHOTO_PATH = "/api/camera/photo"
DEVICE_CAPTURE_PATH = "/api/camera/capture"
DEVICE_STATUS_PATH = "/api/camera/status"

SOURCE_DEVICE_GALLERY = "device_gallery"
SOURCE_LOCAL_UPLOAD = "local_upload"

DISCLAIMERS_BASE = (
    "score_is_raw_model_output_not_accuracy",
    "rule_diagnostics_are_not_model_output",
    "feedback_is_user_opinion_not_model_label",
)
"""结果记录随附的免责语义键；用于界面与审计时防止误读。"""


# ──────────────────────────────────────────────
# 异常
# ──────────────────────────────────────────────


class VisionFeedbackError(RuntimeError):
    """反馈闭环的基类异常。"""


class VisionConfigError(VisionFeedbackError):
    """服务配置非法（例如阈值环境变量不是合法数值）。"""


class PayloadTooLargeError(ValueError):
    """输入超过允许上限。"""


class FeedbackValidationError(ValueError):
    """人工反馈请求体非法（对应 HTTP 400）。"""


class FeedbackNotFoundError(LookupError):
    """反馈引用的记录不存在（对应 HTTP 404）。"""


class FeedbackConflictError(VisionFeedbackError):
    """重复提交完全相同的反馈（对应 HTTP 409）。"""


class FeedbackStoreError(VisionFeedbackError):
    """本地记录读写失败（对应 HTTP 500）。"""


class DeviceError(VisionFeedbackError):
    """设备 HTTP 调用的基类异常。"""


class DeviceNotConfigured(DeviceError):
    """未配置 `VISION_DEVICE_BASE_URL`，且未显式提供设备地址。"""


class DeviceUnreachable(DeviceError):
    """设备不可达（连接失败/超时）。"""


class DeviceHttpError(DeviceError):
    """设备返回非 2xx 状态码。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class DeviceProtocolError(DeviceError):
    """设备响应不符合预期（坏 JSON / 非 JPEG 照片）。"""


# ──────────────────────────────────────────────
# 通用工具
# ──────────────────────────────────────────────


def utc_now_iso() -> str:
    """返回 PC 端 UTC ISO-8601（毫秒精度，`Z` 后缀），与 `vision_result` 一致。"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


_RECORD_COUNTER = 0
_RECORD_COUNTER_LOCK = threading.Lock()


def make_record_id(now_ms: Optional[int] = None) -> str:
    """生成分析记录 ID：`vision-{ms}-{counter:04d}`（沿用项目命名习惯）。"""
    global _RECORD_COUNTER
    moment = int(time.time() * 1000) if now_ms is None else int(now_ms)
    with _RECORD_COUNTER_LOCK:
        _RECORD_COUNTER = (_RECORD_COUNTER + 1) % 10000
        counter = _RECORD_COUNTER
    return f"vision-{moment}-{counter:04d}"


def sha256_hex(payload: bytes) -> str:
    """返回字节内容的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(payload).hexdigest()


def repo_root() -> Path:
    """返回仓库根目录（`service/` 的上一级）。"""
    return Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    """返回默认本地记录目录（被 `.gitignore` 忽略，不提交用户数据）。"""
    return repo_root().joinpath(*DEFAULT_DATA_SUBDIR)


def resolve_data_dir(explicit: Optional[str | Path] = None) -> Path:
    """按「显式参数 → 环境变量 → 默认目录」解析记录目录。"""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser()
    raw = os.environ.get(ENV_DATA_DIR)
    if raw is not None and raw.strip():
        return Path(raw.strip()).expanduser()
    return default_data_dir()


def is_loopback_host(host: str) -> bool:
    """判断绑定地址是否为本机回环地址（默认只监听 127.0.0.1）。"""
    return host in ("127.0.0.1", "localhost", "::1")


def _mask_ipv4(host: str) -> str:
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return f"{parts[0]}.{parts[1]}.*.*"
    return host


def masked_endpoint(base_url: Optional[str]) -> Optional[str]:
    """返回脱敏后的设备端点，避免在日志/界面回显完整私有地址。"""
    if not base_url:
        return None
    try:
        parsed = urllib.parse.urlsplit(base_url)
    except ValueError:
        return None
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{_mask_ipv4(host)}{port}"


def normalize_base_url(raw: Optional[str]) -> str:
    """校验并规范化设备基地址。

    Raises:
        DeviceNotConfigured: 未提供地址。
        ValueError: 不是 `http(s)` 地址，或带有非根路径。
    """
    if raw is None or not str(raw).strip():
        raise DeviceNotConfigured(
            f"未配置设备地址（设置 {ENV_DEVICE_BASE_URL} 或使用 --device-url）"
        )
    text = str(raw).strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"设备地址必须是 http(s)://host[:port]，实际 {text!r}")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("设备地址不得包含查询串、片段或凭据")
    if parsed.path not in ("", "/"):
        raise ValueError(f"设备地址不得包含路径，实际 {parsed.path!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _run_id_or_none(value: Any) -> Optional[str]:
    """设备返回的 ID 字段可能缺失或为空；只接受非空字符串。"""
    if isinstance(value, str) and value.strip():
        return value
    return None


DISCLAIMERS_BASE = (
    "score_is_raw_model_output_not_accuracy",
    "rule_diagnostics_are_not_model_output",
    "feedback_is_user_opinion_not_model_label",
)



# ──────────────────────────────────────────────
# 设备客户端（复用既有设备 HTTP API，不新增设备端接口）
# ──────────────────────────────────────────────


def _default_device_opener(
    request: urllib.request.Request, timeout: float
) -> tuple[int, bytes, str]:
    """默认 `urllib` 打开器；失败时转换为明确设备异常。"""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                int(response.status),
                response.read(),
                response.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as exc:
        raise DeviceHttpError(exc.code, f"设备返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise DeviceUnreachable(f"设备不可达: {exc.reason}") from exc
    except OSError as exc:
        raise DeviceUnreachable(f"设备不可达: {type(exc).__name__}: {exc}") from exc


class DeviceClient:
    """最小设备 HTTP 客户端：读取照片元数据/照片，并按需触发一次抓拍。

    `opener` 可注入，便于在不接触真实设备的情况下测试错误路径。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = DEVICE_TIMEOUT_SECONDS,
        opener: Optional[
            Callable[[urllib.request.Request, float], tuple[int, bytes, str]]
        ] = None,
    ) -> None:
        raw = base_url if base_url is not None else os.environ.get(ENV_DEVICE_BASE_URL)
        try:
            self._base_url: Optional[str] = normalize_base_url(raw)
        except DeviceNotConfigured:
            self._base_url = None
        self._timeout = float(timeout)
        self._opener = opener or _default_device_opener

    @property
    def configured(self) -> bool:
        return self._base_url is not None

    @property
    def base_url(self) -> Optional[str]:
        return self._base_url

    def _require_base(self) -> str:
        if self._base_url is None:
            raise DeviceNotConfigured(
                f"未配置设备地址（设置 {ENV_DEVICE_BASE_URL} 或使用 --device-url）"
            )
        return self._base_url

    def _request(
        self, path: str, *, method: str = "GET", body: Optional[bytes] = None
    ) -> tuple[int, bytes, str]:
        base = self._require_base()
        headers = {"Accept": "application/json, image/jpeg"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{base}{path}", data=body, headers=headers, method=method
        )
        return self._opener(request, self._timeout)

    def _json_request(
        self, path: str, *, method: str = "GET", body: Optional[bytes] = None
    ) -> dict[str, Any]:
        _, payload, _ = self._request(path, method=method, body=body)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise DeviceProtocolError(f"设备响应不是合法 JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise DeviceProtocolError("设备响应 JSON 不是对象")
        return decoded

    def gallery(self) -> dict[str, Any]:
        """读取设备照片元数据（`GET /api/camera/gallery`）。"""
        data = self._json_request(DEVICE_GALLERY_PATH)
        photos = data.get("photos")
        if not isinstance(photos, list):
            raise DeviceProtocolError("设备 gallery 响应缺少 photos 数组")
        return data

    def photo(self, index: int) -> bytes:
        """读取设备照片 JPEG 字节（`GET /api/camera/photo?index=N`）。"""
        _, payload, content_type = self._request(
            f"{DEVICE_PHOTO_PATH}?index={int(index)}"
        )
        if not payload.startswith(JPEG_MAGIC):
            raise DeviceProtocolError(
                f"设备返回的不是 JPEG（Content-Type={content_type or 'unknown'}）"
            )
        return payload

    def capture(self) -> dict[str, Any]:
        """触发一次按需抓拍（`POST /api/camera/capture`），沿用现有采集链路。"""
        return self._json_request(DEVICE_CAPTURE_PATH, method="POST", body=b"")

    def status(self) -> dict[str, Any]:
        """读取设备采集四态状态（`GET /api/camera/status`）。"""
        return self._json_request(DEVICE_STATUS_PATH)


# ──────────────────────────────────────────────
# 本地仅追加存储（分析记录 + 人工反馈，两类文件分离）
# ──────────────────────────────────────────────


class VisionFeedbackStore:
    """本地 JSONL 存储。

    - `records.jsonl`：每次分析的输入引用 + 模型/规则结果（写入一次，不再修改）；
    - `feedback.jsonl`：人工反馈事件（可多次修订，`revision` 单调递增）；
    - 反馈**不写回**分析记录，避免把用户意见混成模型结论；
    - 图像字节不落盘，只保存 SHA-256/字节数/设备引用。
    """

    def __init__(self, data_dir: Optional[str | Path] = None) -> None:
        self._data_dir = resolve_data_dir(data_dir)
        self._records_path = self._data_dir / RECORDS_FILE
        self._feedback_path = self._data_dir / FEEDBACK_FILE
        self._lock = threading.RLock()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def records_path(self) -> Path:
        return self._records_path

    @property
    def feedback_path(self) -> Path:
        return self._feedback_path

    # ── 内部读写 ──────────────────────────────

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        """追加一行 JSON；失败时回滚到追加前长度并抛 `FeedbackStoreError`。"""
        line = (json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8")
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("ab") as handle:
                    start = handle.tell()
                    try:
                        handle.write(line)
                        handle.flush()
                        os.fsync(handle.fileno())
                    except OSError:
                        try:
                            handle.seek(start)
                            handle.truncate()
                        except OSError:
                            pass
                        raise
            except OSError as exc:
                raise FeedbackStoreError(
                    f"无法写入本地记录 {path.name}: {type(exc).__name__}: {exc}"
                ) from exc

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        """读取 JSONL；忽略空行与损坏行（含写入中断的残行），不抛异常。"""
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        item = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except OSError as exc:
            raise FeedbackStoreError(
                f"无法读取本地记录 {path.name}: {type(exc).__name__}: {exc}"
            ) from exc
        return rows

    # ── 分析记录 ──────────────────────────────

    def append_record(self, record: dict[str, Any]) -> None:
        """写入一条分析记录（一次写入，不可变）。"""
        self._append(self._records_path, record)

    def records(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """返回分析记录（按写入顺序倒序，即最新在前）。"""
        rows = list(reversed(self._read(self._records_path)))
        if limit is not None and limit >= 0:
            return rows[:limit]
        return rows

    def get_record(self, record_id: str) -> Optional[dict[str, Any]]:
        for record in reversed(self._read(self._records_path)):
            if record.get("record_id") == record_id:
                return record
        return None

    # ── 反馈事件 ──────────────────────────────

    def append_feedback(self, event: dict[str, Any]) -> None:
        """写入一条人工反馈事件。"""
        self._append(self._feedback_path, event)

    def feedback_events(self, record_id: Optional[str] = None) -> list[dict[str, Any]]:
        """返回反馈事件（按写入顺序）；可按记录 ID 过滤。"""
        rows = self._read(self._feedback_path)
        if record_id is not None:
            rows = [row for row in rows if row.get("record_id") == record_id]
        return rows

    def latest_feedback(self, record_id: str) -> Optional[dict[str, Any]]:
        events = self.feedback_events(record_id)
        return events[-1] if events else None

    def feedback_by_record(self) -> dict[str, list[dict[str, Any]]]:
        """按记录 ID 聚合反馈历史，供列表接口一次性读取。"""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in self._read(self._feedback_path):
            key = event.get("record_id")
            if isinstance(key, str):
                grouped.setdefault(key, []).append(event)
        return grouped


# ──────────────────────────────────────────────
# 人工反馈校验
# ──────────────────────────────────────────────


def normalize_feedback_payload(payload: Any) -> dict[str, Any]:
    """校验人工反馈请求体，返回规范化后的字段。

    规则：
      - `record_id` 必填且为非空字符串；
      - `verdict` 必须属于 `usable / unusable / undetermined`；
      - `reason` 可省略或为 `None`，否则必须属于 `FEEDBACK_REASONS`；
      - `reason="other"` 时必须提供非空 `note`；
      - 不接受未知字段，避免把模型/规则字段偷偷塞进反馈。

    Raises:
        FeedbackValidationError: 任一规则不满足。
    """
    if not isinstance(payload, dict):
        raise FeedbackValidationError("反馈请求体必须是 JSON 对象")

    allowed = {"record_id", "verdict", "reason", "note"}
    unknown = set(payload) - allowed
    if unknown:
        raise FeedbackValidationError(
            f"反馈请求包含未知字段: {', '.join(sorted(unknown))}"
        )

    record_id = payload.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise FeedbackValidationError("record_id 必须是非空字符串")

    verdict = payload.get("verdict")
    if not isinstance(verdict, str) or verdict not in FEEDBACK_VERDICTS:
        raise FeedbackValidationError(
            f"verdict 必须是 {' / '.join(FEEDBACK_VERDICTS)} 之一"
        )

    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or reason not in FEEDBACK_REASONS:
            raise FeedbackValidationError(
                f"reason 必须是 {' / '.join(FEEDBACK_REASONS)} 之一或省略"
            )

    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        raise FeedbackValidationError("note 必须是字符串")
    note_text = (note or "").strip()
    if len(note_text) > MAX_NOTE_LENGTH:
        raise FeedbackValidationError(f"note 不得超过 {MAX_NOTE_LENGTH} 字")
    if reason == REASON_NEEDS_NOTE and not note_text:
        raise FeedbackValidationError("reason=other 时必须填写 note 说明")

    return {
        "record_id": record_id.strip(),
        "verdict": verdict,
        "reason": reason,
        "note": note_text,
    }


# ──────────────────────────────────────────────
# 服务编排
# ──────────────────────────────────────────────


class DevicePhotoNotFound(DeviceError):
    """设备 gallery 中不存在指定 index 的照片。"""


class VisionFeedbackService:
    """把「设备取图 → 真实推理 → 结构化展示 → 人工反馈」串成本地闭环。"""

    def __init__(
        self,
        *,
        data_dir: Optional[str | Path] = None,
        device: Optional[DeviceClient] = None,
        store: Optional[VisionFeedbackStore] = None,
        scorer_factory: Optional[Callable[[], Any]] = None,
        analyzer: Optional[Callable[..., dict[str, Any]]] = None,
    ) -> None:
        self.store = store or VisionFeedbackStore(data_dir)
        self.device = device if device is not None else DeviceClient()
        self._scorer_factory = scorer_factory or _default_scorer_factory
        self._analyzer = analyzer or vision_analysis.analyze_jpeg
        self._scorer: Any = None
        self._scorer_lock = threading.Lock()

    # ── 配置与健康状态 ────────────────────────

    def _get_scorer(self) -> Any:
        """惰性创建 DBCNN 评分器；失败不缓存，便于修复配置后重试。"""
        with self._scorer_lock:
            if self._scorer is None:
                self._scorer = self._scorer_factory()
            return self._scorer

    def _threshold(self) -> Optional[float]:
        """读取显式配置的分数门槛；未配置返回 None（结果将恒为 needs_review）。"""
        try:
            return vision_analysis.get_configured_threshold()
        except ValueError as exc:
            raise VisionConfigError(str(exc)) from exc

    def health(self) -> dict[str, Any]:
        """返回服务配置摘要；不抛出异常，不泄露完整个人路径。"""
        model_path = (os.environ.get(ENV_MODEL_PATH) or "").strip()
        model_version = (
            (os.environ.get(ENV_MODEL_VERSION) or "").strip() or DEFAULT_MODEL_VERSION
        )

        threshold_error: Optional[str] = None
        threshold: Optional[float] = None
        try:
            threshold = vision_analysis.get_configured_threshold()
        except ValueError as exc:
            threshold_error = str(exc)

        return {
            "service": "vision_feedback",
            "record_version": RECORD_VERSION,
            "model": {
                "path_configured": bool(model_path),
                "file_exists": bool(model_path)
                and Path(model_path).expanduser().is_file(),
                "file_name": Path(model_path).name if model_path else None,
                "model_version": model_version,
                "score_source": LABEL_SOURCE_MODEL,
                "loads_lazily": True,
            },
            "threshold": {
                "configured": threshold is not None,
                "value": threshold,
                "status": "configured" if threshold is not None else "not_configured",
                "error": threshold_error,
                "note": (
                    "未校准；未配置阈值时结果恒为 needs_review，"
                    "不会用 0.5 之类的默认值代替。"
                ),
            },
            "device": {
                "configured": self.device.configured,
                "endpoint_masked": masked_endpoint(self.device.base_url),
                "note": "设备地址由 VISION_DEVICE_BASE_URL 或 --device-url 显式提供",
            },
            "storage": {
                "data_dir": str(self.store.data_dir),
                "records_file": RECORDS_FILE,
                "feedback_file": FEEDBACK_FILE,
            },
            "feedback": {
                "verdicts": list(FEEDBACK_VERDICTS),
                "reasons": list(FEEDBACK_REASONS),
                "reason_needs_note": REASON_NEEDS_NOTE,
                "max_note_length": MAX_NOTE_LENGTH,
                "label_source": FEEDBACK_SOURCE,
            },
            "limits": {"max_input_bytes": MAX_INPUT_BYTES},
            "disclaimers": list(DISCLAIMERS_BASE),
        }

    # ── 分析 ──────────────────────────────────

    @staticmethod
    def _validate_image_bytes(jpeg_bytes: Any) -> bytes:
        if not isinstance(jpeg_bytes, (bytes, bytearray)):
            raise ValueError("jpeg_bytes 必须是 bytes 或 bytearray")
        payload = bytes(jpeg_bytes)
        if not payload:
            raise ValueError("输入图像为空")
        if len(payload) > MAX_INPUT_BYTES:
            raise PayloadTooLargeError(
                f"输入图像超过上限 {MAX_INPUT_BYTES} 字节（实际 {len(payload)}）"
            )
        if not payload.startswith(JPEG_MAGIC):
            raise ValueError("输入不是 JPEG（缺少 SOI 魔数）")
        return payload

    def analyze(
        self,
        jpeg_bytes: Any,
        *,
        source: str = SOURCE_LOCAL_UPLOAD,
        device_meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """对一张 JPEG 执行真实本地推理并写入分析记录。

        Raises:
            ValueError / PayloadTooLargeError: 输入非法。
            VisionConfigError: 阈值配置非法。
            DbcnnError: 模型缺失/加载失败/推理失败（不回退规则）。
            FeedbackStoreError: 记录写入失败。
        """
        if source not in (SOURCE_DEVICE_GALLERY, SOURCE_LOCAL_UPLOAD):
            raise ValueError(f"未知输入来源: {source!r}")

        payload = self._validate_image_bytes(jpeg_bytes)
        record_id = make_record_id()
        threshold = self._threshold()

        analysis = self._analyzer(
            request_id=record_id,
            jpeg_bytes=payload,
            scorer=self._get_scorer(),
            score_threshold=threshold,
        )
        if not isinstance(analysis, dict) or analysis.get("request_id") != record_id:
            raise VisionFeedbackError("分析结果与请求 ID 不一致")

        created_at = analysis.get("created_at") or utc_now_iso()
        disclaimers = list(DISCLAIMERS_BASE)
        if analysis.get("threshold_status") == "not_configured":
            disclaimers.append("threshold_not_configured")

        record = {
            "record_id": record_id,
            "record_version": RECORD_VERSION,
            "created_at": created_at,
            "time_quality": "pc_utc",
            "input": {
                "source": source,
                "image_ref": {
                    "sha256": sha256_hex(payload),
                    "bytes_len": len(payload),
                    "format": "jpeg",
                },
                "device": device_meta,
            },
            "analysis": analysis,
            "disclaimers": disclaimers,
        }
        self.store.append_record(record)
        return record

    def analyze_device_photo(self, index: int) -> dict[str, Any]:
        """读取设备指定 index 的照片并发起真实推理。"""
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("index 必须是整数")
        if index < 0:
            raise ValueError("index 必须 >= 0")

        gallery = self.device.gallery()
        entry: Optional[dict[str, Any]] = None
        for photo in gallery.get("photos", []):
            if isinstance(photo, dict) and photo.get("index") == index:
                entry = photo
                break
        if entry is None:
            raise DevicePhotoNotFound(f"设备当前没有 index={index} 的保留照片")

        payload = self.device.photo(index)
        device_meta = {
            "index": index,
            "request_id": _run_id_or_none(entry.get("request_id")),
            "captured_ms": entry.get("captured_ms"),
            "img_status": entry.get("img_status"),
            "usable": entry.get("usable"),
            "file_name": _run_id_or_none(entry.get("file_name")),
            "width": entry.get("width"),
            "height": entry.get("height"),
            "size_bytes": entry.get("size_bytes"),
            "device_retain_limit": gallery.get("retain_limit"),
            "device_retained": gallery.get("retained"),
        }
        return self.analyze(
            payload, source=SOURCE_DEVICE_GALLERY, device_meta=device_meta
        )

    # ── 反馈与记录 ────────────────────────────

    def submit_feedback(self, payload: Any) -> dict[str, Any]:
        """校验并追加一条人工反馈事件。

        Raises:
            FeedbackValidationError: 请求体非法（400）。
            FeedbackNotFoundError: 记录不存在（404）。
            FeedbackConflictError: 与上一条反馈完全相同（409）。
            FeedbackStoreError: 写入失败（500）。
        """
        fields = normalize_feedback_payload(payload)
        record_id = fields["record_id"]

        if self.store.get_record(record_id) is None:
            raise FeedbackNotFoundError(f"记录不存在: {record_id}")

        events = self.store.feedback_events(record_id)
        latest = events[-1] if events else None
        if latest is not None and all(
            latest.get(key) == fields[key] for key in ("verdict", "reason", "note")
        ):
            raise FeedbackConflictError(
                f"该记录已有相同反馈（revision {latest.get('revision')}），未重复保存"
            )

        event = {
            "record_id": record_id,
            "revision": len(events) + 1,
            "verdict": fields["verdict"],
            "reason": fields["reason"],
            "note": fields["note"],
            "submitted_at": utc_now_iso(),
            "time_quality": "pc_utc",
            "label_source": FEEDBACK_SOURCE,
        }
        self.store.append_feedback(event)
        return event

    def records(self, limit: int = 20) -> dict[str, Any]:
        """返回最近的分析记录，并按 `record_id` 附加反馈历史（来源分离）。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit 必须是非负整数")
        rows = self.store.records(limit=limit)
        grouped = self.store.feedback_by_record()
        merged = []
        for record in rows:
            history = grouped.get(record.get("record_id"), [])
            merged.append(
                {
                    **record,
                    "feedback": {
                        "latest": history[-1] if history else None,
                        "history": history,
                    },
                }
            )
        return {"count": len(merged), "records": merged}

    # ── 设备代理（不改动设备端策略）──────────

    def gallery(self) -> dict[str, Any]:
        return self.device.gallery()

    def photo(self, index: int) -> bytes:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("index 必须是非负整数")
        return self.device.photo(index)

    def recapture(self) -> dict[str, Any]:
        return self.device.capture()

    def device_status(self) -> dict[str, Any]:
        return self.device.status()


def _default_scorer_factory() -> Any:
    """默认评分器工厂（延迟导入 onnxruntime，避免模块导入即失败）。"""
    from .vision_dbcnn import DbcnnScorer

    return DbcnnScorer()


__all__ = [
    "DeviceClient",
    "DeviceError",
    "DeviceHttpError",
    "DeviceNotConfigured",
    "DevicePhotoNotFound",
    "DeviceProtocolError",
    "DeviceUnreachable",
    "FeedbackConflictError",
    "FeedbackNotFoundError",
    "FeedbackStoreError",
    "FeedbackValidationError",
    "PayloadTooLargeError",
    "VisionConfigError",
    "VisionFeedbackError",
    "VisionFeedbackService",
    "VisionFeedbackStore",
    "normalize_feedback_payload",
    "utc_now_iso",
    "make_record_id",
    "sha256_hex",
    "resolve_data_dir",
    "default_data_dir",
    "normalize_base_url",
    "masked_endpoint",
    "is_loopback_host",
    "FEEDBACK_REASONS",
    "FEEDBACK_VERDICTS",
    "FEEDBACK_SOURCE",
    "RECORD_VERSION",
    "RECORDS_FILE",
    "FEEDBACK_FILE",
    "ENV_DEVICE_BASE_URL",
    "ENV_DATA_DIR",
    "SOURCE_DEVICE_GALLERY",
    "SOURCE_LOCAL_UPLOAD",
    "MAX_INPUT_BYTES",
]
