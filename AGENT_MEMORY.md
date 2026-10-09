# Agent Memory — 项目持久事实

> 精简。只记录已确认的长期事实。不写日志、不写完整代码。

---

## 项目架构

```
AIinteraction/
├── main/                   # 所有业务代码
│   ├── main.c              # 入口 + 初始化 + 主循环
│   ├── CMakeLists.txt      # 组件注册
│   ├── idf_component.yml   # esp32-camera ^2.0.18
│   ├── Kconfig.projbuild
│   ├── qma6100p.c/h        # 加速度计驱动
│   ├── adc_button.c/h      # ADC 按键检测
│   ├── wifi_app.c/h        # Wi-Fi STA
│   ├── http_server.c/h     # HTTP 服务器 + Web 仪表盘 + REST API
│   ├── camera_app.c/h      # OV2640 摄像头驱动
│   ├── help_event.c/h      # Week 03: 实体按键→物理反馈闭环
│   └── help_uplink.c/h     # Week 03: 求助事件 VPS 上行推送
├── partitions.csv          # 8MB Flash 分区表 (factory 3MB, storage ~5MB SPIFFS)
├── CMakeLists.txt          # 顶层项目: week02-sensor-collect
├── sdkconfig.defaults      # 公开配置模板
├── sdkconfig               # 本地配置（gitignored，含 Wi-Fi 凭据）
├── dependencies.lock
├── README.md
├── week2_api_check.py
└── 每周计划/               # 18 周课程计划 HTML
```

---

## 已完成模块

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| QMA6100P 驱动 | `qma6100p.c/h` | ✅ Week 01 | I2C addr 0x12, ±2g, 100Hz, 单位 mg |
| ADC 按键 | `adc_button.c/h` | ✅ Week 01 | GPIO1/ADC1_CH0, 4-key voltage divider |
| Wi-Fi STA | `wifi_app.c/h` | ✅ Week 01 | SSID/密码从 sdkconfig 读取 |
| HTTP 服务器 | `http_server.c/h` | ✅ Week 02 | Web 仪表盘 + REST API + 远程采集 |
| 摄像头 OV2640 | `camera_app.c/h` | ✅ Week 02 | JPEG 抓拍, PSRAM 存储, 15 次连续验证 |
| 物理反馈 | `help_event.c/h` | ✅ Week 03 | 按键→LED→Web 闭环, 去抖 FSM (30Hz), 快速连按抑制通过 |
| VPS 上行推送 | `help_uplink.c/h` | 🔧 Week 03 | help_event_t 序列化为 JSON → HTTP POST, **已注册 CMake 但尚未接入 main.c** |
| 任务契约校验 | `service/` (Python) | ✅ Week 04 | IntentEnum, TaskContract, JSON Schema, 严格校验, 24 单元测试全部 PASS |
| query_last 工具 | `service/query_last.py` | ✅ Week 04 | 读取 `ESP32_BASE_URL` 环境变量 → HTTP GET `/api/observation/last`，纯标准库，mock 测试 13 PASS |

---

## 已确认硬件事实

| 项目 | 值 | 确认方式 |
|------|-----|----------|
| I2C SDA | GPIO4 | README + 代码 |
| I2C SCL | GPIO5 | README + 代码 |
| QMA6100P 地址 | 0x12 | 代码 #define |
| ADC 按键引脚 | GPIO1 / ADC1_CH0 | README + 代码 |
| 按键分压 | MENU=2.41V, PLAY=1.98V, DOWN=0.38V, UP+=0.82V | 代码注释 |
| LED GPIO | GPIO38 (RGB), GPIO3 (PWR) | main.c #define |
| 摄像头 PWDN | GPIO42 | main.c #define |
| PSRAM | 8MB Octal | sdkconfig.defaults |
| Flash | 8MB | sdkconfig.defaults |

---

## 关键接口

### 传感器
- `qma6100p_create(bus, addr, &handle)` → `qma6100p_get_acce(handle, &val)` → `val.acce_x/y/z` (float, mg)
- I2C bus handle 由 main.c 初始化，传递给各模块

### HTTP API（Week 02 稳定）
| 端点 | 作用 |
|------|------|
| `GET /` | Web 仪表盘 |
| `GET /api/sensor` | 页面直读（不产生观测） |
| `GET /api/observation/last` | 刷新已存数据（不触发采集） |
| `POST /api/collect` | 采集一次最新数据 |
| `GET /api/collect/status?request_id=xxx` | 查询任务状态 |
| `GET /api/collect/history` | 设备端最近 6 条记录 |
| `POST /api/auto_refresh?on=0\|1` | 周期上报开关 |

### 采集任务状态机（Week 02）
```
IDLE → SUBMITTED → RECEIVED → COMPLETED / FAILED
                              → TIMEOUT (>5s)
```

---

## 命名约定

| 类别 | 格式 | 示例 |
|------|------|------|
| 手动请求 ID | `req-{ms}-{counter:04d}` | `req-75253-0001` |
| 观测 ID | `obs-{seq:05d}` | `obs-00047` |
| 教学测试事件 ID | `help-{ms}-{counter:04d}` | `help-110000-0001` |
| 日志 TAG | 模块前缀 | `W3-EVENT`, `MAIN`, `CAM` |
| 来源标记 | `live` / `cadence` / `none` | |
| 时间 | esp_timer 相对时间 (μs/ms since boot) | `time_quality: "relative"` |

---

## Git 基线

| 引用 | 说明 |
|------|------|
| `main` | 最新合并版本 (94e3350) |
| `week02-stable` (tag) | Week 02 稳定基线, 12 PASS / 0 FAIL |
| `feature/week02-remote-task` | Week 02 开发分支（已合并） |
| `feature/repo-cleanup` | 仓库清洁分支（已合并） |
| `feature/week03-physical-feedback` | **当前** Week 03 开发分支 |