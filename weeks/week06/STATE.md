# Week 06 State — 让语音任务可以澄清与停止

**状态**: 🟡 开发中（2026-10-13）
**分支**: `feature/week06-voice-task-context`（基线 `f0653f0` = Week 05 HEAD）
**上游**: 无（本地分支，待推送）

## 目标

让语音任务支持：有限多轮任务上下文、任务澄清与重新输入、用户中途取消、停止播报与迟到结果隔离。

## 当前架构现状（实施前基线）

### 已存在的关键机制
- `AsrTaskRunner._epoch`：单调递增任务编号，已用于丢弃过期 ASR 结果（`epoch == self._epoch` 检查）
- `voice_button.c`：ESP32 侧已有 `voice-{ms}-{counter:04d}` 格式的 request_id
- ESP32 仪表盘 `🎙 语音`面板：开始/停止按钮 + 状态轮询（每 1.5s）
- Week 04 `task_pipeline.py`：`parse_and_validate → dispatch_task → format_result` 完整链路

### 缺失的能力
- PC 服务端无 request_id 生成与追踪
- 无任务状态机（当前只有 recording/idle + asr status）
- 无取消接口
- 无澄清/重新输入流程
- ASR 结果未接入 Week 04 任务管线
- 无 TTS（停止播报能力无法实施，如实记录）
- 前端无取消按钮/状态展示

## 实施方案

### 3.1 request_id 与日志
- PC 端生成 `voice-{timestamp_ms}-{counter:04d}` 格式 request_id
- 贯穿 start → recording → stop → ASR → task → 取消/完成 全链路

### 3.2 任务状态机
```
IDLE → RECORDING → PROCESSING → COMPLETED
                              → FAILED
                              → CLARIFYING → (re-input) → PROCESSING
                 → CANCELLING → CANCELLED
```
- CANCEL 可从 RECORDING/PROCESSING/CLARIFYING 状态触发

### 3.3 取消与迟到结果隔离
- 任务代次 (generation) 单调递增
- 所有副作用点（播报、设备动作、状态更新）检查代次
- 旧任务迟到返回记录丢弃，不覆盖新任务

### 3.4 停止播报
- **项目无 TTS**：标记为未实现，预留接口

### 3.5 前端扩展
- 取消按钮
- request_id 显示
- 任务状态显示

## 实施状态

- [ ] Step 1: voice_control_server.py 核心重构（request_id + 任务状态机 + 取消 + 管线集成）
- [ ] Step 2: test_voice_control_server.py 测试扩展
- [ ] Step 3: http_server.c 仪表盘更新
- [ ] Step 4: 编译验证
- [ ] Step 5: 全量测试回归
- [ ] Step 6: 文档收尾