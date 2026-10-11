# Week 05 State — 接通按键说话与语音播报

**状态**: 🟢 **Step 1 完成（WAV 格式 PASS；真实麦克风有效语音第 3 轮 ✅ PASS）；Step 2A PC 端录音 HTTP 控制服务 ✅ 已实现并通过自动测试（39/39）；Step 2B ESP32 固件侧 PLAY→PC 控制代码 ✅ 已实现并 `idf.py build` 通过；Step 2C 实机联调 ✅ 已烧录并实测通过（PLAY→start→recording→stop→idle 双端取证，含一次瞬时连接失败的如实记录）；Step 3A 本地 Whisper ASR 模块（`service/asr_whisper.py`）✅ 已实现并通过自动测试（18/18，回归 190 通过）；真实语音识别 ✅ 已执行（stdlib `wave` 解码路径经实测不依赖 FFmpeg；第 2 轮 PASS——base/zh/cpu 纯推理 0.68s，转录非空且语义对应；第 1 轮窗口内无有效语音、转录为空，已如实记录）；Step 3B 录音停止后自动提交后台本地 ASR ✅ 已实现并通过自动测试（`service/test_voice_control_server.py` 58/58，新增 19 项 ASR 相关；`pytest service/` 回归 209 passed + 6 subtests）；Step 3C 真实端到端验证 ✅ 已实测（2026-10-11：ESP32 两次 PLAY→PC 录音→自动后台 Whisper base/zh/cpu 识别 success，START 双端取证成功；ASR 文本「現在測試Whisper本地與音識別」非空且中段语义对应、**前半句未识别，如实记录**；发现真实缺陷：Step 3B 的 stop 响应新增 asr 块后超 ESP32 512B 缓冲 → 固件侧 STOP 契约校验失败（RESPONSE_INVALID，HTTP=200；PC 实际已停录并完成 ASR），本地确认状态停留 RECORDING；**未擅自修改代码，缺陷待修**）；Step 3D 修复该契约缺陷 ✅（2026-10-11：控制接口与查询接口解耦——`POST /api/voice/stop` 改为**紧凑控制回执**（ok/state/recording/wav.frames/wav.duration_seconds/asr.status），完整 wav/asr 调试信息保留在 `GET /api/voice/status`；真实量级 619200 帧场景实测响应 **120B ≪ 512B**，不再截断；固件契约字段核对满足，**无需改 ESP32 固件**；`service/test_voice_control_server.py` 62/62，`pytest service/` 回归 213 passed + 6 subtests；改动仅限 `service/` 两个文件与本文档，未烧录、未真实联调）；**Step 3E 实机验证发现 `btn_scan` 任务栈溢出 → 已修复**（`BTN_TASK_STACK_SIZE` 2048→4096，`idf.py build` 通过，未烧录复核，详见下方 Step 3E 段）**
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
- 分支提交分两类：早期 6 个**文档提交**（截至事实修正提交 `e322dc7`）+ Step 1 的**功能/文档提交**（提交快照，本轮复测提交见 `git log --oneline`）：
  | 提交 | 说明 |
  |------|------|
  | `8a522b5` | docs(week05): sync project documents for Week 05 branch initialization |
  | `575aba4` | docs(status): mark Week 03 merged and correct Week 04 branch status |
  | `bef1432` | docs(week03): correct help_uplink integration status across docs |
  | `ae5c467` | docs(status): correct help_uplink integration status and Week 05 branch commits |
  | `7f7698c` | docs(week05): add voice interaction architecture design v1.0 |
  | `e322dc7` | docs(week05): correct Whisper ASR and edge-tts TTS service facts |
  | `07b21a1` | docs(week05): correct ASR timeout wording, service facts and stale HEAD records |
  | `d187249` | feat(week05): add PC audio capture module with simulated-stream tests |
  | `2e83909` | docs(week05): record Step 1 real-device audio evidence |
- 未配置远端 upstream（分支尚未推送）
- Step 1 已新增源码与测试（详见下方「Step 1 状态」），最新提交以 `git log --oneline` 为准

> 提交记录为截至 2026-10-10 的快照；最新提交以 `git log --oneline` 为准。

## Step 1 状态 — PC 音频采集（2026-10-10）

**新增文件**（仅此三项，未改 `main/` 固件、未加 ASR/TTS/网络/按键集成）:

| 文件 | 作用 |
|------|------|
| `service/audio_capture.py` | `sounddevice.RawInputStream` 采集 + 标准库 `wave` 写 WAV；API `start_recording()` / `stop_recording()`；默认 16kHz/16bit/mono，采样率可配置 |
| `service/test_audio_capture.py` | 31 项自动测试，全部使用模拟音频流（假 `sounddevice`），不依赖真实麦克风 |
| `requirements-audio.txt` | 仅声明 `sounddevice==0.5.6`（音频采集最小依赖；ASR/TTS 依赖未提前引入） |

**关键行为**: 重复启动 / 未启动就停止 / 空录音 / 数据长度不符 / 设备打开失败 / 设备实际采样率与请求不一致 → 一律抛明确异常，不写空 WAV、不静默降级。

**验证结果**:

| 验证项 | 结果 | 证据 |
|--------|------|------|
| 自动测试（模拟音频流） | ✅ PASS 31/31（另 6 subtests） | 隔离虚拟环境 `D:\AIinteraction-venvs\week05-audio`（Python 3.11.7 / sounddevice 0.5.6 / cffi 2.1.1 / pytest 9.1.1） |
| 既有回归测试（Week 04 等 102 项） | ✅ PASS 102/102 | 同一虚拟环境运行 `pytest service/`，未受影响 |
| 输入设备枚举 | ✅ PASS（实测） | 识别到 21 个输入设备；默认输入 = `麦克风阵列 (Realtek(R) Audio)`（MME，device 1，44100Hz） |
| 真实录音 + WAV 参数 | ✅ PASS（实测） | 默认设备（MME）16 kHz：16 bit / 1 ch / 2.90 s（46400 帧）/ 92844 字节；`read_wav_info` 文件头复核一致；`status_flags` 为空 |
| 采样率可配置（真实设备） | ✅ PASS（实测） | WASAPI 麦克风阵列（device 18）@ 48 kHz：3.00 s / 144000 帧 / 288044 字节，文件头复核一致 |
| 设备不支持采样率时的错误反馈（真实设备） | ✅ PASS（实测） | device 18 拒绝 16 kHz → `AudioDeviceError: 打开录音设备失败 (device=18, 16000Hz/1ch/int16): PortAudioError: Invalid sample rate [PaErrorCode -9997]`；未产出文件、状态回到 IDLE |
| 有效语音信号（第 1–2 轮，历史记录，保留不删） | ❌ **未通过**（含 2026-10-10 复测，见下） | 三次真实录音峰值分别为 39/32768（0.12%）、218/32768（0.67%）、108/32768（0.33%）满量程，均属近乎静音的环境底噪，未捕捉到可辨认的说话声 |
| 有效语音信号（第 3 轮，2026-10-10） | ✅ **PASS**（实测 + 用户确认） | 默认 Realtek 麦克风阵列；16 kHz / 1 ch / int16 PCM；9.90 s / 158400 帧 / 316844 B；峰值 4391、整体 RMS 493.73；100 ms 分帧 RMS p10=0.47 / p50=0.49 / p90=914.95 / p99=1634.51 / max=1690.94；超自身底噪 10 倍的活动帧 28/99（28.3%）；用户确认录音窗口内确实说话。详见下方「第 3 轮」记录 |
| 麦克风权限 | ✅ 未被拒绝（间接证据） | 录音确实采集到 46400 / 62400 / 144000 帧真实样本，非空数据 |

> **不得夸大（适用范围：第 1–2 轮）**: 设备可用性与 WAV 格式已由实测证明；第 1–2 轮「录到有效语音」**未被证明**，
> 这两轮的真实麦克风「有效语音」部分**不应**被判为通过（第 3 轮追加结果见下，其结论边界单独标注）。仓库内不保留任何录音文件（录音只写仓库外临时目录并在验证后删除）。
> 复现方式（临时探针脚本位于仓库外，验证完成后已删除，输出 WAV 也已清理；仓库内不保留任何录音）:
> `<venv>\Scripts\python.exe <probe.py> [录音秒数] [提示等待秒数] [设备下标] [采样率]`
> 例如 `... 3 6`（默认设备 16 kHz）、`... 3 4 18 48000`（WASAPI 麦克风阵列 48 kHz）。
> 若要做有效语音验证，需要有人在提示时间窗内对着麦克风说一句测试短语并确认麦克风输入音量未被静音或调至最低。

### 真实麦克风「有效语音」复测（2026-10-10 第 2 轮，追加记录）

> 上表失败记录**保留作为历史证据**；以下为复测追加结果，不覆盖此前记录。

**前置条件**: 分支 `feature/week05-voice-interaction`、HEAD `0ec43a2`、工作区干净；复用 `service/audio_capture.py`（**未修改任何源码**，复测前后 SHA256 均为 `640AAAF4...602B95`）与仓库外虚拟环境 `D:\AIinteraction-venvs\week05-audio`；**未安装任何新依赖**；探针脚本与录音文件均在仓库外，验后已删除。

**设备确认**: PortAudio `V19.7.0-devel`；默认输入设备 = `麦克风阵列 (Realtek(R) Audio)`（hostapi=0 MME，device index 1，2 in，默认 44100 Hz）。两次复测均使用该默认设备、16 kHz / 1 ch / int16，`status_flags` 为空。

| 复测 | 时长 | 帧数 | 文件大小 | 峰值 (int16) | 整体 RMS (int16) | 分帧 RMS 100ms p10 / p90 / p99 | 活动帧 (>3×p10) |
|------|------|------|----------|--------------|------------------|-------------------------------|-----------------|
| 第 1 次（录 8 s） | 7.90 s | 126400 | 252844 B | **215**（0.66% FS） | 4.21（0.013% FS） | 0.47 / 0.50 / 26.08 | 4/79（5.1%） |
| 第 2 次（录 6 s） | 6.00 s | 96000 | 192044 B | **204**（0.62% FS） | 9.06（0.028% FS） | 0.47 / 0.51 / 49.36 | 4/60（6.7%） |

**与历史底噪对比**: 上次三次录音峰值 39 / 218 / 108；本轮峰值 **215 / 204**，与「无说话」那次（218）**基本一致**。
分帧 RMS 中位数长期恒定在 0.47–0.48（分布极均匀），仅约 400 ms 出现 26–50 的短促抬升；整体 RMS 仅 4.21 / 9.06（满量程 0.013% / 0.028%，而正常说话通常为 1–10% FS）。
该特征更像固定低电平伪迹 / 极微弱瞬态，**不是连续语音**。

**结论**: ❌ **有效语音验证仍未通过**（不得判定通过）。已证明的部分：设备可枚举、可打开、可连续采集 6–8 秒真实样本、WAV 参数与文件头完全正确、权限未被拒绝；**未证明**的部分：录到可辨认的说话声音。

**下一步需排查的因素**（建议按序）:

1. `设置 → 系统 → 声音 → 输入 → 麦克风阵列` 的输入音量是否为 0 或已静音（本轮 RMS 长期恒定在 4–9，与零增益/静音输入特征相符）。
2. 用系统自带「录音机 / 声音设置 → 测试麦克风」独立验证硬件拾音（与本项目代码无关）。
3. 换其它输入设备复测：device 2/9/48（蓝牙耳机麦克风，48 默认 16 kHz）、device 28/44（WDM-KS 麦克风）、device 17（WASAPI 蓝牙，默认 16 kHz）。
4. 确认说话距离与朝向（笔电阵列麦克风通常位于屏幕上方，需 20–40 cm 内、正常音量）。
5. 若均无改善，需人工确认麦克风硬件是否可用；或改用课程允许的其它录音来源，并在记录中注明运行位置。

> **结论边界（不得夸大）**: 本复测不改变 Step 1 的验收结论——真实麦克风验证**未完全通过**；文本入口不受影响，录音模块代码与自动测试结论维持不变（31/31 PASS）。

> **范围说明**: 上述 ❌ 结论仅针对第 1–2 轮，**保留为历史证据**；第 3 轮追加结果见下一节，不覆盖、不改判上述记录。

### 真实麦克风「有效语音」验证（2026-10-10 第 3 轮，✅ PASS，追加记录）

> 第 1–2 轮的 ❌ 记录**原样保留、不覆盖、不删除**；本节为追加结果。

**前置条件**: 分支 `feature/week05-voice-interaction`、HEAD `7997544`、工作区干净；复用既有 `service/audio_capture.py`（**未修改任何源码/配置**）；使用仓库外虚拟环境 `D:\AIinteraction-venvs\week05-audio`；**未安装新依赖、未运行 ASR**；临时录音与临时探针脚本均在仓库外，验证后**已清理**，仓库内**未保存任何录音文件**。

**设备与格式**: 输入设备 = **Realtek 麦克风阵列**（使用**默认输入设备**）；WAV = 16 kHz / 单声道 / 16-bit PCM。

**实测数据（录音窗口内说的话已由用户确认）**:

| 指标 | 实测值 |
|------|--------|
| 时长 | 9.90 s |
| 帧数 | 158400 |
| 文件字节数 | 316844 |
| 峰值振幅 | 4391 |
| 整体 RMS | 493.73 |
| 100 ms 分帧 RMS p10 / p50 / p90 / p99 / 最大值 | 0.47 / 0.49 / 914.95 / 1634.51 / 1690.94 |
| 超过自身底噪 10 倍的活动帧 | 28 / 99（28.3%） |

**派生百分比（按给定数值推算，仅供与历史记录对比）**: 整体 RMS ≈ 1.51% 满量程（493.73 / 32768）；峰值 ≈ 13.4% 满量程（4391 / 32768）。

**与历史对比**: 第 1–2 轮峰值仅 204–215（整体 RMS 4.21 / 9.06，活动帧约 5–7%）；本轮峰值 **4391（约为其 20 倍以上）**、整体 RMS **493.73**、活动帧占比 **28.3%**，分帧 RMS p90 相对 p10 / p50（0.47 / 0.49）抬升约 2000 倍——**声音活动显著高于此前无人说话的测试**，与「窗口内确实说话」相符。
（判据差异提示：本轮活动帧判据为「超过自身底噪 10 倍」，与第 2 轮记录中的「>3×p10」不同，两者百分比**不宜直接互比**。）

**结论**: ✅ **本次有效语音采集验证通过**——本轮为 2026-10-10 首次**同时**具备「用户确认窗口内确实说话」与「声音活动显著高于底噪」两项证据的录音。

**结论边界（不得夸大）**:

- 仅证明：默认 Realtek 麦克风阵列在 16 kHz / 1 ch / int16 下能够采到**明显高于底噪的人声活动**，且 WAV 参数正确。
- **不代表 Whisper 已接入，也不代表识别正确**——Step 3 ASR 接入尚未开始，本轮未安装、未运行任何 ASR。
- 统计仅为「活动帧数量与占比」，**不证明所有活动帧严格连续**，因此**不得**写成「连续语音已验证」。
- 用户已确认**峰值 781 的那一轮测试窗口内没有说话**：该轮**保留为无效测试**，**不得再称其为「首次采到语音」的证据**，本记录不引用该轮数据（该轮在仓库文档中无独立数据记录，故此处只记录其无效结论，不新增数据）。
- 第 1–2 轮的失败记录（峰值 204 / 215、整体 RMS 4.21 / 9.06）**保留为历史证据**，不删除、不改判。
- 本次临时录音文件与临时脚本已清理；仓库内**未保存任何录音文件**；本轮**未修改源码、配置或其他文档**。

**下一步（未开始，需另行授权）**: Step 2 按键事件 → PC 链路；Step 3 Whisper ASR 接入（依赖安装、模型下载、推理性能与识别准确率验证）。

## Step 2A 状态 — PC 端语音录音 HTTP 控制服务（2026-10-10）

**结论边界（先读）**: 本节只证明「**PC 端 HTTP 录音控制服务已实现并通过自动测试**」。
**ESP32 尚未接入**——未修改固件、未烧录、未做 ESP32 → PC 的真实网络请求、未改防火墙规则。
本步未调用 ASR / LLM / TTS，也未重复验证真实麦克风（真实录音能力见上方 Step 1 记录）。

**前置条件**: 分支 `feature/week05-voice-interaction`、HEAD `0a46039`、工作区干净；使用仓库外虚拟环境 `D:\AIinteraction-venvs\week05-audio`。

**新增文件**（仅此三项 + 本文件，未改 `main/` 固件与既有 Python 模块）:

| 文件 | 作用 |
|------|------|
| `service/voice_control_server.py` | Flask 服务：`POST /api/voice/start`、`POST /api/voice/stop`、`GET /api/voice/status`；**完全复用** `audio_capture.AudioCapture`（本模块不含任何 PCM/写盘逻辑）；提供 `create_app(capture=...)` 注入点与 CLI（默认 `127.0.0.1:8080`） |
| `service/test_voice_control_server.py` | 39 项自动测试；Flask test client + 假 `AudioCapture`，不打开真实麦克风、不写任何录音文件 |
| `requirements-voice-control.txt` | 仅声明 `flask==3.1.3` 及其传递依赖（固定为实测通过版本） |

**依赖（实测通过版本）**:

| 包 | 版本 | 说明 |
|----|------|------|
| flask | 3.1.3 | 本步唯一新增的直接依赖 |
| werkzeug / jinja2 / markupsafe / itsdangerous / click / blinker | 3.1.9 / 3.1.6 / 3.0.4 / 2.2.0 / 8.5.0 / 1.9.0 | Flask 传递依赖，固定为实测版本 |

安装位置：仓库外虚拟环境 `D:\AIinteraction-venvs\week05-audio`（Python 3.11.7）；**未安装到系统全局 Python**。

**API 行为与错误映射**:

| 场景 | HTTP | JSON `error` | 说明 |
|------|------|--------------|------|
| start 成功 | 200 | — | `state=recording`，含 `target_path`（尚未落盘） |
| stop 成功 | 200 | — | `state=idle`，含 `wav` 元数据（path/samplerate/channels/sample_width_bytes/frames/duration_seconds/size_bytes/status_flags） |
| status 查询 | 200 | — | `state` / `recording` / `frames_recorded` / `elapsed_seconds` / `target_path` / `last_recording` / `config` |
| 重复启动 | 409 | `already_recording` | 不重复触达底层采集器 |
| 未录音就停止 / 二次停止 | 409 | `not_recording` | — |
| 请求体非法 JSON / 非 JSON 对象 | 400 | `invalid_request` | body 可省略（兼容 ESP32 极简请求） |
| 录音为空 | 422 | `empty_recording` | 不登记 `last_recording`、不留待写路径 |
| 设备/依赖错误 | 503 | `audio_device_error` | 失败后状态仍为 `idle`，可再次 start |
| 其它录音错误 | 500 | `audio_capture_error` | — |
| 未预期异常 | 500 | `internal_error` | `exception` 字段保留异常类名 |
| 未知路径 / 方法不允许 | 404 / 405 | `not_found` / `method_not_allowed` | 同样返回 JSON（非 Flask 默认 HTML） |

错误对象统一形如 `{"ok":false,"error":"<code>","message":"<可读中文>","exception":"<类名>","state":"idle|recording"}`；响应为 UTF-8 原样中文（`ensure_ascii=False`）。

**输出路径与暴露面控制**:
- 默认输出目录 = `audio_capture.DEFAULT_OUTPUT_DIR` = `%TEMP%/voice_task/`（仓库外），**不写仓库**
- 显式覆盖输出目录时，若解析后位于仓库内（含仓库根）→ 直接拒绝（`VoiceControlConfigError`），不静默改写
- 默认**只监听** `127.0.0.1:8080`；`--host` 可显式改为局域网地址，此时打印「未验证」警告。本次**未做**局域网暴露、**未改**防火墙

**验证结果**:

| 验证项 | 结果 | 证据 |
|--------|------|------|
| 新增测试 `service/test_voice_control_server.py` | ✅ PASS **39/39** | `python -m pytest service/test_voice_control_server.py -q`（0.72 s） |
| 回归测试（Week 04 + Step 1 全部既有用例） | ✅ PASS **133/133** | 同一命令集合下 `pytest service/`；既有文件未改动 |
| 合计 `pytest service/` | ✅ PASS **172/172**（另 6 subtests） | 1.02 s：audio 31 + query_last 13 + result_formatter 20 + task_dispatcher 16 + task_pipeline 7 + integration 10 + trigger_collect 12 + validator 24 + voice_control 39 |
| 测试是否留下录音文件 | ✅ 无残留 | `%TEMP%/voice_task/` 为空；仓库内无 `*.wav`；测试用 `TemporaryDirectory` 已自动清理 |
| 真实麦克风录音 | ⛔ **本次未验证**（不在 Step 2A 范围） | Step 2A 全程使用假采集器；真实录音能力见上方 Step 1 记录 |
| ESP32 → PC 真实请求 | ⛔ **未验证**（未接入固件） | 未改 `main/`、未烧录、未做网络连通性/防火墙测试 |

**测试覆盖（39 项）**: 正常路径 8（status/start/stop、WAV 元数据、无残留文件）、调用顺序错误 3、录音异常映射 8、HTTP/JSON 契约 6、默认配置与输出目录保护及 CLI 14（含 `--help` 渲染回归保护）。

**下一步（需另行授权）**: Step 2B 实机联调（配置真实 PC 地址 + 防火墙 + 烧录 + 串口取证，见下节「Step 2B 状态」）；Step 3 Whisper ASR 接入。

## Step 2B 状态 — ESP32 固件侧 PLAY 按键 → PC 录音控制（2026-10-10）

### 结论边界（先读）

- ✅ **本次完成的是「固件侧代码实现 + 编译通过」**：`idf.py build` 在本机 ESP-IDF v5.4.3 环境实际执行，退出码 **0**，`voice_button.c` / `main.c` 均参与编译，**0 warning / 0 error**。
- ⛔ **未烧录、未做 ESP32 → PC 真实网络请求**：未接设备、未改防火墙、未启动 PC 服务、未做局域网连通性验证；下文中所有 HTTP 行为均为**代码审阅结论**，不是实测结论。
- 未调用 ASR / LLM / TTS；**未修改** `service/voice_control_server.py`（Step 2A 契约保持不变）。
- 未读取/未修改本地 `sdkconfig`；仓库内无真实 PC 私有 IP、无任何凭据。

### PLAY 按键证据（不猜引脚、不猜语义）

| 项 | 证据 |
|----|------|
| 引脚与电压 | `adc_button.h:26` `BTN_PLAY`（注释 PLAY 键 1.98V）；`adc_button.c:26,64` `BTN_PLAY_MV 1980` + `BTN_TOLERANCE_MV` 判定 |
| 事件语义 | `help_event.c` `button_scan_task`（30 Hz，去抖阈值 3）产生「按下」事件 → 队列（长度 1）→ `help_button_get_pending()` 非阻塞取出（`help_event.c:179`） |
| 修改前行为 | `main.c` 原 L581：`help_button_get_pending(&btn) && help_event.status == HELP_IDLE` → **4 个按键（含 PLAY）在 `HELP_IDLE` 时都会触发 Week 03 教学测试事件** |
| 本次路由规则 | `btn == BTN_PLAY && voice_button_is_configured()` → 交给语音控制；其余情况（含「未配置时的 PLAY」）→ 保持 Week 03 原行为 |
| 与 Week 03 的关系 | 语音地址未配置（**默认**）时 PLAY 行为与本分支修改前**一致**；配置后 PLAY 专用于开/停录音、不再触发 help_event |
| MENU / DOWN / UP+ | 行为完全未改（仍走 help_event 通道） |

**设计权衡（如实记录）**：现有实现里 PLAY 并非专用键——4 个按键都能触发 help_event。`ARCHITECTURE.md` §4.3 已决定「语音模式下 PLAY 不触发 help_event，MENU 保留给 help_event」，本次按该决策实现，并做两处保守化处理：

1. 功能**默认关闭**（地址为空 → 完全不创建资源），默认固件的按键行为与本分支修改前一致；
2. PLAY 被语音通道占用后，即使语音请求被拒（在途/队列满）也**不回退** help_event——避免一次按键同时产生两个通道的动作。

### 新增/修改文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `main/voice_button.h` | 新增 | 动作/状态枚举、状态快照、模块自定义错误码、API 契约说明 |
| `main/voice_button.c` | 新增 | 独立任务 `voice_ctl` + 请求队列 + 互斥锁；`esp_http_client` POST；Step 2A 契约校验；本地「已确认状态」 |
| `main/CMakeLists.txt` | 修改 | `SRCS` 增加 `voice_button.c`（复用已有 `esp_http_client` / `json`：未新增组件依赖） |
| `main/Kconfig.projbuild` | 修改 | 新增 menu「Week 05 Voice Control Configuration」：`VOICE_PC_URL`（默认空）、`VOICE_HTTP_TIMEOUT_MS`（默认 5000，范围 1000–30000） |
| `main/main.c` | 修改 | `#include`；启动时调用 `voice_button_start()`；主循环按键路由（PLAY → 语音，其余不变） |
| `sdkconfig.defaults` | 修改 | 公开模板增加 `CONFIG_VOICE_PC_URL=""`（= 关闭；无真实 IP/凭据）与超时默认值 |

**未改**：`service/*`（Step 2A 实现与契约）、`main/help_event.c/h`、`main/help_uplink.c/h`、`main/http_server.c`、LED 反馈逻辑（Step 7 再处理）。

### 配置与「默认关闭」

| 项 | 行为 |
|----|------|
| `CONFIG_VOICE_PC_URL` 为空（默认） | 启动打印 `W5-VOICE: CONFIG_VOICE_PC_URL 未配置 → 语音控制关闭…`；**不创建任务/队列/锁**；`voice_button_start()` 返回 `VOICE_ERR_NOT_CONFIGURED`；`main.c` 再打印一行「语音控制未启用…PLAY 按键保持 Week 03 行为」 |
| 缺少 `http://` / `https://` 前缀 | 拒绝启动（`ESP_ERR_INVALID_ARG`）+ ERROR 日志 |
| 长度 ≥ 128 字节 | 拒绝启动（`ESP_ERR_INVALID_SIZE`），**不静默截断** |
| 结尾 `/` | 自动去除，避免拼出 `//api/voice/start` |
| 路径 | 配置项只填**基地址**（形如 `http://192.168.x.x:8080`）；固件自动追加 `/api/voice/start`、`/api/voice/stop` |

### HTTP 控制状态逻辑（代码审阅结论，未实测）

- 两次有效 PLAY = 开始、结束：`voice_button_request_toggle()` 按**已确认状态**选择动作（`IDLE` → `START`，`RECORDING` → `STOP`），入队前先置「在途」标记。
- 本地状态：`DISABLED`（未配置）/ `IDLE` / `REQUESTING`（有请求在途，**未确认**）/ `RECORDING`；另有 `confirmed_state` 字段单独保存「已确认状态」。
- **只有成功响应才更新已确认状态**：HTTP 2xx 且 `ok == true` 且 `state` 与动作一致（start → `recording`，stop → `idle`；且 start 必须含 `target_path`、stop 必须含 `wav` 对象）→ 才改写为 `RECORDING` / `IDLE`。
- **失败不改变已确认状态**：连接失败 / 超时 / 非 2xx / JSON 不符 / 字段缺失 → 状态保持原值，仅累加 `failure_count`、`consecutive_failures` 并记录 `last_error` / `last_http_status` / `last_error_code`；**请求失败不会被伪装成「已开始/已结束录音」**。
- **重复请求有明确表现**：请求在途时再按 PLAY → `busy_reject_count++`，打印「忽略（重复请求）…本地确认状态保持 X（未修改）」，不入队、不改状态。
- 失败后**重试语义**：已确认状态未变 → 下次 PLAY 自动重试同一动作（start 失败后重试 start；stop 失败后重试 stop，因为 PC 可能仍在录音）。
- 请求体（可选 JSON，PC 端只要求「是 JSON 对象」）：`{"event":"voice_btn","button":"PLAY","action":"start|stop","device_id":"esp32s3-eye-wk01","request_id":"voice-{ms}-{nnnn}","requested_at_ms":…}`，并带 `Content-Type: application/json`（缺该头时 PC 端 `optional_json_object()` 解析失败 → 400 `invalid_request`）。
- 可追踪关联：每次请求生成 `voice-{ms}-{nnnn}`（沿用 Week 03 `help-{ms}-{nnnn}` 命名），出现在串口日志与状态快照中。

### 错误处理映射（代码审阅结论）

| 场景 | 本地表现（日志/状态） | 已确认状态是否改变 |
|------|----------------------|-------------------|
| 连接失败 / DNS / 无路由 | `HTTP POST 失败：… (err=0x…)`；`last_error` 保留原始 err | ❌ 不变 |
| 超时（`CONFIG_VOICE_HTTP_TIMEOUT_MS`） | 同上，错误名显示 `TIMEOUT(请求超时)` | ❌ 不变 |
| 非 2xx（409 `already_recording` / `not_recording`、422、503、500…） | `PC 未受理请求：HTTP xxx, pc_error=…`，并针对 `already_recording` / `not_recording` / 5xx 打印恢复提示 | ❌ 不变 |
| 2xx 但响应体不是合法 JSON 对象 | `2xx 但响应体不是合法 JSON 对象（len/truncated）→ 不更新本地状态` | ❌ 不变 |
| 2xx 但 `ok != true`，或缺 `target_path` / `wav` | `…契约不符，不更新本地状态` | ❌ 不变 |
| 2xx 但 `state` 与请求动作不一致 | `响应 state="…" 与请求动作 … 期望的 "…" 不一致 → 不更新本地状态` | ❌ 不变 |
| 在途时重复按 PLAY | 警告日志（含累计抑制次数） | ❌ 不变（不排队） |
| 连续失败 ≥ 2 次 | 追加警告：检查地址 / PC 服务 / 防火墙，并说明文字入口不受影响 | ❌ 不变 |
| 地址未配置 | 启动警告 + 功能关闭提示 | 状态 `DISABLED` |

### 非阻塞与资源检查

| 项 | 值 / 依据 |
|----|-----------|
| 请求执行位置 | 独立任务 `voice_ctl`（优先级 3）；按键扫描任务优先级 5 → HTTP 阻塞不会饿死按键扫描 |
| 主循环 | 仅 `voice_button_request_toggle()`（`xQueueSend` 超时 0）+ 日志，不等待网络 |
| 请求队列 | `xQueueCreate(2)`；同一时刻最多 1 个在途请求 |
| 互斥锁 | `xSemaphoreCreateMutex()` 保护状态快照；`voice_button_get_status()` 加锁拷贝 |
| 任务栈 | 5120 字节（`esp_http_client` + cJSON + 512B 响应缓冲 + URL/body 缓冲） |
| 超时 | `CONFIG_VOICE_HTTP_TIMEOUT_MS` 默认 5000 ms，仅限制该任务阻塞时长 |
| 内存 | 未配置时**不创建任何资源**；启用时 = 1 任务 + 1 队列 + 1 互斥锁 + 小型静态结构 |
| 响应缓冲 | 512 字节；超出置 `truncated` 并按**契约不符**失败（不静默当成功） |

### 构建与验证结果

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `idf.py build`（ESP-IDF v5.4.3，`export.ps1` 激活） | ✅ **PASS** | 退出码 **0**；日志含 `Building C object …/voice_button.c.obj`、`Building C object …/main.c.obj`、`Project build complete.` |
| 编译告警 | ✅ **0 warning / 0 error** | 构建日志中 `warning:` 出现次数 = 0 |
| 产物大小 | ℹ️ 记录 | `week02-sensor-collect.bin` = 0xF1C40 字节（约 967 KB）；app 分区 0x100000，剩余 6% |
| 静态检查 | ✅ | `git diff --check` 退出码 0（无空白错误） |
| 烧录 / 串口验证 | ⛔ **未做**（本阶段明确排除） | 未连接 COM 端口、未执行 `idf.py flash` |
| ESP32 → PC 真实 HTTP 请求（成功 / 409 / 超时 / 连接拒绝） | ⛔ **未验证** | 无局域网/防火墙/PC 服务；需另行授权后再做 |
| PC 端回归测试 | ℹ️ 未重跑 | **未修改任何 `service/*` 文件**（Step 2A 的 172/172 结论不受影响） |

**构建命令（本次实际执行）**:

```
D:\esp\Espressif\frameworks\esp-idf-v5.4.3\export.ps1   # 激活 ESP-IDF 环境
cd D:\AIinteraction
idf.py build
```

### 本步未完成（不得无证据标记为完成）

- 真实 ESP32 → PC HTTP 请求的实机验证与串口日志取证（含 409 / 超时 / 连接拒绝路径）
- 局域网连通性、PC 防火墙入站（8080）、PC 服务改为监听局域网地址
- 录音起止与按键时序的实测比对（本次只有代码审阅结论）
- LED 语音状态反馈（Step 7；本次刻意未改 LED 逻辑，避免与 Week 03 LED 反馈冲突）
- ASR / LLM / TTS 全链路（Step 3–6）与文字入口回归测试

**下一步（需另行授权）**: Step 2C 实机联调（配置真实 PC 地址 + 防火墙 + 烧录 + 串口取证）；Step 3 Whisper ASR 接入。

## Step 2C 实机联调验证（2026-10-11，本地分支，未推送）

> 以下均为**实际硬件/网络取证**，不是代码审阅结论。测试后未保留任何录音文件（按指令清理）。

### 前置条件确认

| 前置项 | 结果 | 证据 |
|--------|------|------|
| 分支 / HEAD / 工作区 | ✅ | `feature/week05-voice-interaction` @ `fd1e5a70`，`git status` 干净 |
| `sdkconfig` 是否曾入 git | ✅ 从未 | `git ls-files sdkconfig` 空、`git log --all -- sdkconfig` 空；公共仓库唯一 Wi-Fi 相关跟踪文件 `sdkconfig.defaults` 仅含占位符（`YOUR_WIFI_SSID`/`YOUR_WIFI_PASSWORD`）→ 无旧凭据提交，无需更换密码 |
| 板载旧 SPIFFS 数据 | ✅ 用户确认可放弃 | 新分区表（factory 3 MiB + storage SPIFFS 紧随其后）会改变旧数据偏移；已获用户授权后烧录 |
| PC LAN 地址 | ✅ `10.132.124.130` | WLAN `Get-NetIPAddress` |
| PC 录音服务监听 | ✅ | PID 139680 `python -m service.voice_control_server --host 10.132.124.130 --port 8080`；另有 127.0.0.1:8080 node 进程（不相关）；重复进程 154076（week05-audio venv）未处于 Listen，仅记录 |
| `GET /api/voice/status` | ✅ HTTP 200 | 基线 `state=idle, ok=true`，输出目录 `%TEMP%\voice_task` |
| ESP32 串口 | ✅ 唯一 | VID/PID `303A:1001` → **COM10**（仅一台 ESP32-S3） |
| 防火墙 | ℹ️ 全 profile Disabled + `Python Allow Public` 规则 | 非阻塞点；实际首测仍遇一次瞬时 TCP 连接失败（见下） |

### 烧录结果

- 命令：`idf.py -p COM10 flash`（ESP-IDF v5.4.3，bootloader + partition-table + app）
- `week02-sensor-collect.bin` = `0x10CF60` B（1,101,664 ≈ 1.05 MiB）写入 `0x10000`；**factory 分区 0x300000，剩余 65%**
- 三段写入 hash 均 verified，`Hard resetting via RTS pin... Done`，退出码 0
- 串口证据（复位后捕获）：`WIFI: Got IP: 10.132.124.63`（同子网 10.132.124.0/24）、`W5-VOICE: 语音控制已启用：PC 服务 = http://10.132.124.130:8080（请求超时 5000 ms）`、`HTTP ready`、IMU/camera 初始化正常

### PLAY 按键 → PC 录音实测（双端取证）

| 项 | 结果 | 证据 |
|----|------|------|
| 第一次 PLAY（初测） | ❌ **失败（瞬时）** | ESP32 日志 `btn pressed: PLAY` → 入队 START → `HTTP POST 失败：ESP_ERR_HTTP_CONNECT (err=0x7002)` / HTTP=0；PC 端 /api/voice/status 保持 idle、无请求到达（netstat 无 ESP32 连接）。**并非按键超时**——按键事件完整落日志 |
| 第二次重测 PLAY（按 1 次，两段同时发生 start+stop，即两次按下） | ✅ **PASS** | 见下 |
| 第 1 次按下 → start | ✅ | ESP32 `[voice-475439-0002] PC 已受理：HTTP 200, state=recording` + `已确认：PC 已开始录音 → 本地确认状态 RECORDING`；PC netstat 出现 `10.132.124.130:8080 ↔ 10.132.124.63`（ESP32）连接 |
| 第 2 次按下 → stop | ✅ | ESP32 `[voice-488688-0003] PC 已受理：HTTP 200, state=idle` + `已确认：PC 已结束录音（wav: frames=203200, 12.70 s）`；PC `/api/voice/status` → `state=idle, last_recording.duration_seconds=12.7` |
| 可追踪关联 | ✅ | 请求 ID `voice-{ms}-{nnnn}` 贯穿 ESP32 日志与 PC 契约校验；本地确认状态仅在实际成功响应后才变化（start→RECORDING、stop→IDLE） |

### WAV 元数据（本次实机录音，随后已删除）

- 文件：`%TEMP%\voice_task\voice_20261011_005539_511.wav`（仓库外）
- 头部解析：`RIFF/WAVE`、PCM（fmt=1）、**1 声道 mono、16000 Hz、16 bit**、byteRate 32000、blockAlign 2
- 文件 406,444 B（数据 406,400 B）→ 时长 **12.70 s**；与 PC 服务返回 `frames=203200` 完全一致（203200 / 16000 = 12.7s）
- `status_flags: []`（无异常标记）；未上传、未播放、未输出内容

### 清理与收尾

- ✅ 已删除本次录音 WAV，`%TEMP%\voice_task` 现为空目录；仓库内**无任何 WAV**（递归检查 + `git status`）
- ✅ 已停用两处临时串口捕获进程；PC 服务保持运行且最终 `/api/voice/status` = `idle`
- ✅ `git diff --check` 通过；工作区仅含本文件修改（见提交记录）

### 未验证（不得宣称已完成）

- ASR / LLM / TTS 全链路（Step 3–6）——未接入
- PC 端 409（already_recording/not_recording）与超时路径的**实机**触发（本次只实测成功路径 + 一次无线层瞬时失败）
- LED 语音状态反馈（Step 7；本次未改 LED 逻辑）
- 首次瞬时 `ESP_ERR_HTTP_CONNECT` 的根因（可能为 AP/无线瞬时状态；重测即通过，未复现，不无限重试）
- 文字入口回归测试（未被本次改动涉及）

## Step 3A 状态 — 本地 Whisper ASR 模块（2026-10-11）

**新增文件**（仅此三项加本 STATE.md，未改 `main/` 固件、未改 PC 服务、未改 Week 04 管道）:

| 文件 | 作用 |
|------|------|
| `service/asr_whisper.py` | 本地 Whisper 语音识别：`asr_whisper.transcribe_wav(path)` / `Transcriber`；默认 `base` 多语言模型、语言固定 `zh`、任务 `transcribe`（转录非翻译）；本地推理无 API Key、不调用云端语音服务；可注入模型/模型加载器；明确异常类型（`AsrInputError` / `AsrDependencyError` / `AsrModelError` / `AsrInferenceError`）；标准 PCM WAV 用标准库 `wave` 解码 → float32 16kHz 数组交给 whisper（不依赖 ffmpeg），非 16k 输入 numpy 线性重采样、立体声按均值下混 |
| `service/test_asr_whisper.py` | 18 项自动测试，全部使用假模型（`FakeModel`），不下载权重、不碰麦克风、不调网络服务 |
| `requirements-asr.txt` | 版本固定：`openai-whisper==20250625`、`torch==2.6.0+cpu`（+ CPU wheel 索引）；见下方依赖表 |

### 依赖安装（隔离虚拟环境 `D:\AIinteraction-venvs\week05-audio`，Python 3.11.7）

| 包 | 版本 | 说明 |
|----|------|------|
| openai-whisper | 20250625 | PyPI 最新稳定（requires_python>=3.8，含 3.11） |
| torch | 2.6.0+cpu | 官方 CPU wheel 索引；避免 PyPI 默认 torch 捆绑大型 CUDA 运行时 |
| numpy | 1.26.4 | 与 numba/torch 组合验证 |
| numba / llvmlite | 0.68.0 / 0.50.0 | whisper 依赖 |
| tiktoken / more-itertools / tqdm | 0.14.0 / 11.1.0 / 4.70.1 | whisper 依赖 |
| pytest | 9.1.1 | 已有 |

安装说明：本机 pip 走清华镜像（`pypi.tuna.tsinghua.edu.cn`）+ 本地代理（127.0.0.1:7897），PyPI 直连偶发超时；按 **numpy → torch(CUDA 无关 CPU 构建) → openai-whisper** 分阶段安装成功。未修改系统 PATH / 全局环境。

### 自动测试结果（假模型，无权重 / 无麦克风 / 无网络语音服务）

| 验证项 | 结果 | 证据 |
|--------|------|------|
| 新模块测试 | ✅ PASS 18/18 | 隔离 venv `pytest service -q`（test_asr_whisper.py 单文件 3.49s） |
| 全量回归 | ✅ PASS 190 + 6 subtests | 含 Week 04 / Step 1（audio_capture）/ Step 2A（voice_control_server）等既有测试，未改动既有模块 |
| 覆盖场景 | ✅ 18 项 | 正常识别（文本+元数据+语言/任务/fp16 传参）、文件不存在、无效 WAV、空 WAV、不支持的采样宽度、超过支持声道数、立体声下混、48k→16k 重采样、空识别结果、模型加载失败、模型加载器参数透传、已注入模型跳过加载器、推理异常包装、numpy/whisper 依赖缺失、构造参数校验、默认值（base/zh/transcribe） |

### 环境检查与真实语音识别（如实记录，不伪报通过）

| 项 | 结果 | 证据 |
|----|------|------|
| FFmpeg | ❌ **未安装** | `ffmpeg -version` / `where.exe ffmpeg` 均无结果；ESP-IDF 工具目录递归扫描无 `ffmpeg.exe` |
| GPU / CUDA | 驱动可用，PyTorch 侧未启用 CUDA | `nvidia-smi`：驱动 595.97 / CUDA 13.2；为控制依赖体积安装 CPU 构建 torch，`torch.cuda.is_available()=False`、`device_count=0`（不盲目安装大型 CUDA 依赖） |
| PyTorch / whisper 导入 | ✅ | `torch 2.6.0+cpu`、`whisper 20250625`、`numpy 1.26.4` 均可导入 |
| 真实语音识别（前次记录） | ⏳ **当时未执行（阻塞）** | 当时任务约束：FFmpeg 缺失时「先停止真实推理并报告安装需求」。未录音、未下载 base 权重、未运行真实推理、**未伪报通过**。说明：标准库 wave 解码技术上可不依赖 ffmpeg（**该路径已于 2026-10-11 实测通过，见下方执行记录**） |

### 真实语音识别执行记录（2026-10-11，Step 3A 实机验证）

> 与上方的自动化测试（假模型）严格区分：本节为真人说话 → 真实麦克风 → WAV → 本地 Whisper 推理的实际证据。

- 方式：`service/audio_capture.py` 默认输入设备（「麦克风阵列 (Realtek(R) Audio)」，device 1）16kHz/1ch/16bit 真实录音 8 s；`asr_whisper.transcribe_wav()` 默认 `base`/`zh`/`transcribe`、`device="auto"`→cpu。
- **解码路径（实测确认）**：`transcribe_wav()` → `_decode_wav_to_float32()` 用标准库 `wave` 读 PCM → 校验采样宽度 2 B、声道 ≤2 → float32 数组（`/32768.0`）→ 必要时 16 kHz 线性重采样 → 把 float32 **数组**直接传入 `model.transcribe(audio, language=zh, task=transcribe, fp16=False)`。全程无 FFmpeg 调用，系统未安装 FFmpeg 不影响本链路。
- 模型权重：首次下载成功一次（`~/.cache/whisper/base.pt`，145,262,807 B ≈ 139M，whisper 20250625 默认机制 `_download(url, root, in_memory=False)`）。
- 运行环境：`D:\AIinteraction-venvs\week05-audio`（Python 3.11.7 / whisper 20250625 / torch 2.6.0+cpu 无 CUDA / numpy 1.26.4 / sounddevice 0.5.6）。

| 轮次 | WAV 参数（RecordingInfo / 文件头 / read_wav_info 三源一致） | 录音能量 | 转录文本 | 判定 |
|------|------------------------------------------------------------|----------|----------|------|
| 第 1 轮 | 16kHz / 1ch / 16bit / 7.90 s（126400 帧）/ 252,844 B / status_flags 空 | rms=0.013、peak=0.066、近静音 55.5% → 窗口内无有效语音 | **空字符串** | ❌ FAIL（如实记录，未虚报通过） |
| 第 2 轮 | 16kHz / 1ch / 16bit / 7.90 s（126400 帧）/ 252,844 B / status_flags 空 | rms=0.079、peak=0.625、近静音 26.7% → 有效语音 | 「查看最近的加速度數據,現在測試Whisper中文與音時別」 | ✅ PASS（语义对应，链路成立） |

- 期望句：查看最近的加速度数据。现在测试 Whisper 中文语音识别。
- 结果分析（如实）：前段「查看最近的加速度数据」、中段「现在测试 Whisper 中文」识别正确（`数据`→`數據`、`现在`→`現在` 为 base 模型常见繁体输出）；**末段「语音识别」误识为音近「與音時別」——base 小模型局部误识，如实记录，不宣称 100% 字准**；整体语义可懂，WAV→中文文本链路成立。
- 推理元数据：model=`base` / language=`zh` / task=`transcribe` / device=`cpu` / 纯推理 `0.68 s`（含模型加载 wall `1.48 s`）。
- 运行位置：Windows PC（`C:\Users\Qin`）；录音与临时脚本存放于仓库外 `%TEMP%\week05_step3a_real`，完成后已删除，仓库内无 WAV、无录音脚本。
- 结论：**真实中文语音识别验证通过（第 2 轮）**，并实测证明 stdlib `wave` 解码路径不依赖 FFmpeg；本轮未接入 LLM / TTS / ESP32，未修改任何业务代码或依赖清单。

### 清理与合规

- ✅ 仓库内**无任何 WAV**（`git status` + 递归检查）；未创建本次临时 WAV / 录音脚本
- ✅ 2026-10-11 真实识别：仓库外 `%TEMP%\week05_step3a_real`（录音 WAV / 录音与下载脚本 / report.json）推理后已整体删除，仓库内无 WAV、无录音脚本
- ✅ 临时构建文件（`.asr_build_*.txt`）已删除
- ✅ 未修改：系统 PATH / 全局环境 / 防火墙 / ESP32 固件 / PC Flask 服务 / Week 04 模块
- ✅ 未读取或输出 `sdkconfig`；未验证未接入的项目均如实标记

### 未验证（不得宣称已完成）

- `base` 模型在其他口音 / 语速 / 噪声环境下的中文识别鲁棒性（本次仅验证单句、正常语速、安静环境）
- 末段「语音识别」类常见词的误识率（base 小模型局限，不宣称 100% 字准）
- Step 4 LLM NL→JSON / Step 5 TTS / Step 6 全链路（未开始）

## Step 3B 状态 — 录音停止后自动提交后台本地 ASR（2026-10-11）

**实现范围**（仅修改 `service/voice_control_server.py`、`service/test_voice_control_server.py`；未改 `asr_whisper.py`、依赖清单与 ESP32 固件）:

| 项 | 说明 |
|----|------|
| 自动提交 | `POST /api/voice/stop` 成功保存 WAV 后自动向 `AsrTaskRunner.submit(path)` 入队一个本地 Whisper 识别任务；任务由后台工作线程 `voice-asr-worker` 执行 |
| 快速返回 | `submit()` 只做登记 + 唤醒线程，立即返回；模型加载与推理全部在后台，**不阻塞 HTTP 响应**（避免 ESP32 默认约 5 秒 HTTP 超时） |
| 模型复用 | 复用 `asr_whisper.Transcriber`（`base` / `zh` / `transcribe`，默认值一致）；模型实例在 Transcriber 内缓存，进程内重复识别不重复加载 |
| 状态扩展 | `GET /api/voice/status` 与 `POST /api/voice/stop` 响应新增只读 `asr` 块：`status`（`idle|pending|running|success|failed`）、`text`、`wav_path`、`model`、`language`、`task`、`device`、`duration_seconds`、`error`；既有录音字段（`state/recording/frames_recorded/elapsed_seconds/target_path/last_recording/config`）全部保持兼容 |
| 失败解耦 | ASR 失败（依赖缺失/模型加载失败/推理异常/输入异常）只把 `asr.status` 置为 `failed` 并给出 `error`；`stop` 仍返回 200、`state=idle`、`last_recording` 保留，**不把录音操作伪装成失败** |
| 竞争与重复 | 单工作线程串行推理 + 单调递增 epoch；新提交取代在途任务，旧任务完成时因 epoch 不匹配被丢弃；重复轮询 `GET /status` 不触发重复推理；未录音 / 空录音不提交任务 |
| 日志 | 不打印识别文本 / 语音内容日志 |

**验证结果**（全部自动测试：注入假模型/脚本转录器 + 假采集器；不下载模型权重、不打开真实麦克风、不调用外部服务）:

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `service/test_voice_control_server.py` | ✅ PASS 58/58（原 39 + 新增 19） | 隔离虚拟环境 `D:\AIinteraction-venvs\week05-audio` |
| 新增 ASR 用例（19 项） | ✅ PASS | 自动提交与成功元数据；gate 阻塞下 stop 仍快速返回；重复轮询不重复推理；推理/模型加载/输入异常失败路径；重复请求与提交取代；idle 快照/默认配置/参数互斥；AsrTaskRunner 单元测试（含 stale 结果丢弃） |
| 回归 | ✅ PASS 209 passed + 6 subtests | `pytest service/ -q`，asr_whisper / audio_capture / Week 04 等未受影响 |

**未做（如实记录）**：本阶段不做真实硬件联调；真实 PLAY→录音→Whisper 全流程验证与 ESP32 实测留给下一阶段。

## Step 3C 状态 — 真实 ESP32→PC 录音→自动 Whisper 中文识别端到端验证（2026-10-11）

> 本步只做实测与状态记录，**未新增/未修改任何业务代码、依赖或固件**。

### 环境前提（均实证）

| 项 | 结果 | 证据 |
|----|------|------|
| 分支 / HEAD / 工作区 | ✅ `feature/week05-voice-interaction` @ `fef3705`，工作区干净 | `git branch --show-current` / `git rev-parse HEAD` / `git status --porcelain` |
| PC LAN 地址 | ✅ `10.132.124.130`（WLAN） | `ipconfig` |
| 8080 端口占用 | ℹ️ `127.0.0.1:8080` = PlatformIO MCP Dashboard（node.exe PID 124036，**未终止**）；本次测试服务绑定 `10.132.124.130:8080` 与之共存 | `netstat -ano` + 进程命令行复核 |
| PC 服务版本与运行方式 | ✅ 当前 HEAD（Step 3B 版）代码；venv `D:\AIinteraction-venvs\week05-audio`（flask 3.1.3 / whisper 20250625 / torch 2.6.0+cpu） | 启动命令 `python -m service.voice_control_server --host 10.132.124.130 --port 8080`（PID 94308→150168，Windows venv shim 机制）；`GET /api/voice/status` HTTP 200 且含 `asr` 块 |
| ESP32 串口 | ✅ 唯一 `VID/PID 303A:1001` → **COM10** | PnP 枚举（其余为蓝牙串口，不相关） |
| ESP32 固件 | ✅ 不重新构建/不烧录 | `main/` 自 Step 2C（`a6fa258`）至 HEAD **无任何改动** → 与已烧录固件匹配 |

### 端到端实测流程图与双端证据

录音由 12:49:46 开始、约 12:50:24 保存 WAV，ASR 于 12:50:54 完成。串口日志存于仓库外 `%TEMP%\week05_step3c_real\serial.log`（后随清理删除）。

| 阶段 | PC HTTP 证据（`GET /api/voice/status`） | ESP32 串口证据（UTF-8） | 判定 |
|------|----------------------------------------|------------------------|------|
| 第 1 次 PLAY（START） | 12:49:46 `state=recording` | `W3-EVENT: btn pressed: PLAY` → `[voice-54298-0001] 执行 PLAY 语音请求：START（POST http://10.132.124.130:8080/api/voice/start）` → `[voice-54298-0001] PC 已受理：HTTP 200, state=recording` → `已确认：…本地确认状态 RECORDING` | ✅ 双端成功 |
| 第 2 次 PLAY（STOP） | 12:50:26 `state=idle`、`asr.status=running`；WAV 已保存（LastWriteTime 12:50:24） | `btn pressed: PLAY` → `[voice-95023-0002] 执行 PLAY 语音请求：STOP（POST …/api/voice/stop）` → ⚠️ `E (98707) 2xx 但响应体不是合法 JSON 对象（len=511, truncated=1）→ 不更新本地状态` → `请求 STOP 失败：RESPONSE_INVALID（HTTP=200, pc_error=(无)）；本地确认状态保持 RECORDING` | 🟡 **PC 成功；ESP32 侧契约确认失败**（见下方缺陷） |
| ASR 完成 | 12:50:54 `asr.status=success`；`text` / `model` / `device` 见下 | — | ✅ ASR success |

> **实测发现的真实缺陷（记录待修，未擅自修改任何代码）**：Step 3B 使 `POST /api/voice/stop` 响应新增 `asr` 块后，响应体长度超过 ESP32 端 512 字节响应缓冲（`voice_button.c` 响应缓冲 512B；ESP32 报告 `len=511, truncated=1`）。**PC 实际已停止录音**（`state=idle`、WAV 已落盘、ASR 已自动提交并 success），但 **ESP32 因响应截断判定「响应不符合契约」→ 本地确认状态未更新（停留 RECORDING）**。即：服务器受理=成功、ASR 链路成功；**设备接收确认在 STOP 侧失败**。属 Step 3B 改动引入的固件/PC 响应契约回归，本步不修（本步仅实测与记录）。

### ASR 结果与录音元数据

- 期望句：**查看最近的加速度数据。现在测试 Whisper 本地语音识别。**
- **真实转录文本**：`現在測試Whisper本地與音識別`
- 结果分析（如实）：转录**非空**且**中段语义对应**（「现在测试 Whisper 本地[语音]识别」）；`语音识别`→`與音識別` 为 base 小模型常见音近误识（与 Step 3A 实测同类）；**前段「查看最近的加速度数据」未出现在转录中**（录音 38.7 s 含启动后等待/静音段，实际识别出窗口内的后半句）。**不宣称完成整句识别**。
- 模型 / 语言 / 任务 / 设备：`base` / `zh` / `transcribe` / `cpu`
- 推理耗时：`asr.duration_seconds = 6.825`（38.7 s 音频 CPU 纯推理）；含首次模型加载的整体 wall 时间 ≈ 28 s（12:50:26 running → 12:50:54 success）
- WAV 元数据（PC `last_recording` 与 `wave` 模块双源一致）：`voice_20261011_124945_372.wav`；`1 ch / 16000 Hz / 16 bit（sampwidth=2）/ 619200 帧 / 38.7 s / 1,238,444 B / status_flags=[]`（文件头 RIFF/PCM fmt=1，未压缩；PC 返回 `frames=619200` 与文件头一致）

### 清理与合规

- ✅ 本次录音 WAV 已删除（`%TEMP%\voice_task` 测试前不存在，测试后整目录删除确认消失）
- ✅ 串口捕获进程已停用（COM10 已释放）；本次测试启动的 PC 服务实例已停止（进程 94308/150168 均已退出）
- ✅ 仓库外临时目录 `%TEMP%\week05_step3c_real`（服务日志 / 串口日志 / 临时脚本）已删除
- ✅ 仓库内无 WAV、无临时探针文件（`git status` 干净；递归 `*.wav` 检查为空）
- ✅ 未触碰 PlatformIO MCP Dashboard（PID 124036）等无关进程；未读取/输出 `sdkconfig` 与 Wi-Fi 凭据；未修改业务代码、依赖、ESP32 固件与防火墙

### 结论

- **端到端链路（PLAY → PC 录音 → 自动后台 Whisper 中文识别）成立并实测通过**：START 双端取证成功 + ASR `success` + 转录文本非空。
- **ASR 文本与期望句的不一致如实记录**（前段漏识、末段音近误识），不宣称 100% 整句通过。
- **Step 3C 发现的真实缺陷（stop 响应超 ESP32 512B 缓冲 → 固件侧 STOP 契约失败）已在 Step 3D 修复**：控制/查询接口解耦，见下方 Step 3D 段。

## Step 3D 状态 — 修复 stop 响应超 ESP32 512B 缓冲的契约缺陷（2026-10-11）

> 目标：把「控制接口」与「查询接口」解耦，使 `POST /api/voice/stop` 的响应适配
> ESP32 固件 `voice_button.c` 的 512B 响应缓冲（`VOICE_RESP_BUF_SIZE`），修复
> Step 3C 实测的固件侧 STOP 契约确认失败（PC 已停录并完成 ASR，但 ESP32 因响应
> 截断判定 `RESPONSE_INVALID`、本地确认状态停留 RECORDING）。

### 根因

Step 3B 让 `POST /api/voice/stop` 返回完整 `wav` 元数据（path/samplerate/channels/
sample_width_bytes/size_bytes/status_flags/…）+ 完整 `asr` 快照（status/text/wav_path/
model/language/task/device/duration_seconds/error）+ 中文 message。响应体超过
511B（ESP32 报告 `len=511, truncated=1`），超出 512B 缓冲即被截断，cJSON 解析出
「非合法 JSON 对象」→ 判定契约不符。

### 修改（Step 3D）

| 文件 | 改动 |
|------|------|
| `service/voice_control_server.py` | `POST /api/voice/stop` 改为**紧凑控制回执**：只返回 `ok` / `state` / `recording` / `wav.frames` / `wav.duration_seconds` / `asr.status`；**不再返回** `message`、完整 `wav` 元数据（`info.as_dict()`）、完整 `asr` 快照（`runner.snapshot()`）。新增常量 `ESP32_RESP_BUFFER_BYTES = 512`（对齐固件 `VOICE_RESP_BUF_SIZE`，未来加字段须在此约束下评估）。`GET /api/voice/status` **不变**，继续提供完整 `last_recording` / `asr`（含 text、duration 等调试信息） |
| `service/test_voice_control_server.py` | 更新 stop 契约相关断言到紧凑回执（`test_stop_returns_wav_metadata`→`test_stop_returns_compact_ack`，3 处依赖 stop 响应 `asr.wav_path` 的断言改由 `GET /status` 查询）；新增 `VoiceStopContractTest`（4 项）：成功/失败响应字节数 ≤ 512B（成功用真实量级 619200 帧）、stop 仍触发后台 ASR、status 仍返回完整调试信息；顶层 docstring 记录 Step 3D 覆盖 |
| `weeks/week05/STATE.md` | 本文档记录本步 |

### 新旧 stop 成功响应对比

| 项 | 旧（Step 3B） | 新（Step 3D） |
|----|---------------|---------------|
| `ok` / `state` / `recording` | ✅ | ✅（不变） |
| `message`（中文长说明） | ✅ | ❌ 移除 |
| `wav` | 完整 8 字段元数据（path 等） | 仅 `frames` + `duration_seconds` |
| `asr` | 完整 9 字段快照（含 text/wav_path/error） | 仅 `status` |
| 实测字节数 | >511（ESP32 报告 `len=511, truncated=1`） | **120 B**（38.7s 真实量级场景实测） |
| ESP32 契约必需字段（JSON 对象/ok/state/wav 对象/frames/duration_seconds） | 满足但被截断 | 满足 ✓（无截断） |

新响应实测（Flask test client 原始 UTF-8 字节，真实量级 619200 帧）：
`{"asr":{"status":"running"},"ok":true,"recording":false,"state":"idle","wav":{"duration_seconds":38.7,"frames":619200}}` → **120 bytes**。

固件契约兼容性核对（`voice_button.c voice_post_action`）：stop 成功路径只要求
响应是 JSON 对象、`ok==true`、`state=="idle"`、`wav` 为对象且读取
`wav.frames` / `wav.duration_seconds`（用于日志关联）——新响应**全部满足，无需修改
ESP32 固件**（`main/voice_button.c/h` 无改动）。

### 验证结果（自动测试，未实机联调）

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `service/test_voice_control_server.py` | ✅ PASS 62/62 | 隔离虚拟环境 `D:\AIinteraction-venvs\week05-audio`（flask 3.1.3 / pytest 9.1.1） |
| `pytest service/` 回归 | ✅ 213 passed + 6 subtests | 同上 |
| stop 成功响应字节数 ≤ 512B | ✅ 120 B（真实量级 619200 帧） | `VoiceStopContractTest::test_stop_success_response_fits_esp32_buffer` |
| stop 失败响应字节数 ≤ 512B（409 / 422） | ✅ | `VoiceStopContractTest::test_stop_failure_responses_fit_esp32_buffer` |
| stop 成功仍触发后台 ASR、status 可查 text | ✅ | `VoiceStopContractTest::test_stop_success_still_triggers_background_asr` |
| status 仍返回完整 last_recording / asr 调试信息 | ✅ | `test_stop_returns_compact_ack` + `test_status_after_stop_returns_full_debug_info` |

### 范围与合规（Step 3D）

- ✅ 未修改 Whisper（`service/asr_whisper.py` 不动）、未修改 ESP32 固件（契约核对满足，
  无需固件变更）、未烧录、未真实联调、未进入 LLM/TTS
- ✅ 修改仅限 `service/voice_control_server.py`、`service/test_voice_control_server.py`、
  `weeks/week05/STATE.md` 三个文件
- ✅ 一次本地 commit，未 push；未读取/输出 `sdkconfig` 与 Wi-Fi 凭据

### 结论

- Stop 响应在真实量级下从「>511B 被截断」降为 **120B**，留出约 3.3 倍余量给契约字段
  演进；完整调试信息由 `GET /api/voice/status` 保留，控制/查询职责清晰分离。
- 本次为 **PC 侧契约预防性修复**，**尚未在实机重新联调**：固件解析逻辑对新响应预期
  满足（契约字段逐一核对），但真实 PLAY→STOP 双端取证属于后续实测步骤，本步不做
  （遵循本步范围约束）。

## Step 3E 状态 — ESP32 btn_scan 任务栈溢出修复（2026-10-11）

> 背景：Step 3E 实机验证失败项 → FreeRTOS 报
> `A stack overflow in task btn_scan has been detected.`

### 定位

- 任务创建与栈定义均在 `main/help_event.c`：
  - `#define BTN_TASK_STACK_SIZE 2048`（原值，全固件最小任务栈）
  - `xTaskCreate(button_scan_task, "btn_scan", BTN_TASK_STACK_SIZE, …)`
- 任务循环内调用 `adc_button_read()`（→ `adc_oneshot_read()` + `adc_cali_raw_to_voltage()`
  校准路径）+ `ESP_LOGI` 格式化打印；2048B 栈在实机被 FreeRTOS CANARY 检测击穿
  （`sdkconfig`: `CONFIG_FREERTOS_CHECK_STACKOVERFLOW_CANARY=y`）。
- 同固件任务栈对比：`uplink=4096`、`cam_task=4096`、主任务=4096、`voice_ctl=5120`，
  `btn_scan` 的 2048 是最小且唯一触发溢出者。

### 修复（最小改动，仅一处宏配置）

| 文件 | 改动 |
|------|------|
| `main/help_event.c` | `BTN_TASK_STACK_SIZE` **2048 → 4096**，并加注释说明原因；**未改按键 FSM 逻辑**、任务结构、优先级与事件状态机 |

- 取 4096 而非 3072：与本固件 `uplink`/`cam_task` 任务栈一致，为 esp_adc 校准路径 +
  日志格式化留出稳定余量；任务栈仅增加 2KB（内部 DRAM），无资源压力。
- **未修改** `voice_button.c/h`、PC 服务（Step 2A/3B/3D）、ASR（Step 3A）、LLM/TTS。

### 验证结果

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `idf.py build`（ESP-IDF v5.4.3） | ✅ PASS | 退出码 0；`Project build complete.` |
| 编译告警 / 错误 | ✅ 0 warning / 0 error | 构建日志中 `warning:` / `error:` 出现次数 = 0 |
| `git diff --check` | ✅ 通过 | 退出码 0，无空白错误 |
| 烧录复核「不再溢出」 | ⛔ **未做** | 本步骤明确不烧录、不实机验证 |

### 范围与合规（Step 3E）

- ✅ 修改仅限 `main/help_event.c`、`weeks/week05/STATE.md` 两个文件
- ✅ 未烧录、未实机验证、未进入 LLM/TTS、未读取/输出 `sdkconfig` 与 Wi-Fi 凭据
- ✅ 一次本地 commit，未 push

### 结论

- 栈溢出根因是任务栈过小（2048B）叠加 esp_adc 校准与日志格式化调用路径；
  增大到 4096B 后与同固件其他任务栈一致，预期不再触发栈溢出检测。
- 该结论基于代码静态分析与编译验证；**是否符合实机仍需后续烧录复核**
  （未实机验证，不得标记为已通过）。

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
| PC 音频采集验证 (Step 1) | 🟡 代码 + 自动测试完成；真实录音格式 PASS；有效语音采集（第 3 轮）**PASS**；ASR（Whisper）**未接入** |
| 按键事件→PC 链路 (Step 2) | 🟢 **完成**：Step 2A「PC 端录音 HTTP 控制服务」✅ 自动测试 39/39；Step 2B「ESP32 按键事件→PC 控制」固件侧代码 ✅ 已实现并编译通过（`idf.py build`，0 warning）；**Step 2C 实机联调 ✅ 已烧录并实测通过**（两次 PLAY→start→recording→stop→idle 双端取证，含一次瞬时连接失败的如实记录，详见上方 Step 2C 段） |
| ASR 接入 (Step 3) | 🟢 **Step 3A/3B/3C 均按实测完成，Step 3D 契约缺陷已修复**：Step 3A 本地 Whisper ASR 模块（`service/asr_whisper.py`）代码+自动测试 18/18，真实语音识别已执行（第 2 轮 PASS，base/zh/cpu 纯推理 0.68s；第 1 轮转录为空已如实记录；stdlib wave 解码不依赖 FFmpeg 经实测）；Step 3B 录音停止后自动提交后台本地 ASR ✅ 自动测试 58/58（回归 209 通过）；**Step 3C 真实端到端验证 ✅ 已实测（2026-10-11）**：PLAY→PC 录音→自动 Whisper base/zh/cpu 识别 success，START 双端取证；转录文本非空、中段语义对应、**前段未识别如实记录**；**发现 stop 响应超 ESP32 512B 缓冲导致固件侧 STOP 契约失败**（详见 Step 3C 段），**Step 3D 已修复**（控制/查询接口解耦，见 Step 3D 段）；**Step 3E 实机验证发现 `btn_scan` 任务栈溢出 → 已修复**（`BTN_TASK_STACK_SIZE` 2048→4096，`idf.py build` 通过，未烧录复核，见 Step 3E 段）；不同口音/噪声鲁棒性待后续验证；Step 4 LLM NL→JSON 未开始 |
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
