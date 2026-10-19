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
| `origin/main` | **`5aebea1`**（Merge PR #7，Week 06 已合并）——2026-10-17 核实，非旧报告的 `6761db6` |
| `integration/week06-followup` | ⚠️ **历史遗留冗余分支**（`ea04269`）：`git diff origin/main integration/week06-followup` **为空**，内容已完全包含于 `origin/main`。**不得作为开发基线**；是否删除由用户决定 |