# Week 08 State — 把视觉推理结果变成可理解反馈

**分支**: `feature/week08-vision-feedback`（基线 `origin/main` `333a697`，PR #8 已合并 Week 07）
**日期**: 2026-10-18（官方要求核实日）；W8-1 实施 2026-10-19；W8-2 实施 2026-10-19；W8-3 实施 2026-10-20
**阶段**: 🟢 **W8-1 / W8-2 / W8-3 / W8-4 均已实现并有测试证据**；⚠️ 阈值校准、真机设备端到端、更多真值样例仍未完成

---

## 1. 课程官方要求（2026-10-18 核实自课程主页）

**本周问题**：模型返回一个标签和分数，系统应该如何表达"看到了什么"和"还有什么不能确定"？

**单元归属**：单元 3「视觉感知与事件反馈」（第 7–9 周），对应课程目标 1、3、4、5、6。

### 前 2 学时（理论 1、实践 1）
- 讲解预测标签、分数、拒识及模型版本；
- 本轮以**画面可用性/遮挡识别**为共同保底任务，场景粗分类作为拓展；
- 接入真实模型推理。

### 后 2 学时（理论 0、实践 2）
- 实现结构化结果解析、**低分/未知反馈**、**重新采集**和**人工纠正**；
- **保留输入、结果和反馈三者对应**，比较高分误判与正常样例。

### 当堂验证
- 未经校准的模型分数不写成现实正确率；
- 场景类别不转换为精确位置或人身安全判断。

### 交付物
- 视觉识别—反馈闭环；
- 正常、低分、错误和未知样例对照；
- 迁移：为个人项目的一项模型判断补上纠错入口。

### 范围控制（备用路径）
- 学生模型未就绪时先调用教师真实基线；**静态标签只能测试界面，不计入真实推理证据**。

### 对应测试用例
- T03（视觉结果与用户纠错）、T04（低置信度/异常结构输出）

---

## 2. 与 Week 07 既有能力的衔接分析

| Week 08 要求 | Week 07 已有基础 | 缺口 |
|---|---|---|
| 画面可用性/遮挡识别（保底） | ✅ 双端 `image_quality`（四态+score+metrics） | 无 |
| 结构化结果解析 | ✅ `img_status`/metrics/score 已结构化 | 需补**拒识/未知**语义 |
| 分数与模型版本 | ⚠️ 有 score（0~1 相对值） | 需补**算法版本标识**（如 `iq_v1`） |
| 低分/未知反馈 | ⚠️ score 存在但未驱动反馈 | 需新增低分/不确定反馈路径 |
| 重新采集 | ✅ `POST /api/camera/capture` | 需与反馈闭环关联 |
| 人工纠正 | ❌ 无 | 需新增纠正接口与记录 |
| 输入-结果-反馈三者对应 | ⚠️ 输入(request_id)+结果(img_status)已关联 | 需补**反馈记录**关联 |
| 正常/低分/错误/未知样例对照 | ⚠️ 已有 too_dark/low_detail 实机样例 | 需系统整理（部分待覃总实机配合） |

## 3. 待办

- [x] 待 Week 07 分支合并后创建本周分支（`origin/main` `333a697`）
- [x] W8-1 结构化视觉结果契约与拒识语义
- [x] W8-2 接入真实模型推理（本地 DBCNN ONNX；**阈值校准仍未完成**，见第 5 节）
- [x] W8-3 低分/未知反馈、重新采集与人工纠正闭环（独立本地服务，见第 6 节）
- [x] W8-4 将视觉反馈自然并入 ESP32 Dashboard（API-only 本地服务 + 内嵌面板 + CORS/PNA，见第 7 节）
- [ ] 阈值校准与正式默认值（**不得自造**；需用户提供校准依据）
- [ ] 真实设备端到端（设备地址 + 人工操作）
- [ ] 更多带真值的正常/低分/错误/未知样例对照

---

## 4. W8-1 结构化视觉结果契约与拒识语义（2026-10-19 完成）

**目标**：把「标签 + 分数」变成可追踪、可审计的结构化结果，明确 `ok / low_score / unknown` 三态与拒识原因。

**实现**：
- `service/vision_result.py`：`build_vision_result(request_id, raw_output, score_threshold, model_version="iq_v1", created_at=None)`
  - 契约字段：`request_id / label / score / model_version / decision / reject_reason / created_at`
  - 附带解释性元数据：`time_quality="pc_utc"`、`score_kind="relative_usability"`、`score_threshold`
  - `decision`：`ok`（结构合法、标签受支持、分数达门槛；**不表示画面可用**）/ `low_score` / `unknown`；W8-2 为仅分数模型增加 `needs_review`（阈值未配置，见第 5 节）
  - 拒识原因：`no_result`、`parse_error`、`missing_label`、`unsupported_label`、`missing_score`、`invalid_score`、`image_invalid`、`score_below_threshold`
  - `label` 词汇表复用 `image_quality.IMAGE_STATUSES`（新增具名常量），**未新造标签**
  - **阈值不内置**：`score_threshold` 无默认值，调用方必须显式提供且位于 `(0, 1]`；W8-1 未校准，不编造阈值
  - `score` 为原始 0~1 相对可用性，合法时原值保留；**不得称准确率/置信度**
  - 模型侧异常（`None`/坏 JSON/缺字段/非法分数/未知标签/无效图像）→ `unknown` + 原因，不抛异常
  - 调用方错误（`request_id`、阈值、`model_version`）→ `ValueError`，不伪装成模型结果
  - 时间：`created_at` 为 PC 端 UTC ISO-8601（毫秒 + `Z`），不与设备相对时间混用（D002）
- `service/test_vision_result.py`：**52 项**测试（含参数化用例）

**验证证据**：
- 命令：`python -m pytest service/test_vision_result.py service/test_image_quality.py -q`
- 结果：**62 passed in 0.44s**（新增 52 + `image_quality` 回归 10）；日志 `.workbuddy/logs/w8-1-tests.log`（本地忽略，未提交）
- 验证层级：**单元测试**；未编译/未烧录/未接入真实模型

**范围外（未做，不做宣称）**：真实模型推理、模型适配器、HTTP API、持久化、反馈文案/界面、人工纠正、重采闭环。

**W8-2 原阻塞点（2026-10-19 更新）**：
1. ✅ **真实推理入口**：已接入本地 DBCNN ONNX（`service/vision_dbcnn.py`），ONNX Runtime CPU；见第 5 节。
2. ⏳ **真实失败/验证样本与真值**：仅有 `ok.jpg` / `occluded.jpg` 两张既有样例，**无质量真值/标签**，不能评估准确率或泛化。
3. ⏳ **校准后的低分阈值**：仍未校准；未配置时返回 `needs_review`，**不设生产默认值**。
4. ✅ **模型版本标识**：使用 `dbcnn_onnx_v1` 作为本适配器/权重快照标识；上游模型无可用语义版本，未伪造。

---

## 5. W8-2 本地 DBCNN 真实模型接入（2026-10-19）

**目标**：用已验证的 DBCNN ONNX 权重替换「仅规则判断」，让本地视觉流程返回真实质量分数，并严格区分模型分数与规则诊断。

**实现**：
- `service/vision_dbcnn.py`：独立 DBCNN 适配器
  - 本地 `.onnx` + 外部 `.onnx.data`；`onnxruntime==1.22.1` 仅 `CPUExecutionProvider`
  - 预处理复用已验证约定：BGR→RGB、`cv2.INTER_CUBIC` 224×224、`/255.0`、ImageNet mean/std、NCHW float32
  - 返回原始 0~1 分数（不乘 100、不裁剪）、`model_version`、`.onnx`/`.onnx.data` SHA-256、推理耗时
  - 模型路径缺失/文件不存在/加载失败/推理失败 → 明确 `DbcnnError` 子类，**不静默退回规则**
  - 配置：`VISION_DBCNN_MODEL_PATH`（必需）、`VISION_DBCNN_MODEL_VERSION`（可选，默认 `dbcnn_onnx_v1`）
- `service/vision_result.py`：新增 `build_score_vision_result()`
  - 仅分数模型：`label=None`、`label_source=None`、`score_source` 显式
  - 未配置阈值 → `decision=needs_review` / `reject_reason=threshold_not_configured` / `review_required=true`
  - 新增 `threshold_status=configured|not_configured`；阈值仍无默认值
- `service/vision_analysis.py`：现有本地视觉模块之上的组合入口 / CLI（**不新开端口**）
  - DBCNN 结果与 `rule_diagnostics` 分开；规则诊断 `source=week07_image_quality_rules`
  - 规则依赖缺失时 `available=false` + `label=null`，不冒充 `invalid`
  - 配置：`VISION_DBCNN_SCORE_THRESHOLD`（可选，未设置即不判定）
- `service/image_quality.py`：新增 `is_available()`，用于区分「规则依赖缺失」与「图像无效」
- 依赖：`requirements-vision.txt` 增加 `onnxruntime==1.22.1`、`opencv-python-headless==5.0.0.93`
- 文档：`docs/vision-dbcnn.md`（配置、来源、许可注意事项、限制、测试命令）

**真实推理证据（隔离 venv，本地权重）**：

| 样例 | DBCNN 原始分数 | 规则诊断 | `decision` |
|---|---:|---|---|
| `.workbuddy/week07_acceptance/ok.jpg` | `0.7561315298080444` | `ok`（luma 118.57 / contrast 63.41 / detail 8.32） | `needs_review` |
| `.workbuddy/week07_acceptance/occluded.jpg` | `0.25810927152633667` | `too_dark`（luma 5.99 / contrast 3.71 / detail 0.37） | `needs_review` |

- 未配置阈值：两张均为 `needs_review` / `threshold_not_configured`；
- 显式测试阈值 `0.5`：`ok.jpg` → `ok`，`occluded.jpg` → `low_score`（**仅契约行为验证，不是生产默认值**）；
- 缺少 `VISION_DBCNN_MODEL_PATH` 时 CLI 返回 `DbcnnConfigError` 且退出码 2，没有规则结果冒充模型；
- 在 ONNX Runtime 导入失败的 Anaconda 环境中，CLI 返回 `DbcnnDependencyError` 且退出码 2，同样不回退规则判断（真实推理使用隔离 venv）。

**验证证据**：
- 单元测试（Anaconda）：`service/test_vision_result.py` + `test_vision_dbcnn.py` + `test_vision_analysis.py` + `test_image_quality.py` → **124 passed, 1 skipped**
- 真实集成（隔离 venv）：同一组测试 + `VISION_DBCNN_RUN_INTEGRATION=1` → **125 passed**
- 验证层级：单元测试 + 本地真实 ONNX 推理；**未烧录、未改固件、未做 HTTP/UI/设备端集成、未做阈值校准**

**W8-2 未完成（如实记录）**：
- ⏳ 校准后的正式阈值；当前没有生产默认值；
- ⏳ 更多真实失败/正常样例与质量真值；当前只有 2 张；
- ⏳ 模型卡 0–100 与实测 0–1 的差异未解释；
- ⏳ 原始 DBCNN 权重许可未核实（HF 仓库标签不自动证明）；
- ✅ **HTTP 端点/反馈 UI**：已由第 6 节 W8-3 的独立本地服务补齐（W8-2 时点的这条缺口不再成立）。

---

## 6. W8-3 真实分数 → 用户可操作的反馈闭环（2026-10-20）

**目标**：把 W8-2 的真实质量分数接入可操作的本地界面：看结果、给反馈、必要时重采，并保留输入/结果/反馈的对应关系。

**入口选择**：
- 设备 Dashboard 内嵌在固件 `main/http_server.c` 中，直接改需改固件并重新烧录；语音服务 `service/voice_control_server.py`（8080）属于语音链路，**不得塞入视觉功能**；
- 因此新增**独立**本地服务 `service/vision_feedback_server.py`：仅标准库、默认 `127.0.0.1:8090`、不自动启动、不与 8080/80 冲突；
- 设备端只调用既有接口（`/api/camera/gallery|photo|status|capture`），**未改固件、未改 Week 07 定时抓拍策略**。

**实现**：
- `service/vision_feedback.py`（领域层）：
  - `VisionFeedbackService.analyze()`：JPEG 校验 → `vision_analysis.analyze_jpeg()`（真实 DBCNN + 规则诊断）→ 追加 `records.jsonl`；
  - `analyze_device_photo(index)`：先读 gallery 匹配 index，再取 JPEG；保留设备 `request_id`、`captured_ms`、`img_status`；
  - `submit_feedback()`：校验词表/说明 → 追加 `feedback.jsonl`（`revision` 单调递增；与上一条完全相同 → 409 冲突）；
  - `DeviceClient`：复用设备既有 API，显式超时与错误分类；地址必须显式配置，不猜测；
  - `VisionFeedbackStore`：仅追加 JSONL，失败回滚半行；容忍残行；**不保存图像字节**；
  - 反馈词表：`usable/unusable/undetermined`；原因 `score_mismatch/too_dark/too_bright/blurred/occluded/wrong_scene/other`，`other` 必须填写说明；
  - `occluded` 只作为人工观察，禁止由低分或 `noise_drift` 自动推导。
- `service/vision_feedback_server.py`（HTTP/UI）：
  - 9 个端点（页面、health、gallery、status、photo、analyze、recapture、records、feedback），统一 JSON 错误码；
  - 页面分列展示：DBCNN 原始分数/版本、判定状态、Week 07 规则诊断、输入与对应关系、阅读限制；
  - 反馈表单（三选一 + 原因 + 说明）与反馈历史；
  - 重新采集：调用设备 `POST /api/camera/capture`，轮询 `/api/vision/status` 显示四态，8 秒内轮询 gallery 是否出现新照片；
  - 阈值未配置时页面显著提示「分数尚未校准 / needs_review / 不使用 0.5 默认值」；
  - health 中设备地址脱敏（如 `198.51.*.*`）。
- 文档：`docs/vision-feedback.md`；`docs/vision-dbcnn.md` 限制条目更新；`requirements-vision.txt` 说明不新增依赖。

**验证证据（详见 TEST_LOG「N」）**：

| 层级 | 命令/方式 | 结果 |
|---|---|---|
| 单元 + 接口 | Anaconda `pytest`（视觉相关 6 个测试文件） | **204 passed, 2 skipped** |
| 单元 + 接口 + 真实 ONNX | 隔离 venv `.workbuddy/venvs/camera-health-nano` + `VISION_DBCNN_RUN_INTEGRATION=1` | **206 passed** |
| 真实进程 + HTTP | 隔离 venv 启动 `127.0.0.1:8090`，`ok.jpg`/`occluded.jpg` | `0.7561315298080444` / `0.25810927152633667`，均 `needs_review`；规则 `ok` / `too_dark` |
| 反馈闭环 | 真实进程 POST 反馈、重复、非法、未知记录 | 201 / 409 / 400 / 404；中文说明 UTF-8 回读一致 |
| 错误边界 | 无设备地址 gallery、photo 缺 index、GET analyze | 503 `device_not_configured` / 400 / 405 `Allow: POST` |
| 页面 | Node `--check` 提取脚本 | 语法通过；页面 200、25077 B，含未校准与来源分离提示 |

**W8-3 未完成（如实记录）**：
- ⏳ **真实设备端到端**：未提供设备地址，`gallery/photo/capture` 仅以替身测试；未在真机上点击验证；
- ⏳ 阈值校准与正式默认值（仍无生产阈值，结果恒为 `needs_review`）；
- ⏳ 只有两张真实样例；模型卡 0–100 与实测 0–1 差异未解释；
- ⏳ 原始 DBCNN 权重许可未核实；
- ℹ️ 存储为单进程 JSONL，无多进程并发保护与清理策略；服务无认证（默认仅回环）。

---

## 7. W8-4 将视觉反馈并入 ESP32 Dashboard（2026-10-21）

**目标**：不再新增第二个用户可见网页；把 W8-3 的视觉反馈功能自然整合进原有 ESP32 Dashboard 页面。

**实现**：
- `main/http_server.c`：Dashboard 新增「🔍 视觉反馈」导航 + 内嵌面板（沿用既有 `.card`/`.cmp`/`.kv` 样式与锚点滚动）；面板展示 DBCNN 原始分数/模型版本/判定状态/Week 07 规则诊断/输入引用，含未校准警告；支持本地 JPEG 选择、设备照片分析（同源 `fetch('/api/camera/photo')`）、「可用/不可用/无法判断」人工反馈（原因+说明）、反馈历史、重新采集（复用 `doCameraCapture()`）；
- 设备 API 全部相对路径；本地视觉 API 地址经 Kconfig `CONFIG_VISION_API_URL`（默认 `http://127.0.0.1:8090`）模板注入，置空隐藏面板；`root_handler()` 改为双占位符替换；
- `service/vision_feedback_server.py`：**移除独立页面**（`INDEX_HTML` 删除、`GET /` 改为 JSON 说明、`GET /index.html` → 404）；新增有边界 Origin 校验 + CORS + PNA（`allowed_origin()` 仅放行回环/私网/链路本地 IP 字面量与显式配置来源，绝不 `*`）；仍只监听 `127.0.0.1`、不改固件/不自动启动；
- 设备代理接口（`/api/vision/gallery|photo|status|recapture`）保留但内嵌面板不再依赖（基本功能不需 `VISION_DEVICE_BASE_URL`）。

**验证证据（详见 TEST_LOG「O」）**：
- Anaconda 视觉相关 `pytest` **224 passed, 2 skipped**；
- 隔离 venv + 真实 ONNX `VISION_DBCNN_RUN_INTEGRATION=1` **226 passed**；
- 固件 `idf.py build` **成功**（`Project build complete`，`week02-sensor-collect.bin` 0x113ad0，64% free，无 warning/error）；
- 内嵌 JS 提取后 `node --check` 通过；无 `window.open`/`location.href`/`iframe`/独立 HTML。

**W8-4 未完成（如实记录）**：
- ✅ 真实设备端到端：已烧录、已在真机浏览器点击验证（见第 8 节）；
- ⏳ 阈值校准与正式默认值（仍无生产阈值）；只有两张真实样例；权重许可未核实。

---

## 8. W8-4 真实设备验收（用户实机，2026-10-21）

> 本节把**用户提供的真实设备证据**与自动化/构建证据（第 7 节、TEST_LOG「O」）分开记录。Agent 负责烧录与启动/HTTP 核验；浏览器内点击与人工反馈由用户完成。

**烧录与启动（Agent 执行）**
- 串口重新枚举确认为 Espressif USB 设备（VID 303A:PID 1001），`flash_id` 报 ESP32-S3 / 8MB Flash / 8MB PSRAM；其余串口为蓝牙，非目标设备（未输出 MAC）。
- 固件 `build/week02-sensor-collect.bin`，`idf.py -p <COM> flash` 退出码 0；bootloader / 分区表 / app 均 `Hash of data verified`。
- 提交对应：启动日志 `App version: week02-stable-101-g27e1d42`，与 `27e1d42` 一致。
- 启动：PSRAM test PASS、OV2640 检测、QMA6100P ACTIVE、Wi-Fi 已连接、`HTTP ready ... port 80`、IMU cadence 正常。
- HTTP（同源，设备 IP 取自设备自身日志、未扫描未回显）：`GET /` → 200，页面含 `vision-panel`、`/api/vision/analyze`、注入的 `http://127.0.0.1:8090`（占位符已替换）；`/api/camera/status` → `idle`。

**用户实机验收（用户提供，Agent 未代操作浏览器）**
- 视觉反馈面板显示在**原有 Dashboard 内**，未打开第二个用户界面、未跳转网址。
- 通过设备「重新采集」获得 `cap-00001.jpg`，画廊显示一张照片。
- 对设备照片完成**真实 DBCNN 推理**：原始分数 `0.34175166487693787`、模型 `dbcnn_onnx_v1`、判定 `needs_review`（阈值未校准）、Week 07 规则诊断 `low_detail`。
- 用户提交「不可用 / 遮挡」，页面显示「反馈已保存（revision 1）」，最新记录的历史反馈列显示 `unusable` → 反馈保存与记录关联**通过**。
- 前一张正常样例：原始分数 `0.7561315298080444`、规则诊断 `ok`、仍无人工反馈。
- 未设置生产阈值；未把质量分数解释为准确率或遮挡分类。

**本轮仍未完成（Week 08 保持开放）**
- ⏳ 有质量真值的更多「正常 / 低分 / 错误 / 未知」样本对照；
- ⏳ 正式阈值校准与默认值；
- ⏳ 模型卡 0–100 与实测 0–1 的元数据差异说明；
- ⏳ 原始 DBCNN 权重许可核实；
- ⏳ 连续帧、并发、长期运行等稳定性验证（尚未执行）。


