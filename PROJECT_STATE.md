# Project State — 18 周当前进度

> 每次 Agent 窗口开始时应首先读取此文件了解当前状态。

---

## 当前：Week 06 — 让语音任务可以澄清与停止（核心开发完成，实机取消复测完成，ASR/NL→JSON 待验收）

**分支**: `feature/week06-voice-task-context`（HEAD `66ecbb6`，领先 `origin/feature/week06-voice-task-context` 3 提交；本次文档收尾提交后为 4 提交；2026-10-17 收尾）
**基线**: `f0653f0`（= Week 05 HEAD）
**远端**: `origin/main` `6761db6` 已包含 PR #6（合并 Week 06 至 `28aa45c`）；当前分支后续 3 个功能提交与本次文档提交**尚未合并 main**
**阶段**: 🟢 **核心开发完成 + 实机取消/反馈停止复测完成**；⏳ **中文 ASR 内容验收待补**；⛔ **完整 ASR→NL→JSON→任务链路未端到端验收**

**本周已完成（有证据）**:
- **任务上下文**：request_id `voice-{ms}-{counter:04d}` 贯穿 start → recording → stop → ASR → 管线 → 取消/完成
- **任务状态机**：IDLE→RECORDING→PROCESSING→COMPLETED / FAILED / CLARIFYING（→PROCESSING）/ CANCELLING→CANCELLED；取消可从 RECORDING / PROCESSING / CLARIFYING 触发
- **任务取消**：`POST /api/voice/cancel`（停止录音、幂等、已取消/完成/失败返回 409）
- **澄清/重新输入**：`POST /api/voice/clarify`（仅 CLARIFYING 接受、空输入拒绝、轮次递增）
- **迟到结果隔离**：generation 代次单调递增 + `_check_stale()`，旧代次结果记录 `result_stale_discarded` 不覆盖当前状态
- **ESP32 仪表盘**：取消按钮 + 任务状态显示（`6e14622`），固件已烧录、仪表盘 HTTP 200
- **生产计时**：`task.timing` 暴露 `latency_first_feedback_ms` / `latency_completion_ms` / `latency_termination_ms` / `latency_cancel_to_feedback_stop_ms`（`6ff8086`、`66ecbb6`）
- **自动测试（2026-10-17 本次运行）**：`test_voice_control_server.py` **99/99**；全量 `pytest service/` **250 passed + 6 subtests**（0 失败）
- **实机取消复测（2026-10-17）**：ESP32 PLAY 启动 → Web 取消 `POST /api/voice/cancel` HTTP 200 → 状态链 `recording → cancelling → cancelled`；generation `5 → 6`；`latency_cancel_to_feedback_stop_ms = 79 ms`；服务端取消状态与浏览器显示一致。**单次实测，不代表所有场景的固定性能；项目无 TTS，不得称为语音播报停止时延**
- **此前实机取消联动（2026-10-14）**：generation `7 → 8`（另一会话/任务，见 TEST_LOG）

**本周未完成（如实标注，收尾不扩开发范围）**:
- ⏳ **中文 ASR 准确率**：**未完成人工验收**（用户当前不方便进行语音输入；**不是失败，属待补验收**）
- ⛔ **完整语音任务链路**：`service/nl_to_task.py` 缺失，ASR 原始文本无法转为 TaskContract JSON，`task_pipeline.run_task()` 无法处理；详见 TEST_LOG 2026-10-16 诊断
- ⛔ **TTS 停止播报**：项目无 TTS 实现，不适用（不存在可中断的音频播报）
- ⏳ 语音澄清（CLARIFYING）真实语音交互闭环：待 ASR 人工验收后配合补验

---

## Week 05 汇总 — 接通按键说话与语音播报（代码集成已完成，收尾中）

**分支**: `feature/week05-voice-interaction`（本地分支 HEAD `91959fb`，领先 `origin/feature/week05-voice-interaction` 4 提交；2026-10-13 收尾）
**基线**: `7dd788b` — Merge pull request #4（= `main` = `origin/main`）
**阶段**: 🟢 **语音输入链路已实现并通过真实双端闭环验证**（2026-10-11 Step 3I：PLAY→PC 录音→自动后台 Whisper ASR→STOP 回执→ESP32 恢复 IDLE，START/STOP 均 HTTP 200、0 崩溃）。含：PC 音频采集、录音 HTTP 控制服务、本地 Whisper ASR 模块、ESP32 PLAY 按键控制、录音停止自动后台 ASR、**ESP32 仪表盘 🎙 语音交互面板**（2026-10-13 提交 `91959fb`）。已处理 Step 3C 截断（3D 解耦）、Step 3E 栈溢出（2048→4096，实机复核通过）；Step 3F 传输层超时与 Step 3G Brownout 均未在 Step 3I 复现（留档）。

> ⚠️ **Week 05 代码集成已完成；真实中文语音识别和简体中文输出待次日人工验收。** 不得把静音/背景噪声导致的空转录记录为识别成功，也不得宣称识别准确率已通过。

**本周已完成（有证据）**:
- Step 1 PC 音频采集：WAV 格式 + 真实麦克风有效语音 PASS
- Step 2A/2B/2C：PC 录音 HTTP 控制服务 + ESP32 PLAY 按键控制 + 实机联调通过
- Step 3A/3B/3C/3D/3E/3F/3G/3I：本地 Whisper ASR、自动后台 ASR、端到端验证、契约修复、栈修复与实机复核、最终双端闭环验证
- **ESP32 仪表盘 🎙 语音交互面板**：浏览器端语音按钮（开始/停止录音）、实时状态/帧数/ASR 文本显示，提交 `91959fb`（2026-10-13）
- **D008 决策草案**（`339c2ad`）：Week 05 语音链路 5 项决策点记录，待用户审阅
- **Step 4A/4B PC 端语音控制台集成**（`fedd33c`）→ 已 revert（`adf123f`），当前代码不含独立 PC 语音控制台页面

**本周未完成（如实标注，收尾不扩开发范围）**:
- Step 4 语言模型 NL→JSON（未开始）
- Step 5 TTS 播报（未开始）——课程目标「语音播报」输出侧未实现
- Step 6 全链路串联 / Step 7 状态显示与 LED / Step 8 异常处理与文字对照测试（未开始）

> ⚠️ 遗留待确认（不影响本周验收，留档后续）:
> - **edge-tts**：调用微软**在线** TTS 服务，**不需要 API Key**，但**需要网络**且合成文本会外发给外部服务；课程许可与隐私范围**尚未获得确认授权**（待向教师/助教确认）
> - **LLM API 凭据**：与 ASR 无关的**另一项独立配置**（仅在选用云服务时需要）；凭据不得写入仓库

---

## Week 04 汇总 — 自然语言查询与请求采集（已合并）

**分支**: `feature/week04-natural-language`（HEAD `0b4b323`，已合并至 `main`）
**阶段**: Step 1-6 全部完成 ✅（102 test PASS）+ **Web 仪表盘 localStorage 持久化 ✅** + **CSV 导出 ✅** + **浏览器持久化人工检查（4 项）✅** + **CSV 核验 ✅（29 条、12 列、全部 cadence）**
**合并**: PR #4 → `main` `7dd788b`（2026-10-10）
**仍待补验**: 真实 HTTP 调用（ESP32 设备）* 真实语言模型接入

---

## 全局进度

| 单元 | 周 | 主题 | 状态 |
|------|-----|------|------|
| 单元1 | Week 01 | 传感数据采集与 Web 展示 | ✅ 完成 |
| | Week 02 | 远程采集指令与执行反馈 | ✅ 完成 (tag: week02-stable) |
| | Week 03 | 实体按键与物理反馈闭环 | ✅ 已合并至 main (PR #3, `ec9c94b`) |
| 单元2 | Week 04 | 自然语言查询与请求采集 | ✅ 已合并至 main (PR #4, `7dd788b`) |
| | **Week 05** | **接通按键说话与语音播报** | 🟢 语音输入链路已完成实机双端闭环验证（Step 3I）；TTS 播报/LLM/全链路未完成（feature 分支） |
| | **Week 06** | **让语音任务可以澄清与停止** | 🟢 核心开发完成（分支 `feature/week06-voice-task-context`，HEAD `66ecbb6` + 文档收尾，voice server **99/99**、全量 **250 passed + 6 subtests**）；✅ 实机取消/反馈停止复测（generation 5→6，取消→反馈停止 **79 ms**）；✅ 此前实机取消联动（7→8）；⏳ 中文 ASR 内容验收待补；⛔ NL→JSON 缺失，完整语音任务链路待验收 |
| 单元3 | Week 07-09 | 视觉感知与事件反馈 | ⏳ 未开始 |
| 单元4 | Week 10-12 | 多源上下文与状态推断 | ⏳ 未开始 |
| 单元5 | Week 13-15 | 边云协作与韧性通信 | ⏳ 未开始 |
| 单元6 | Week 16-18 | 体验检验、改进与结课交付 | ⏳ 未开始 |

---

## Week 03 历史记录 — 实体按键与物理反馈闭环（已合并至 main，PR #3 `ec9c94b`）

**目标**: 实体按键触发 → 去抖 → 生成唯一 event_id → 本地 LED 反馈 → 发送教学测试消息 → Web 接收 → 远端证据返回 → 更新物理反馈 → 支持取消/回应

**已完成**:
- `help_event.h` — 完整的接口定义（状态枚举、数据结构、按键 FSM、LED 模式、事件生命周期）
- `help_event.c` — 事件生命周期实现、按键去抖 FSM、LED 反馈驱动
- `main.c` — 主循环中集成按键去抖 + 事件触发 + 网络检测 + 超时保护 + LED 更新
- `http_server.h` — `sensor_ctx_t` 中增加 `help_event_t *help` 字段
- `http_server.c` — 三个 HTTP API 端点：`GET /api/help/status`、`POST /api/help/confirm`、`POST /api/help/cancel`
- `main/CMakeLists.txt` — 添加 `help_event.c`
- 👉 **Week 03 按键扫描解耦**：独立 FreeRTOS 任务 (30 Hz) + FreeRTOS Queue 通信，主循环不再负责按键采样
- 👉 **help_uplink 模块**：独立 HTTP POST 模块 (`help_uplink.c/.h`)，将 `help_event_t` 构造为 JSON 推送到外部 VPS 端点（`ae89be8`）
- 👉 **JSON 时间戳语义修正**：`sent_at_us` 标注为设备端 HTTP POST 完成时刻，0 值序列化为 `null`（`5fe738a`）
- 👉 **help_uplink 异步接口集成至 `main.c`**（`29dd72d`）：新增异步接口 `help_uplink_start()` / `help_uplink_request_send()` / `help_uplink_poll()`，由后台 FreeRTOS 任务执行 HTTP POST；`main.c` 初始化阶段调用 `help_uplink_start()`，事件状态机接入 `help_uplink_is_configured()` / `help_uplink_request_send()` / `help_uplink_poll()`（源码位置 `main.c:364` / `:595` / `:601` / `:612`）

**待完成**:
- ~~**`help_uplink` 接入 `main.c`**~~ → **已完成（软件集成）**：异步接口已集成至 `main.c` 事件状态机（`29dd72d`），**「模块已存在但未被调用」的旧描述已作废**
- **完整闭环链路测试**（按键 → 去抖 → event_id → LED 反馈 → HTTP 发送 → 远端确认/取消 → 物理反馈更新）
- **真实 ESP32→VPS 端到端实机验收（仍未完成）**：软件集成已完成（`29dd72d`），但需配置真实 VPS URL，实测 HTTP POST 成功/失败/超时路径并检查远端接收状态及响应；**在取得实机证据前不得声称端到端链路已通过**

### ⚠️ 实机测试记录 (2026-10-09)

| 测试项 | 结果 | 说明 |
|--------|------|------|
| idf.py build | ✅ PASS | week02-stable-15-g65b001f |
| 串口检测 COM10 | ✅ PASS | ESP32-S3-EYE (USB VID:PID=303A:1001) |
| idf.py flash | ✅ PASS | 烧录成功 |
| 设备启动 | ✅ PASS | IMU 数据正常输出（cadence 循环） |
| 按键单次按下检测 | ✅ PASS | 日志: `[W3-BTN] pressed MENU → event help-141745-0001` |
| 事件创建 | ✅ PASS | `W3-EVENT: created id=help-141745-0001 btn=MENU counter=1 (LOCAL CONFIRMED)` |
| HTTP 发送 | ✅ PASS | `W3-EVENT: sent id=help-141745-0001` |
| 网络 API 可用 | ✅ PASS | `[W3-NET] event help-141745-0001 available via API` |
| `[W3-FEEDBACK]` 日志 | ❌ 不存在 | LED 反馈走 `gpio_set_level()` 未打印单独日志，代码中无 `W3-FEEDBACK` 标签 |
| Brownout 稳定性 | ✅ **PASS** | 118s+ 连续运行，无 BOD 日志 |
| MENU 短按 ~294ms | ✅ **PASS** | btn pressed → released (294ms) → created, 仅1个 event_id |
| MENU 长按 ~6s 不重复 | ✅ **PASS** | held 5978ms，因前事件活跃正确抑制 |
| 30s 超时自动重置 | ✅ PASS | help-141745-0001 stale (>30s), auto-reset |
| MENU 连续快速按 3 次 | ⚠️ 未捕获 | 日志仅含短按+长按两组，无第3组 |
| LED 物理反馈观察 | ⛔ 未完成 | 未安排

---

## ⚠️ 已知问题

### Brownout 检测触发 — 间歇性 (2026-10-09)
- **现象**: 按键触发 → 事件创建 → HTTP 发送后，约 1 秒后 `E BOD: Brownout detector was triggered` → 设备重启
- **复现步骤**: 单次按下 PLAY 按键，事件流程正常完成后设备崩
- **可能原因**: 
  - 供电不足：WiFi HTTP POST + 摄像头 + IMU 同时运行导致瞬时电流超限
  - USB 线缆/供电能力不足
  - `CONFIG_BROWNOUT_DET_LVL` 设置过保守（当前未知）
- **建议排查**: 检查电源、USB 线缆；或在 menuconfig 中调整 Brownout 阈值
- **第2轮测试备注 (2026-10-09)**: MENU长按≥5s 操作下未复现 Brownout，设备稳定运行 25s+。
- **第3轮测试备注 (2026-10-09)**: 三次实机验证（短按294ms+长按6s）**均未复现 Brownout**，设备稳定运行 118s+。Brownout 概率较低。

### ✅ 快速连按验证通过
- **MENU 连续快速按 4 次**（含 2 次快速点击间隔 ~343ms release-to-press）→ 第4轮实机验证 (**8f8c511**) 已通过
- 结果：仅生成 **1 个 event_id**，非 IDLE 状态时后续 3 次按下**正确抑制**
- 参见 TEST_LOG.md "快速连续按键验证" 详细记录

### ✅ 已修复的编译问题
- **`main.c:370`** — `help_event` 先使用后声明：将 `help_event_t help_event` / `help_button_fsm_t btn_fsm` 声明移到 `sensor_ctx_t ctx` 初始化之前
- **`help_event.c`** — `help_status_name()` 函数体损坏修复：将孤立 case 标签移回 switch 内，删除末尾孤儿代码
- **`http_server.c`** — `HTTPD_500` → `HTTPD_500_INTERNAL_SERVER_ERROR`；注释块缺失 `/*` 修复；双重 `/*` 修复；添加 help URI 路由定义与注册（消除 unused-function warning）

### 文件修改状态
```
modified:   main/main.c             (变量声明顺序修复)
modified:   main/help_event.c       (函数体结构修复)
modified:   main/http_server.c      (编译错误修复 + URI 路由注册)
```
### ✅ 按键扫描解耦 (2026-10-09)
- **方案**: 独立 FreeRTOS 任务 `button_scan_task` (~30 Hz) 运行去抖 FSM，通过 `xQueueSend` 通知主循环
- **接口变更**: `help_event.h` 移除 `help_button_fsm_t` 类型/`help_button_fsm_init`/`help_button_fsm_update`，新增 `help_button_service_start`/`help_button_get_pending`
- **`main.c`**: 移除 `btn_fsm` 变量和主循环 ADC 读/FSM 更新，改为 `help_button_get_pending()` 非阻塞收队列
- **`help_event.c`**: FSM 改为 `static` 内部类型，新增 `button_scan_task`、Queue、两个公开 API
- **`idf.py build`**: ✅ PASS