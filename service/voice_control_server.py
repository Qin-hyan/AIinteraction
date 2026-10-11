"""
voice_control_server.py — Week 05 Step 3B: PC 端语音录音 HTTP 控制 + 后台本地 ASR

职责
----
1. 用 Flask 暴露三个 HTTP 接口，控制 service/audio_capture.py 的录音：
     POST /api/voice/start   → 开始录音（映射 AudioCapture.start_recording()）
     POST /api/voice/stop    → 停止录音，返回 **ESP32 友好的小响应**（ok/state/recording
                              /wav.frames/wav.duration_seconds/asr.status，受固件 512B
                              缓冲约束），并**自动在后台提交本地 Whisper 识别任务后
                              立即返回**（不等待推理）；完整 WAV/ASR 信息由 GET /status 提供
     GET  /api/voice/status  → 当前录音状态 + ASR 状态/识别文本
2. 录音逻辑**完全复用** audio_capture.AudioCapture：本模块不碰 PCM、不写 WAV，
   只做「状态编排 + 错误语义 + ASR 任务编排」。
3. ASR 复用 service/asr_whisper.py 的 Transcriber/transcribe_wav 接口：
   模型 `base` / 语言 `zh` / 任务 `transcribe`；模型实例在 Transcriber 内缓存，
   模型加载与推理都发生在后台工作线程，**不阻塞 HTTP 响应**
   （避免 ESP32 默认约 5 秒 HTTP 超时）。
4. 明确错误语义：重复启动 / 未录音就停止 / 设备错误 / ASR 失败
   → 稳定的 HTTP 状态码 + JSON 错误对象（见下方接口约定）。
   录音停止成功与 ASR 失败**完全解耦**：ASR 失败只把 ASR 状态置为 failed，
   绝不把录音操作伪装成失败。
5. 默认只监听 127.0.0.1（仅本机回环），避免未经验证就把录音控制暴露到局域网；
   监听地址可用 create_app()/CLI 参数显式配置，为后续 ESP32 局域网接入预留。

范围约束（Step 3B）
-------------------
- 不接入 ESP32 固件，不做 ESP32 ↔ PC 真实网络测试（真实 PLAY→录音→ASR
  联调属于下一阶段）
- 不调用 LLM / TTS
- 不修改防火墙、不做任何端口暴露操作
- 录音只写本机 audio_capture.DEFAULT_OUTPUT_DIR（%TEMP%/voice_task/）；
  显式覆盖输出目录时拒绝指向仓库内（见 ensure_output_outside_repo）

接口约定
--------
POST /api/voice/start   请求体可省略；若带 body 必须是 JSON 对象（可为 {}）
  200 {"ok":true,"state":"recording","recording":true,"message":...,
       "target_path":...,"config":{...}}
  409 already_recording   已有进行中的录音
  400 invalid_request     请求体不是合法 JSON / 不是 JSON 对象
  503 audio_device_error  设备或驱动不可用（如 sounddevice 缺失、设备打开失败）
  500 audio_capture_error / internal_error

POST /api/voice/stop   （Step 3D 起为「控制回执」：小响应，与查询接口解耦）
  200 {"ok":true,"state":"idle","recording":false,
       "wav": {"frames":int,"duration_seconds":float},   # 极简关联：能定位本次录音
       "asr": {"status":"pending"|...}}                  # 回执 = 后台 ASR 已提交
      —— 录音停止成功；ASR 任务已自动提交并在后台执行，本接口不等待模型加载/推理。
      —— **不返回**完整 wav 元数据、asr 文本、长错误说明——这些调试信息一律由
         GET /api/voice/status 提供（完整 last_recording 与 asr 快照）。
      —— 响应体 UTF-8 字节数 = ESP32_RESP_BUFFER_BYTES=512 上限，防止 ESP32 固件
         512B 响应缓冲截断（voice_button.c VOICE_RESP_BUF_SIZE，Step 3C 实测回归）。
  409 not_recording       当前没有进行中的录音
  422 empty_recording     未采集到任何帧（audio_capture 不生成空 WAV）
  503 / 500               同上

GET /api/voice/status
  200 {"ok":true,"state":"idle"|"recording","recording":bool,
       "frames_recorded":int,"elapsed_seconds":float,"target_path":str|null,
       "last_recording":{...}|null,"config":{...},
       "asr":{
         "status": "idle"|"pending"|"running"|"success"|"failed",
         "text":             str|null,  # 识别文本；仅 success 后非空，失败为 null
         "wav_path":         str|null,  # 与本次 ASR 任务关联的录音文件路径
         "model":            str|null,  # Whisper 模型名（如 "base"）
         "language":         str|null,  # 固定 "zh"
         "task":             str|null,  # 固定 "transcribe"
         "device":           str|null,  # 推理设备（如 "cpu"）
         "duration_seconds": float|null, # 纯推理耗时（秒）
         "error":            str|null}   # 仅 failed 时有值；不含完整语音内容
      —— asr 是只读快照：重复轮询不会触发重复推理

404 / 405 也返回 JSON（而不是 Flask 默认 HTML），便于设备端解析。

错误对象统一形如：
  {"ok":false,"error":"<code>","message":"<可读说明>",
   "exception":"<异常类名>","state":"idle"|"recording"}

运行（务必使用仓库外隔离虚拟环境，勿用系统 Python）
--------------------------------------------------
    <venv>\\Scripts\\python.exe -m service.voice_control_server --host 127.0.0.1 --port 8080

依赖：flask（见仓库根 requirements-voice-control.txt）
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import threading

try:  # 正常以包模块方式导入（pytest / python -m service.voice_control_server）
    from . import audio_capture
except ImportError:  # pragma: no cover — 兼容 `python service/voice_control_server.py` 直接运行
    import audio_capture  # type: ignore[no-redef]

try:  # 正常以包模块方式导入（pytest / python -m service.voice_control_server）
    from . import asr_whisper
except ImportError:  # pragma: no cover — 兼容直接运行
    import asr_whisper  # type: ignore[no-redef]

# Flask 是运行本服务的最小依赖；允许在未安装时导入本模块（便于静态检查），
# 真正创建应用时再抛出带安装提示的明确错误（保持与 audio_capture 一致的风格）。
try:
    from flask import Flask, jsonify, request

    FLASK_IMPORT_ERROR: Optional[str] = None
except Exception as _exc:  # noqa: BLE001 — 导入期任何异常都记录原因
    Flask = None  # type: ignore[assignment]
    jsonify = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]
    FLASK_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

DEFAULT_HOST = "127.0.0.1"
"""默认监听地址：仅本机回环。Step 2A 不做局域网暴露。"""

DEFAULT_PORT = 8080
"""默认监听端口（架构 §4.3 约定 8080）。"""

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
"""判定为「仅本机」的监听地址；其它值视为局域网/网络暴露。"""

_REPO_ROOT = Path(__file__).resolve().parents[1]
"""仓库根目录（service/ 的上一级）；用于拒绝把录音写入仓库。"""

ESP32_RESP_BUFFER_BYTES = 512
"""ESP32 固件 voice_button.c 的响应体缓冲上限（``VOICE_RESP_BUF_SIZE = 512``）。

Step 3D 起，控制接口 ``POST /api/voice/stop`` 成功响应的 UTF-8 字节数**必须**不超过
本上限（否则固件截断并判定 `RESPONSE_INVALID`，见 Step 3C 实测缺陷）。因此 stop 只
返回 ESP32 契约所需的最小关联字段；完整 WAV/ASR 调试信息统一由 ``GET /status`` 提供。
未来若给该接口加字段，须在此约束下评估，或把新信息放到查询接口。
"""

# ASR 状态常量（JSON 中暴露的稳定英文编码；中文语义见 docstring 接口约定）
ASR_STATUS_IDLE = "idle"
"""尚无任何识别任务。"""
ASR_STATUS_PENDING = "pending"
"""识别任务已提交，等待后台工作线程执行。"""
ASR_STATUS_RUNNING = "running"
"""正在加载模型 / 执行推理（后台线程占用）。"""
ASR_STATUS_SUCCESS = "success"
"""识别成功，识别文本有效。"""
ASR_STATUS_FAILED = "failed"
"""识别失败（含依赖缺失/模型加载失败/推理异常等），录音停止事实不受影响。"""

# ──────────────────────────────────────────────
# 异常体系（每个异常自带稳定错误码与 HTTP 状态码）
# ──────────────────────────────────────────────


class VoiceControlError(Exception):
    """语音控制服务的基类异常。"""

    code = "internal_error"
    http_status = 500


class VoiceControlConfigError(VoiceControlError):
    """服务配置非法（例如把录音输出目录指向仓库内）。"""

    code = "invalid_configuration"
    http_status = 400


class InvalidRequestError(VoiceControlError):
    """HTTP 请求不符合接口约定（如 body 不是 JSON 对象）。"""

    code = "invalid_request"
    http_status = 400


class AlreadyRecordingError(VoiceControlError):
    """已有进行中的录音，禁止重复启动。"""

    code = "already_recording"
    http_status = 409


class NotRecordingError(VoiceControlError):
    """当前没有进行中的录音，无法停止。"""

    code = "not_recording"
    http_status = 409


_CAPTURE_ERROR_MAP: Tuple[Tuple[type, str, int], ...] = (
    (audio_capture.RecordingStateError, "state_error", 409),
    (audio_capture.EmptyRecordingError, "empty_recording", 422),
    (audio_capture.AudioDeviceError, "audio_device_error", 503),
    (audio_capture.AudioCaptureError, "audio_capture_error", 500),
)
"""audio_capture 异常 → (错误码, HTTP 状态码)；顺序敏感，子类必须排在基类之前。"""


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def require_flask() -> Any:
    """返回 Flask 类；未安装时抛出带安装提示的 RuntimeError。"""
    if Flask is None:
        raise RuntimeError(
            f"Flask 不可用，无法创建语音控制服务（导入错误: {FLASK_IMPORT_ERROR}）；"
            "请安装依赖: pip install -r requirements-voice-control.txt"
        )
    return Flask


def ensure_output_outside_repo(output_dir: Any) -> Path:
    """校验录音输出目录不在仓库内，返回规范化绝对路径。

    项目规则与 Step 2A 约束：「不得将 WAV 写入仓库」。默认目录是
    ``audio_capture.DEFAULT_OUTPUT_DIR``（%TEMP%/voice_task/）；本函数仅在调用方
    **显式覆盖**输出目录时起保护作用——明确失败，不静默改写为用户未指定的目录。

    Raises:
        VoiceControlConfigError: 目录位于仓库根之内（含仓库根本身）。
    """
    target = Path(output_dir).expanduser().resolve()
    if target == _REPO_ROOT or _REPO_ROOT in target.parents:
        raise VoiceControlConfigError(
            f"拒绝把录音输出目录指向仓库内: {target}"
            f"（仓库根 {_REPO_ROOT}）；请使用仓库外目录，默认 %TEMP%/voice_task/"
        )
    return target


def error_payload(
    exc: BaseException, service: Optional["VoiceControlService"] = None
) -> Tuple[Dict[str, Any], int]:
    """把任意异常映射为统一的 JSON 错误对象与 HTTP 状态码。

    仅做映射，不吞掉信息：错误码取自本模块异常或 audio_capture 异常表，
    原始异常类名保留在 ``exception`` 字段，方便排查。
    """
    if isinstance(exc, VoiceControlError):
        code, status = exc.code, exc.http_status
    else:
        code, status = "internal_error", 500
        for exc_type, mapped_code, mapped_status in _CAPTURE_ERROR_MAP:
            if isinstance(exc, exc_type):
                code, status = mapped_code, mapped_status
                break

    payload: Dict[str, Any] = {
        "ok": False,
        "error": code,
        "message": str(exc),
        "exception": type(exc).__name__,
    }
    if service is not None:
        try:
            payload["state"] = service.state_name()
        except Exception:  # noqa: BLE001 — 状态不可读时不得掩盖原始错误
            payload["state"] = None
    return payload, status


def optional_json_object() -> Optional[Dict[str, Any]]:
    """读取可选的 JSON 对象请求体。

    Returns:
        省略 body → None；合法 JSON 对象 → dict。

    Raises:
        InvalidRequestError: body 存在但不是合法 JSON，或不是 JSON 对象。
    """
    raw = request.get_data(cache=True)
    if not raw:
        return None
    body = request.get_json(silent=True)
    if body is None:
        raise InvalidRequestError("请求体存在但不是合法 JSON")
    if not isinstance(body, dict):
        raise InvalidRequestError("请求体必须是 JSON 对象，例如 {}")
    return body


# ──────────────────────────────────────────────
# ASR 后台任务编排（Week 05 Step 3B）
# ──────────────────────────────────────────────


class AsrTaskRunner:
    """后台 ASR 任务执行器：录音停止后自动入队，模型加载与推理都在工作线程。

    设计约束（Week 05 Step 3B）：
    * ``submit()`` 只做「登记任务 + 唤醒工作线程」，立即返回——POST /api/voice/stop
      不会等待模型加载或推理结束（避免 ESP32 默认约 5 秒 HTTP 超时）。
    * 同一时刻最多一个识别任务在跑（单工作线程）；若识别期间又提交了新任务，
      旧任务完成时因 epoch 不匹配被丢弃，只有最新一次提交会发布到状态。
    * 状态轮询（GET /api/voice/status 的 snapshot）是只读快照，绝不触发推理。
    * 录音停止成功与 ASR 状态解耦：识别失败只把状态置为 failed，不回去改写
      录音结果。
    * 不打印识别文本/语音内容日志（不把完整语音内容写入日志）。
    """

    def __init__(self, transcriber: Optional[Any] = None) -> None:
        self._transcriber = transcriber
        self._cond = threading.Condition()
        self._epoch = 0  # 最新任务编号（单调递增，用于丢弃过期任务的结果）
        self._job: Optional[Tuple[int, str, Dict[str, Any]]] = None
        self._thread: Optional[threading.Thread] = None
        self._status = ASR_STATUS_IDLE
        self._text: Optional[str] = None
        self._meta: Dict[str, Any] = {}

    # ── 只读视图 ─────────────────────────────

    @property
    def transcriber(self) -> Any:
        """被注入的转录器（asr_whisper.Transcriber 或兼容对象）。"""
        return self._transcriber

    def _base_meta(self) -> Dict[str, Any]:
        tr = self._transcriber
        return {
            "wav_path": None,
            "model": getattr(tr, "model_name", None),
            "language": getattr(tr, "language", None),
            "task": getattr(tr, "task", None),
            "device": getattr(tr, "device", None),
            "duration_seconds": None,
            "error": None,
        }

    # ── 任务提交 ─────────────────────────────

    def submit(self, wav_path: Any) -> int:
        """登记一个识别任务并唤醒工作线程；立即返回新任务的 epoch。

        不执行任何模型加载/推理；真实工作由工作线程 ``_run`` 承担。
        """
        with self._cond:
            self._epoch += 1
            epoch = self._epoch
            meta = self._base_meta()
            meta["wav_path"] = str(wav_path)
            self._job = (epoch, str(wav_path), meta)
            self._status = ASR_STATUS_PENDING
            self._text = None
            self._meta = meta
            self._cond.notify_all()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="voice-asr-worker", daemon=True
                )
                self._thread.start()
        return epoch

    def fail_submit(self, message: str) -> None:
        """登记路径出错时的兜底：把 ASR 状态直接置为 failed，并作废在途任务。

        仅当 ``submit()`` 自身异常时由服务调用，保证「录音停止成功」的事实
        绝不会因为 ASR 提交失败而被改写。
        """
        with self._cond:
            self._epoch += 1  # 让任何在途任务过期
            self._job = None
            self._status = ASR_STATUS_FAILED
            self._text = None
            self._meta = {"error": message}

    # ── 状态查询 ─────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """返回 ASR 状态只读快照（无副作用，可安全反复轮询）。"""
        with self._cond:
            status = self._status
            text = self._text
            meta = dict(self._meta)
        out: Dict[str, Any] = {"status": status, "text": text}
        for key in (
            "wav_path",
            "model",
            "language",
            "task",
            "device",
            "duration_seconds",
            "error",
        ):
            out[key] = meta.get(key)
        return out

    # ── 工作线程 ─────────────────────────────

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._job is None:
                    # 没有待处理任务：交还线程所有权，允许下次 submit 重建线程
                    self._thread = None
                    return
                epoch, wav_path, pending_meta = self._job
                self._job = None
                self._status = ASR_STATUS_RUNNING
                self._meta = dict(pending_meta)
            # ── 推理阶段：不持有锁（模型加载/解码/推理耗时可能很长） ──
            result_meta = dict(pending_meta)
            text: Optional[str] = None
            try:
                result = self._transcriber.transcribe_wav(wav_path)
            except Exception as exc:  # noqa: BLE001 — 统一收敛为失败状态
                result_meta["error"] = f"{type(exc).__name__}: {exc}"
            else:
                text = getattr(result, "text", None)
                result_meta.update(
                    {
                        "model": getattr(result, "model", None)
                        or pending_meta["model"],
                        "language": getattr(result, "language", None)
                        or pending_meta["language"],
                        "task": getattr(result, "task", None)
                        or pending_meta["task"],
                        "device": getattr(result, "device", None)
                        or pending_meta["device"],
                        "duration_seconds": getattr(result, "duration_seconds", None),
                    }
                )
            with self._cond:
                # 只有「最新一次提交」允许发布结果；旧任务被新提交取代时直接丢弃
                if epoch == self._epoch:
                    self._status = (
                        ASR_STATUS_SUCCESS if text is not None else ASR_STATUS_FAILED
                    )
                    self._text = text
                    self._meta = result_meta


# ──────────────────────────────────────────────
# 服务：录音状态编排
# ──────────────────────────────────────────────


class VoiceControlService:
    """把 ``AudioCapture`` 的 start/stop 暴露为可查询的 HTTP 服务语义。

    本类**不实现任何录音逻辑**：参数校验、设备打开、缓冲、写 WAV、落盘复核全部由
    ``audio_capture.AudioCapture`` 负责。这里只做：

    * 状态编排：记录本次录音的目标路径与最近一次成功录音的元数据
    * 顺序检查：重复启动 → AlreadyRecordingError；未录音就停止 → NotRecordingError
    * 串行化：start/stop 由本类锁保护，避免并发按键事件互相踩踏

    状态事实来源单一：录音是否在进行，一律读 ``capture.is_recording``，
    不在本类里维护第二份录音状态。
    """

    def __init__(
        self,
        capture: Optional[Any] = None,
        output_dir: Optional[Any] = None,
        asr: Optional[Any] = None,
        asr_task_runner: Optional[Any] = None,
    ) -> None:
        """构造服务。

        Args:
            capture:         可注入的 AudioCapture 兼容对象（测试用假实现）；
                            None 表示使用真实 ``audio_capture.AudioCapture``。
            output_dir:      录音输出目录；仅当 capture=None 时可用。
                            None → ``audio_capture.DEFAULT_OUTPUT_DIR``（%TEMP%/voice_task/）。
            asr:             可注入的转录器（asr_whisper.Transcriber 或兼容对象；
                            测试用假模型包装）。None → 真实 ``asr_whisper.Transcriber()``
                            （base/zh/transcribe，模型惰性加载，首次推理才加载/下载权重）。
            asr_task_runner: 可注入的替代 AsrTaskRunner（高级定制/测试用）；与 asr 互斥。

        Raises:
            VoiceControlConfigError: capture 与 output_dir 同时指定；asr 与 asr_task_runner
                            同时指定；或输出目录在仓库内。
        """
        if capture is not None and output_dir is not None:
            raise VoiceControlConfigError(
                "capture 与 output_dir 不能同时指定：注入 capture 时以其自带 output_dir 为准"
            )
        if asr is not None and asr_task_runner is not None:
            raise VoiceControlConfigError(
                "asr 与 asr_task_runner 不能同时指定：注入 runner 时以其自带转录器为准"
            )

        if capture is None:
            effective = (
                ensure_output_outside_repo(output_dir)
                if output_dir is not None
                else audio_capture.DEFAULT_OUTPUT_DIR
            )
            capture = audio_capture.AudioCapture(output_dir=effective)
        else:
            configured = getattr(capture, "output_dir", None)
            if configured is not None:
                ensure_output_outside_repo(configured)

        if asr_task_runner is not None:
            self._asr_runner = asr_task_runner
        else:
            transcriber = asr if asr is not None else asr_whisper.Transcriber()
            self._asr_runner = AsrTaskRunner(transcriber=transcriber)

        self._capture = capture
        self._lock = threading.Lock()
        self._target_path: Optional[Path] = None
        self._last_recording: Optional[Any] = None

    # ── 只读视图 ─────────────────────────────

    @property
    def capture(self) -> Any:
        """底层采集器（真实 AudioCapture 或被注入的兼容对象）。"""
        return self._capture

    @property
    def asr_runner(self) -> AsrTaskRunner:
        """后台 ASR 任务执行器（录音停止后自动提交识别任务）。"""
        return self._asr_runner

    @property
    def last_recording(self) -> Optional[Any]:
        """最近一次成功停止的 RecordingInfo；从未成功录音时为 None。"""
        return self._last_recording

    def config(self) -> Dict[str, Any]:
        """录音配置快照（不做设备 I/O）。"""
        return {
            "samplerate": self._capture.samplerate,
            "channels": self._capture.channels,
            "dtype": self._capture.dtype,
            "device": self._capture.device,
            "output_dir": str(self._capture.output_dir),
        }

    def state_name(self) -> str:
        """当前录音状态名：``"recording"`` 或 ``"idle"``（取自采集器真实状态）。"""
        return "recording" if self._capture.is_recording else "idle"

    def status(self) -> Dict[str, Any]:
        """返回当前状态的 JSON 可序列化表示（无副作用）。"""
        recording = bool(self._capture.is_recording)
        with self._lock:
            target = self._target_path
            last = self._last_recording
        return {
            "ok": True,
            "state": "recording" if recording else "idle",
            "recording": recording,
            "frames_recorded": int(self._capture.frames_recorded),
            "elapsed_seconds": round(float(self._capture.duration_seconds), 4),
            "target_path": str(target) if target is not None else None,
            "last_recording": last.as_dict() if last is not None else None,
            "config": self.config(),
            "asr": self._asr_runner.snapshot(),
        }

    # ── 控制 ─────────────────────────────────

    def start(self) -> Dict[str, Any]:
        """开始录音。

        Raises:
            AlreadyRecordingError: 已有进行中的录音（不重复调用底层采集器）。
            audio_capture.*:       设备/依赖/参数错误，原样上抛（由 error_payload 映射）。
        """
        with self._lock:
            if self._capture.is_recording:
                raise AlreadyRecordingError(
                    "已有进行中的录音，禁止重复启动（请先调用 /api/voice/stop）"
                )
            target = self._capture.start_recording()
            self._target_path = Path(target)
            self._last_recording = None
            return {
                "ok": True,
                "state": "recording",
                "recording": True,
                "message": (
                    "录音已开始（device="
                    f"{self._capture.device!r}, {self._capture.samplerate}Hz/"
                    f"{self._capture.channels}ch/{self._capture.dtype}）"
                ),
                "target_path": str(self._target_path),
                "config": self.config(),
            }

    def stop(self) -> Dict[str, Any]:
        """停止录音，返回 **ESP32 友好的小响应**，并自动在后台提交本地 ASR 任务。

        控制接口与查询接口解耦（Step 3D）：
        - 本接口只返回固件契约与「必要关联信息」所需的最小字段
          （``ok`` / ``state`` / ``recording`` / ``wav.frames`` /
          ``wav.duration_seconds`` / ``asr.status``），保证响应体 UTF-8 字节数
          远小于 ESP32 固件 ``voice_button.c`` 的 512B 响应缓冲
          （``VOICE_RESP_BUF_SIZE``）——避免 Step 3C 实测的截断回归。
        - 完整 WAV 元数据（``last_recording``）与 ASR 文本/耗时等调试信息一律由
          ``GET /api/voice/status`` 提供，本接口**不返回**。

        录音停止成功后**立即返回**（不等待模型加载/推理）：ASR 任务入队后由
        AsrTaskRunner 的工作线程执行；状态与识别文本通过 ``status()`` 查询。

        Raises:
            NotRecordingError:      当前没有进行中的录音。
            audio_capture.*:        空录音/设备错误/数据不完整等，原样上抛。
        """
        with self._lock:
            if not self._capture.is_recording:
                raise NotRecordingError("当前没有进行中的录音，无法停止")
            try:
                info = self._capture.stop_recording()
            finally:
                # 无论成功或失败，本次的待写路径都作废（真实状态以采集器为准）
                self._target_path = None
            self._last_recording = info
            # 录音已成功保存：自动提交后台 ASR（入队即返回，不阻塞本接口）。
            # 提交异常也绝不能把「录音停止成功」改写成失败——收敛为 ASR failed。
            try:
                self._asr_runner.submit(info.path)
            except Exception as exc:  # noqa: BLE001 — 后台提交失败不影响录音事实
                self._asr_runner.fail_submit(f"{type(exc).__name__}: {exc}")
            # 紧凑回执：只含 ESP32 契约必需字段 + 极简关联信息。
            # wav 只取 frames/duration_seconds（固件用于日志关联；见
            # voice_button.c voice_post_action），asr 只取 status（回执 = 已提交）。
            return {
                "ok": True,
                "state": "idle",
                "recording": False,
                "wav": {
                    "frames": int(info.frames),
                    "duration_seconds": round(float(info.duration_seconds), 4),
                },
                "asr": {
                    "status": self._asr_runner.snapshot()["status"],
                },
            }


# ──────────────────────────────────────────────
# Flask 应用工厂
# ──────────────────────────────────────────────


def create_app(
    capture: Optional[Any] = None,
    output_dir: Optional[Any] = None,
    asr: Optional[Any] = None,
) -> Any:
    """创建 Flask 应用（工厂函数，便于测试注入假采集器/假转录器）。

    Args:
        capture:    注入的采集器；None → 真实 ``AudioCapture``。
        output_dir: 录音输出目录；仅当 capture=None 时可用，默认 %TEMP%/voice_task/。
        asr:        注入的转录器（测试用假模型包装）；None → 真实
                    ``asr_whisper.Transcriber()``（base/zh/transcribe）。

    Returns:
        Flask app。服务实例挂在 ``app.extensions["voice_control"]``。
    """
    flask_cls = require_flask()
    service = VoiceControlService(capture=capture, output_dir=output_dir, asr=asr)

    app = flask_cls(__name__)
    # 中文提示按 UTF-8 原样输出（不转成 \uXXXX），便于 ESP32/浏览器直接读取
    app.json.ensure_ascii = False
    app.extensions["voice_control"] = service

    def _fail(exc: BaseException):
        payload, status = error_payload(exc, service)
        return jsonify(payload), status

    @app.post("/api/voice/start")
    def voice_start():
        try:
            optional_json_object()
            payload = service.start()
        except Exception as exc:  # noqa: BLE001 — 统一转为 JSON 错误响应
            return _fail(exc)
        return jsonify(payload), 200

    @app.post("/api/voice/stop")
    def voice_stop():
        try:
            optional_json_object()
            payload = service.stop()
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)
        return jsonify(payload), 200

    @app.get("/api/voice/status")
    def voice_status():
        try:
            payload = service.status()
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)
        return jsonify(payload), 200

    @app.errorhandler(404)
    def _not_found(_exc):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "not_found",
                    "message": "接口不存在",
                    "exception": "NotFound",
                    "state": service.state_name(),
                }
            ),
            404,
        )

    @app.errorhandler(405)
    def _method_not_allowed(_exc):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "method_not_allowed",
                    "message": "HTTP 方法不被允许",
                    "exception": "MethodNotAllowed",
                    "state": service.state_name(),
                }
            ),
            405,
        )

    return app


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """启动 HTTP 服务（默认仅监听本机回环地址）。

    默认 ``127.0.0.1:8080``：不暴露到局域网，符合 Step 2A 范围约束。
    显式传入 ``--host 0.0.0.0`` 等地址才会对外监听，此时打印警告——
    局域网接入（含防火墙）尚未验证，属于后续任务。
    """
    parser = argparse.ArgumentParser(
        prog="voice_control_server",
        description="Week 05 Step 2A — PC 端语音录音 HTTP 控制服务（默认仅监听本机）",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            f"监听地址，默认 {DEFAULT_HOST}（仅本机）；"
            "改为 0.0.0.0 才会暴露到局域网（未验证，请自行评估授权与风险）"
        ),
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"监听端口，默认 {DEFAULT_PORT}"
    )
    parser.add_argument(
        "--device", default=None, help="输入设备下标或名称（默认系统默认输入设备）"
    )
    parser.add_argument(
        "--samplerate",
        type=int,
        default=audio_capture.DEFAULT_SAMPLERATE,
        help=f"采样率 (Hz)，默认 {audio_capture.DEFAULT_SAMPLERATE}",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        # 注意：argparse 会对 help 文本做 % 格式化，字面量百分号必须写成 %%
        help="WAV 输出目录，默认 %%TEMP%%/voice_task/（不得指向仓库内）",
    )
    args = parser.parse_args(argv)

    device: Optional[Any] = args.device
    if isinstance(device, str) and device.isdigit():
        device = int(device)  # "1" → 1（PortAudio 设备下标）

    capture = audio_capture.AudioCapture(
        samplerate=args.samplerate,
        device=device,
        output_dir=(
            ensure_output_outside_repo(args.output_dir) if args.output_dir else None
        ),
    )
    app = create_app(capture=capture)

    if args.host not in LOOPBACK_HOSTS:
        print(
            f"[警告] 监听地址 {args.host} 不是本机回环地址：服务将暴露到局域网/网络。"
            "Step 2A 未验证该暴露方式（无防火墙/网络测试证据），请自行确认授权与安全。"
        )
    print(f"[voice] 监听 http://{args.host}:{args.port}")
    print(f"[voice] 录音输出目录: {capture.output_dir}")
    print(
        f"[voice] 录音参数: {capture.samplerate}Hz / {capture.channels}ch / {capture.dtype}"
    )

    # 固定 debug=False / use_reloader=False：重载器会再起一个进程，可能重复占用麦克风
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())