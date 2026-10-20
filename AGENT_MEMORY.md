# Agent Memory — 项目持久事实

> 精简。只记录已确认的长期事实。不写日志、不写完整代码。

---

## 项目架构

```
AIinteraction/
├── main/                   # 所有业务代码
│   ├── main.c              # 入口 + 初始化 + 主循环
│   ├── CMakeLists.txt      # 组件注册
│   ├── idf_component.yml   # esp32-camera ^2.0.18
│   ├── Kconfig.projbuild
│   ├── qma6100p.c/h        # 加速度计驱动
│   ├── adc_button.c/h      # ADC 按键检测
│   ├── wifi_app.c/h        # Wi-Fi STA
│   ├── http_server.c/h     # HTTP 服务器 + Web 仪表盘 + REST API
│   ├── camera_app.c/h      # OV2640 摄像头驱动
│   ├── help_event.c/h      # Week 03: 实体按键→物理反馈闭环
│   ├── voice_button.c/h    # Week 05: PLAY 按键 → PC 录音控制（已烧录/已实机验证 Step 3I）
│   └── help_uplink.c/h     # Week 03: 求助事件 VPS 上行推送（已集成 main.c，真实 VPS 验收未完成）
├── partitions.csv          # 8MB Flash 分区表 (factory 3MB, storage ~5MB SPIFFS)
├── CMakeLists.txt          # 顶层项目: week02-sensor-collect（Week 02 遗留名称，仅影响固件文件名）
├── sdkconfig.defaults      # 公开配置模板
├── sdkconfig               # 本地配置（gitignored，含 Wi-Fi 凭据）
├── dependencies.lock
├── README.md
├── week2_api_check.py
└── 每周计划/               # 18 周课程计划 HTML
```

---

## 已完成模块

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| QMA6100P 驱动 | `qma6100p.c/h` | ✅ Week 01 | I2C addr 0x12, ±2g, 100Hz, 单位 mg |
| ADC 按键 | `adc_button.c/h` | ✅ Week 01 | GPIO1/ADC1_CH0, 4-key voltage divider |
| Wi-Fi STA | `wifi_app.c/h` | ✅ Week 01 | SSID/密码从 sdkconfig 读取 |
| HTTP 服务器 | `http_server.c/h` | ✅ Week 02 | Web 仪表盘 + REST API + 远程采集 |
| 摄像头 OV2640 | `camera_app.c/h` | ✅ Week 02 | JPEG 抓拍, PSRAM 存储, 15 次连续验证 |
| 物理反馈 | `help_event.c/h` | ✅ Week 03 | 按键→LED→Web 闭环, 去抖 FSM (30Hz), 快速连按抑制通过 |
| VPS 上行推送 | `help_uplink.c/h` | 🔧 Week 03 | help_event_t 序列化为 JSON → HTTP POST；**异步接口已集成 main.c**（`29dd72d`：`help_uplink_start` / `request_send` / `poll`），**真实 ESP32→VPS 端到端实机验收未完成** |
| 语音控制（PLAY→PC） | `voice_button.c/h` | ✅ Week 05 | PLAY 按键 → `esp_http_client` POST `/api/voice/start` \| `/api/voice/stop`；独立任务+队列（非阻塞）；Kconfig `CONFIG_VOICE_PC_URL`（空 = 功能关闭）；**已烧录并经 Step 3I 真实双端闭环验证（START/STOP 双端 HTTP 200、ESP32 本地回 IDLE）** |
| 语音任务管理 | `service/voice_control_server.py` | ✅ Week 06 | `_generate_request_id()` → `voice-{ms}-{counter:04d}`；任务状态机 IDLE→RECORDING→PROCESSING→COMPLETED/FAILED/CLARIFYING（→PROCESSING）/CANCELLING→CANCELLED；CANCEL 可自 RECORDING/PROCESSING/CLARIFYING 触发；`POST /api/voice/cancel`（幂等/409）+ `/api/voice/clarify`（仅 CLARIFYING）；`_task_generation` 代次单调递增 + `_check_stale()` 迟到结果隔离（`result_stale_discarded`，日志显式关联旧任务代次）；集成 Week 04 `task_pipeline`（`run_task()` 返回 str 已归一化为 invalid_result→FAILED，不再崩溃）；`test_voice_control_server.py` **99/99**（含慢响应/连续查询/迟到结果隔离验收、生产计时 14 项）、全量 **250 passed + 6 subtests**（2026-10-17，0 失败）；`task.timing` 暴露首次反馈/完成/终止/取消→反馈停止四项时延 |
| ESP32 仪表盘语音面板 | `main/http_server.c` | ✅ Week 05/06 | 🎙 语音交互面板：开始/停止/取消任务按钮 + 实时帧数/时长/ASR 文本 + 任务状态（state/generation/request_id）显示；`__VOICE_PC_URL__` 模板注入（`CONFIG_VOICE_PC_URL` 空时自动隐藏）；Week 05 `91959fb` → Week 06 `6e14622`（取消按钮+状态显示，实机 HTTP 200 已验证） |
| PC 录音控制服务 | `service/voice_control_server.py` | ✅ Week 05 | Flask HTTP 控制录音（start/stop/status）；stop 返回紧凑回执（≤120B）；停录后自动后台 Whisper ASR；自动测试 62/62（Step 3D 版） |
| PC 音频采集 | `service/audio_capture.py` | ✅ Week 05 | sounddevice 采集 16000Hz/1ch/int16 → stdlib `wave` 写 WAV；自动测试 31/31 + 真实麦克风 PASS |
| 本地 Whisper ASR | `service/asr_whisper.py` | ✅ Week 05 | 本地推理 `base/zh/transcribe/cpu`，stdlib `wave` 解码不依赖 FFmpeg；自动测试 18/18 + 真实中文语音 PASS |
| 任务契约校验 | `service/` (Python) | ✅ Week 04 | IntentEnum, TaskContract, JSON Schema, 严格校验, 24 单元测试全部 PASS |
| query_last 工具 | `service/query_last.py` | ✅ Week 04 | 读取 `ESP32_BASE_URL` 环境变量 → HTTP GET `/api/observation/last`，纯标准库，mock 测试 13 PASS |
| trigger_collect 工具 | `service/trigger_collect.py` | ✅ Week 04 | POST `/api/collect` → 轮询 `/api/collect/status` 直至完成/失败/超时，
纯标准库，mock 测试 12 PASS，复用 `query_last` 的 `ESP32_BASE_URL` 配置 |
| 受限任务分发 | `service/task_dispatcher.py` | ✅ Week 04 | 单一入口 `dispatch_task()` → `parse_and_validate()` → 四种意图分发，
`clarify`/`unsupported`/校验失败不调工具，16 单元测试 PASS |
| 工具结果格式化 | `service/result_formatter.py` | ✅ Week 04 | 单一入口 `format_result()` → 五种场景格式化，
| | 任务处理入口 | `service/task_pipeline.py` | ✅ Week 04 | 单一入口 `run_task()` 串联 dispatch->format，7 集成测试 PASS |
保留 source/seq/相对时间，不伪装日历时间，20 单元测试 PASS |
| | Web 仪表盘 localStorage 持久化 | `main/http_server.c` | ✅ Week 04 | 有效观测自动持久化至 localStorage（50 条上限，`record_id` 去重），页面加载恢复，`try/catch` 容错 |
| | CSV 导出 | `main/http_server.c` | ✅ Week 04 | 从 localStorage 导出有效记录为 UTF-8+BOM CSV，12 列字段，文件名含时间戳，字段缺失留空 |
| 画面可用性规则 | `service/image_quality.py` | ✅ Week 07 | `assess_image()` 输出 `ok/too_dark/too_bright/low_contrast/low_detail/invalid`；`is_available()` 区分依赖缺失与图像无效 |
| 结构化视觉契约 | `service/vision_result.py` | ✅ Week 08 | `build_vision_result()`（标签+分数）与 `build_score_vision_result()`（仅分数）；`decision ∈ {ok, low_score, needs_review, unknown}` |
| DBCNN 适配器 | `service/vision_dbcnn.py` | ✅ Week 08 | 本地 ONNX + 外部权重，ORT CPU；原始 0~1 分数、模型版本、权重 SHA-256；失败明确报错不回退 |
| 视觉分析组合入口 | `service/vision_analysis.py` | ✅ Week 08 | DBCNN + 可选 Week 07 规则诊断；来源分离；未配置阈值 → `needs_review`；CLI 不新开端口 |
| 视觉反馈领域层 | `service/vision_feedback.py` | ✅ Week 08 W8-3 | 真实推理 → 仅追加 `records.jsonl`；人工反馈独立 `feedback.jsonl`；设备代理；反馈词表校验；图像不落盘 |
| 视觉反馈本地服务 | `service/vision_feedback_server.py` | ✅ Week 08 W8-3 | 独立 `127.0.0.1:8090`（仅标准库）；页面 + 9 个端点；错误码统一；默认不自动启动、无认证 |

---

## Week 07 关键机制（长期事实）

### 画面可用性/遮挡识别（课程单元 3 保底任务）

- **双端实现、阈值对齐**：PC 端 `service/image_quality.py::assess_image()` 与端侧 `main/image_quality.c::image_quality_assess()` 使用**完全相同的阈值与判定顺序**，可交叉验证
  - 阈值：`LUMA_DARK=25` / `LUMA_BRIGHT=230` / `CONTRAST_LOW=8` / `DETAIL_LOW=8`
  - 判定顺序：`invalid → too_dark → too_bright → low_contrast → low_detail → ok`
  - 指标：`mean_luma`（灰度均值）、`contrast`（标准差）、`detail`（前向差分梯度能量）
- **端侧解码**：`esp_jpeg`（`esp_jpeg_decode()`，头文件 `jpeg_decoder.h`，组件名需在 `main/CMakeLists.txt` REQUIRES 中声明）；RGB888 + 1/4 缩放，缓冲优先 `MALLOC_CAP_SPIRAM`
- **两端指标绝对值不可直接比对**：端侧为 1/4 缩放图，交叉验证应比对**状态一致性**而非数值

### 采集任务状态 vs 图像质量状态（语义分离，易错点）

| 类别 | 枚举 | 取值 | 用途 |
|---|---|---|---|
| 采集任务状态 | `capture_task_status_t` | `idle/capturing/ok/unusable/failed` | 任务层四态反馈 |
| 图像质量状态 | `image_status_t` | `ok/too_dark/too_bright/low_contrast/low_detail/invalid` | 说明 `unusable` 的具体原因 |

- 映射：受理→`capturing`；取帧成功且 `img_status==OK`→`ok`；取帧成功且非 OK→`unusable`；取帧失败/无摄像头→`failed`
- **不得**把"采集失败"直接记为 `IMG_STATUS_INVALID`（该值仅表示图像解码失败），两类语义必须分开

### 课程隐私约束

- 课程要求「**只在请求或教学事件触发后取图，设置最少保留**」
- 现有 `auto_cap.enabled` **默认 `false`**（`main.c:411`），符合按需原则；用户可主动开启
- **不得**把未经校准的模型分数写成现实正确率或安全概率；API/UI 均不得出现"准确率"字样

### 环境事实（Windows / ESP-IDF）

- ESP-IDF 在 **PowerShell** 中需用点源激活：`. D:\esp\Espressif\frameworks\esp-idf-v5.4.3\export.ps1`（直接调用 `export.bat` 不会持久化环境变量；Git Bash 下不可用）
- 工具链符号检查：`/d/esp/Espressif/tools/xtensa-esp-elf/esp-14.2.0_20250730/xtensa-esp-elf/bin/xtensa-esp32s3-elf-nm.exe`
- 本机 `import torch` 崩溃（`OSError WinError 1114`，`c10.dll`），属**预存环境问题**；`pytest service/` 仍可全量通过（退出码 0），其栈打印出现在进程退出阶段，**不构成测试失败**

---

## Week 08 W8-1 关键机制（长期事实）

### 结构化视觉结果契约（`service/vision_result.py`）

- 单一入口 `build_vision_result(request_id, raw_output, score_threshold, model_version="iq_v1", created_at=None)`
- 契约字段：`request_id / label / score / model_version / decision / reject_reason / created_at`；附 `time_quality="pc_utc"`、`score_kind="relative_usability"`、`score_threshold`
- `decision` 为 `ok / low_score / needs_review / unknown`；`ok` 只表示结果可采纳，**不表示画面可用**
  - `needs_review` 仅由 `build_score_vision_result()` 在「分数合法但阈值未配置」时产生
- 拒识原因：`no_result / parse_error / missing_label / unsupported_label / missing_score / invalid_score / image_invalid / score_below_threshold`
- 标签词汇直接复用 `image_quality.IMAGE_STATUSES`，**不得另造标签**；`label=invalid` 归 `unknown/image_invalid`
- `score_threshold` **无默认值**，调用方必须显式传入 `(0, 1]`；未校准前不得给出低分门槛
- `score` 保留原始相对值，**不得称准确率/置信度**
- `created_at` 为 PC 端 UTC ISO-8601，`time_quality="pc_utc"`（设备时间仍为 D002 相对时间）

---

## Week 08 W8-2 关键机制（长期事实）

### 本地 DBCNN ONNX 真实分数

- 适配器 `service/vision_dbcnn.py::DbcnnScorer`；配置：
  - `VISION_DBCNN_MODEL_PATH`（必需，`.onnx`；外部 `.onnx.data` 同目录）
  - `VISION_DBCNN_MODEL_VERSION`（可选，默认 `dbcnn_onnx_v1`）
  - `VISION_DBCNN_SCORE_THRESHOLD`（可选，`(0,1]`；未设置 = 不判定）
- 预处理：BGR→RGB、`cv2.INTER_CUBIC` 224×224、`/255.0`、ImageNet mean/std、NCHW float32
- 输出：原始 0~1 分数（**不乘 100**）、`model_version`、`.onnx`/`.onnx.data` SHA-256、`inference_ms`
- 本地权重实测：`ok.jpg=0.7561315298080444`、`occluded.jpg=0.25810927152633667`；与隔离冒烟报告一致
- 模型卡标称 0–100 与实测 0–1 不一致，**原因未确认**；不得据此换算阈值
- DBCNN 是通用质量分数，**不是遮挡类别/准确率**；`label` 恒为 `None`

### 来源分离与阈值

- `service/vision_analysis.py::analyze_jpeg()`：DBCNN 分数与 `rule_diagnostics` 分开
  - `rule_diagnostics.source="week07_image_quality_rules"`；规则标签/分数**不是**模型输出
  - 规则依赖缺失 → `available=false`、`label=null`，**不得**当作图像无效
- 未配置阈值 → `decision=needs_review`、`reject_reason=threshold_not_configured`、`threshold_status=not_configured`、`review_required=true`
- 显式阈值优先于环境变量；非法值抛 `ValueError`；等于门槛视为达标
- 模型路径缺失/加载失败/推理失败 → 明确 `DbcnnError`，**不退回规则判断**
- 模型与外部权重留在本地 `.workbuddy/`，**不提交 Git**；不调用云端推理
- 许可：HF 仓库 Apache-2.0 标签不自动证明原始 DBCNN 权重许可；未作法律结论
- 详细配置/限制见 `docs/vision-dbcnn.md`

---

## Week 08 W8-3 关键机制（长期事实）

### 本地视觉反馈闭环（独立服务，未改固件）

- 入口 `service/vision_feedback_server.py`：独立进程、默认 `127.0.0.1:8090`（仅标准库）；**不复用**语音 8080 或设备 80；不自动启动、无认证
- 领域层 `service/vision_feedback.py::VisionFeedbackService`：`analyze()` / `analyze_device_photo()` / `submit_feedback()` / `records()`
- 输入：`Content-Type: image/jpeg` 原始字节（本地图片）或 `application/json {"index":N}`（设备照片）
- 设备只调用既有 API：`/api/camera/gallery|status|photo|capture`；地址必须显式 `VISION_DEVICE_BASE_URL` 或 `--device-url`，**不猜测**
- 存储：`.workbuddy/vision-feedback/{records,feedback}.jsonl`（gitignored）；仅追加、反馈不写回分析记录、图像字节不落盘
- 关联链：`record_id` ↔ `input.device.request_id` ↔ `input.image_ref.sha256` ↔ `analysis.score/model_version` ↔ `feedback.revision`
- 反馈词表：verdict `usable/unusable/undetermined`；reason `score_mismatch/too_dark/too_bright/blurred/occluded/wrong_scene/other`（`other` 必填说明）
- `occluded` 仅代表人工观察，**禁止**由低分或 `noise_drift` 自动推导；重复反馈 → 409，记录不存在 → 404，保存失败 → 500
- 端点：`/` 与 `/api/vision/{health,gallery,status,photo,analyze,recapture,records,feedback}`
- 阈值未配置 → 恒 `needs_review`；模型缺失/推理失败/阈值非法 → 明确错误码，不回退规则
- 重新采集复用设备 `POST /api/camera/capture`，不改 Week 07 定时抓拍策略
- **未做真机端到端**：设备地址需用户提供；替身测试不得冒充硬件验收
- 详细见 `docs/vision-feedback.md`。

---

## Week 06 关键机制（长期事实）

### 任务 generation 隔离

- 语音任务代次 `_task_generation` 单调递增（新任务/取消时递增）；所有副作用点（状态更新、`task_pipeline.run_task()` 调用、结果回写）先经 `_check_stale(generation)` 校验
- 旧代次迟到结果记录 `result_stale_discarded` 事件，不覆盖当前状态；`GET /api/voice/status` 的 `task.generation` 暴露当前代次
- 目的（课程要求关联）：迟到结果不得污染新任务状态，保证输入/事件/结果/反馈可追踪关联

### ESP32 dashboard ↔ PC voice service 联动

- ESP32 仪表盘（`main/http_server.c` 内嵌 HTML/JS）通过 `__VOICE_PC_URL__` 模板注入指向 PC 语音服务基地址（`CONFIG_VOICE_PC_URL`；空 = 面板功能隐藏）
- 面板调用 `/api/voice/{start,stop,status,cancel}`；任务状态 / request_id / generation 由 status 的 `task` 块返回并在页面显示
- 实机验证网络（2026-10-14）：ESP32 `10.28.x.x`；PC voice service `10.28.x.x:8080`（私有地址已脱敏）

### 生产计时指标（2026-10-16/17）

- `GET /api/voice/status` → `task.timing`：`latency_first_feedback_ms`（输入结束→首次反馈）、`latency_completion_ms`（→完成）、`latency_termination_ms` + `termination_reason`（→失败/取消）、`latency_cancel_to_feedback_stop_ms`（`cancel()` 入口→CANCELLED，仅取消任务）
- 使用 `time.monotonic()` 差值，仅暴露 ms 差值；新任务开始时清除，跨任务不串值
- 实测：2026-10-17 取消→反馈停止 **79 ms**（单次实测，非固定性能）；项目无 TTS，不得称为语音播报停止时延

---

## 已确认硬件事实

| 项目 | 值 | 确认方式 |
|------|-----|----------|
| I2C SDA | GPIO4 | README + 代码 |
| I2C SCL | GPIO5 | README + 代码 |
| QMA6100P 地址 | 0x12 | 代码 #define |
| ADC 按键引脚 | GPIO1 / ADC1_CH0 | README + 代码 |
| 按键分压 | MENU=2.41V, PLAY=1.98V, DOWN=0.38V, UP+=0.82V | 代码注释 |
| LED GPIO | GPIO38 (RGB), GPIO3 (PWR) | main.c #define |
| 摄像头 PWDN | GPIO42 | main.c #define |
| PSRAM | 8MB Octal | sdkconfig.defaults |
| Flash | 8MB | sdkconfig.defaults |

---

## 关键接口

### 传感器
- `qma6100p_create(bus, addr, &handle)` → `qma6100p_get_acce(handle, &val)` → `val.acce_x/y/z` (float, mg)
- I2C bus handle 由 main.c 初始化，传递给各模块

### HTTP API（Week 02 稳定）
| 端点 | 作用 |
|------|------|
| `GET /` | Web 仪表盘 |
| `GET /api/sensor` | 页面直读（不产生观测） |
| `GET /api/observation/last` | 刷新已存数据（不触发采集） |
| `POST /api/collect` | 采集一次最新数据 |
| `GET /api/collect/status?request_id=xxx` | 查询任务状态 |
| `GET /api/collect/history` | 设备端最近 6 条记录 |
| `POST /api/auto_refresh?on=0\|1` | 周期上报开关 |

### HTTP API（Week 07 图像采集）
| 端点 | 作用 |
|------|------|
| `POST /api/camera/capture` | 按需抓拍（受理即 `capturing`，结果四态） |
| `GET /api/camera/status` | 四态 + 最近评估指标 + 保留状态 |
| `GET /api/camera/gallery` | 照片元数据（`img_status/usable/retain_limit/retained/evicted`） |
| `GET /api/camera/photo?index=N` | 返回 JPEG（index 0=最新） |
| `GET/POST /api/camera/retention` | 查询/设置保留上限（1–9，默认 3） |
| `POST /api/camera/clear` | 显式清除全部已存图像 |
| `GET/POST /api/auto_capture` | 定时抓拍已禁用；启用请求返回 400 `course_policy_disabled` |

- 保留策略：`capture_retention_apply()` 在每次保存后从环形最旧端释放，超出上限的图像不再可读
- 质量指标存于任务级 `last_cap_*`，清除图像后仍保留最近一次评估结果（不冒充新采集）
- 2026-10-19 实机确认：limit=3、第 4 张后 index=3 → 404；clear 后 photo → 404；启用定时抓拍 → 400 `course_policy_disabled`；正常光照 `ok` / 遮挡 `too_dark`，遮挡不覆盖上一张 ok 记录

### 采集任务状态机（Week 02）
```
IDLE → SUBMITTED → RECEIVED → COMPLETED / FAILED
                              → TIMEOUT (>5s)
```

---

## 命名约定

| 类别 | 格式 | 示例 |
|------|------|------|
| 手动请求 ID | `req-{ms}-{counter:04d}` | `req-75253-0001` |
| 观测 ID | `obs-{seq:05d}` | `obs-00047` |
| 教学测试事件 ID | `help-{ms}-{counter:04d}` | `help-110000-0001` |
| 语音控制请求 ID | `voice-{ms}-{counter:04d}` | `voice-123456-0001` |
| 日志 TAG | 模块前缀 | `W3-EVENT`, `MAIN`, `CAM` |
| 来源标记 | `live` / `cadence` / `none` | |
| 时间 | esp_timer 相对时间 (μs/ms since boot) | `time_quality: "relative"` |

---

## Git 基线

| 引用 | 说明 |
|------|------|
| `main` | 最新合并版本 `7dd788b` (PR #4)，与 `origin/main` 同步 |
| `week02-stable` (tag) | Week 02 稳定基线, 12 PASS / 0 FAIL |
| `feature/week02-remote-task` | Week 02 开发分支（已合并） |
| `feature/repo-cleanup` | 仓库清洁分支（已合并） |
| `feature/week03-physical-feedback` | Week 03 开发分支（已合并，PR #3 → `ec9c94b`） |
| `feature/week04-natural-language` | Week 04 开发分支（已合并，PR #4 → `7dd788b`；分支 HEAD `0b4b323` 保留） |
| `feature/week05-voice-interaction` | **已收尾**（作为 Week 06 基线 `f0653f0`）— Week 05 开发分支（基线 `7dd788b`）；功能（PC 音频采集/录音 HTTP 控制服务/本地 Whisper ASR/ESP32 PLAY 语音控制）已实现并经 Step 3I 实机双端闭环验证；2026-10-13 收尾（HEAD `91959fb`） |
| `feature/week06-voice-task-context` | **当前** Week 06 开发分支（基线 `f0653f0` = Week 05 HEAD）；HEAD `66ecbb6` + 本次文档收尾提交；任务上下文/取消/澄清/迟到结果隔离 + 生产计时 + ESP32 仪表盘取消按钮与任务状态显示；voice server **99/99**、全量 **250 passed + 6 subtests**（2026-10-17）；ESP32↔PC 实机取消复测（generation 5→6，取消→反馈停止 **79 ms**）与 2026-10-14 实机取消联动（7→8）均通过；`origin/main` 已合并 PR #6 至 `28aa45c`，后续 3 个功能提交及本次文档提交尚未合并；中文 ASR 内容验收待补；`service/nl_to_task.py` 缺失，完整语音任务链路未验收 |
| `feature/week07-vision-capture` | **当前** Week 07 开发分支（基线 `origin/main` `5aebea1`）；PC 端画面可用性基线 + 端侧 `esp_jpeg` 评估 + 四态采集反馈 API/UI；`pytest service/` 260 passed、固件构建成功；**未烧录、未实机验证** |
| `origin/main` | **`333a697`**（Merge PR #8，Week 07 已合并）——2026-10-19 核实 |
| `feature/week08-vision-feedback` | **当前** Week 08 开发分支（基线 `origin/main` `333a697`）；W8-1 视觉结果契约 + W8-2 本地 DBCNN ONNX 接入 + W8-3 本地反馈闭环 + W8-4 并入 ESP32 Dashboard（`main/http_server.c` 内嵌「视觉反馈」面板 + `service/vision_feedback_server.py` 转 API-only + 有边界 CORS/PNA，默认 `127.0.0.1:8090`）；视觉相关测试 Anaconda **224 passed/2 skipped**、隔离 venv 含真实推理 **226 passed**、`idf.py build` 成功；阈值校准与真机设备端到端未完成；本地提交，未推送、未合并 |
| `integration/week06-followup` | ⚠️ **历史遗留冗余分支**（`ea04269`）：`git diff origin/main integration/week06-followup` **为空**，内容已完全包含于 `origin/main`。**不得作为开发基线**；是否删除由用户决定 |