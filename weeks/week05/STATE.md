# Week 05 State — 接通按键说话与语音播报

**状态**: 📋 **方案已形成，功能未实现**
**分支**: `feature/week05-voice-interaction`（本地分支，无远端 upstream）
**基线**: `7dd788b` — Merge pull request #4 (origin/main)

## 方案文档

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Week 05 语音交互架构设计（2026-10-10，v1.0）
  - 完整 Mermaid 数据流图：ESP32 按键→PC HTTP Server→录音→ASR→语言模型→run_task()→TTS→播放
  - 12 个设计节：课程要求、数据流图、模块运行位置、按键→PC 方案比较与推荐、核心接口、状态机、服务授权与依赖、异常矩阵、分步实施、前置条件、文件规划、审查记录
  - 推荐方案：ESP32 PLAY 按键 HTTP 回调 → PC Flask Server → whisper 本地模型推理 ASR（无需 API Key） → DeepSeek/Qwen LLM → edge-tts（PC 端调用微软在线 TTS 服务，需网络）
  - 状态机与 help_event 状态机隔离设计
  - 14 种异常场景覆盖
  - 8 步原子实施路线 + 12 项待确认前置条件

## 目标（来自课程计划）

接通按键说话与语音播报：先用按键开始/结束录音，完成「音频 → 任务文本」，
复用 Week 04 的查询/采集任务处理，再连接语音合成播报；
显示录音、识别、任务执行与播放状态，用同一句任务对照文字与语音入口。

课程计划范围控制要点:

- 复用第 1—2 周的查询和远程采集接口
- 基础语音为**按键式半双工**，保留文字通道
- 语言与语音采用**授权预训练服务**

## 当前状态

- `feature/week05-voice-interaction` 已从 `main` (`7dd788b`) 创建
- 分支上的提交全部为**文档提交**（无功能实现提交）；截至事实修正提交 `e322dc7` 共 6 个：
  | 提交 | 说明 |
  |------|------|
  | `8a522b5` | docs(week05): sync project documents for Week 05 branch initialization |
  | `575aba4` | docs(status): mark Week 03 merged and correct Week 04 branch status |
  | `bef1432` | docs(week03): correct help_uplink integration status across docs |
  | `ae5c467` | docs(status): correct help_uplink integration status and Week 05 branch commits |
  | `7f7698c` | docs(week05): add voice interaction architecture design v1.0 |
  | `e322dc7` | docs(week05): correct Whisper ASR and edge-tts TTS service facts |
- 未配置远端 upstream（分支尚未推送）
- **尚无源码变更、功能实现提交或测试**

> 提交记录为截至 2026-10-10 的快照；最新提交以 `git log --oneline` 为准。

## 架构设计状态

| 设计项 | 状态 |
|--------|------|
| 完整数据流图 (Mermaid) | ✅ 完成 |
| 模块与运行位置表 | ✅ 完成 |
| 按键→PC 方案比较与推荐 | ✅ 完成 (推荐 HTTP 回调) |
| 核心接口 (按键/ASR/NL→JSON/TTS/LED) | ✅ 完成 |
| 语音交互状态机 (Mermaid) | ✅ 完成 |
| 异常处理矩阵 (14 场景) | ✅ 完成 |
| 服务选型推荐 (whisper 本地推理 + edge-tts 在线 TTS) | ✅ 完成 |
| 分步实施路线 (8 Steps) | ✅ 完成 |
| 实机验证前置条件清单 (12 项) | ✅ 完成 |
| 人工审阅 | ⏳ 待安排 |

## 待完成（均未开始，不得无证据标记为已完成）

| 项目 | 状态 |
|------|------|
| PC 音频采集验证 (Step 1) | ⏳ 未开始 |
| 按键事件→PC 链路 (Step 2) | ⏳ 未开始 |
| ASR 接入 (Step 3) | ⏳ 未开始 |
| 语言模型 NL→JSON (Step 4) | ⏳ 未开始 |
| TTS 播报 (Step 5) | ⏳ 未开始 |
| 全链路串联 (Step 6) | ⏳ 未开始 |
| 状态显示与 LED 反馈 (Step 7) | ⏳ 未开始 |
| 异常处理与文字对照测试 (Step 8) | ⏳ 未开始 |
| 实机验证 (人工审阅 + 实际硬件证据) | ⏳ 未开始 |

## 前置条件

- Week 04 已通过 PR #4 合并至 `main`（`7dd788b`）
- 可复用既有模块：`adc_button`（按键）、`help_event`（按键→反馈闭环）、`http_server`（REST API + Web 仪表盘）、`service/`（结构化任务管道）、`help_uplink`（ESP32 HTTP client 参考）
- **待确认事项**（未取证前不作结论）:
  - PC 麦克风/扬声器硬件可用性
  - PC 防火墙允许 ESP32 HTTP 入站 (8080 端口)
  - ESP32 与 PC 在同一 LAN 子网
  - whisper 本地推理前置条件：依赖安装、首次下载模型权重、FFmpeg、电脑推理性能（**不需要 ASR API Key**）
  - LLM 服务授权与 API Key（凭据不得写入仓库）
  - whisper 本地推理速度是否满足交互体验要求
  - edge-tts 网络连通性：PC 端调用微软在线 TTS 服务，**需要网络、不需要 API Key**，合成文本会发送给外部服务（隐私待确认）
  - 课程许可：教师确认 whisper 模型与微软在线 TTS 服务的使用权限（独立于 API Key，待确认）
