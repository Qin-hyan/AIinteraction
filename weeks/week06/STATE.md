# Week 06 State — 让语音任务可以澄清与停止

**状态**: 🟢 **核心开发完成 + 实机取消验证完成**；⏳ ASR 内容验收待补（2026-10-14 最终收尾）
**分支**: `feature/week06-voice-task-context`
**基线**: `f0653f0`（= Week 05 HEAD）
**HEAD**: `6e14622` — feat(week06): add voice task cancel button and status display to ESP32 dashboard

## 提交历史（本周）

| 提交 | 说明 |
|------|------|
| `54857b9` | feat(week06): add task context, cancel, clarify to voice control server |
| `4c0fb6d` | feat(week06): add Week 06 tests, docs, and full regression |
| `7b2d3fc` | test(week06): add late result isolation tests |
| `a74e3b0` | fix(week06): update voice service endpoint |
| `6e14622` | feat(week06): add voice task cancel button and status display to ESP32 dashboard |

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
| Week 06 测试 | `service/test_voice_control_server.py` | ✅ 85/85 |
| ESP32 仪表盘取消按钮 + 状态显示 | `main/http_server.c` | ✅ 已烧录实机验证 |

## 已验证

- **85/85** `test_voice_control_server.py` 通过（任务上下文/取消/澄清/迟到隔离/兼容性回归 + 新增慢响应/连续查询/迟到结果隔离验收 `Week06SlowAndLateResultAcceptanceTest` 7/7）
- **235/236** 全量 `pytest service/` 通过（1 失败为既有 flake：`test_audio_capture.py::test_state_transitions_allow_repeated_recordings`，毫秒级文件名碰撞，HEAD 基线同现，与本任务无关）
- 兼容性：所有 Week 05 既有字段保持不变

## 实机验证记录（2026-10-14）

| 项 | 结果 | 证据 |
|----|------|------|
| ESP32 固件烧录 | ✅ PASS | ESP-IDF `idf.py flash` 成功 |
| ESP32 实际 IP | ✅ PASS | `10.28.53.63` |
| 仪表盘 | ✅ PASS | HTTP 200 |
| 新增取消按钮页面 | ✅ PASS | 页面实测可用 |
| PC voice service | ✅ PASS | `10.28.53.130:8080` |
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

## 已知限制 / 未验收

- ⏳ **中文 ASR 准确率：未完成人工验收** —— 用户当前不方便进行语音输入。**如实记录为「待补验收」，不得描述为失败。**
- ⛔ **TTS 停止播报：不适用** —— 项目无 TTS 实现，不存在可中断的音频播报；取消操作已确保不触发后续副作用（含后台 ASR 结果回写）。
- ⏳ 语音澄清（CLARIFYING）真实语音交互闭环：待 ASR 人工验收后配合补验。

## 遗留待办

- [ ] 中文 ASR 内容人工验收（用户方便时补录真实语音）
- [ ] （如后续引入 TTS）再验证「停止播报」行为