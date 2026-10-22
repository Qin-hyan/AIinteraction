# Week 06 State — 让语音任务可以澄清与停止

**状态**: 🟢 **核心开发完成 + 实机取消/反馈停止复测完成**；⏳ ASR 内容验收待补；⛔ 完整 ASR→NL→JSON 链路未验收（2026-10-17 收尾）
**分支**: `feature/week06-voice-task-context`
**基线**: `f0653f0`（= Week 05 HEAD）
**HEAD**: `66ecbb6` — feat(week06): add cancel-to-feedback-stop latency metric and audit three curriculum timings（本次文档收尾提交随后）

## 提交历史（本周）

| 提交 | 说明 |
|------|------|
| `54857b9` | feat(week06): add task context, cancel, clarify to voice control server |
| `4c0fb6d` | feat(week06): add Week 06 tests, docs, and full regression |
| `7b2d3fc` | test(week06): add late result isolation tests |
| `a74e3b0` | fix(week06): update voice service endpoint |
| `6e14622` | feat(week06): add voice task cancel button and status display to ESP32 dashboard |
| `6074c7d` | test(week06): add slow-response/continuous-query/late-result acceptance tests |
| `6ff8086` | feat(week06): add production-side task latency measurement and automated tests |
| `66ecbb6` | feat(week06): add cancel-to-feedback-stop latency metric and audit three curriculum timings |

## 目标

让语音任务支持：有限多轮任务上下文、任务澄清与重新输入、用户中途取消、停止播报与迟到结果隔离。

## 已实现

### 3.1 request_id 与日志
- PC 端 `_generate_request_id()` 生成 `voice-{ms:08d}-{counter:04d}` 格式
- 贯穿 start → recording → stop → ASR → 管线 → 取消/完成 全链路
- 所有状态变更通过 `_log_task_event()` 记录到 `_task_log`（最近 20 条）

### 3.2 任务状态机
```
IDLE → RECORDING → PROCESSING → COMPLETED
                              → FAILED
                              → CLARIFYING → (clarify_input) → PROCESSING
                 → CANCELLING → CANCELLED
```
- CANCEL 可从 RECORDING / PROCESSING / CLARIFYING 触发
- 代次 (`_task_generation`) 单调递增用于迟到结果隔离
- 所有副作用点（状态更新、管线调用）检查代次

### 3.3 取消与迟到结果隔离
- `POST /api/voice/cancel`：取消活跃任务、停止录音（如果在进行中）、递增代次
- `_check_stale(generation)` 方法在所有状态更新点校验代次
- 旧任务迟到返回记录 `result_stale_discarded` 事件
- 重复取消幂等，已取消/已完成/已失败任务返回 409

### 3.4 澄清与重新输入
- `POST /api/voice/clarify`：接收 `{"text": "..."}` 重新输入
- 仅 CLARIFYING 状态接受，其他状态返回错误
- 澄清输入计入 `_task_context.clarify_input` + 轮次递增
- 集成 Week 04 `task_pipeline.run_task()` 进行意图解析

### 3.5 新增 API
| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/voice/cancel` | POST | 取消当前活跃任务 |
| `/api/voice/clarify` | POST | 提交澄清/重新输入文本 |

### 3.6 status 响应扩展
- 新增 `task` 块：`request_id`, `state`, `generation`, `result`, `context`, `log`

## 完成模块（汇总）

| 模块 | 位置 | 状态 |
|------|------|------|
| 任务上下文 / 状态机 / 代次隔离 | `service/voice_control_server.py` | ✅ |
| 取消 `/api/voice/cancel` | `service/voice_control_server.py` | ✅ 自动测试 + 实机 |
| 澄清/重新输入 `/api/voice/clarify` | `service/voice_control_server.py` | ✅ 自动测试 |
| Week 06 测试 | `service/test_voice_control_server.py` | ✅ 99/99 |
| ESP32 仪表盘取消按钮 + 状态显示 | `main/http_server.c` | ✅ 已烧录实机验证 |

## 已验证

- **99/99** `test_voice_control_server.py` 通过（任务上下文/取消/澄清/迟到隔离/兼容性回归 + 慢响应/连续查询/迟到结果隔离验收 + 生产计时 14 项）
- **250 passed + 6 subtests** 全量 `pytest service/` 通过（2026-10-17 本次运行，0 失败；此前 `test_audio_capture` 毫秒级文件名碰撞 flake 未复现）
- 兼容性：所有 Week 05 既有字段保持不变

## 实机验证记录（2026-10-14 首次 + 2026-10-17 复测）

| 项 | 结果 | 证据 |
|----|------|------|
| ESP32 固件烧录 | ✅ PASS | ESP-IDF `idf.py flash` 成功 |
| ESP32 实际 IP | ✅ PASS | `10.28.x.x`（已脱敏） |
| 仪表盘 | ✅ PASS | HTTP 200 |
| 新增取消按钮页面 | ✅ PASS | 页面实测可用 |
| PC voice service | ✅ PASS | `10.28.x.x:8080`（已脱敏） |
| **实机取消联动**（ESP32/dashboard → PC voice service） | ✅ **PASS** | 见下方闭环证据 |

**实机取消闭环（request_id=`voice-09188851-0004`）**：

| 阶段 | 证据 |
|------|------|
| start | `state=recording` |
| cancel | `cancelled_recording=true` |
| result | `state=cancelled`；generation **7 → 8** |

状态链：

```
recording
   ↓
cancel
   ↓
cancelled
```

generation：`7 → 8`（代次单调递增，旧结果不覆盖取消状态）

### 2026-10-17 复测（本次收尾归档）

| 项 | 结果 | 证据 |
|----|------|------|
| ESP32 PLAY 启动任务 | ✅ PASS | 串口与服务端日志互证，`state=recording` |
| Web 取消任务 | ✅ PASS | `POST /api/voice/cancel` HTTP **200**，`cancelled_recording=true` |
| 状态链 | ✅ PASS | `recording → cancelling → cancelled` |
| generation | ✅ PASS | **5 → 6** |
| 取消→反馈停止时延 | ✅ PASS | `latency_cancel_to_feedback_stop_ms = **79 ms**`（同一 `task` 块，generation 6） |
| 服务端/浏览器一致性 | ✅ PASS | 服务端 `task.state=cancelled` 与仪表盘显示一致 |

> 79 ms 为**本次单次实测值**，不代表所有场景固定性能；项目无 TTS，不得称为「语音播报停止时延」。本次现场记录未单独留存 `request_id`，指标与同一 `task` 块的 generation 6 关联；2026-10-14 的 7→8 属另一会话/任务，不矛盾。

## 已知限制 / 未验收

- ⏳ **中文 ASR 准确率：未完成人工验收** —— 用户当前不方便进行语音输入。**如实记录为「待补验收」，不得描述为失败。**
- ⛔ **完整 ASR→NL→JSON→任务链路：未实现** —— `service/nl_to_task.py` 缺失，ASR 原始文本无法转为 TaskContract JSON，`task_pipeline.run_task()` 无法处理（详见 TEST_LOG 2026-10-16 诊断）。
- ⛔ **TTS 停止播报：不适用** —— 项目无 TTS 实现，不存在可中断的音频播报；取消操作已确保不触发后续副作用（含后台 ASR 结果回写）。
- ⏳ 语音澄清（CLARIFYING）真实语音交互闭环：待 ASR 人工验收后配合补验。

## 遗留待办

- [ ] 中文 ASR 内容人工验收（用户方便时补录真实语音）
- [ ] （如后续引入 TTS）再验证「停止播报」行为
## 生产计时（2026-10-16）

**提交**: `6ff8086`（生产侧计时 + 自动化测试）、`66ecbb6`（取消→反馈停止指标 + 三项课程计时审计）

### 首次反馈定义

「首次反馈」= 音频输入结束后（`stop()` 或 cancel 中断录音），任务状态首次从 PROCESSING 变迁为 COMPLETED / FAILED / CLARIFYING，或 CANCELLING → CANCELLED。此后 `GET /api/voice/status` 的 `task.timing.latency_first_feedback_ms` 首次可观测到非空值。

### 计时字段（`status().task.timing`）

| 字段 | 类型 | 含义 | 出现条件 |
|------|------|------|----------|
| `latency_first_feedback_ms` | int | 输入结束→首次有效反馈的耗时（ms），单调时钟差值 | 任务到达 COMPLETED/FAILED/CLARIFYING/CANCELLED 后 |
| `latency_completion_ms` | int | 输入结束→成功完成的耗时（ms） | 仅 COMPLETED |
| `latency_termination_ms` | int | 输入结束→非成功终止的耗时（ms） | FAILED 或 CANCELLED |
| `termination_reason` | str | 终止原因 `"failed"` / `"cancelled"` | 与 `latency_termination_ms` 同时出现 |
| `latency_cancel_to_feedback_stop_ms` | int | 取消触发→反馈停止的耗时（ms） | 仅 CANCELLED |

### 三项指标对照课程要求

| 课程指标 | 测量起止 | 当前状态 | 说明 |
|----------|----------|----------|------|
| 输入结束→首次反馈 | `stop()` 返回 → `_transition_task_state(COMPLETED/FAILED/CLARIFYING/CANCELLED)` | ✅ | 成功任务时与完成时延相等（流水线无中间反馈点） |
| 输入结束→任务完成 | `stop()` 返回 → `_transition_task_state(COMPLETED)` | ✅ | |
| 取消→反馈停止 | `cancel()` 入口 → `_transition_task_state(CANCELLED)` | ✅ 本次补齐 | 独立于 `latency_termination_ms`（后者量的是输入结束→CANCELLED） |

### 为什么成功任务的首次反馈时延等于完成时延

当流水线架构中 ASR 完成后直接产生最终结果时，首个用户可观察的反馈
（仪表盘轮询 `/api/voice/status` 看到 `task.state == "completed"` 且含
`task.result`）与任务完成是同一事件。没有中间的部分结果通知。这是对
「可观察反馈」的如实度量，不是缺陷。CLARIFYING 和 FAILED 路径中二者不同。

### 自动化测试

`Week06ProductionTimingAcceptanceTest`（14 项）：
- 正常完成时延、失败终止时延、录音中/处理中取消时延
- **取消→反馈停止**：存在性、非完成不留痕、跨任务清除
- request_id 一致性、跨任务计时隔离
- 慢响应×取消交错、CLARIFYING 首反馈≠完成
- 输入未结束/处理中边界、计时字段异常不影响状态机

全量回归：**250 passed + 6 subtests**（2026-10-17 本次运行；原 235 → 247 → 250；0 失败）


---

## 2026-10-22 语音命令链路补强（真人语音测试）

- **问题**：`CONFIG_VOICE_PC_URL` 旧 LAN IP 失效，浏览器语音面板连不上；ASR 文本未转 JSON，命令必失败；`trigger_collect` 在设备完成后 idle+linked 场景误报超时。
- **修复**：`CONFIG_VOICE_BROWSER_URL=127.0.0.1:8080`（仅浏览器；ESP32 PLAY 仍用 LAN）；`GET /api/voice/health` + 面板检查服务；`service/nl_to_task.py` 确定性映射；字符串结果按 intent 归一化；`trigger_collect` 识别 idle+linked+valid。
- **模型对比（单句、非基准）**：Whisper `base` 把“重新采集一次”识别为“同心踩击一次”，任务进入 clarify；`small` 正确识别“重新采集一次”，任务 completed，真实设备新观测 seq #61、request `req-63048-0002`。用户反馈 small 也需要较标准发音；不能据此宣称整体识别率。
- **真人语音**：`查看上次数据` → completed，真实观测 seq #1070；`重新采集一次` → completed，真实采集成功。
- **未完成**：TTS、LLM 语义解析、base/small 系统化对比、安静/嘈杂环境测试、长期稳定性。
