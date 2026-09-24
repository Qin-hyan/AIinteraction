# Week 1-2: 传感数据采集 + 远程采集指令

**ESP32-S3-EYE v2.2 + ESP32-S3-EYE-SUB V1.1**

课程: AI交互原型与用户体验设计 · 第 1-2 周
版本: v2.0 · 2026-09-24

---

## 📋 功能概述

### Week 1 (已完成)
1. **QMA6100P 三轴加速度驱动** — I2C 总线 (SDA=GPIO4, SCL=GPIO5, 地址 0x12)
2. **ADC 按键检测** — GPIO1 / ADC1_CH0 (MENU/PLAY/DOWN/UP+)
3. **Wi-Fi STA 连接** — 连接路由器获取 IP
4. **HTTP 服务器** — Web 仪表盘 + REST API, 每 500ms 刷新

### Week 2 (本次新增)
5. **远程采集指令** — POST /api/collect 触发一次手动采集
6. **request_id 追踪** — 每次采集生成唯一ID (req-{timestamp}-{counter})
7. **任务状态反馈** — submitted → received → completed/failed/timeout
8. **采集结果展示** — Web 页面显示本次新观测数据 + 耗时
9. **暂停周期刷新** — 可暂停/恢复 500ms 自动刷新

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

### Week 2 新增功能

- 📡 **"采集一次最新数据" 按钮** — 点击触发一次新采集（非周期刷新）
- 🔢 **request_id 显示** — 证明是新采集而非旧数据
- 📊 **任务状态追踪** — 提交中 → 设备已接收 → 完成/失败
- ⏱️ **耗时显示** — 从提交到完成的精确耗时
- 🔄 **自动刷新开关** — 可暂停周期刷新验证手动采集

### API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/` | Web 仪表盘 |
| GET | `/api/sensor` | 实时传感器数据 |
| POST | `/api/collect` | **Week 2: 触发手动采集** |
| GET | `/api/collect/status?request_id=xxx` | **Week 2: 查询采集状态** |

`GET /api/sensor` 返回 JSON:

```json
{
  "device_id": "esp32s3-eye-wk01",
  "accel_x": 12,
  "accel_y": -45,
  "accel_z": 1008,
  "button": "NONE",
  "uptime_ms": 45230.5,
  "auto_refresh": true
}
```

`POST /api/collect` 返回 JSON:

```json
{
  "request_id": "req-45230500-0001",
  "status": "submitted"
}
```

`GET /api/collect/status?request_id=xxx` 返回 JSON (完成时):

```json
{
  "request_id": "req-45230500-0001",
  "status": "completed",
  "accel_x": 15,
  "accel_y": -42,
  "accel_z": 1010,
  "button": "NONE",
  "elapsed_ms": 105.2
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
    ├── main.c              # 主入口 (Week 2: +采集任务处理)
    ├── qma6100p.h/c        # QMA6100P 三轴加速度驱动
    ├── adc_button.h/c      # ADC 按键检测驱动
    ├── wifi_app.h/c        # Wi-Fi STA 管理
    └── http_server.h/c     # HTTP 服务器 + Web 仪表盘 (Week 2: +collect API)
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
4. 浏览器打开 IP 地址，看到实时传感器数据

### Week 2 新增验证
5. 点击"采集一次最新数据"按钮
6. 观察 request_id 生成、状态变化 (submitted → received → completed)
7. 核对本次新观测数据与 request_id —— 证明是新采集
8. 暂停周期刷新，倾斜开发板后再次点击采集，对比数据变化
9. 观察采集耗时显示

---

## 📝 Week 2 关键设计

### 采集任务状态机

```
IDLE → [用户点击] → SUBMITTED → [设备确认] → RECEIVED → [传感器读取]
  ↑                                                   ↓
  └──────────── [完成后自动回到 IDLE] ←── COMPLETED / FAILED
```

### request_id 格式

```
req-{uptime_ms}-{counter:04d}
例如: req-45230500-0001
```

---

*ESP32-S3-EYE v2.2 · ESP-IDF v5.4.3 · QMA6100P · Week 1-2*