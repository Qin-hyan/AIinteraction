# 本地视觉反馈闭环（Week 08 W8-3）

> 本文记录 W8-3「把真实视觉质量评分接入用户可操作的反馈闭环」的实现、运行方式、数据模型与限制。
> 所有结论以实际代码、测试与真实进程冒烟为准；**未做真机端到端**的部分明确标注。

---

## 1. 作用与边界

**作用**

- 在 PC 端启动一个**独立**的本地服务（默认 `127.0.0.1:8090`），把已有 DBCNN 质量分数、Week 07 规则诊断、人工反馈放在同一个页面上，形成可操作闭环；
- 支持两种输入：设备既有照片（经设备 HTTP API）与本机 JPEG 文件（仅发送到本机回环服务）；
- 每次分析保留输入引用（SHA-256/字节数/设备 `request_id`）、模型版本、原始分数、规则诊断、判定状态与时间戳；
- 人工反馈支持「可用 / 不可用 / 无法判断」与纠正原因，独立持久化，可多版本修订；
- 重新采集复用设备既有 `POST /api/camera/capture`，**不修改** Week 07 的定时抓拍策略。

**不是**

- 不把 DBCNN 质量分数当作准确率、正确概率或遮挡类别；
- 不把 Week 07 规则诊断合并进模型结果；
- 不设置生产阈值：未显式配置 `VISION_DBCNN_SCORE_THRESHOLD` 时，判定恒为 `needs_review`，界面明确提示「分数尚未校准」；
- 不上传图片、不调用云端推理、不修改固件、不修改网络/防火墙配置；
- 不自动启动常驻服务，不提供认证，默认只监听回环地址。

---

## 2. 端到端调用路径

```
浏览器（http://127.0.0.1:8090/）
   │  GET  /api/vision/gallery            → 服务端代理设备 /api/camera/gallery
   │  GET  /api/vision/photo?index=N      → 服务端代理设备 /api/camera/photo（仅转发，不落盘）
   │  POST /api/vision/analyze            → DBCNN 真实推理 + Week 07 规则诊断 → 写 records.jsonl
   │  POST /api/vision/feedback           → 校验 + 写 feedback.jsonl（不写回分析记录）
   │  POST /api/vision/recapture          → 服务端代理设备 /api/camera/capture
   └─ GET  /api/vision/records            → 分析记录 + 反馈历史（按 record_id 关联）
```

- 设备地址由 `VISION_DEVICE_BASE_URL` 或 `--device-url` **显式**提供；未配置时设备相关接口返回
  `503 device_not_configured`，本地图片分析仍可用。
- 设备端接口全部为 Week 07 既有接口，**未新增/未修改设备端路由**。

---

## 3. 运行环境与启动

**依赖（不新增）**

- 服务本身只用 Python 标准库（`http.server`/`urllib`/`json`）；
- DBCNN 推理沿用 W8-2：`onnxruntime==1.22.1`、`opencv-python-headless`、`numpy`、`Pillow`（见 `requirements-vision.txt`）；
- 使用已验证的隔离 venv：`.workbuddy/venvs/camera-health-nano`（Python 3.11.7），**不污染 Anaconda 主环境**。

**配置**

| 环境变量 | 必需 | 说明 |
|---|---|---|
| `VISION_DBCNN_MODEL_PATH` | 是（分析时） | 本地 `dbcnn_model.onnx`；同目录需有 `.onnx.data` |
| `VISION_DBCNN_MODEL_VERSION` | 否 | 默认 `dbcnn_onnx_v1` |
| `VISION_DBCNN_SCORE_THRESHOLD` | 否 | 未配置 = 不判定；**不得**用 0.5 等值代替 |
| `VISION_DEVICE_BASE_URL` | 否 | 设备基地址（如 `http://<device>`），不猜测 |
| `VISION_FEEDBACK_DATA_DIR` | 否 | 本地记录目录，默认 `.workbuddy/vision-feedback` |

**启动（前台运行，Ctrl+C 停止）**

```powershell
$env:VISION_DBCNN_MODEL_PATH = "<repo>\.workbuddy\artifacts\dbcnn-iqa-test\weights\dbcnn_model.onnx"
$env:VISION_DEVICE_BASE_URL  = "http://<device>"   # 可选；未配置则仅本地图片可用
Remove-Item Env:VISION_DBCNN_SCORE_THRESHOLD -ErrorAction SilentlyContinue

<repo>\.workbuddy\venvs\camera-health-nano\Scripts\python.exe -m service.vision_feedback_server `
  --port 8090 --log-file "<repo>\.workbuddy\logs\vision-feedback-server.log"
```

- 默认绑定 `127.0.0.1`；使用其他地址会在启动日志中明确告警（服务无认证）；
- 端口 8090 与语音服务 8080、设备 80 独立；如端口占用请显式改 `--port`，不要改动其他服务；
- 也支持 `--model-path` / `--model-version` / `--data-dir` / `--device-timeout`。

---

## 4. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 单页反馈界面（无外部 CDN/资源） |
| GET | `/api/vision/health` | 模型/阈值/设备/存储摘要；设备端点脱敏 |
| GET | `/api/vision/gallery` | 代理设备照片元数据 |
| GET | `/api/vision/status` | 代理设备采集四态状态 |
| GET | `/api/vision/photo?index=N` | 代理设备 JPEG（`image/jpeg`，不落盘） |
| POST | `/api/vision/analyze` | `Content-Type: image/jpeg`（本地图片）或 `application/json` `{"index":N}`（设备照片） |
| POST | `/api/vision/recapture` | 下发一次按需抓拍（请求体可为 `{}`） |
| GET | `/api/vision/records?limit=20` | 最近记录 + 反馈历史（`limit` ≤ 200） |
| POST | `/api/vision/feedback` | `{record_id, verdict, reason?, note?}` |

**错误响应**统一为 `{"error": "<code>", "message": "...", "status": <http>}`：

| HTTP | code | 触发条件 |
|---:|---|---|
| 400 | `invalid_request` | JSON 非法、index 非整数、photo 缺 index、limit 非法、非 JPEG 字节 |
| 400 | `invalid_feedback` | verdict/reason 不在词表、`other` 缺说明、未知字段、note 过长 |
| 404 | `record_not_found` | 反馈引用的记录不存在 |
| 404 | `photo_not_found` | 设备 gallery 中没有该 index |
| 404 | `not_found` | 未知路径 |
| 405 | `method_not_allowed` | 方法不匹配（附 `Allow`） |
| 409 | `duplicate_feedback` | 与上一条反馈完全相同 |
| 413 | `payload_too_large` | 请求体超过上限（4 MiB + 余量） |
| 415 | `unsupported_media_type` | analyze 的 Content-Type 不受支持 |
| 500 | `model_load_failed` | 模型加载/元数据错误 |
| 500 | `threshold_config_invalid` | 阈值环境变量非法 |
| 500 | `feedback_save_failed` | 反馈写入失败（已回滚半行） |
| 500 | `storage_error` | 记录读写失败 |
| 502 | `inference_failed` | 推理失败（不回退规则） |
| 502 | `device_unreachable` / `device_http_error` / `device_protocol_error` | 设备不可达/非 2xx/响应不合规 |
| 503 | `model_not_configured` | 缺少 `VISION_DBCNN_MODEL_PATH` |
| 503 | `model_dependency_unavailable` | onnxruntime/opencv 等依赖不可用 |
| 503 | `device_not_configured` | 未提供设备地址 |

---

## 5. 页面行为

1. **服务状态**：模型版本/文件是否存在、阈值状态、设备是否配置、本地记录目录；
2. **输入**：
   - 设备照片：缩略图 + `index`、设备 `request_id`、画面状态、设备相对时间；「分析这张」；
   - 本地图片：文件选择 + 「分析这张图片」，浏览器仅在回环地址内发送，不上传云端；
3. **结果分列**：
   - DBCNN（模型）：原始分数（不四舍五入）、`model_version`、`score_source`、权重 SHA-256 前缀、耗时；
   - 判定状态：`decision`、`reject_reason`、`threshold_status`、`review_required`；
   - Week 07 规则诊断：明确标注「规则，不是模型输出」，含 `source` 与亮度/对比度/细节指标；
   - 输入与对应关系：`record_id`、设备 `request_id`、index、图像 SHA-256、字节数、记录时间；
   - 阅读限制：分数未校准、`needs_review` 含义、低分≠遮挡、反馈不改写模型结果；
4. **人工反馈**：可用/不可用/无法判断 + 原因（`score_mismatch/too_dark/too_bright/blurred/occluded/wrong_scene/other`），
   `other` 必须填写说明；显示反馈来源 `user_feedback` 与 `revision`；
5. **重新采集**：调用设备既有 `/api/camera/capture`，轮询 `/api/vision/status` 显示四态，
   并在 8 秒内轮询 gallery 是否出现新照片；
6. **最近记录**：点击一行可回看该次结果的完整对应关系与反馈历史。


---

## 6. 数据模型与持久化

仅追加（append-only）JSONL，默认目录 `.workbuddy/vision-feedback/`（已被 `.gitignore` 忽略）：

**`records.jsonl`（一次分析一行，不可变）**

```json
{
  "record_id": "vision-<ms>-<counter>",
  "record_version": "w8_3_feedback_v1",
  "created_at": "<PC UTC ISO-8601>",
  "time_quality": "pc_utc",
  "input": {
    "source": "device_gallery | local_upload",
    "image_ref": {"sha256": "...", "bytes_len": 12345, "format": "jpeg"},
    "device": {"index": 0, "request_id": "req-...", "captured_ms": 12345,
                "img_status": "ok", "file_name": "...", "device_retain_limit": 3}
  },
  "analysis": { "...": "vision_analysis.analyze_jpeg 的完整结果（含 rule_diagnostics）" },
  "disclaimers": ["score_is_raw_model_output_not_accuracy", "..."]
}
```

**`feedback.jsonl`（人工反馈事件，可多次修订）**

```json
{
  "record_id": "vision-...",
  "revision": 1,
  "verdict": "usable | unusable | undetermined",
  "reason": "score_mismatch | ... | null",
  "note": "",
  "submitted_at": "<PC UTC>",
  "time_quality": "pc_utc",
  "label_source": "user_feedback"
}
```

**关联链**：`record_id`（分析） ↔ `input.device.request_id`（设备采集）↔ `input.image_ref.sha256`（输入内容）
↔ `analysis.model_version`/`score`（DBCNN）↔ `feedback.revision`（人工意见）。

- 图像字节**不落盘**，只保存摘要与引用；`records.jsonl` 中不出现 JPEG 魔数；
- 反馈不写回分析记录；读取时按 `record_id` 组合，避免把用户意见混成模型结论；
- 写入失败时回滚半行并返回明确错误；读取容忍写入中断造成的残行。

---

## 7. 来源分离与阅读限制

| 字段 | 来源 | 允许的解读 | 禁止的解读 |
|---|---|---|---|
| `analysis.score` / `model_version` | DBCNN | 同一样本间的相对高低 | 准确率、正确概率、遮挡类别 |
| `analysis.rule_diagnostics.*` | Week 07 规则 | 亮度/对比度/细节规则诊断 | 模型输出、模型分类 |
| `feedback.*` | 用户 | 用户对这张图的主观判断 | 模型标签、自动结论 |

- `decision=needs_review` 表示「阈值未配置，需人工复核」，不是「画面不可用」；
- 低分不等于遮挡；`occluded` 只能由用户反馈表达；
- 目前只有两张样例的实测分数（`0.7561` / `0.2581`），**不能**据此评估准确率或泛化。

---

## 8. 测试

```powershell
# 单元测试（Anaconda 环境；真实模型用例自动跳过）
python -m pytest service/test_vision_result.py service/test_vision_dbcnn.py `
  service/test_vision_analysis.py service/test_image_quality.py `
  service/test_vision_feedback.py service/test_vision_feedback_server.py -q

# 含真实 ONNX 推理的冒烟（隔离 venv）
$env:VISION_DBCNN_RUN_INTEGRATION = "1"
$env:VISION_DBCNN_MODEL_PATH = "<repo>\.workbuddy\artifacts\dbcnn-iqa-test\weights\dbcnn_model.onnx"
<venv>\Scripts\python.exe -m pytest service/test_vision_feedback.py service/test_vision_feedback_server.py -q
```

结果与证据见 `TEST_LOG.md`「N」节。

覆盖要点：

- 未配置阈值 → `needs_review`；显式阈值仅为契约行为；
- 模型路径缺失/加载失败/推理失败 → 明确错误码，**规则评估器未被调用**、无记录写入；
- 缺阈值、非法阈值、超大/非 JPEG/空输入、错 Content-Type、错方法、未知路径；
- 反馈：可用/不可用/无法判断、原因词表、`other` 必填说明、重复冲突、无效输入、记录不存在、写入失败；
- 设备：未配置、gallery 缺 index、设备 HTTP 错误、非 JPEG 响应、重新采集代理；
- UTF-8 中文反馈说明可正确落盘与回读。

---

## 9. 已知限制（如实记录）

1. **未做真实设备端到端**：设备地址未提供，设备代理路径仅以替身完成接口测试；真实 `gallery/photo/capture` 需覃总提供设备地址并操作。
2. **阈值未校准**：没有生产默认值，所有真实结果均为 `needs_review`；不能用界面上的分数判断准确率。
3. **只有两张真实样例**：`ok.jpg=0.7561`、`occluded.jpg=0.2581`；不足以评估排序能力或泛化。
4. **模型卡 0–100 与实测 0–1 差异未解释**；未做 ×100 或裁剪。
5. **原始 DBCNN 权重许可未核实**（仓库 Apache-2.0 标签不构成原始权重许可结论）。
6. **存储为单进程 JSONL**：无并发多进程写入保护、无分页/清理策略；记录量增大后读取为全文件扫描。
7. **无认证**：默认仅回环地址；如需局域网访问，需自行承担访问控制风险（本任务未启用、未改防火墙）。
8. **预处理近似**：`cv2.INTER_CUBIC` 作为模型卡 PIL BICUBIC 的近似，未做像素级对齐。
9. **未验证浏览器兼容性矩阵**：页面仅用原生 JS/CSS，已在 Node 中做语法检查，未在多种浏览器实机点击验收。

---

## 10. 隐私与提交约束

- 模型（`.onnx` / `.onnx.data`）、图片、记录、日志均位于 `.workbuddy/`，**不纳入 Git**；
- 服务不采集也不回显完整设备 IP（health 中脱敏为 `198.51.*.*` 形式，仅保留前两段）；
- 不写入 API Key、密码、token；不读取 `sdkconfig`；
- 记录中不含图像字节，仅含摘要与设备引用。
