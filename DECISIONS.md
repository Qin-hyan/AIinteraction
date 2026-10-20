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

## D011 — Week 08 W8-1 视觉结果契约：标签复用、阈值显式传入 ——【已确认】

- **日期**: 2026-10-19
- **决定**:
  1. `label` 词汇表直接复用 `service/image_quality.py` 的 `IMAGE_STATUSES`（`ok/too_dark/too_bright/low_contrast/low_detail/invalid`），新增具名常量避免两处漂移；**不另造场景标签**。
  2. `score_threshold` 在 `build_vision_result()` 中**无默认值**，必须由调用方显式提供且位于 `(0, 1]`；W8-1 未做校准，**不编造阈值**。
  3. `decision` 仅 `ok / low_score / unknown`（W8-2 由 D012 为仅分数模型扩展 `needs_review`）；`decision=ok` 只表示结果可采纳，**不表示画面可用**，画面语义由 `label` 表达；`label=invalid` 归入 `unknown/image_invalid`，不得当作低分。
  4. `score` 保留原始 0~1 相对值，字段 `score_kind=relative_usability`；不得称准确率/置信度。
  5. `created_at` 为 PC 端 UTC ISO-8601，`time_quality=pc_utc`，不与设备相对时间混用（D002）。
- **原因**: 课程要求讲解预测标签、分数、拒识与模型版本；未经校准的分数不得表述为正确率。复用既有词表与阈值显式化可避免伪造校准结论。
- **影响**: W8-2 模型适配器必须提供真实模型版本、阈值配置与样本；未提供时无法产生 `low_score` 判定。
- **证据**: `service/vision_result.py`、`service/test_vision_result.py`；TEST_LOG 2026-10-19 L 节。

## D012 — Week 08 W8-2 DBCNN 接入：仅分数契约、来源分离、阈值未配置不判定 ——【已确认】

- **日期**: 2026-10-19
- **决定**:

## D013 — Week 08 W8-3 反馈闭环：独立本地服务、来源分离、仅追加存储、无默认阈值 ——【已确认】

- **日期**: 2026-10-20
- **决定**:
  1. **不复用语音链路，不改固件 Dashboard**：新增独立本地服务 `service/vision_feedback_server.py`，默认只监听 `127.0.0.1:8090`，仅标准库，不自动启动；语音服务保持 8080，设备保持 80。
  2. **设备端零改动**：只调用 Week 07 既有 `/api/camera/gallery|status|photo|capture`；设备地址必须由 `VISION_DEVICE_BASE_URL` / `--device-url` 显式提供，**不扫描网络、不猜 IP**；未配置时设备接口返回 503，仅本地图片仍可用。
  3. **三类来源严格分离**：`analysis.score/model_version` = DBCNN；`analysis.rule_diagnostics` = Week 07 规则；`feedback.*` = 用户意见。反馈事件 `label_source="user_feedback"`，单独写入 `feedback.jsonl`，**不写回**分析记录；`occluded` 只能是用户观察，禁止由低分或 `noise_drift` 推导。
  4. **仅追加持久化**：`records.jsonl`（一次分析一行，不可变）与 `feedback.jsonl`（可修订，`revision` 递增）均位于 `.workbuddy/vision-feedback/`（gitignored）；图像字节不落盘，只存 SHA-256/字节数/设备引用；写入失败回滚半行。
  5. **阈值仍无默认值**：未配置 `VISION_DBCNN_SCORE_THRESHOLD` 时判定恒为 `needs_review`；界面与 health 必须明示「分数未校准」，禁止使用 0.5 作为生产默认值。
  6. **反馈校验**：verdict `usable/unusable/undetermined`；reason `score_mismatch/too_dark/too_bright/blurred/occluded/wrong_scene/other`，`other` 必须填写说明；与上一条完全相同 → 409；记录不存在 → 404；非法输入 → 400；保存失败 → 500 `feedback_save_failed`。
  7. **验证边界**：单元/接口测试与真实模型 HTTP 冒烟可自动完成；**真实设备端到端需用户提供设备地址并操作**，未验证部分必须标注为待查验。
- **原因**: 课程要求「输入—结果—反馈」可追踪、低分/未知需反馈与重采、人工纠正入口；把视觉功能塞进语音链路或改固件会扩大回归面。未校准的分数只能以「需复核」呈现，不能伪造阈值或准确率。
- **影响**: W8-3 之后若要接入正式阈值，必须先有校准依据；若要真机验证，需要设备地址与人工点击/遮挡配合；后续可在此基础上补充样例对照与阈值校准，但不得改写本决策的来源分离与默认不判定原则。
- **证据**: `service/vision_feedback.py`、`service/vision_feedback_server.py`、`service/test_vision_feedback.py`、`service/test_vision_feedback_server.py`、`docs/vision-feedback.md`；`TEST_LOG.md` 2026-10-20 N 节。

  1. DBCNN 只作为「仅分数」模型接入：`build_score_vision_result()` 返回 `label=None` / `label_source=None` / `score_source=dbcnn_onnx`；**不把规则诊断或分数高低伪装成分类标签**。
  2. `decision` 增加 `needs_review`：分数合法但 `score_threshold` 未配置时使用；`reject_reason=threshold_not_configured`、`threshold_status=not_configured`、`review_required=true`；**不设生产默认阈值**。
  3. 阈值仍无默认值；显式参数优先于 `VISION_DBCNN_SCORE_THRESHOLD`；非法值抛 `ValueError`，不静默忽略；等于门槛视为达标（闭区间）。
  4. Week 07 `image_quality` 仅作为 `rule_diagnostics` 附带，`source=week07_image_quality_rules`；其 `label/score` 不是 DBCNN 输出；依赖缺失时 `available=false`，不冒充 `invalid`。
  5. 模型路径 `VISION_DBCNN_MODEL_PATH` 必需；加载/推理失败抛明确 `DbcnnError`，**不退回规则判断**；固定 `CPUExecutionProvider`，不调用云端推理 API。
  6. 模型与外部权重留在本地忽略目录 `.workbuddy/`，**不纳入 Git**；DBCNN 输出保留原始 0~1，不乘 100；模型卡 0–100 差异记录为已知元数据不一致。
  7. 许可：Hugging Face 仓库的 Apache-2.0 标签**不代表原始 DBCNN 权重许可已核实**；不作法律结论。
- **原因**: 课程要求区分预测标签、分数、拒识和模型版本；未校准的模型分数不得当作准确率，也不得把规则诊断混作模型分类。阈值未校准时最诚实的表达是「需要复核」，而不是自造默认门槛。
- **影响**: W8-3 的反馈/重采/纠错必须读取 `decision` / `reject_reason` / `review_required`，不得把 `needs_review` 显示为 `ok`；正式阈值需用户提供校准依据后再配置。
- **证据**: `service/vision_dbcnn.py`、`service/vision_analysis.py`、`service/vision_result.py`、`docs/vision-dbcnn.md`；TEST_LOG 2026-10-19 M 节。
