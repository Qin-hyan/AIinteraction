# Week 1-2: 传感数据采集 + 远程采集指令

**ESP32-S3-EYE v2.2 + ESP32-S3-EYE-SUB V1.1**

课程: AI交互原型与用户体验设计 · 第 1-2 周
版本: v2.1 · 2026-09-28（Week 2 完成：远程采集指令与执行结果反馈闭环）

---

## 📋 功能概述

### Week 1 (已完成)
1. **QMA6100P 三轴加速度驱动** — I2C 总线 (SDA=GPIO4, SCL=GPIO5, 地址 0x12)
2. **ADC 按键检测** — GPIO1 / ADC1_CH0 (MENU/PLAY/DOWN/UP+)
3. **Wi-Fi STA 连接** — 连接路由器获取 IP
4. **HTTP 服务器** — Web 仪表盘 + REST API, 每 500ms 刷新

### Week 2 (本次新增 · 实现 Web 远程采集指令与执行结果反馈)
5. **"刷新已存数据" vs "采集一次最新数据"** — 两个动作可对照：前者只读板上已保存观测（`GET /api/observation/last`，**绝不触发采集**），后者创建 `request_id` 并让开发板真正读一次传感器
6. **request_id 追踪与结果关联** — 新观测必须携带与本次相同的请求号才算"本次完成"（`linked`），否则不显示成功
7. **任务状态链** — 已提交·已受理 → 设备已接收（回执）→ 完成 / 失败 / 超时；页面显示每个阶段及时间
8. **证据字段** — 观测序号 `seq`（每完成一次真实采集 +1）、观测标识 `record_id`、来源 `live`/`cadence`、采集时间与时间质量 `relative`
9. **周期上报开关** — 暂停后板端停止周期采样、页面停止刷新，但**命令通道保持可用**；已存观测保留原采集时间并提示"数据未更新（≠ 硬件故障）"
10. **请求—回执—观测记录** — 设备端环形缓冲保留最近 6 条（`GET /api/collect/history`）；浏览器端补充记录"设备无响应"的尝试
11. **超时与重复点击** — 完成条件窗口 5 s；重复点击沿用当前请求号（逐次编号）；超时不接受迟到结果、不把旧值改标为本次完成

---

## 🔧 环境要求

| 工具 | 版本 |
|------|------|
| ESP-IDF | v5.4.3 |
| 开发板 | ESP32-S3-EYE v2.2 + SUB v1.1 |
| 模组 | ESP32-S3-WROOM-1 (8MB Flash / 8MB Octal PSRAM) |
| 传感器 | QMA6100P 三轴加速度计 |

### 激活 ESP-IDF 环境

```powershell
D:\esp\Espressif\frameworks\esp-idf-v5.4.3\export.bat
```

---

## 🚀 编译与烧录

### 1. 设置目标芯片

```bash
idf.py set-target esp32s3
```

### 2. 配置 Wi-Fi

```bash
idf.py menuconfig
```

进入 `Example Connection Configuration`：
- 设置 `WiFi SSID`
- 设置 `WiFi Password`

### 3. 编译

```bash
idf.py build
```

### 4. 烧录并查看日志

```bash
idf.py -p COM端口 flash monitor
```

---

## 🌐 Web 仪表盘

### 页面结构（Week 2 版）

| 区块 | 作用 |
|------|------|
| 📡 远程采集控制台 | 目标设备 / 传感源 / 完成条件 / 超时窗口 / 手动请求编号；两个对照动作按钮；状态链与三段相对时间；**已存观测 vs 本次新观测** 对比区 |
| 📐 实时传感器监控 | Week 1 保留：页面 500 ms 直读（`direct_read_count` 单列留证，不产生观测）；周期上报开关 |
| 📋 报文监视 · 请求—回执—观测记录 | 设备端记录（请求号 / 状态 / 来源 / 观测 seq 与数值 / 受理→结果 / 记录来源） |

### API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/` | Web 仪表盘 |
| GET | `/api/sensor` | 页面直读实时值（不产生"已存观测"，`direct_read_count` +1） |
| GET | `/api/observation/last` | **Week 2: 刷新已存数据** — 只读板上已保存观测，不触发采集 |
| POST | `/api/collect` | **Week 2: 采集一次最新数据** — 创建 request_id 并下发指令 |
| GET | `/api/collect/status?request_id=xxx` | **Week 2: 查询状态**（含设备回执与关联观测） |
| GET | `/api/collect/history` | **Week 2: 请求—回执—观测记录**（设备端最近 6 条） |
| POST | `/api/auto_refresh?on=0\|1` | **Week 2: 周期上报开关**（暂停后命令通道仍可用） |

`GET /api/observation/last`（刷新已存数据，不触发采集）:

```json
{
  "action": "refresh_stored",
  "triggered_read": false,
  "fresh_window_ms": 3000,
  "valid": true,
  "record_id": "obs-00046",
  "request_id": "cadence",
  "source": "cadence",
  "seq": 46,
  "accel_x": 517, "accel_y": -856, "accel_z": -38,
  "button": "NONE",
  "observed_ms": 49574,
  "received_ms": 49574,
  "data_age_ms": 202,
  "time_quality": "relative",
  "stale": false,
  "note": "读取的是板上已保存观测，本次未读取传感器（观测序号与采集时间不会变化）。"
}
```

`POST /api/collect`（下发指令）:

```json
{
  "request_id": "req-75253-0001",
  "status": "submitted",
  "task_seq": 1,
  "timeout_ms": 5000,
  "message": "submitted"
}
```

`GET /api/collect/status?request_id=req-75253-0001`（完成时，观测与请求关联）:

```json
{
  "request_id": "req-75253-0001",
  "status": "completed",
  "matches_current": true,
  "linked": true,
  "submitted_ms": 75253,
  "received_ms": 75996.042,
  "completed_ms": 76069.942,
  "elapsed_ms": 816.159,
  "observation": {
    "valid": true,
    "record_id": "obs-00047",
    "request_id": "req-75253-0001",
    "source": "live",
    "seq": 47,
    "accel_x": 504, "accel_y": -826, "accel_z": -174,
    "observed_ms": 76070,
    "time_quality": "relative"
  },
  "note": "完成：已收到与本请求号关联的新观测。"
}
```

`GET /api/collect/history`（设备端请求—回执—观测记录）:

```json
{
  "count": 1,
  "records": [
    {
      "request_id": "req-75253-0001",
      "status": "completed",
      "has_observation": true,
      "source": "live",
      "seq": 47,
      "accel_x": 504, "accel_y": -826, "accel_z": -174,
      "button": "NONE",
      "submitted_ms": 75253,
      "received_ms": 75996,
      "completed_ms": 76069,
      "elapsed_ms": 816
    }
  ]
}
```

---

## 📂 项目结构

```
week02-sensor-collect/
├── CMakeLists.txt          # 顶层 CMake
├── sdkconfig.defaults      # 默认配置
├── README.md               # 本文件
└── main/
    ├── CMakeLists.txt
    ├── main.c              # 主入口（Week 2: +周期上报 / 观测缓存 / 采集任务执行 / 超时判定）
    ├── qma6100p.h/c        # QMA6100P 三轴加速度驱动（Week 1 未改动）
    ├── adc_button.h/c      # ADC 按键检测驱动（Week 1 未改动）
    ├── wifi_app.h/c        # Wi-Fi STA 管理（Week 1 未改动）
    └── http_server.h/c     # HTTP 服务器 + Web 控制台
                            #   Week 1: / 与 /api/sensor
                            #   Week 2: /api/observation/last（刷新已存数据，只读）
                            #           /api/collect（采集一次最新数据）
                            #           /api/collect/status（状态 + 设备回执 + 关联观测）
                            #           /api/collect/history（请求—回执—观测记录）
                            #           /api/auto_refresh（周期上报开关）
```

---

## 🔌 硬件引脚对照

| 外设 | 引脚 | 说明 |
|------|------|------|
| QMA6100P SDA | GPIO4 | 与摄像头 I2C 共享 |
| QMA6100P SCL | GPIO5 | 与摄像头 I2C 共享 |
| ADC 按键 | GPIO1 (ADC1_CH0) | 四键分压网络 |
| RGB LED | GPIO38 | 子板 V1.1, 低电平亮 |
| 模组电源 LED | GPIO3 | 必须开漏模式! |

---

## ⚠️ 硬件约束

1. GPIO3 必须配置为**开漏模式**，否则可能损坏模组电源 LED。
2. GPIO4/5 与摄像头 I2C 共享，后续扩展摄像头时不要重新初始化 I2C 总线。
3. GPIO38 与 MicroSD DATA0 共享，SD 卡初始化后不可再控制 RGB LED。
4. 传感器为 QMA6100P，**不是** QMA7981，寄存器定义不同。

---

## 🧪 验证步骤

### Week 1 验证 (保留)
1. 上电后双 LED 闪烁 3 次（启动指示）
2. 串口日志输出 CHIP_ID = 0x90（确认 QMA6100P）
3. 串口日志输出 Got IP: xxx（确认 Wi-Fi）
4. 浏览器打开 `http://<设备IP>/`，看到实时传感器数据（页面直读）

### Week 2 验证（当堂验证样例，逐项留证）

前提：设备与浏览器在同一网络；串口日志用于对照。

| 步骤 | 操作 | 期望结果（可留证） |
|------|------|--------------------|
| 1 | 打开页面，记录"已存观测"的 `record_id` / `seq` / 采集时间 | 三个字段随周期上报每 1 s 变化 |
| 2 | 关闭 **周期上报** 开关（`POST /api/auto_refresh?on=0`） | 串口打印 `[CADENCE] 周期上报 暂停（命令通道保持可用）`；已存观测序号冻结、数据年龄持续增长并提示"数据未更新"（暂停前"在途"的一次采样可能仍会落地，最多 1 次） |
| 3 | 改变设备状态（倾斜开发板）后点 **🔄 刷新已存数据** | 数值与 `seq` 仍是旧值（`triggered_read=false`）→ 说明这是读已存数据，不是重显新采集 |
| 4 | 点 **⚡ 采集一次最新数据** | 生成 `req-…`；状态链 已提交·已受理 → 设备已接收 → 完成；新观测 `seq = 旧值+1`、`source=live`、关联请求号与本次一致（`linked=true`）；右侧显示"✓ 与本次请求一致" |
| 5 | 连续快速点击两次 | 按钮在任务进行中禁用；第二次返回 `message: "Task in progress"` 且沿用同一请求号（任务可区分） |
| 6 | 关闭开发板电源（或离开网络）后再点采集 | 页面等待并显示重试，最终进入**超时**：提示"暂未收到设备结果，这不代表硬件故障"；记录表新增一条**浏览器端**记录；**旧观测序号与采集时间保持不变**（未被标为本次成功） |
| 7 | 打开 **报文监视 · 请求—回执—观测记录** | 每条记录含 请求号 / 状态 / 来源 / 观测 seq 与数值 / 受理→回执→结果时间 / 记录来源（设备端或浏览器端） |
| 8 | 恢复周期上报（`on=1`） | 串口打印恢复日志；已存观测重新每 1 s 更新（`seq` 继续增长） |

命令行等价留证（把 `10.132.124.63` 换成实际 IP）：

```powershell
curl.exe http://10.132.124.63/api/observation/last          # 刷新已存数据（不触发采集）
curl.exe -X POST http://10.132.124.63/api/collect            # 采集一次最新数据
curl.exe "http://10.132.124.63/api/collect/status?request_id=req-xxxxx-0001"
curl.exe http://10.132.124.63/api/collect/history            # 请求—回执—观测记录
curl.exe -X POST "http://10.132.124.63/api/auto_refresh?on=0" # 暂停周期上报
```

一键复验脚本（只用 Python 标准库，逐项输出 PASS/FAIL）：

```powershell
python week2_api_check.py http://10.132.124.63
```

### 本次实测留证（2026-09-28 · ESP32-S3-EYE v2.2，设备 IP 10.132.124.63）

串口启动日志：

```
I (1799) MAIN: QMA6100P CHIP_ID=0x90
I (1834) MAIN: QMA6100P ready — MODE = ACTIVE ✓
I (3826) WIFI: Got IP: 10.132.124.63
I (3831) HTTP: HTTP ready (Week 2: +collect/status/history/stored) port 80
I (3835) MAIN: READY! Open http://10.132.124.63/ in browser (Week 2: +collect)
I (…) MAIN: IMU(cadence #46 obs-00046): X= 517 Y= -856 Z= -38 mg
```

暂停周期上报前后（周期上报已冻结，命令通道保持可用）：

```
[last1] seq=46 record_id=obs-00046 source=cadence data_age_ms=202  stale=false
[pause] {"auto_refresh": false, "note": "周期上报已暂停：板端停止周期采样，命令通道仍可用；…"}
[last2] seq=46 record_id=obs-00046 observed_ms=49574 data_age_ms=4936 stale=true
[pause-check] seq unchanged=True  age grew=True
```

暂停状态下下发一次采集（新观测与请求号关联）：

```
[collect]     {"request_id":"req-75253-0001","task_seq":1,"timeout_ms":5000,"status":"submitted"}
[status #0]   received   received_ms=75996.042
[status #1]   completed  completed_ms=76069.942  note=完成：已收到与本请求号关联的新观测。
[observation] {"record_id":"obs-00047","request_id":"req-75253-0001","source":"live","seq":47,
               "accel_x":504,"accel_y":-826,"accel_z":-174,"observed_ms":76070}
[link]        linked=True  obs_request_id==req=True  elapsed_ms=816.159
[status-sequence] received -> completed
```

重复点击、未知请求号：

```
[dup-second] {"request_id":"req-77110-0002","status":"submitted","message":"Task in progress","task_seq":2}
[dup-check]  second kept same req=True msg=Task in progress
[unknown-req] {"status":"unknown","matches_current":false,"linked":false,
               "note":"未找到该请求号（设备可能已重启）；绝不会把当前数值当作本次采集结果。"}
```

恢复周期上报后已存观测继续更新：`obs-00051 / seq=51 / data_age_ms=313`；
`/api/sensor` 字段：`sensor_source=QMA6100P 三轴加速度 (mg, I2C 0x12)`、`obs_seq=47`、`direct_read_count=1`、`poll_count=46`、`timeout_ms=5000`。

页面自检：`GET /` 返回 200 / 22508 字节；`<div>` 开闭 51/51 平衡；脚本经 `node --check` 语法校验通过；所有 `$('id')` 引用均在页面中存在（无缺失引用）。

一键复验最终结果（`python week2_api_check.py http://10.132.124.63`）：

```
[PASS] 页面 200 且含两个对照按钮 status=200 bytes=22508
[PASS] 含请求—回执—观测记录区块与证据说明
[PASS] 刷新已存数据不触发采集
[PASS] 暂停后观测序号冻结（3 s 内不再产生新观测） seq=363
[PASS] 暂停后数据年龄增长（数据未更新） age 2144 -> 5455
[PASS] 状态链含设备回执阶段 completed
[PASS] 完成且新观测关联本次请求号
[PASS] 新观测为真实读取 source=live 且序号递增 seq 363 -> 364
[PASS] 任务进行中沿用同一请求号
[PASS] 未知请求号不返回成功
[PASS] 设备端有记录且含受理/回执/结果时间
[PASS] 恢复后已存观测继续更新
== 汇总: 12 PASS / 0 FAIL ==
```

---

## 📝 Week 2 关键设计

### 首版状态图（请求 → 设备回执 → 新观测）

```
                  [Web 点击“采集一次最新数据”]
                              │
                              ▼
  IDLE ──POST /api/collect──> SUBMITTED(已提交·服务器已受理, 登记 submitted_at)
                              │  主循环取到任务
                              ▼
                          RECEIVED(设备已接收, 登记 received_at ← 设备回执时间)
                              │  真实读取一次传感器
              ┌───────────────┴────────────────┐
              ▼                                ▼
        COMPLETED(新观测)                  FAILED(读取失败)
        观测写入“已存观测”，                 本次不产生新观测，
        并记录 record_id/seq/request_id      旧观测保持不变
              │                                │
              └──────────► 写请求记录 ◄─────────┘
                                 │
       SUBMITTED/RECEIVED 超过 5 s 窗口 ──> TIMEOUT(未收到设备结果)
                                            仅写记录，不含新观测；
                                            超时后不接受迟到结果
```

- 三个阶段在界面上分开展示：**服务器受理**（`submitted_ms`）、**设备回执**（`received_ms`）、**结果/新观测**（`completed_ms` + `observation`）。只有同时具备设备回执与新观测才显示"完成"。
- 超时只表示"暂未收到设备结果"，**不直接判定硬件故障**；超时后本请求关闭（不再接受迟到结果），旧观测不会被改标为本次完成。
- 重复点击：任务进行中沿用当前请求号（返回 `message: "Task in progress"`），每次成功下发按 `task_seq` 逐次编号，任务可区分。

### 证据链：怎么证明"是新采集"而不是"重显旧值"

| 证据 | 说明 |
|------|------|
| `seq`（观测序号） | 只有**真实传感器读取成功**才 `+1`；刷新已存数据不会改变它 |
| `record_id` | 每次观测的标识（`obs-00047`），与已存观测可直接对比 |
| `linked` / 观测的 `request_id` | 新观测必须携带本次 `request_id`，页面显示"✓ 与本次请求一致" |
| `source` | `live`＝手动采集指令产生的读取；`cadence`＝周期上报产生的读取；`none`＝无观测 |
| `observed_ms` / `data_age_ms` | 采集时间（相对时间轴）与数据年龄；暂停后年龄持续增长＝未更新 |
| `direct_read_count` | 页面直读次数，单列留证（直读不产生"已存观测"，不参与本次结果判定） |

### 记录字段（请求—设备回执—新观测）

| 字段 | 含义 |
|------|------|
| `request_id` / `task_seq` | 请求号与手动采集编号 |
| `status` | `completed` / `failed` / `timeout` |
| `has_observation` | 是否带回与本次请求关联的新观测 |
| `submitted_ms` / `received_ms` / `completed_ms` | 受理 / 设备回执 / 结果时间（相对时间，`-1` 表示未回执） |
| `elapsed_ms` | 受理→结果耗时 |
| 观测字段 | `seq` / `accel_x,y,z`(mg) / `button` / `source` |

### 时间与字段约定

- 本工程把"服务端受理"与"设备执行"合并为同一块开发板，所有时间都是同一 `esp_timer` **相对时间轴**（`time_quality: "relative"`，单位 ms since boot）。板端未启用 SNTP，**不伪造墙钟时间**（已移除 Week 2 初版中误用 `localtime()` 的显示）。
- 记录与观测字段沿用课程建议的最小字段：device_id、source、record_id、采集时间及时间质量、received_at、单位(mg)、采集状态与载荷。

### request_id 格式

```
req-{uptime_ms}-{counter:04d}      例如: req-75253-0001
obs-{seq:05d}                      例如: obs-00047
```

### 已知限制（留证，不做超出本周范围的实现）

1. 设备端记录环形缓冲仅保留最近 6 条（防内存增长），更新记录会覆盖最旧一条。
2. `collect_task_t` 由 HTTP 任务与主循环共享，当前未加互斥锁（沿用 Week 1 的共享方式）；极端并发下个别字段仍可能读到中间态。
3. 完成条件窗口固定 5 s（`COLLECT_TIMEOUT_MS`），未做可配置。
4. 本周未实现取消/过期深化处理（课程安排在后续周次）。

---

*ESP32-S3-EYE v2.2 · ESP-IDF v5.4.3 · QMA6100P · Week 1-2*