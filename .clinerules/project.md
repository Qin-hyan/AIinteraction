# Project Rules — 18-Week Agent Memory (Long-Term)

> 精简规则。每个新 Agent 窗口应首先读取此文件。

---

## 1. 项目与设备

| 项 | 值 |
|---|-----|
| 项目 | AI 交互原型与用户体验设计（18 周课程） |
| 开发板 | ESP32-S3-EYE v2.2 + SUB v1.1 |
| 模组 | ESP32-S3-WROOM-1 (8MB Flash / 8MB Octal PSRAM) |
| 摄像头 | OV2640（板载） |
| 传感器 | QMA6100P 三轴加速度计 (I2C: SDA=GPIO4, SCL=GPIO5, addr=0x12) |
| 按键 | ADC 分压: MENU/PLAY/DOWN/UP+ (GPIO1 / ADC1_CH0) |
| LED | GPIO38 (RGB), GPIO3 (模组电源 LED) |

---

## 2. 环境

- **构建系统**: ESP-IDF v5.4.3（CMake），非 PlatformIO
- **工具链激活**:
  ```powershell
  D:\esp\Espressif\frameworks\esp-idf-v5.4.3\export.bat
  ```
- **编译**: `idf.py build`
- **烧录**: `idf.py -p COM端口 flash monitor`
- **目标芯片**: `idf.py set-target esp32s3`

---

## 3. Git 安全规则（强制）

- **禁止** `commit` 和 `push`，除非用户明确授权
- **禁止** 修改 `.gitignore` 中已列出的忽略项
- `sdkconfig` 已被 `.gitignore` 忽略——含 Wi-Fi 真实凭据，**禁止** 读取/输出/提交其内容

---

## 4. sdkconfig 规则

- `sdkconfig.defaults` 是公开模板（占位符 `YOUR_WIFI_SSID` / `YOUR_WIFI_PASSWORD`）
- 真实凭据在本地 `sdkconfig`（已 gitignore），通过 `idf.py menuconfig` 配置
- **任何 Agent 不得读取、回显、或输出 `sdkconfig` 中的 Wi-Fi 凭据**

---

## 5. 不猜原则

- **不猜硬件引脚** — 以代码中的 `#define` 和 README 为准
- **不猜串口号** — 必须通过 `idf.py monitor` 或用户告知确认
- **不猜传感器地址** — QMA6100P 地址已确认 0x12
- **不猜摄像头型号** — OV2640 已确认
- **任何不确定** → 先停止，提供已有证据，向用户提问

---

## 6. 增量开发

- 优先复用已有模块（qma6100p, adc_button, wifi_app, http_server, camera_app, help_event）
- 新增功能先在 `main/` 中建立独立 `.c/.h` 模块
- 不破坏 Week 01-02 已完成功能（`week02-stable` tag 为可回退基线）
- 修改后必须先 `idf.py build` 验证编译通过

---

## 7. 记忆文件约定

- `AGENT_MEMORY.md` — 持久事实（架构、模块、接口、命名）
- `PROJECT_STATE.md` — 当前进度
- `DECISIONS.md` — 技术决策
- `TEST_LOG.md` — 测试记录
- `weeks/weekXX/STATE.md` — 每周详细状态
- **不写**完整代码、完整日志、聊天记录到记忆文件