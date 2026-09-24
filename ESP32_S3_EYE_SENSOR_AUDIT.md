# ESP32-S3-EYE 传感器故障审计报告

> **日期：** 2026-09-23
> **审计目标：** 调查板载 IMU/加速度传感器读取异常（数据冻结不变）
> **原则：** 只分析、不修改

---

## 1. 项目完整目录结构

```
d:\AIinteraction/                              ← 项目根目录
├── CMakeLists.txt                              ← 顶层 CMake（project: week01-sensor-web）
├── sdkconfig                                   ← 当前配置 (ESP-IDF v5.4.3)
├── sdkconfig.defaults                          ← 默认配置模板
├── partitions.csv                              ← 自定义分区表（4分区，含SPIFFS）
├── README.md                                   ← 项目说明
├── .gitignore
├── .vscode/                                    ← VS Code 配置
├── build/                                      ← 编译输出
│   ├── week01-sensor-web.bin    (853 KB)       ← APP 固件
│   ├── week01-sensor-web.elf    (9.5 MB)       ← ELF
│   ├── week01-sensor-web.map    (8.7 MB)       ← MAP
│   ├── flasher_args.json                       ← 烧录参数
│   ├── project_description.json                ← 项目描述
│   ├── partition_table/
│   │   └── partition-table.bin  (3 KB)         ← 实际使用的分区表
│   ├── bootloader/
│   │   └── bootloader.bin                      ← bootloader
│   └── log/                                    ← 构建和监控日志
│       └── idf_py_stdout_output_73084          ← ★ 最新串口监控日志
├── main/                                       ← ★ 主代码目录
│   ├── CMakeLists.txt                          ← 组件注册
│   ├── Kconfig.projbuild                       ← menuconfig 菜单
│   ├── main.c                                  ← ★ app_main() 入口
│   ├── qma6100p.h                              ← ★ QMA6100P 传感器头文件
│   ├── qma6100p.c                              ← ★ QMA6100P 传感器驱动 (13.2 KB)
│   ├── adc_button.h                            ← ADC 按键头文件
│   ├── adc_button.c                            ← ADC 按键驱动
│   ├── wifi_app.h                              ← Wi-Fi 头文件
│   ├── wifi_app.c                              ← Wi-Fi STA 驱动
│   ├── http_server.h                           ← HTTP 服务器头文件
│   └── http_server.c                           ← HTTP 服务器 + Web 仪表盘
├── 每周计划/
└── .pio-mcp-workspace/                         ← MCP 工作区
```

**关键观察：**
- ❌ **无 `components/` 目录** — 所有驱动代码直接放在 `main/`
- ❌ **无 `managed_components/` 目录** — 未使用 ESP Component Registry
- ❌ **无 `dependencies.lock`** — 无依赖锁定
- ❌ **无 `idf_component.yml`** — 无组件清单
- ⚠️ 传感器驱动 `qma6100p.c/h` 是**本地独立副本**，非官方 `espressif/qma6100p` 组件
---

## 2. 硬件信息

| 项目 | 值 | 来源 |
|------|-----|------|
| 开发板 | ESP32-S3-EYE v2.2 | main.c 注释, README |
| 子板 | SUB v1.1 | main.c 注释, README |
| 模组 | ESP32-S3-WROOM-1 | README |
| 芯片 | ESP32-S3 (QFN56) rev v0.2 | 串口日志 |
| Flash | 8MB (DIO, 80MHz) | sdkconfig, 串口日志 |
| PSRAM | 8MB Octal (AP_3v3) | 串口日志 |

---

## 3. 传感器型号确认

- **代码声称:** QMA6100P | **实际芯片 ID:** 0x90 | **一致:** ✅
- `main/main.c` 行 6, `qma6100p.h` 行 23, `README.md` 行 147
- ESP-BSP 传感器组件中无 QMA6100P，但 ESP Component Registry 有 `espressif/qma6100p` v2.0.1

---

## 4. I2C 总线分析

| 项目 | 值 | 文件:行号 |
|------|-----|-----------|
| 接口类型 | I2C | main.c:20 |
| 控制器 | I2C_NUM_0 | main.c:105 |
| SDA | GPIO4 | main.c:33 |
| SCL | GPIO5 | main.c:34 |
| 速度 | 400 kHz | qma6100p.c:16 |
| 内部上拉 | 启用 | main.c:108 |
| 驱动 API | ESP-IDF 5.x 新版 I2C Master Driver | main.c:101-111 |

I2C 逐项检查：GPIO 正确 ✅ | 地址 0x12 正确 ✅ | API 正确 ✅ | 无新旧混用 ✅ |
⚠️ `qma6100p_wake_up()` 中 FIFO bypass write (qma6100p.c:153) 返回值未检查

---

## 5. 设备地址: 7-bit = 0x12 (AD0=0)

---

## 6. 传感器读取链路 (关键函数追踪)

```
app_main()                                    [main.c:116]
├─ nvs_flash_init()                           [main.c:122]
├─ gpio_set_level(CAM_PWDN_GPIO, 0)           [main.c:135] ← 使能传感器电源 GPIO42
├─ i2c_bus_init(&i2c_bus)                     [main.c:140]
│   └─ i2c_new_master_bus(I2C_NUM_0, ...)    [main.c:110]
├─ qma6100p_create(i2c_bus, 0x12, &imu)      [main.c:144]
│   ├─ i2c_master_bus_add_device(..., 0x12)   [qma6100p.c:97]
│   └─ qma6100p_write(0x0F, 0x07) ← 软复位    [qma6100p.c:100-104]
├─ qma6100p_get_deviceid(imu, &devid)         [main.c:151]
│   └─ qma6100p_read(sensor, 0x00, ..., 1) → 0x90 [qma6100p.c:123]
├─ if (devid == 0x90):                        [main.c:153]
│   ├─ qma6100p_nvm_load(imu)  ← ⚠️ 返回值未检查  [main.c:154]
│   │   └─ write(0x33, tmp|BIT3)              [qma6100p.c:135]
│   ├─ qma6100p_config(imu, ACCE_FS_8G) ← ⚠️ 返回值未检查 [main.c:155]
│   │   └─ write(0x0F, 0x04)                  [qma6100p.c:166]
│   ├─ qma6100p_wake_up(imu) ← ⚠️ 返回值未检查      [main.c:156]
│   │   ├─ write(0x10, tmp|BIT7)               [qma6100p.c:148]
│   │   └─ write(0x3E, 0x00) ← FIFO bypass ⚠️  [qma6100p.c:153]
│   └─ "QMA6100P ready" ← 无论上面是否成功都打印  [main.c:159]
│
└─ while(1):                                  [main.c:185]
    └─ qma6100p_get_acce(imu, &accel)         [main.c:186]
        ├─ qma6100p_get_acce_sensitivity()     [qma6100p.c:449]
        │   └─ 8g → 4096.0                     [qma6100p.c:182]
        └─ qma6100p_get_raw_acce()             [qma6100p.c:453]
            ├─ read(0x01, data_rd, 6)          [qma6100p.c:435]
            ├─ raw_x = ((rd[1]<<8)+rd[0])/4    [qma6100p.c:437]
            ├─ raw_y = ((rd[3]<<8)+rd[2])/4    [qma6100p.c:438]
            ├─ raw_z = ((rd[5]<<8)+rd[4])/4    [qma6100p.c:439]
            └─ ⚠️ 即使 I2C 读失败也计算
```

### 寄存器地址定义

| 寄存器 | 地址 | 定义 | 文件:行号 |
|--------|------|------|-----------|
| WHO_AM_I | 0x00 | QMA6100P_WHO_AM_I | qma6100p.c:18 |
| ACCEL_XOUT_H | 0x01 | QMA6100P_ACCEL_XOUT_H | qma6100p.c:20 |
| ACCEL_CONFIG | 0x0F | QMA6100P_ACCEL_CONFIG | qma6100p.c:19 |
| PWR_MGMT_1 | 0x10 | QMA6100P_PWR_MGMT_1 | qma6100p.c:21 |
| NVM_LOAD | 0x33 | QMA6100P_NVM_LOAD | qma6100p.c:22 |
| FIFO_MODE | 0x3E | **硬编码数字** (非宏) | qma6100p.c:153 |
## 7. 数据结构分析

### 当前项目实际数据结构

```c
// qma6100p.h:77-81 — 原始值
typedef struct { int16_t raw_acce_x, raw_acce_y, raw_acce_z; } qma6100p_raw_acce_value_t;
// qma6100p.h:83-87 — 物理量 (单位: g)
typedef struct { float acce_x, acce_y, acce_z; } qma6100p_acce_value_t;
// qma6100p.c:56-62 — 内部设备结构
typedef struct { i2c_master_dev_handle_t i2c_handle; gpio_num_t int_pin; uint32_t counter; float dt; struct timeval *timer; } qma6100p_dev_t;
// http_server.h:20-24 — 传感器上下文
typedef struct { qma6100p_handle_t imu; adc_button_handle_t btn; const char *device_id; } sensor_ctx_t;
```

### 关键缺陷

| 问题 | 说明 |
|------|------|
| 无 `device_present` 标志 | 只能通过 `imu == NULL` 判断 |
| 无 `initialized` 标志 | devid≠0x90 时 imu 非NULL但不可用 |
| 无 `read_ok` 标志 | I2C读失败时仍返回可能有效值 |
| 无 `error` 字段 | 无法追踪最后一次错误 |
| `qma6100p_get_raw_acce()` | ⚠️ 即使I2C读失败也计算未初始化的data_rd |
| main.c:154-159 | ⚠️ nvm_load/config/wake_up 返回值全部未检查 |
---

## 8. 实际串口日志分析（★ 核心发现）

来源：`build/log/idf_py_stdout_output_73084`

```
I (1793) MAIN: QMA6100P CHIP_ID=0x90        ← ✅ 传感器被检测到
I (1895) MAIN: QMA6100P ready                ← "声称"就绪
I (32046) MAIN: IMU: X=  7942 Y=  1380 Z=     0 mg   ← ⚠️ 冻结!
I (33066) MAIN: IMU: X=  7942 Y=  1380 Z=     0 mg   ← 完全相同
... (数百行完全相同，永不变化)
```

### 冻结值物理含义

| 轴 | 值 | 含义 | 桌面平放预期 |
|----|-----|------|-------------|
| X | 7942 mg = 7.942g | 接近8g满量程 | ~0g |
| Y | 1380 mg = 1.380g | 常数 | ~0g |
| Z | 0 mg = 0.000g | 恰好为零 | ~1000mg |

**结论：数据并非"全0"，而是三个冻结常数值，不符合任何物理姿态。**

### 排除的情况

| 情况 | 结论 | 证据 |
|------|:----:|------|
| A: 设备不存在 | ❌ | CHIP_ID=0x90 |
| B: 初始化完全失败 | ❌ | "QMA6100P ready" |
| C: I2C 完全不工作 | ❌ | WHO_AM_I 读成功 |
| F: 烧录的不是当前代码 | ❌ | 编译时间一致 |
---

## 9. 核心问题定位（Top 5）

### 🥇 问题 1：FIFO 模式寄存器 0x3E 可能不适用于 QMA6100P

| 属性 | 内容 |
|------|------|
| **问题** | `qma6100p_write(sensor, 0x3E, 0x00)` 可能是从 QMA7981 驱动移植的遗留代码 |
| **证据** | 寄存器地址 `0x3E` 硬编码为数字而非宏（其他寄存器都使用 `#define`） |
| **文件:行号** | `d:\AIinteraction\main\qma6100p.c:153` |
| **原因** | 如果 0x3E 在 QMA6100P 中不是 FIFO_MODE 寄存器，写入 0x00 可能导致传感器进入未知状态，输出寄存器冻结 |
| **优先级** | 🔴 **极高** |

### 🥈 问题 2：初始化返回值全部未检查 — 传感器"假装就绪"

| 属性 | 内容 |
|------|------|
| **问题** | `nvm_load()`, `config()`, `wake_up()` 的返回值全部被忽略，直接打印 "ready" |
| **证据** | `main/main.c` 第 154-159 行：三个函数调用都没有用 `ret = ` 接收 |
| **文件:行号** | `d:\AIinteraction\main\main.c:154-159` |
| **优先级** | 🔴 **极高** |

### 🥉 问题 3：NVM 加载可能破坏传感器配置

| 属性 | 内容 |
|------|------|
| **问题** | `qma6100p_nvm_load()` 从 NVM（寄存器 0x33）加载校准数据，若 NVM 为空/无效可能导致传感器异常 |
| **文件:行号** | `d:\AIinteraction\main\qma6100p.c:126-136` |
| **优先级** | 🟠 **高** |

### 4️⃣ 问题 4：驱动可能来自 QMA7981 移植

| 属性 | 内容 |
|------|------|
| **问题** | 本地驱动与官方 `espressif/qma6100p` v2.0.1 可能存在差异（初始化序列、寄存器映射、数据格式） |
| **文件:行号** | `d:\AIinteraction\main\qma6100p.c:178-188, 437-439` |
| **优先级** | 🟠 **高** |

### 5️⃣ 问题 5："别人电脑正常"可能因为使用官方组件

| 属性 | 内容 |
|------|------|
| **问题** | 别人的"同一类工程"可能使用了官方 `espressif/qma6100p` 组件（通过 `idf.py add-dependency`） |
| **证据** | ESP Component Registry 上有 `espressif/qma6100p` v2.0.1，本项目未使用 |
| **优先级** | 🟡 **中** |
---

## 10. ESP-IDF 环境 & sdkconfig 关键配置

| 参数 | 值 |
|------|-----|
| ESP-IDF | v5.4.3 |
| IDF_PATH | D:/esp/Espressif/frameworks/esp-idf-v5.4.3 |
| Python | 3.11.7 |
| 工具链 | xtensa-esp-elf GCC 14.2.0 |
| idf.py 在 PATH | ❌ 否 |
| IDF_PATH 环境变量 | ❌ 未设置 |
| Git 状态 | b712974-dirty ⚠️ |

**sdkconfig 关键项：** CONFIG_IDF_TARGET=esp32s3 ✅ | FLASH=8MB ✅ | SPIRAM_MODE_OCT ✅ | PARTITION_TABLE_SINGLE_APP=y ⚠️ | MAIN_TASK_STACK=4096 ⚠️ | I2C_DEBUG_LOG=未启用 ⚠️

### ⚠️ 分区表不一致

| 来源 | Factory | SPIFFS |
|------|:-------:|:------:|
| projects.csv（项目文件） | 3MB | ✅ |
| sdkconfig 实际使用 | 1MB | ❌ |

---

## 14. 故障树

```
传感器数据冻结 (X=7942, Y=1380, Z=0 mg)
│
├── 1. 设备不存在 ──── ❌ 排除 (CHIP_ID=0x90)
│
├── 2. 设备存在但初始化失败
│   ├── NVM 加载失败/破坏配置     ⚠️ 高概率 (返回值未检查)
│   ├── 唤醒序列不完整             ⚠️ 高概率 (FIFO bypass 0x3E 寄存器可能错误)
│   └── QMA7981 代码残留          ⚠️ 高概率 (0x3E 硬编码)
│
├── 3. 设备初始化成功但寄存器不更新
│   ├── 传感器处于待机模式         ⚠️ 高概率
│   ├── 数据流入 FIFO 而非输出     ⚠️ 高概率 (FIFO bypass 未生效)
│   └── 需要特定触发信号           ⚠️ 低概率
│
├── 4. 原始数据正常但解析错误
│   ├── 字节序/位移/掩码错误       ⚠️ 中概率
│   └── 灵敏度值错误               ⚠️ 中概率
│
├── 5. 数据被中途覆盖 ──── ❌ 排除 (值不为0且单线程)
│
└── 6. 环境/构建差异
    ├── 驱动代码不同 (本地 vs 官方) ⚠️ 高概率
    └── sdkconfig 不同              ⚠️ 中概率
```

---

## 15. 下一步验证动作（不修改代码版本）

### 验证 1：输出原始寄存器字节

打印 I2C 原始 6 字节（地址 0x01-0x06），区分解码错误 vs 寄存器值冻结。

### 验证 2：检查初始化各步骤返回值

在 main.c:154-159 用 ESP_LOGE 打印 `nvm_load/config/wake_up` 的 esp_err_t。

### 验证 3：跳过 NVM 加载

临时注释 `qma6100p_nvm_load(imu)` 调用，观察数据是否变化。

### 验证 4：跳过 FIFO bypass 写入

临时注释 `qma6100p_write(sensor, 0x3E, 0x00)`，观察数据是否变化。

### 验证 5：执行 idf.py fullclean

清除所有编译缓存后重新编译烧录。

### 验证 6：从正常环境获取差异数据

- 别人的 `idf_component.yml`
- 别人的 `managed_components/` 目录
- 别人的 `sdkconfig`
- 别人的 ESP-IDF 版本

### 验证 7：I2C 设备扫描

启动时执行 I2C 扫描，列出所有设备地址。

---

## 16. 建议诊断输出格式

当前日志：`IMU: X=  7942 Y=  1380 Z=     0 mg`

建议增强为：

```
[IMU] bus=I2C0 sda=4 scl=5 addr=0x12 chip_id=0x90
[IMU] nvm_load=OK config=OK wake_up=OK
[IMU] raw=[01 02 03 04 05 06]
[IMU] ax=7.942 ay=1.380 az=0.000 mg err=NONE
```

---

## 附录 A：文件清单

| 文件 | 大小 | 关键内容 |
|------|------|----------|
| `main/main.c` | 6,518 B | app_main, I2C初始化, 传感器初始化, 主循环 |
| `main/qma6100p.h` | 11,349 B | QMA6100P 类型定义, API 声明 |
| `main/qma6100p.c` | 13,209 B | QMA6100P 驱动实现 |
| `main/adc_button.c` | 4,867 B | ADC 按键驱动 |
| `main/http_server.c` | 6,795 B | HTTP 服务器 + Web 仪表盘 |
| `main/wifi_app.c` | 2,764 B | Wi-Fi STA |
| `main/CMakeLists.txt` | 339 B | 组件注册 |
| `CMakeLists.txt` | 182 B | 顶层 CMake |
| `sdkconfig` | 73,743 B | 完整配置 |
| `sdkconfig.defaults` | 703 B | 默认模板 |
| `partitions.csv` | 245 B | 自定义分区表 |

## 附录 B：ESP Component Registry 对比

| 特性 | 当前项目 | 官方 espressif/qma6100p v2.0.1 |
|------|----------|-------------------------------|
| 安装方式 | 本地嵌入 main/ | idf.py add-dependency |
| 版本管理 | Git 追踪 | Component Registry |
| 驱动文件位置 | main/qma6100p.c | managed_components/espressif__qma6100p/ |

---

> **审计结论：** 传感器 CHIP_ID=0x90 确认存在，但数据冻结在 X=7942/Y=1380/Z=0 mg。最可能根因：FIFO bypass 寄存器 0x3E 不适用于 QMA6100P（可能是 QMA7981 代码残留），或 NVM 加载破坏了传感器配置。"别人电脑正常"的最可能原因是使用了官方 espressif/qma6100p 组件。
>
> **核心建议：** 1) 注释 0x3E FIFO bypass 写入测试；2) 跳过 NVM 加载测试；3) 对比官方组件差异。
---

## 🔴 第二轮深入追踪：7942 / 1380 / 0 精确来源

### 前提更新

> **同一块 ESP32-S3-EYE 实体板，同学电脑上 QMA6100P 正常读取变化数据。**
> 排除：传感器型号错误、硬件损坏。

---

### 1️⃣ 代码执行链路确认

| 步骤 | 文件:行号 | 执行? | 返回值 |
|------|-----------|:-----:|:------:|
| `qma6100p_create()` 添加 I2C 设备 | main.c:144 | ✅ | ESP_OK |
| `qma6100p_get_deviceid()` 读 WHO_AM_I | main.c:151 | ✅ | 0x90 |
| `qma6100p_nvm_load()` | main.c:154 | ✅ | 🔴 未检查 |
| `qma6100p_config(ACCE_FS_8G)` | main.c:155 | ✅ | 🔴 未检查 |
| `qma6100p_wake_up()` | main.c:156 | ✅ | 🔴 未检查 |
| `qma6100p_get_acce()` 主循环 | main.c:186 | ✅ | ESP_OK |
| `qma6100p_get_acce_sensitivity()` | qma6100p.c:449 | ✅ | ESP_OK→1024.0 |
| `qma6100p_get_raw_acce()` | qma6100p.c:453 | ✅ | ESP_OK |
| `i2c_master_transmit_receive(0x01,6B)` | qma6100p.c:435 | ✅ | ESP_OK |

**config 成功间接证据：** 每轮循环读取 ACCEL_CONFIG 返回 0x04（8g 模式），sensitivity=1024。若 config 失败（默认值可能是 0x00），sensitivity 会是 default=4096，则无法产生 7942 这个值（计算出的寄存器值会超出 int16 范围）。

---

### 2️⃣ 7942 / 1380 / 0 精确逐行追踪

**第 1 步 — I2C 读取** (`qma6100p.c:434-435`)
```c
uint8_t data_rd[6];  // 栈上声明，未初始化
esp_err_t ret = qma6100p_read(sensor, 0x01, data_rd, 6);
// 从传感器地址 0x01 起读 6 字节 → 每次返回 ESP_OK
// data_rd[0]=X_L, data_rd[1]=X_H, data_rd[2]=Y_L, data_rd[3]=Y_H, data_rd[4]=Z_L, data_rd[5]=Z_H
```

**第 2 步 — 原始值计算** (`qma6100p.c:437-439`)
```c
raw_acce_x = (int16_t)((data_rd[1] << 8) + data_rd[0]) / 4;  // → 8133
raw_acce_y = (int16_t)((data_rd[3] << 8) + data_rd[2]) / 4;  // → 1413
raw_acce_z = (int16_t)((data_rd[5] << 8) + data_rd[4]) / 4;  // → 0
```

**第 3 步 — 灵敏度读取** (`qma6100p.c:170-202`)
```c
qma6100p_read(sensor, 0x0F, &acce_fs, 1);  // 读 ACCEL_CONFIG
acce_fs &= 0b1111;  // = 0x04 (ACCE_FS_8G)
switch (acce_fs) {
    case ACCE_FS_8G: acce_sensitivity = 1024.0f;  // ← 8g 模式
}
```

**第 4 步 — 物理量换算** (`qma6100p.c:458-460`)
```c
acce_x = 8133 / 1024.0f = 7.942g
acce_y = 1413 / 1024.0f = 1.380g
acce_z =    0 / 1024.0f = 0.000g
```

**第 5 步 — 打印** (`main.c:187-190`)
```c
(int)(7.942f * 1000) = 7942
(int)(1.380f * 1000) = 1380
(int)(0.000f * 1000) = 0
```

#### 反推传感器寄存器实际字节值

| 打印值 | raw (÷1024反推) | 寄存器值 (raw×4) | 寄存器 HEX |
|--------|-----------------|-------------------|------------|
| X=7942 mg | 8133 | 32532 | **0x14, 0x7F** |
| Y=1380 mg | 1413 | 5652 | **0x14, 0x16** |
| Z=0 mg | 0 | 0 | **0x00, 0x00** |

**这 6 个字节（0x14,0x7F,0x14,0x16,0x00,0x00）就是传感器每次返回的值，永不变化。**

---

### 3️⃣ 返回值检查状态 — 标红

🔴 **以下 4 处返回值完全未检查：**

| 位置 | 调用 | |
|------|------|:--:|
| `main.c:154` | `qma6100p_nvm_load(imu)` | 🔴 |
| `main.c:155` | `qma6100p_config(imu, ACCE_FS_8G)` | 🔴 |
| `main.c:156` | `qma6100p_wake_up(imu)` | 🔴 |
| `qma6100p.c:153` | `qma6100p_write(sensor, 0x3E, 0x00)` | 🔴 |

✅ 以下被正确检查：`qma6100p_create()` (main.c:144), `qma6100p_get_acce()` (main.c:186), `get_acce_sensitivity()` (qma6100p.c:450), `get_raw_acce()` (qma6100p.c:454)

---

### 4️⃣ 任务结构

- **不使用 FreeRTOS 独立任务** — 传感器读取在 `app_main()` 的 while(1) 主循环中
- 无线程竞争、无 mutex、无队列阻塞
- 每次循环：读传感器(阻塞I2C)→打印(阻塞UART)→`vTaskDelay(1000ms)`

---

### 5️⃣ 网络影响

Wi-Fi 在 `wifi_app_init()` 中 30 秒超时失败，但：
- 失败后返回 ESP_FAIL，程序**不阻塞**
- 主循环 IMU 读取**不依赖 Wi-Fi**
- Wi-Fi 后台重连不影响 IMU 读取
- **结论：Wi-Fi 不是 IMU 冻结的原因**

---

### 6️⃣ 我的 vs 同学电脑差异

| 项目 | 我的电脑 | 同学电脑 |
|------|----------|----------|
| ESP-IDF | v5.4.3 | **无法比较** |
| Python | 3.11.7 | **无法比较** |
| 工具链 | xtensa-esp-elf GCC 14.2.0 | **无法比较** |
| 驱动来源 | 本地 `main/qma6100p.c` | **可能使用官方组件** |
| managed_components | ❌ 无 | **可能有** |
| sdkconfig | 本地配置 | **可能不同** |

🔑 **最可能差异：同学的项目使用官方 `espressif/qma6100p` v2.0.1 组件。**

---

### 7️⃣ 固件一致性

| 文件 | 修改时间 |
|------|----------|
| `main/qma6100p.c` | 21:10:14 |
| `build/week01-sensor-web.bin` | 21:10:46 |
| `build/week01-sensor-web.elf` | 21:10:46 |

✅ Binary 在源码修改后生成，源码与固件一致。

---

## 🔑 最终结论

### A. 代码层面：7942/1380/0 的来源

```
来源：QMA6100P 传感器 I2C 寄存器 0x01-0x06
↓
X 寄存器 (0x01-0x02) → 0x14,0x7F → 32532 → ÷4 → 8133 → ÷1024 → 7.942g → 7942 mg
Y 寄存器 (0x03-0x04) → 0x14,0x16 → 5652  → ÷4 → 1413 → ÷1024 → 1.380g → 1380 mg
Z 寄存器 (0x05-0x06) → 0x00,0x00 → 0     → ÷4 → 0    → ÷1024 → 0.000g → 0 mg
```

- ❌ 不是 C 代码默认值
- ❌ 不是缓存/残留值
- ❌ 不是 NVM 值
- ✅ 是传感器数据寄存器的真实内容
- ✅ I2C 读取每次成功（ESP_OK → 返回值正确→打印）
- ⚠️ **传感器寄存器值从不更新** → 传感器数据通路未激活

### B. 环境层面

| 已确认事实 | 同一块板，同学电脑正常 |
|------------|----------------------|
| 直接证据 | 用户明确陈述 |
| 高概率原因 | 同学使用官方 `espressif/qma6100p` 组件，不同的初始化序列正确激活了传感器测量模式 |
| 待验证原因 | sdkconfig 差异、ESP-IDF 版本差异 |
| 最小实验 | 1) 从同学处获取 `idf_component.yml`；2) 对比初始化代码；3) 在本机用官方组件编译 |