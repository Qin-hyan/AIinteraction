# Week 1: 传感数据采集与 Web 展示系统

**ESP32-S3-EYE v2.2 + ESP32-S3-EYE-SUB V1.1**

课程: AI交互原型与用户体验设计 · 第 1 周
版本: v1.0 · 2026-09-23

---

## 📋 功能概述

1. **QMA6100P 三轴加速度驱动** — I2C 总线 (SDA=GPIO4, SCL=GPIO5, 地址 0x12)
2. **ADC 按键检测** — GPIO1 / ADC1_CH0 (MENU/PLAY/DOWN/UP+)
3. **Wi-Fi STA 连接** — 连接路由器获取 IP
4. **HTTP 服务器** — Web 仪表盘 + REST API, 每 500ms 刷新

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

烧录成功后，串口会打印开发板的 IP 地址：

```
I (MAIN) READY! Open http://192.168.1.100/ in browser
```

在浏览器中访问该地址即可看到实时传感器数据仪表盘。

### Web 页面功能

- 📊 QMA6100P 三轴加速度值 (mg)
- 🔘 按键状态显示 (MENU / PLAY / DOWN / UP+)
- ⚡ 每 500ms 自动刷新
- 🌙 GitHub 暗色主题风格 UI

### API 端点

`GET /api/sensor` 返回 JSON:

```json
{
  "device_id": "esp32s3-eye-wk01",
  "accel_x": 12,
  "accel_y": -45,
  "accel_z": 1008,
  "button": "NONE",
  "uptime_ms": 45230.5
}
```

---

## 📂 项目结构

```
week01-sensor-web/
├── CMakeLists.txt          # 顶层 CMake
├── sdkconfig.defaults      # 默认配置
├── README.md               # 本文件
└── main/
    ├── CMakeLists.txt
    ├── main.c              # 主入口
    ├── qma6100p.h/c        # QMA6100P 三轴加速度驱动
    ├── adc_button.h/c      # ADC 按键检测驱动
    ├── wifi_app.h/c        # Wi-Fi STA 管理
    └── http_server.h/c     # HTTP 服务器 + Web 仪表盘
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

### 按键电压阈值

| 按键 | 电压 |
|------|------|
| MENU | 2.41V |
| PLAY | 1.98V |
| UP+ | 0.82V |
| DOWN | 0.38V |
| 无按下 | ~3.3V |

---

## ⚠️ 硬件约束

1. GPIO3 必须配置为**开漏模式**，否则可能损坏模组电源 LED。
2. GPIO4/5 与摄像头 I2C 共享，后续扩展摄像头时不要重新初始化 I2C 总线。
3. GPIO38 与 MicroSD DATA0 共享，SD 卡初始化后不可再控制 RGB LED。
4. 传感器为 QMA6100P，**不是** QMA7981，寄存器定义不同。

---

## 🧪 验证步骤

1. 上电后双 LED 闪烁 3 次（启动指示）
2. 串口日志输出 CHIP_ID = 0x90（确认 QMA6100P）
3. 串口日志输出 Got IP: xxx（确认 Wi-Fi）
4. 浏览器打开 IP 地址，看到三轴加速度数据和按键状态
5. 倾斜开发板，观察 Web 页面数值变化
6. 按下按键，观察 Web 页面显示对应按键名称

---

## 📝 GitFlow 工作流

```bash
# 初始化 GitFlow
git flow init -d

# 创建功能分支
git flow feature start week01-sensor-web

# 开发完成后推送
git push -u origin feature/week01-sensor-web

# 完成功能分支
git flow feature finish week01-sensor-web

# 推送 develop 分支
git push -u origin develop
```

---

*ESP32-S3-EYE v2.2 · ESP-IDF v5.4.3 · QMA6100P*