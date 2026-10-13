# Week 06 State — 让语音任务可以澄清与停止

**状态**: 🟢 核心实现与测试完成（2026-10-13）
**分支**: `feature/week06-voice-task-context`（基线 `f0653f0` = Week 05 HEAD）
**提交**: `54857b9` feat(week06): add task context, cancel, clarify to voice control server

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

## 已验证

- **74/74** 语音控制测试通过（含 12 项 Week 06 新增）
- **224/225** 全量 service 测试通过（1 项已知历史失败，非本次引入）
- 兼容性：所有 Week 05 既有字段保持不变

## 未完成 / 限制

### 停止播报
- **项目无 TTS 实现**：`停止当前语音播报并回到空闲状态` 标记为无法实施
- 取消操作确保不触发后续副作用，但不存在实际的音频播放可中断

### ESP32 仪表盘
- 取消按钮和任务状态展示 **尚未更新**（需修改 `main/http_server.c` 中 C 字符串 HTML）
- 需要重新编译和烧录 ESP32 固件才能生效

### 真实语音交互验证
- ASR 准确率仍标记为「待人工验收」
- 真实按键取消、语音澄清交互未在 ESP32 实机上验证

## 待办
- [ ] ESP32 仪表盘添加取消按钮 + 任务状态（需固件重编译/烧录）
- [ ] 真实中文语音交互验收（PLAY→取消→新任务 闭环）