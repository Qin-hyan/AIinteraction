# Week 05 State — 接通按键说话与语音播报

**状态**: 🔧 分支已建立（含 3 个本地文档提交），**Week 05 功能实现尚未开始**
**分支**: `feature/week05-voice-interaction`（本地分支，无远端 upstream）
**基线**: `7dd788b` — Merge pull request #4 (origin/main)
**当前已知 HEAD**: `bef1432` docs(week03): correct help_uplink integration status across docs
**上游**: 无

## 目标（来自课程计划）

接通按键说话与语音播报：先用按键开始/结束录音，完成「音频 → 任务文本」，
复用 Week 04 的查询/采集任务处理，再连接语音合成播报；
显示录音、识别、任务执行与播放状态，用同一句任务对照文字与语音入口。

课程计划范围控制要点:

- 复用第 1—2 周的查询和远程采集接口
- 基础语音为**按键式半双工**，保留文字通道
- 语言与语音采用**授权预训练服务**

## 当前状态

- `feature/week05-voice-interaction` 已从 `main` (`7dd788b`) 创建（基线 `7dd788b`）
- 分支上已有 3 个本地**文档提交**（无功能实现提交）：
  | 提交 | 说明 |
  |------|------|
  | `8a522b5` | docs(week05): sync project documents for Week 05 branch initialization |
  | `575aba4` | docs(status): mark Week 03 merged and correct Week 04 branch status |
  | `bef1432` | docs(week03): correct help_uplink integration status across docs（**当前已知 HEAD**） |
- 未配置远端 upstream（`git for-each-ref` 显示该分支 upstream 为空）
- **尚无源码变更、功能实现提交或测试**（分支上的提交均为文档修改）

## 已完成

- 分支初始化（基线 `7dd788b`，取自已合并 PR #4 的 `main`）
- 文档同步提交 3 个（`8a522b5`、`575aba4`、`bef1432`）—— 仅文档事实修正，不含任何 Week 05 功能代码

## 待完成（尚未开始，不得无证据标记为已完成）

| 项目 | 状态 |
|------|------|
| 音频输入（按键开始/结束录音、采样格式） | ⏳ 未开始 |
| 音频 → 任务文本（ASR 接入，复用 Week 04 任务管道） | ⏳ 未开始 |
| 任务结果 → 语音合成播报（TTS 接入） | ⏳ 未开始 |
| 录音 / 识别 / 任务执行 / 播放状态显示 | ⏳ 未开始 |
| 文字入口与语音入口同一句任务对照 | ⏳ 未开始 |
| 实机验证（人工审阅 + 实际硬件证据） | ⏳ 未开始 |

## 前置条件

- Week 04 已通过 PR #4 合并至 `main`（`7dd788b`）；Week 04 分支 HEAD `0b4b323` 保留
- 可复用既有模块：`adc_button`（按键）、`help_event`（按键→反馈闭环）、
  `http_server`（REST API + Web 仪表盘）、`service/`（结构化任务管道）
- 待确认事项（未取证前不作结论）:
  - 板载麦克风 / 音频输出的硬件能力及对应引脚（须以实际代码/硬件证据确认，不猜引脚）
  - 授权预训练 ASR/TTS 服务与访问凭据（凭据不得写入仓库或提交）