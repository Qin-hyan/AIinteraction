# Week 05 State — 接通按键说话与语音播报

**状态**: 🟢 **Step 1 完成（WAV 格式 PASS；真实麦克风有效语音第 3 轮 ✅ PASS）；Step 2A PC 端录音 HTTP 控制服务 ✅ 已实现并通过自动测试（39/39）；Step 2B ESP32 固件侧 PLAY→PC 控制代码 ✅ 已实现并 `idf.py build` 通过；Step 2C 实机联调 ✅ **已烧录并实测通过**（PLAY→start→recording→stop→idle 双端取证，含一次瞬时连接失败的如实记录）；Whisper ASR 未接入**
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
| 按键事件→PC 链路 (Step 2) | 🟡 **拆分**：Step 2A「PC 端录音 HTTP 控制服务」✅ 已实现并通过自动测试（39/39）；Step 2B「ESP32 按键事件 → PC + 局域网连通性」🟡 **固件侧代码已实现并编译通过**（`idf.py build`，0 warning）；**未烧录、未做真实网络联调**，局域网/防火墙连通性仍未验证 |
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
