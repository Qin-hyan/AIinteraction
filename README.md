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
- **🎙 语音交互面板**：仪表盘内置语音控制（开始/停止录音/取消任务），实时显示录音帧数、时长、ASR 识别文本与语音任务状态（`state`/`generation`/`request_id`）；直接调用 PC 端语音服务 `/api/voice/{start,stop,status,cancel}`
- **🔍 视觉反馈面板**：在原仪表盘内对设备照片或本地 JPEG 做一次真实 DBCNN 质量评分，展示原始分数 / 模型版本 / Week 07 规则诊断 / 未校准状态，并支持「可用 / 不可用 / 无法判断 + 原因」人工反馈与重新采集；分数未校准，不代表准确率或遮挡分类
- **⚙️ 运行环境面板**：打开页面时自动调用本机启动器，幂等启动语音与视觉服务，显示真实状态、端口与日志路径；启动器未运行时给出明确的手动启动命令
- **⏰ 主动询问**：视觉规则/拒识证据触发一次确认/忽略/稍后询问；忽略后同类事件冷却 10 分钟、稍后 5 分钟；显示关联记录、设备请求号与实际运行位置
- **统一导航**：导航顺序与页面卡片一致，滚动高亮当前区块，移除"外场与导出"占位死链，移动端可横向滚动

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
| **语音交互面板** | Week 05：ESP32 仪表盘内置 🎙 语音控制面板，通过 PC 语音服务实现录音/ASR 状态显示与按钮控制；语音输入链路（PLAY→PC 录音→Whisper ASR→STOP 回执→ESP32 回 IDLE）已实机双端闭环验证通过（Step 3I） |
| **语音任务上下文与取消** | Week 06：PC 语音服务全面增加 request_id 全链路追踪、任务状态机、任务取消 `/api/voice/cancel`、澄清/重新输入 `/api/voice/clarify` 与迟到结果代次隔离（generation 单调递增，旧代次结果丢弃）；ESP32 仪表盘新增取消按钮与任务状态显示；新增任务计时（首次反馈/完成/终止/取消→反馈停止，单次实测 79 ms） |
| **视觉反馈（Week 08）** | ESP32 仪表盘内嵌「🔍 视觉反馈」面板；PC 端本地 DBCNN ONNX CPU 推理（仅 `127.0.0.1:8090`），返回原始质量分数 / 模型版本 / Week 07 规则诊断；未配置阈值时判定恒为 `needs_review`；支持人工反馈（可用/不可用/无法判断 + 原因）、重新采集与中文化展示（原始分数可在「查看详细信息」查看）；不新增第二个网页、不上传图片、不调用云端 |

> ⚠️ **Week 05 语音识别待人工验收**：真实中文语音识别准确率和简体中文输出质量**尚未经人工审阅**，不得宣称已通过。静音/背景噪声导致的空转录不得记录为识别成功。

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

### PC 服务自动启动（语音 8080 / 视觉 8090）

浏览器不能直接启动电脑上的 Python 进程。为了让"打开仪表盘就能用语音和视觉"成立，仓库提供一个仅监听 `127.0.0.1` 的本地启动器：

```
浏览器打开 ESP32 仪表盘
        │
        ├─ POST http://127.0.0.1:8079/api/services/ensure
        │        └─ 启动器检查语音 8080 / 视觉 8090，未运行才启动（幂等）
        │
        └─ 页面每 15 s 轮询 /api/services 显示真实状态
```

**一次性安装开机自启动（当前用户，无需管理员）**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1
```

安装后登录时由 Startup 条目启动启动器；之后打开仪表盘，页面会自动确保语音/视觉服务就绪。（本次已验证安装与手动执行，**注销→重新登录的实际触发需下一次登录观察**。）卸载：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

**手动启动（不安装自启动时）**：

```powershell
.\scripts\start_services.cmd
# 或
python -m service.service_launcher
```

启动器控制台：`http://127.0.0.1:8079/`（可查看两个服务的真实状态、日志路径，并手动启动/重启）。

**配置**：复制 `service/launcher.config.example.json` 为 `service/launcher.config.json`（已被 `.gitignore` 忽略），按本机虚拟环境填写 Python 路径、模型路径和端口。语音默认只监听回环；如果仪表盘的语音地址是局域网 IP（ESP32 实体 PLAY 键需要），把 `services.voice.host` 显式设为 `0.0.0.0`，并只在可信局域网使用。

> 启动器**只监听回环地址**，但 ESP32 页面本身来自私网地址，因此 `ensure/start` 允许私网来源；`stop/restart` 只接受本机回环来源。这意味着同一局域网内被攻陷/恶意的网页最多能让本机服务启动，不能停止/重启、执行任意命令或读取凭据。启动器无认证；语音服务 `0.0.0.0` 绑定会暴露到局域网，属于现有 Week 05 链路的要求，不是新增默认值。启动器不会停止由其他终端启动的服务（页面显示"运行中（外部启动）"）。

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

#### Week 06 — 让语音任务可以澄清与停止 🟢（核心开发完成 + 实机取消/反馈停止复测完成；ASR 内容验收待补）2026-10-17

| 能力 | 状态 | 验证方式 |
|------|------|---------|
| 语音任务上下文（`service/voice_control_server.py`） | ✅ request_id（`voice-{ms}-{counter:04d}`）全链路 + 任务状态机 + 代次隔离 | Python 自动测试 |
| 任务取消 `POST /api/voice/cancel` | ✅ 停止录音 + 幂等 + 409 保护 | 自动测试 + 实机联动 |
| 澄清/重新输入 `POST /api/voice/clarify` | ✅ 仅 CLARIFYING 接受、空输入拒绝、轮次递增 | Python 自动测试 |
| 迟到结果隔离 | ✅ generation 单调递增，旧代次结果记录 `result_stale_discarded` 不覆盖新状态 | Python 自动测试 |
| 任务计时 `task.timing` | ✅ 首次反馈 / 完成 / 终止 / 取消→反馈停止四项指标 | Python 自动测试（14 项） |
| 自动测试 | ✅ `test_voice_control_server.py` **99/99 PASS**；全量 `pytest service/` **250 passed + 6 subtests**（0 失败，2026-10-17） | pytest |
| ESP32 仪表盘取消按钮 + 任务状态显示（`main/http_server.c`） | ✅ 固件已烧录，仪表盘 HTTP 200，取消按钮页面已验证 | 实机 |
| **ESP32↔PC 实机取消联动**（2026-10-14） | ✅ `recording → cancel（cancelled_recording=true）→ cancelled`；generation **7→8**；request_id=`voice-09188851-0004` | 实机双端取证 |
| **ESP32↔PC 实机取消复测**（2026-10-17） | ✅ PLAY 启动 → Web 取消 HTTP **200** → `recording → cancelling → cancelled`；generation **5→6**；`latency_cancel_to_feedback_stop_ms=79 ms`；服务端与浏览器显示一致 | 实机双端取证 |

**运行方式**：ESP32 仪表盘（默认 `http://<esp32-ip>`）通过内嵌语音面板调用 PC voice service；PC 服务端为 `service/voice_control_server.py`（绑定如 `10.28.x.x:8080`，`POST /api/voice/{start,stop,status,cancel,clarify}`）。

> ⚠️ **Week 06 未验收项（如实记录，不得描述为失败）**：① **中文 ASR 准确率**——用户当前不方便进行语音输入，**尚未完成人工验收**，按「待补验收」记录，不宣称通过；② **完整 ASR→NL→JSON→任务链路**——`service/nl_to_task.py` 缺失，ASR 原始文本无法转为 TaskContract JSON，链路未端到端验收；③ **TTS 停止播报**——**项目无 TTS 实现**，不存在可中断的音频播报，该能力不适用。

#### Week 07 — 按需获取一张环境图像 🟢（代码 + 构建 + 实机验收完成；真实取帧失败样例待补）2026-10-19

| 能力 | 状态 | 验证方式 |
|------|------|---------|
| 按需抓拍 `POST /api/camera/capture` | ✅ 受理 → `capturing` → 终态；`request_id` + `captured_ms` 关联 | 实机 |
| 四态反馈 | ✅ `capturing / ok / unusable / failed` 语义分离 | 实机（ok/unusable）+ 代码路径（failed） |
| 画面可用性 / 遮挡识别 | ✅ 端侧 esp_jpeg（亮度、对比度、细节）；PC 端同阈值基线 | 实机 + 10 项单元测试 |
| 只在请求后取图 | ✅ 定时自动抓拍禁用；启用请求返回 400 `course_policy_disabled` | 实机 |
| 最少保留 | ✅ 默认上限 3（可配 1–9），超限释放最旧；`POST /api/camera/clear` 显式清除 | 实机（第 4 张后 index=3 → 404；清空后 404） |
| 正常光照 / 遮挡样例 | ✅ `ok`（luma 117.64 / contrast 62.01 / detail 15.54）/ `too_dark`（luma 5.31） | 实机 |
| 当堂验证：上次成功图像不得被改标 | ✅ 遮挡后原 `ok` 条目 request_id、采集时间、字节数均不变 | 实机 |
| 采集失败 `failed` | ⏳ 状态与日志路径已实现，真实取帧失败未构造 | 仅代码 / 构建 |

**触发条件（个人项目边界）**
- 仅由用户显式 `POST /api/camera/capture`（或明确的教学事件）触发；定时/周期抓拍强制禁用，`POST /api/auto_capture {"enabled":true}` 返回 400。
- 仅使用已授权教学设备与无个人敏感信息的场景；当前实现不把图像发送到外部服务。

**停止条件**
- 单次采集在 `ok` / `unusable` / `failed` 任一终态即停止，不自动重试。
- 任务等待超时（5 s）不产生图像，不把旧图标记为本次结果。
- 仅保留最近 3 张；超限或显式 `POST /api/camera/clear` 立即释放对应 JPEG。

**采集权限与失败记录（按官网原文最小实现）**
- 权限边界：无自动采集；只有显式请求触发，请求与图像通过 `request_id`、`captured_ms`、`source=manual` 关联；未实现账户级鉴权（官网原文未要求）。
- 失败记录：`cap_task_status=failed` + `last_img_status=invalid` + 串口 `[DIAG-CAP] status=failed req=<id>`；**不得与 `unusable` 混同**。当前为运行时状态，无独立持久化失败历史（官网原文未要求持久化）。

#### Week 08 — 把视觉推理结果变成可理解反馈 ✅（W8-1…W8-5 完成，已实机验收）2026-10-21

| 能力 | 状态 | 验证方式 |
|------|------|---------|
| 结构化视觉结果契约（`service/vision_result.py`） | ✅ `ok / low_score / needs_review / unknown` + 拒识原因；`score` 为 0~1 相对值 | 单元测试 |
| DBCNN ONNX 适配器（`service/vision_dbcnn.py`） | ✅ 本地权重 + ONNX Runtime CPU；真实分数 `0.7561` / `0.2581` | 真实推理（2 张样例） |
| 本地组合入口 / CLI（`service/vision_analysis.py`） | ✅ 复用既有视觉模块，不新开端口；输出分数、模型版本、阈值状态、规则诊断 | 集成冒烟 |
| 来源分离 | ✅ DBCNN `label=null`、`score_source=dbcnn_onnx`；规则诊断 `source=week07_image_quality_rules` | 集成冒烟 |
| 阈值判定 | ⏳ **未校准**；未配置返回 `needs_review` / `threshold_not_configured`；测试中的 `0.5` 不是生产默认值 | 单元 + 集成 |
| 低分/未知反馈、重新采集、人工纠正 UI | ✅ `needs_review` / `unknown` / `low_score` + 拒识原因；设备重采；三态人工反馈按 `record_id` 关联 | 自动化 + 用户实机 |
| 中文化反馈面板 | ✅ 标题「图像质量分析结果」、四位小数；完整原始分数/SHA-256/记录 ID 等保留在「查看详细信息」 | 用户实机 |
| 课程要求核对 | ✅ T03；T04（自动化/接口/UI 层）；交付物中的错误/未知样例对照仅部分完成 | `weeks/week08/STATE.md` 第 10 节 |

- 模型权重与外部数据文件留在本地忽略目录 `.workbuddy/`，**不纳入 Git**；源码不依赖提交模型二进制。
- 模型来源、配置、许可注意事项与限制见 [docs/vision-dbcnn.md](docs/vision-dbcnn.md)。
- DBCNN 只输出通用质量分数，**不是遮挡类别或准确率**；0~1 实际输出与模型卡 0~100 标称的差异未解释，记录为已知元数据不一致。
- **2026-10-22 收尾修复**：`service/service_launcher.py` + `scripts/` 增加 PC 服务自动启动；Dashboard 新增「运行环境」面板并统一导航顺序/滚动高亮。修复了此前打开页面仍需手动开两个终端的问题。详见 `weeks/week08/STATE.md` 第 11 节与 `docs/service-launcher.md`。


#### Week 09 — 用视觉事件触发一次主动询问 🟡（实现 + 自动化/技术冒烟完成；人工验收未完成）2026-10-22

| 能力 | 状态 | 验证方式 |
|------|------|---------|
| 事件触发条件 | ✅ `unknown` / `low_score` / 规则 `invalid/too_dark/too_bright/low_contrast/low_detail`；`needs_review` 单独不触发 | 单元测试 |
| 优先级 | ✅ 高（unknown/invalid/too_dark/too_bright）> 中（low_contrast/low_detail/low_score） | 单元 + 服务测试 |
| 确认/忽略/稍后 | ✅ 确认无冷却；忽略 10 分钟、稍后 5 分钟；冷却期同类事件 `suppressed` | 单元 + 服务测试 |
| 输入—结果—事件—响应关联 | ✅ `record_id` / `task_ref.request_id` / 来源 / 证据快照 | 服务 + 真实设备冒烟 |
| Dashboard 主动询问面板 | ✅ 消息/优先级/关联记录/运行位置；确认/忽略/稍后/重新采集；历史表 | UI 静态 + 真实页面 |
| 运行位置说明 | ✅ 设备 OV2640 或本地 JPEG → PC 本地 8090 DBCNN → 浏览器；无云端/VPS | 页面 + 文档 |
| 人工体验验收 | ⏳ **未人工核验** | 用户暂时无法操作 |

- 当前环境偏暗时，真实抓拍得到 `low_detail`（20,298 B，score `0.4555`）并触发事件；`occluded.jpg` 得到 `too_dark`（score `0.2581`）。这是环境观察，**未调阈值、未写成准确率**。
- 事件层与 Week 08 人工反馈共用 `record_id`，不改变 DBCNN/规则/反馈格式。详见 [docs/vision-events.md](docs/vision-events.md) 与 `weeks/week09/STATE.md`。

### 仍待补验

| 项目 | 说明 |
|------|------|
| ⚠️ **真实语言模型输出 → 管道输入接口对接** | 当前使用结构化 JSON 样例替代 LLM 输出 |
| ⚠️ **真实 ESP32 HTTP 调用** | query_last、trigger_collect 工具仅通过 mock 测试，未在真实设备上运行 |
| ⚠️ **Week 06 中文 ASR 准确率内容验收** | 用户当前不方便语音输入，实机中文语音识别质量**未完成验收**（非失败，属待补验收）；TTS 停止播报因项目无 TTS 不适用 |
| ⚠️ **Week 06 完整 ASR→NL→JSON→任务链路** | `service/nl_to_task.py` 缺失，ASR 原始文本无法转为 TaskContract JSON；取消/澄清/计时链路已验收，但「真实语音 → 任务执行」未端到端验收 |
| ⚠️ **Week 03 ESP32→VPS HTTP 上行端到端验收** | **软件集成已完成**：help_uplink 异步任务已集成至 `main.c`（初始化调用 `help_uplink_start`，事件状态机调用 `help_uplink_is_configured` / `help_uplink_request_send` / `help_uplink_poll`，见 `29dd72d`）；**仍未完成**真实 VPS URL 配置与 ESP32→VPS 端到端实机验收（HTTP POST 成功/失败路径未验证） |
| ⚠️ **Week 07 真实取帧失败 `failed` 样例** | 四态中的 `failed` 代码路径与日志已实现，但无故障注入手段，**未构造真实失败**；不得用 `unusable` 代替 `failed`，也不得视为已通过 |
| ⚠️ **Week 08 DBCNN 阈值校准与样例对照** | 反馈闭环与中文化面板已实机验收；但质量分数阈值仍未校准（未配置时只返回 `needs_review`，低分/未知路径目前只有自动化证据）；错误/未知类别缺有质量真值的实拍对照 |
| ⚠️ **Week 08 模型能力与许可边界** | 仅少量本地样例做可运行性冒烟，**不能证明准确率/泛化/遮挡识别能力**；Hugging Face 仓库的 Apache-2.0 标签**不自动证明**原始 DBCNN 权重许可，未作法律结论 |
| ⚠️ **Week 08 稳定性与异常注入** | 连续帧/并发/长期运行未验证；T04 异常结构输出仅在单元/接口/UI 层验证，未做真机注入 |

> 所有测试记录详见 [TEST_LOG.md](TEST_LOG.md)（版本、输入、预期、实际、PASS/FAIL、运行位置和证据）。

### 状态标记

- ✅ 已完成且经实机或人工验证
- 🟡 部分完成（核心能力有证据，仍有明确未完成项）
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
│   ├── voice_control_server.py # Week 05/06：PC 录音 HTTP 控制服务 + 后台自动 Whisper ASR + 任务上下文/取消/澄清/代次隔离
│   ├── asr_whisper.py        # Week 05：本地 Whisper ASR 模块（base/zh/transcribe）
│   ├── image_quality.py      # Week 07：画面可用性/遮挡规则诊断
│   ├── vision_result.py      # Week 08：结构化视觉结果契约与拒识语义
│   ├── vision_dbcnn.py       # Week 08：DBCNN ONNX 质量分数适配器（CPU）
│   ├── vision_analysis.py    # Week 08：DBCNN + 规则诊断组合入口 / 本地 CLI
│   ├── vision_event.py       # Week 09：视觉事件触发、优先级、确认/忽略/冷却
│   ├── service_launcher.py   # Week 08 收尾：回环启动器，页面打开时幂等启动语音/视觉
│   ├── launcher.config.example.json # 启动器本机配置模板（真实配置被 gitignore）
│   ├── requirements-*.txt    # Week 05/07/08：PC 端音频/ASR/视觉依赖清单
│   └── test_*.py             # 单元/集成测试（Week 04 共 102 项；Week 05 追加 ASR/录音/服务；Week 06 voice server 99/99；Week 08 相关测试 125 passed；Week 09 事件/服务/UI 44 项；2026-10-22 启动器 31 项 + 面板/导航 22 项）
├── scripts/                  # 启动器一键启动与 Startup 自启动安装/卸载
├── partitions.csv            # 8MB Flash 分区表
├── CMakeLists.txt            # 顶层 CMake 项目
├── sdkconfig.defaults        # 公开配置模板（含 Wi-Fi 占位符）
├── sdkconfig                 # 本地配置（已 gitignore，含真实 Wi-Fi 凭据）
├── dependencies.lock         # 组件依赖锁定
├── TEST_LOG.md               # 测试记录
├── AGENT_MEMORY.md           # 项目持久事实
├── PROJECT_STATE.md          # 当前进度
├── DECISIONS.md              # 技术决策
├── docs/
│   ├── vision-dbcnn.md       # Week 08：DBCNN 模型配置/来源/许可/限制
│   ├── service-launcher.md   # Week 08 收尾：PC 服务启动器架构/API/安全边界
│   └── vision-events.md      # Week 09：视觉事件/主动询问/冷却/API/边界
└── weeks/                    # 每周详细状态
    ├── week01/STATE.md
    ├── week02/STATE.md
    ├── week03/STATE.md
    ├── week04/STATE.md
    ├── week05/STATE.md
    ├── week06/STATE.md
    ├── week07/STATE.md
    └── week08/STATE.md
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
| [weeks/week06/STATE.md](weeks/week06/STATE.md) | Week 06 详细状态（分支 `feature/week06-voice-task-context`：任务上下文/取消/澄清/迟到结果隔离 + 生产计时 + ESP32 仪表盘取消按钮与状态显示；实机取消/反馈停止已复测；ASR 内容验收待补；NL→JSON 缺失） |
| [weeks/week07/STATE.md](weeks/week07/STATE.md) | Week 07 详细状态（分支 `feature/week07-vision-capture`：按需抓拍、四态反馈、画面可用性/遮挡、最少保留与显式清除；实机验收含 ok/too_dark 与当堂验证①；真实 `failed` 样例待补） |
| [weeks/week08/STATE.md](weeks/week08/STATE.md) | Week 08 详细状态（结构化视觉结果契约、DBCNN ONNX 真实分数、来源分离与阈值未配置语义、反馈闭环、并入原 Dashboard；真实设备验收已记录；阈值校准 / 多真值样本 / 权重许可 / 稳定性仍未完成） |
| [docs/vision-dbcnn.md](docs/vision-dbcnn.md) | DBCNN 模型配置、来源、许可注意事项、真实推理证据与已知限制 |
| [docs/service-launcher.md](docs/service-launcher.md) | PC 服务启动器：架构、API、状态、安全边界、配置、自启动与验证边界 |
| [docs/vision-events.md](docs/vision-events.md) | Week 09 视觉事件触发、优先级、确认/忽略/稍后、冷却、API 与验证边界 |
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
| `POST /api/camera/capture` | 按需抓拍（唯一图像采集触发入口） |
| `GET /api/camera/status` | 图像采集四态 + 最近评估指标 + 保留状态 |
| `GET /api/camera/gallery` | 照片元数据（request_id / captured_ms / img_status / 保留计数） |
| `GET /api/camera/photo?index=N` | 返回 JPEG（index 0 = 最新；超出保留范围 404） |
| `GET/POST /api/camera/retention` | 查询/设置保留上限（1–9，默认 3） |
| `POST /api/camera/clear` | 显式清除全部已存图像 |
| `GET/POST /api/auto_capture` | 定时抓拍已禁用；启用请求返回 400 `course_policy_disabled` |
| `GET /api/vision/events?limit=20` | PC 视觉服务：事件列表、active、计数、冷却配置 |
| `GET /api/vision/events/active` | PC 视觉服务：当前最高优先级待响应事件 |
| `POST /api/vision/events/respond` | PC 视觉服务：确认/忽略/稍后（`confirm/ignore/snooze`） |
| `POST /api/voice/start` | PC 语音服务：开始录音 |
| `POST /api/voice/stop` | PC 语音服务：停止录音（返回紧凑回执 ≤120B；自动后台 Whisper ASR） |
| `GET /api/voice/status` | PC 语音服务：查询录音状态、WAV 元数据、ASR 结果 + `task` 块（request_id/state/generation/result/context/log） |
| `POST /api/voice/cancel` | PC 语音服务：取消活跃任务（停止录音；幂等，已完成/已取消/已失败返回 409） |
| `POST /api/voice/clarify` | PC 语音服务：提交澄清/重新输入文本（仅 CLARIFYING 状态接受，空输入拒绝） |

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
| `main` | `origin/main` `333a697`（PR #8，Week 07 已合并）；本地 `main` 非开发基线 |
| `week02-stable` (tag) | **稳定基线** — 12 项实机验证全 PASS |
| `feature/week04-natural-language` | 已合并的**历史分支**（PR #4 → `main` `7dd788b`；分支 HEAD `0b4b323` 保留） |
| `feature/week05-voice-interaction` | **已收尾**（作为 Week 06 基线 `f0653f0`）— Week 05 开发分支（基线 `7dd788b`）；HEAD `91959fb`；含完整功能实现：PC 音频采集/录音 HTTP 控制服务/本地 Whisper ASR 模块 + ESP32 PLAY 按键语音控制 + **ESP32 仪表盘 🎙 语音交互面板**；语音输入链路实机双端闭环验证通过（Step 3I）；真实中文 ASR/简体中文输出待人工验收；TTS/LLM/全链路播报未完成 |
| `feature/week06-voice-task-context` | Week 06 开发分支（基线 `f0653f0` = Week 05 HEAD）；`test_voice_control_server.py` **99/99**、全量 **250 passed + 6 subtests** PASS；ESP32↔PC 实机取消复测与 2026-10-14 实机取消联动均通过；已由 PR #7 合并至 `origin/main` `5aebea1`；中文 ASR 准确率待补人工验收；NL→JSON 缺失，完整任务链路未验收 |
| `feature/week07-vision-capture` | 已由 PR #8 合并至 `origin/main` `333a697`；按需抓拍/四态反馈/画面可用性/最少保留已实机验收 |
| `feature/week08-vision-feedback` | **Week 08 已收尾** — 开发分支（基线 `origin/main` `333a697`）；功能/固件提交 `861ae64`（文档收尾提交见 `git log -1`；已推送同分支至 `origin`）；W8-1 契约 + W8-2 本地 DBCNN + W8-3 反馈闭环 + W8-4 并入原 Dashboard + W8-5 中文化面板；W8-4/W8-5 均已实机验收；**未合并 `main`**；阈值校准 / 错误与未知样例对照 / 权重许可 / 稳定性列为后续补验 |

---

*ESP32-S3-EYE v2.2 · ESP-IDF v5.4.3 · QMA6100P 三轴加速度计 · OV2640 摄像头 · 18 周课程项目*
