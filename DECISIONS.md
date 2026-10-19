# Technical Decisions — 已确认决策记录

> 只记录已确认的技术决策。不猜测、不编造。

---

## D001 — 构建系统：ESP-IDF 原生 CMake（非 PlatformIO）

- **日期**: Week 01
- **决定**: 使用 ESP-IDF v5.4.3 原生 CMake 构建系统，不使用 PlatformIO
- **原因**: ESP32-S3-EYE 官方示例基于 ESP-IDF；项目无 `platformio.ini`
- **影响**: 使用 `idf.py build/flash/monitor`；需要先 `export.bat` 激活环境

---

## D002 — 时间基准：esp_timer 相对时间

- **日期**: Week 02
- **决定**: 所有时间戳使用 `esp_timer_get_time()`（µs since boot），标记 `time_quality: "relative"`
- **原因**: 板端未启用 SNTP，不伪造墙钟时间
- **影响**: 前端显示"相对时间"（ms since boot），不做绝对时间转换

---

## D003 — request_id 格式

- **日期**: Week 02
- **决定**: 手动采集请求 `req-{uptime_ms}-{counter:04d}`，观测 `obs-{seq:05d}`
- **影响**: 前端通过 request_id 关联请求与观测；counter 逐次递增，可区分重复点击

---

## D004 — 采集任务超时窗口 5s

- **日期**: Week 02
- **决定**: `COLLECT_TIMEOUT_MS = 5000`，硬编码在 `http_server.h`
- **原因**: 课程演示需要可预期的等待时间；未做可配置化
- **影响**: 超时后不接受迟到结果；旧观测不改标为本次完成

---

## D005 — JPEG 缓冲区迁移到 PSRAM

- **日期**: Week 02
- **决定**: 摄像头抓拍的 JPEG buffer 使用 `MALLOC_CAP_SPIRAM` 分配到 PSRAM
- **原因**: 大分辨率下内部 DRAM 不足
- **影响**: 15 次连续抓拍验证通过

---

## D006 — 教学测试消息事件命名

- **日期**: Week 03
- **决定**: 事件 ID 格式 `help-{ms}-{counter:04d}`；状态枚举 `HELP_IDLE → HELP_TRIGGERED → ...`
- **原因**: 区别于采集任务（`req-`）和观测（`obs-`）
- **影响**: 前端按 help_id 追踪教学测试消息生命周期

---

## D007 — LED 反馈引脚

- **日期**: Week 03
- **决定**: 使用 GPIO38（RGB LED）和 GPIO3（模组电源 LED）作为物理反馈输出
- **原因**: ESP32-S3-EYE 板载 LED
- **影响**: `help_led_update()` 同时驱动两个 LED

---

## D008 — Week 05 语音链路决策草案（待审查）

> **状态**: ⏳ **待审查** —— 本条为「Week 05 语音链路决策草案」的落实记录，**尚未视为已确认决策**；逐项标注【已确认】【候选方案】【待验证】，并附课程要求或代码证据。不猜测、不编造，未发生事项不声称发生。

- **日期**: Week 05（2026-10-11）
- **基线**: `7dd788b` — Merge pull request #4（Week 04 已进入 `main`）
- **性质**: 待用户审阅后决定是否转为正式 D 条目；仅记录，不改 `TaskContract`、不引入新选项

### 1. 音频处理运行位置 ——【已确认】

- 初版音频处理运行在 **PC 端**；ESP32 侧只发出「开始/结束录音」控制请求，不采集、不处理音频。
  - 代码证据: `service/audio_capture.py`（sounddevice 采集 16kHz/16bit/mono → stdlib `wave` 写 WAV）；`main/voice_button.c` 文件头「不采集音频、不做 ASR/LLM/TTS」。
  - 硬件/课程依据: ESP32-S3-EYE 板上无麦克风（有 OV2640 摄像头与 QMA6100P），音频输入依赖 PC 麦克风；真实麦克风采集已验证（Step 1 PASS，`weeks/week05/STATE.md`）。
- 【待验证】播放侧: TTS 播报（Step 5）**未开始**，语音链路输出侧无实现，不得宣称已完成。
- 【已确认/证据】真实录音与控制服务闭环已通过 Step 2C / Step 3I 双端验证（START/STOP 均 HTTP 200、ESP32 回 IDLE）。

### 2. ESP32 实体按键通知 PC 开始/结束录音 ——【选项与冲突说明】

- **Help 事件冲突（已确认的事实基础）**: Week 03 的 `help_event.c/h` + `help_uplink.c/h` 是**单一事件槽**——同一时间只受理一个上行请求（`help_uplink.c` 单一 `s_event_copy` 拷贝、在途请求被拒）；若语音控制共用该通道，会与教学测试消息状态机（`HELP_TRIGGERED→HELP_SENDING→HELP_SENT→HELP_REMOTE_RECEIVED/HELP_FAILED`）与 LED 反馈逻辑争用，且 PLAY 键在 Week 03 语义上触发 Help 事件，直接复用会产生「同键双义」冲突。
  - 代码证据: `main/help_event.h`、`main/help_uplink.h`（单一在途请求语义）。
- **独立语音事件通道（候选方案；分支已有实现原型，决策待审）**: 在 `main/` 新增独立模块 `voice_button.c/h`——独立请求队列 + FreeRTOS 任务（优先级 3 < 按键扫描 5），不复用 `help_uplink` 事件槽，不修改 Week 03 Help 状态机与 LED。
  - 代码证据: `main/voice_button.c` 文件头声明；对应实现提交 `aff8e8c`（Step 2B）。实机闭环已验证（Step 3I）。
  - **声明**: 按任务约束，本方案仍在分支中以原型形式存在并已验证，但记录为**候选方案**，最终采纳与否由用户审阅。

### 3. 「每次按下切换录音状态」——【暂定方案 / 候选】

- 评估按 PLAY 时依据「本地已确认状态」自动选择 START/STOP（toggle 切换）：区分「录」与「止」两个阶段，即第 1 次按下开始录音、第 2 次按下结束录音。
  - 代码证据: `voice_button_request_toggle()`（`main/voice_button.h` / `voice_button.c`）：`confirmed == VOICE_CTRL_RECORDING ? STOP : START`（`voice_button.c` L616-617）；Step 2C / 3I 实机采用同一按键语义。
- 【待验证】toggle 在以下场景的健壮性与交互体验：请求在途被拒（`busy_reject_count`）、快速连按、长时间录音、设备/PC 状态失步恢复（Step 3G Brownout 复位曾导致设备本地 IDLE 而 PC 仍在录音，用户按下按 START 触发 409 的实机案例，见 `weeks/week05/STATE.md`）。
- **非决策声明**: 本项按任务约束记为**暂定方案/候选**，不构成已确认、已成正式决定的表述。

### 4. 音频来源与运行位置；不修改 TaskContract ——【已确认】

- 不修改 Week 04 `TaskContract` 结构（`service/task_schema.py`）；音频采集来源（麦克风）、运行位置（PC）、WAV 存放目录先记录在**语音会话记录/服务日志**中，不把音频字段并入任务契约。
  - 代码证据: `service/task_schema.py`（IntentEnum / TaskContract 无音频字段）；`service/audio_capture.py` 输出 `%TEMP%/voice_task/`（WAV 不提交仓库）。
  - 关联课程要求: 输入/任务/结果/反馈可追踪关联——语音请求以 `voice-{ms}-{nnnn}` 关联 ID 追踪（`voice_button.c`）。
- 【待验证】多轮会话下语音会话日志与连续请求 ID 的可追溯性审计（属 Week 06 范畴）。

### 5. ASR / TTS 服务选型与依赖 ——【暂不选择；不引入依赖】

- 任务约束: **暂不选择** ASR/TTS 服务提供商，**不引入**相关依赖；本记录不构成任何选型决定。
- 代码现状（**非决策确认**）: 分支为实现 Step 3A 已引入本地 Whisper（`service/asr_whisper.py`）与 `requirements-asr.txt` 等分步依赖清单；`edge-tts` TTS 候选在文档中提及但**课程许可/隐私授权未确认**（`PROJECT_STATE.md` 留档）。以上均属代码现状，不在本记录中作成决策。

### 待用户审阅确认的事项

1. 独立语音事件通道（`voice_button` 独立队列/任务）是否正式采纳为 Week 05 决策；
2. toggle 切换语义是否确定为正式交互方案；
3. ASR/TTS 服务选型与依赖引入范围（当前仅代码现状，未定论）；
4. `TaskContract` 保持不扩展语音字段的边界是否延续到 Week 06 多轮语音对话。

## D009 — 串口连接导致设备复位的根因与监视规范 ——【已确认】

- **现象回顾**: 2026-10-17 曾记录「设备断开 PC 串口后 15-35 秒自动重启」（TEST_LOG F 节），并初步推测为 USB 辅助控制台写阻塞触发 TWDT。
- **实验更正（2026-10-18）**: 三组对照实验证明旧推测错误——
  1. 无串口连接：设备连续运行 300 秒+ uptime 单调增长（uptime 2290→2581s）；
  2. 串口保持连接：150 秒连续日志无任何异常；
  3. **pyserial 默认方式打开 COM10 → 设备立即复位**（uptime 从 574087ms 掉到 7640ms）；同一设备在打开前以 `dtr=False, rts=False` 预置后打开 → **不复位**（80423→90776ms 连续）。
- **真实根因**: COM10 是 ESP32-S3 原生 USB Serial/JTAG。主机以默认方式打开端口时 DTR/RTS 控制线跳变，USB-Serial-JTAG 硬件据此触发 EN 复位（esptool 烧录正是利用此机制）。复位发生在**打开瞬间**，此前观测到的「断开后 15-35 秒」实为轮询间隔造成的发现延迟。
- **决策**:
  1. 固件不为此修改配置：保留 USB 辅助控制台（`CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y`），保证 USB 日志可见性，支撑后续周次调试；
  2. 监视串口必须使用 `tools/serial_monitor_safe.py`（open 前预置 DTR/RTS 为否）或 `idf.py monitor`；
  3. **课堂演示期间不得打开串口**；设备无人值守运行本身是稳定的，无需担忧；
  4. 曾试行的固件侧方案（禁用 USB 辅助控制台，改 `CONFIG_ESP_CONSOLE_SECONDARY_NONE`）已回退——它消除了 USB 日志但并未阻止打开复位（复位是硬件行为），代价大于收益。
- **证据**: TEST_LOG.md 2026-10-18 H 节（复位时机定位实验）。

## D010 — Week 07「采集权限与失败记录」按原文最小实现 ——【已确认】

- **课程原文**（2026-10-19 通过官网 Markdown 下载源核对，Week 07 卡）：
  - 提交与迁移：「按需环境快照功能；带采集时间的输入样例；采集权限与失败记录。 明确个人项目中图像采集的触发条件和停止条件。」
  - 前 2 学时：「…讲解授权、图像输入与有效性…」；后 2 学时：「…只在请求或教学事件触发后取图，设置最少保留。」
  - 课程标准·单元3 评价证据：「真实图像/模型记录、失败样例、中期原型、首轮完整测试。」
- **判定**：原文未规定失败记录的存储格式、独立接口或持久化，也未要求账户级鉴权。因此**不新增**「独立持久化失败记录」功能，按现有能力满足最小要求。
- **现有实现对应**：
  1. 采集权限边界＝仅显式 `POST /api/camera/capture` 触发；定时抓拍强制禁用（400 `course_policy_disabled`）；请求、图像、时间通过 `request_id` / `captured_ms` / `source` 关联。
  2. 失败记录＝`cap_task_status=failed` + `last_img_status=invalid` + 串口 `[DIAG-CAP] status=failed req=<id>`；`unusable` 与 `failed` 在枚举、API 与 UI 中分离，不得混同。
  3. 触发条件与停止条件写入 README「Week 07」。
- **未满足/边界**：真实取帧失败未构造（无故障注入手段），**失败样例待补验**；失败历史不持久化（原文未要求）。
- **证据**：README「Week 07」；TEST_LOG J/K；`weeks/week07/STATE.md` 第 7/8 节。
