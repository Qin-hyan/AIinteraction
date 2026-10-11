# AI 交互原型

**ESP32-S3-EYE v2.2 + SUB v1.1 · QMA6100P 加速度计 · OV2640 摄像头**

在一块手掌大小的开发板上，用浏览器实时看传感器数据、远程下指令采集、把观测记录持久化到 CSV 里带走。这是一门贯穿 18 周的课程项目——从裸机驱动做起，逐步叠加远程控制、物理反馈和自然语言交互。

---

## 效果一览

项目运行后，开发板启动一个 Wi-Fi 热点上的 HTTP 服务器。浏览器打开仪表盘就能看到：

- **传感器示波器**：三轴加速度实时数值（mg）和按键状态，每 500 ms 自动刷新
- **对比面板**：左侧显示"已存观测"（板上最近一次保存的数据），右侧显示"新采集结果"
- **任务状态链**：点击"采集一次最新数据"后，页面显示 → 已提交 → 设备已接收 → 完成（带本次 `request_id` 证明是新数据）
- **请求记录表**：设备端最近 6 条请求与观测历史
- **周期上报开关**：暂停后板端停止采样、页面停止刷新，但命令通道保持可用
- **CSV 导出按钮**：将浏览器本地持久化的有效观测一键导出为 `esp32_obs_YYYY-MM-DD_hh-mm-ss.csv`

仪表盘使用深色主题，所有字段缺失或空值时显示 `—`，不会出现 `undefined` 或 `NaN`。

> 当前仓库不含开发板照片或仪表盘截图。这些素材将在后续课程交付中补充。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| **QMA6100P 加速度计** | I2C 总线 (SDA=GPIO4, SCL=GPIO5, 地址 0x12)，±2g，单位 mg |
| **ADC 按键** | GPIO1 / ADC1_CH0，四个分压按键：MENU / PLAY / DOWN / UP+ |
| **Wi-Fi STA** | 连接路由器，支持 `sdkconfig` 配置 SSID 与密码 |
| **Web 仪表盘** | 浏览器实时显示传感器数据，每 500 ms 自动刷新，暗色主题 |
| **远程采集指令** | `POST /api/collect` 让开发板执行一次真实传感器读取 |
| **request_id 追踪** | 每个采集请求产生唯一编号，新观测必须携带相同请求号才算"本次完成" |
| **状态链** | 已提交·已受理 → 设备已接收（回执）→ 完成 / 失败 / 超时，页面显示各阶段时间 |
| **OV2640 摄像头** | JPEG 抓拍，PSRAM 存储，15 次连续抓拍验证通过 |
| **周期上报开关** | 暂停/恢复板端自动采样，命令通道独立工作 |
| **localStorage 持久化** | 有效观测自动保存至浏览器本地（50 条上限，`record_id` 去重），刷新不丢 |
| **CSV 导出** | 12 列字段，UTF-8+BOM 编码兼容 Excel，字段缺失留空，不伪造数值 |
| **按键物理反馈** | Week 03：独立 FreeRTOS 按键扫描任务 (30 Hz)，事件驱动 LED 反馈与 HTTP 上行 |

### 关键字段说明

| 字段 | 含义 |
|------|------|
| `source: "live"` | 手动采集指令产生的真实读取 |
| `source: "cadence"` | 周期上报（板端自动采样）产生的真实读取 |
| `source: "none"` | 尚未产生观测 |
| `seq`（观测序号） | 仅真实传感器读取成功才 +1；刷新已存数据不会改变它 |
| `record_id` | 每次观测的唯一标识，格式 `obs-{seq:05d}` |
| `time_quality: "relative"` | 所有时间为 `esp_timer` 相对运行时间（ms since boot），**不伪造墙钟时间** |

---

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                   浏览器                              │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────┐  │
│  │ 传感器仪表盘   │  │ 采集指令面板 │  │ CSV 导出    │  │
│  │ (500ms刷新)   │  │ 状态链显示  │  │ localStorage│  │
│  └──────┬───────┘  └─────┬──────┘  └──────┬──────┘  │
└─────────┼─────────────────┼────────────────┼─────────┘
          │ HTTP (Wi-Fi)    │                │
          ▼                 ▼                │
┌────────────────────────────────────────────┘
│  ESP32-S3-EYE (开发板)
│  ┌──────────────────┐  ┌──────────────┐
│  │ HTTP 服务器       │  │ QMA6100P     │
│  │ (Web + REST API) │◄─┤ 加速度计      │
│  ├──────────────────┤  ├──────────────┤
│  │ 主循环            │  │ OV2640       │
│  │ 采集任务状态机     │  │ 摄像头        │
│  │ 周期上报调度       │  ├──────────────┤
│  ├──────────────────┤  │ ADC 按键      │
│  │ 按键扫描任务 (30Hz)│◄─┤ (MENU/PLAY/  │
│  │ 事件生命周期       │  │  DOWN/UP+)   │
│  │ LED 反馈          │  └──────────────┘
│  └──────────────────┘
└────────────────────────────────────────────
```

---

## 硬件与软件环境

### 硬件

| 组件 | 型号 / 规格 |
|------|------------|
| 开发板 | ESP32-S3-EYE v2.2 + SUB v1.1 |
| 模组 | ESP32-S3-WROOM-1 (8MB Flash / 8MB Octal PSRAM) |
| 摄像头 | OV2640（板载） |
| 加速度计 | QMA6100P（I2C 地址 0x12） |
| 按键 | ADC 分压：MENU / PLAY / DOWN / UP+（GPIO1 / ADC1_CH0） |
| LED | GPIO38 (RGB), GPIO3 (模组电源指示) |

### 工具链

| 工具 | 版本 |
|------|------|
| ESP-IDF | v5.4.3（CMake 构建） |
| 目标芯片 | esp32s3 |
| Python | 3.x（标准库，用于 `service/` 测试） |

---

## 快速开始

### 1. 激活 ESP-IDF 环境

```powershell
D:\esp\Espressif\frameworks\esp-idf-v5.4.3\export.bat
```

### 2. 设置目标芯片

```bash
idf.py set-target esp32s3
```

### 3. 配置 Wi-Fi

```bash
idf.py menuconfig
```

进入 `Example Connection Configuration`，设置：
- `WiFi SSID` — 你的路由器名称
- `WiFi Password` — 你的路由器密码

> ⚠️ `sdkconfig.defaults` 仅含占位符 `YOUR_WIFI_SSID` / `YOUR_WIFI_PASSWORD`。真实凭据通过 `menuconfig` 写入本地 `sdkconfig`（已被 `.gitignore` 忽略）。**不得提交真实凭据到仓库。**

### 4. 编译

```bash
idf.py build
```

### 5. 烧录并查看日志

```bash
idf.py -p COM端口 flash monitor
```

> 串口号取决于硬件连接，例如 `COM10`。可在设备管理器中确认。

### 基本使用

1. 开发板启动后，日志会输出分配的 IP 地址（如 `192.168.1.100`）
2. 同一局域网内的浏览器打开 `http://192.168.1.100` 进入仪表盘
3. 页面自动每 500 ms 刷新传感器数据
4. 点击"采集一次最新数据" → 页面显示状态链：已提交 → 设备已接收 → 完成
5. 点击"📥 导出 CSV" → 下载本地持久化的有效观测记录
6. 可使用 Python 工具链（`service/`）以 JSON 任务契约方式查询或采集

---

## 开发进度与验证状态

### 已完成

#### Week 01 — 传感数据采集与 Web 展示 ✅

- QMA6100P 加速度驱动、ADC 按键、Wi-Fi STA、HTTP 仪表盘
- 实机验证通过

#### Week 02 — 远程采集指令与执行反馈 ✅（tag: `week02-stable`）

- request_id 追踪、任务状态链（已提交→已受理→设备已接收→完成/失败/超时）
- 证据字段（seq、record_id、source、相对时间）
- OV2640 摄像头 JPEG 抓拍（PSRAM 存储），15 次连续抓拍通过
- 周期上报开关、超时与重复点击保护
- **12 项实机验证全 PASS，已打 tag `week02-stable` 作为稳定基线**

#### Week 03 — 实体按键与物理反馈闭环 🔀（已合并至 main）

- 独立 FreeRTOS 按键扫描任务 (30 Hz) + FreeRTOS Queue 通信
- 按键去抖 FSM、事件生命周期（help_event）、LED 反馈
- help_uplink HTTP POST 模块（将事件序列化为 JSON 推送至外部 VPS）；异步接口已集成至 `main.c` 事件状态机（`29dd72d`）
- 实机验证：按键检测 ✅、事件创建 ✅、HTTP 发送 ✅、快速连按抑制 ✅、30s 超时自动重置 ✅
- ⚠️ **真实 ESP32→VPS HTTP 上行仍待实机验收**（help_uplink 异步任务已集成至 main.c，需配置真实 VPS URL 验证 HTTP POST 成功/失败路径）

#### Week 04 — 自然语言任务管道 🔀（已合并至 main，PR #4 → `7dd788b`）

| 模块 | 状态 | 验证方式 |
|------|------|---------|
| 任务契约校验（`service/task_schema.py`、`validator.py`） | ✅ 24 单元测试 PASS | Python 单元测试 |
| query_last 受限工具（`service/query_last.py`） | ✅ 13 单元测试 PASS | mock HTTP，不请求真实设备 |
| trigger_collect 受限工具（`service/trigger_collect.py`） | ✅ 12 单元测试 PASS | mock HTTP，不请求真实设备 |
| 受限任务分发（`service/task_dispatcher.py`） | ✅ 16 单元测试 PASS | mock HTTP |
| 工具结果格式化（`service/result_formatter.py`） | ✅ 20 单元测试 PASS | 真实函数体测试 |
| 任务处理入口集成（`service/task_pipeline.py`） | ✅ 10 集成测试 PASS | 真实链路（仅 HTTP mock） |
| **本地 Python 任务管道总计** | **102 测试全部 PASS** | 纯标准库，零外部依赖 |
| **Web 仪表盘 localStorage 持久化** | ✅ **人工验证通过** | 浏览器刷新、重新打开均正确恢复 |
| **CSV 导出** | ✅ **人工验证通过** | 导出 29 条、12 列、全部为 `cadence`，字段完整 |


#### Week 05 — 按键说话（语音输入链路）✅（开发分支 `feature/week05-voice-interaction`，2026-10-11 收尾）

| 模块 | 状态 | 验证方式 |
|------|------|---------|
| PC 音频采集（`service/audio_capture.py`） | ✅ 代码 + 自动测试 31/31 + 真实麦克风录音格式 PASS | 模拟流 + 真实设备实测 |
| PC 录音 HTTP 控制服务（`service/voice_control_server.py`） | ✅ Step 2A 自动测试 39/39；Step 3B 追加 ASR 相关 58/58；Step 3D 62/62 | Python 自动测试（pytest 回归 213 passed） |
| ESP32 PLAY 按键→PC 控制（`main/voice_button.c/h`） | ✅ 已实现并 `idf.py build` 通过，Step 2C 实机联调通过 | 实机双端取证 |
| 本地 Whisper ASR（`service/asr_whisper.py`） | ✅ 18/18 自动测试；真实中文语音识别 PASS（stdlib `wave` 解码不依赖 FFmpeg） | 假模型 + 真实语音 |
| 录音停止自动后台 ASR（Step 3B） | ✅ 已实现并通过自动测试 | Python 自动测试 |
| **完整链路（Step 3I 最终实机验证）** | ✅ **PLAY→PC 录音→自动后台 Whisper→STOP→ESP32 回 IDLE 双端闭环验证 PASS**（2026-10-11：START 200 + STOP 200 + 本地 IDLE，无截断/无传输层超时/无栈溢出/无 Brownout） | 实机（ESP32 × PC 双端日志取证） |

**Week 05 处理的关键问题（记录在 [weeks/week05/STATE.md](weeks/week05/STATE.md)）**：
- Step 3C→3D：stop 响应超 ESP32 512B 缓冲 → 控制/查询接口解耦，紧凑回执 ≤120B
- Step 3E→3F：`btn_scan` 任务栈溢出（2048→4096）实机复核通过
- Step 3F/G→3I：传输层超时与 Brownout 均未在最终验证中复现

⚠️ **Week 05 未完成能力（如实标注，不得声称已完成）**：Step 4 语言模型 NL→JSON、Step 5 TTS 播报、Step 6 全链路串联、Step 7 状态显示与 LED 反馈、Step 8 异常处理与文字对照测试均**未开始**；语音仅实现「按钮开始/结束录音 → 音频 → 任务文本（ASR）」输入侧，输出侧语音播报（TTS）尚未实现。


### 仍待补验

| 项目 | 说明 |
|------|------|
| ⚠️ **真实语言模型输出 → 管道输入接口对接** | 当前使用结构化 JSON 样例替代 LLM 输出 |
| ⚠️ **真实 ESP32 HTTP 调用** | query_last、trigger_collect 工具仅通过 mock 测试，未在真实设备上运行 |
| ⚠️ **Week 03 ESP32→VPS HTTP 上行端到端验收** | **软件集成已完成**：help_uplink 异步任务已集成至 `main.c`（初始化调用 `help_uplink_start`，事件状态机调用 `help_uplink_is_configured` / `help_uplink_request_send` / `help_uplink_poll`，见 `29dd72d`）；**仍未完成**真实 VPS URL 配置与 ESP32→VPS 端到端实机验收（HTTP POST 成功/失败路径未验证） |

> 所有测试记录详见 [TEST_LOG.md](TEST_LOG.md)（版本、输入、预期、实际、PASS/FAIL、运行位置和证据）。

### 状态标记

- ✅ 已完成且经实机或人工验证
- 🔀 已合并但部分环节待验收
- ⚙️ 开发中 / 仅在 mock 层通过
- ⏳ 未开始

稳定基线：`week02-stable` tag（12 项实机验证全 PASS，可随时回退）

---

## 项目结构

```
AIinteraction/
├── main/                     # ESP-IDF 组件：全部业务代码
│   ├── main.c                # 入口 + 初始化 + 主循环
│   ├── CMakeLists.txt        # 组件注册
│   ├── Kconfig.projbuild     # 项目级 Kconfig 配置
│   ├── idf_component.yml     # 依赖：esp32-camera ^2.0.18
│   ├── qma6100p.c/h          # 加速度计 I2C 驱动
│   ├── adc_button.c/h        # ADC 按键检测
│   ├── wifi_app.c/h          # Wi-Fi STA 连接
│   ├── http_server.c/h       # HTTP 服务器 + Web 仪表盘 + REST API
│   ├── camera_app.c/h        # OV2640 摄像头驱动
│   ├── help_event.c/h        # Week 03：按键→LED→Web 物理反馈闭环
│   ├── help_uplink.c/h       # Week 03：事件 VPS 上行推送（已集成至 main.c；真实 VPS 验收待完成）
│   └── voice_button.c/h      # Week 05：PLAY 按键 → PC 录音 HTTP 控制（按键式半双工）
├── service/                  # Python 工具链
│   ├── task_schema.py        # Week 04：IntentEnum / TaskContract / JSON Schema
│   ├── validator.py          # Week 04：三步校验：JSON 解析 → Schema → 意图契约
│   ├── query_last.py         # Week 04：读取 ESP32 设备最近一次观测
│   ├── trigger_collect.py    # Week 04：下发采集指令并轮询结果
│   ├── task_dispatcher.py    # Week 04：受限任务分发入口
│   ├── result_formatter.py   # Week 04：工具结果格式化为可读文本
│   ├── task_pipeline.py      # Week 04：任务处理入口（串联 dispatch → format）
│   ├── audio_capture.py      # Week 05：PC 音频采集（sounddevice + stdlib wave）
│   ├── voice_control_server.py # Week 05：PC 录音 HTTP 控制服务 + 后台自动 Whisper ASR
│   ├── asr_whisper.py        # Week 05：本地 Whisper ASR 模块（base/zh/transcribe）
│   ├── requirements-*.txt    # Week 05：PC 端音频/ASR 依赖清单
│   └── test_*.py             # 单元/集成测试（Week 04 共 102 项；Week 05 追加含 ASR/录音/服务测试）
├── partitions.csv            # 8MB Flash 分区表
├── CMakeLists.txt            # 顶层 CMake 项目
├── sdkconfig.defaults        # 公开配置模板（含 Wi-Fi 占位符）
├── sdkconfig                 # 本地配置（已 gitignore，含真实 Wi-Fi 凭据）
├── dependencies.lock         # 组件依赖锁定
├── TEST_LOG.md               # 测试记录
├── AGENT_MEMORY.md           # 项目持久事实
├── PROJECT_STATE.md          # 当前进度
├── DECISIONS.md              # 技术决策
└── weeks/                    # 每周详细状态
    ├── week01/STATE.md
    ├── week02/STATE.md
    ├── week03/STATE.md
    ├── week04/STATE.md
    └── week05/STATE.md
```
---

## 详细文档

| 文档 | 内容 |
|------|------|
| [AGENT_MEMORY.md](AGENT_MEMORY.md) | 项目架构、已完成模块、已确认硬件事实、关键接口、命名约定、Git 基线 |
| [PROJECT_STATE.md](PROJECT_STATE.md) | 当前进度、Week 03/04/05 详细状态、已知问题（Brownout、传输层超时等） |
| [DECISIONS.md](DECISIONS.md) | 技术决策记录 |
| [TEST_LOG.md](TEST_LOG.md) | 完整测试记录（版本、输入、预期、实际、PASS/FAIL） |
| [weeks/week04/STATE.md](weeks/week04/STATE.md) | Week 04 详细设计、测试覆盖、验证结果 |
| [weeks/week05/STATE.md](weeks/week05/STATE.md) | Week 05 详细状态（分支 `feature/week05-voice-interaction`；按键说话链路 PLAY→录音→Whisper ASR→STOP→回 IDLE 已完成实机双端闭环验证；TTS/LLM/全链路未完成） |
| [每周计划/](每周计划/) | 18 周课程计划 HTML |

### HTTP API 速查

| 端点 | 作用 |
|------|------|
| `GET /` | Web 仪表盘 |
| `GET /api/sensor` | 页面直读（不产生观测） |
| `GET /api/observation/last` | 刷新已存数据（**不触发采集**） |
| `POST /api/collect` | 采集一次最新数据 |
| `GET /api/collect/status?request_id=xxx` | 查询任务状态 |
| `GET /api/collect/history` | 设备端最近 6 条记录 |
| `POST /api/auto_refresh?on=0|1` | 周期上报开关 |

### 采集任务状态机

```
IDLE → SUBMITTED(已提交·已受理) → RECEIVED(设备已接收)
                                → COMPLETED(完成·新观测) / FAILED(失败)
                                → TIMEOUT(>5s 未收到结果)
```

---

## Git 分支与版本

| 引用 | 说明 |
|------|------|
| `main` | 最新合并版本 `7dd788b`（PR #4）— 已包含 Week 01–04 完整代码，与 `origin/main` 同步 |
| `week02-stable` (tag) | **稳定基线** — 12 项实机验证全 PASS |
| `feature/week04-natural-language` | 已合并的**历史分支**（PR #4 → `main` `7dd788b`；分支 HEAD `0b4b323` 保留） |
| `feature/week05-voice-interaction` | **当前** — Week 05 开发分支（基线 `7dd788b`）；含完整功能实现：PC 音频采集/录音 HTTP 控制服务/本地 Whisper ASR 模块 + ESP32 PLAY 按键语音控制；**2026-10-11 收尾推送至 `origin/feature/week05-voice-interaction`**；语音输入链路实机双端闭环验证通过（Step 3I）；TTS/LLM/全链路播报未完成 |

---

*ESP32-S3-EYE v2.2 · ESP-IDF v5.4.3 · QMA6100P 三轴加速度计 · OV2640 摄像头 · 18 周课程项目*
