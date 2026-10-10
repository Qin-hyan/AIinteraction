# Project State — 18 周当前进度

> 每次 Agent 窗口开始时应首先读取此文件了解当前状态。

---

## 当前：Week 04 — 自然语言查询与请求采集（分支已建立）

**分支**: `feature/week04-natural-language`
**阶段**: Step 1 任务契约校验 ✅ + Step 2 query_last 受限工具 ✅ + Step 3 trigger_collect 受限工具 ✅ + Step 4 受限任务分发 ✅ + Step 5 工具结果格式化 ✅ + Step 6 任务管道 ✅（总计 102 test PASS）+ **Web 仪表盘本地持久化 ✅ (`8ca4677`)** + **CSV 导出 ✅**

---

## 全局进度

| 单元 | 周 | 主题 | 状态 |
|------|-----|------|------|
| 单元1 | Week 01 | 传感数据采集与 Web 展示 | ✅ 完成 |
| | Week 02 | 远程采集指令与执行反馈 | ✅ 完成 (tag: week02-stable) |
| | **Week 03** | **实体按键与物理反馈闭环** | ✅ 已合并至 main |
| 单元2 | Week 04-06 | 自然语言与语音任务交互 | 🔧 分支已建立 |
| 单元3 | Week 07-09 | 视觉感知与事件反馈 | ⏳ 未开始 |
| 单元4 | Week 10-12 | 多源上下文与状态推断 | ⏳ 未开始 |
| 单元5 | Week 13-15 | 边云协作与韧性通信 | ⏳ 未开始 |
| 单元6 | Week 16-18 | 体验检验、改进与结课交付 | ⏳ 未开始 |

---

## Week 03 当前任务

**目标**: 实体按键触发 → 去抖 → 生成唯一 event_id → 本地 LED 反馈 → 发送教学测试消息 → Web 接收 → 远端证据返回 → 更新物理反馈 → 支持取消/回应

**已完成**:
- `help_event.h` — 完整的接口定义（状态枚举、数据结构、按键 FSM、LED 模式、事件生命周期）
- `help_event.c` — 事件生命周期实现、按键去抖 FSM、LED 反馈驱动
- `main.c` — 主循环中集成按键去抖 + 事件触发 + 网络检测 + 超时保护 + LED 更新
- `http_server.h` — `sensor_ctx_t` 中增加 `help_event_t *help` 字段
- `http_server.c` — 三个 HTTP API 端点：`GET /api/help/status`、`POST /api/help/confirm`、`POST /api/help/cancel`
- `main/CMakeLists.txt` — 添加 `help_event.c`
- 👉 **Week 03 按键扫描解耦**：独立 FreeRTOS 任务 (30 Hz) + FreeRTOS Queue 通信，主循环不再负责按键采样
- 👉 **help_uplink 模块**：独立 HTTP POST 模块 (`help_uplink.c/.h`)，将 `help_event_t` 构造为 JSON 推送到外部 VPS 端点（`ae89be8`）
- 👉 **JSON 时间戳语义修正**：`sent_at_us` 标注为设备端 HTTP POST 完成时刻，0 值序列化为 `null`（`5fe738a`）

**待完成**:
- **`help_uplink` 接入 `main.c`**：目前模块已存在但未被调用，需要将 `help_uplink_send_event()` 接入事件发送路径
- **完整闭环链路测试**（按键 → 去抖 → event_id → LED 反馈 → HTTP 发送 → 远端确认/取消 → 物理反馈更新）
- **实机网络验证**（需配置真实 VPS URL，验证 HTTP POST 成功/失败路径，检查远端接收状态及响应）

### ⚠️ 实机测试记录 (2026-10-09)

| 测试项 | 结果 | 说明 |
|--------|------|------|
| idf.py build | ✅ PASS | week02-stable-15-g65b001f |
| 串口检测 COM10 | ✅ PASS | ESP32-S3-EYE (USB VID:PID=303A:1001) |
| idf.py flash | ✅ PASS | 烧录成功 |
| 设备启动 | ✅ PASS | IMU 数据正常输出（cadence 循环） |
| 按键单次按下检测 | ✅ PASS | 日志: `[W3-BTN] pressed MENU → event help-141745-0001` |
| 事件创建 | ✅ PASS | `W3-EVENT: created id=help-141745-0001 btn=MENU counter=1 (LOCAL CONFIRMED)` |
| HTTP 发送 | ✅ PASS | `W3-EVENT: sent id=help-141745-0001` |
| 网络 API 可用 | ✅ PASS | `[W3-NET] event help-141745-0001 available via API` |
| `[W3-FEEDBACK]` 日志 | ❌ 不存在 | LED 反馈走 `gpio_set_level()` 未打印单独日志，代码中无 `W3-FEEDBACK` 标签 |
| Brownout 稳定性 | ✅ **PASS** | 118s+ 连续运行，无 BOD 日志 |
| MENU 短按 ~294ms | ✅ **PASS** | btn pressed → released (294ms) → created, 仅1个 event_id |
| MENU 长按 ~6s 不重复 | ✅ **PASS** | held 5978ms，因前事件活跃正确抑制 |
| 30s 超时自动重置 | ✅ PASS | help-141745-0001 stale (>30s), auto-reset |
| MENU 连续快速按 3 次 | ⚠️ 未捕获 | 日志仅含短按+长按两组，无第3组 |
| LED 物理反馈观察 | ⛔ 未完成 | 未安排

---

## ⚠️ 已知问题

### Brownout 检测触发 — 间歇性 (2026-10-09)
- **现象**: 按键触发 → 事件创建 → HTTP 发送后，约 1 秒后 `E BOD: Brownout detector was triggered` → 设备重启
- **复现步骤**: 单次按下 PLAY 按键，事件流程正常完成后设备崩
- **可能原因**: 
  - 供电不足：WiFi HTTP POST + 摄像头 + IMU 同时运行导致瞬时电流超限
  - USB 线缆/供电能力不足
  - `CONFIG_BROWNOUT_DET_LVL` 设置过保守（当前未知）
- **建议排查**: 检查电源、USB 线缆；或在 menuconfig 中调整 Brownout 阈值
- **第2轮测试备注 (2026-10-09)**: MENU长按≥5s 操作下未复现 Brownout，设备稳定运行 25s+。
- **第3轮测试备注 (2026-10-09)**: 三次实机验证（短按294ms+长按6s）**均未复现 Brownout**，设备稳定运行 118s+。Brownout 概率较低。

### ✅ 快速连按验证通过
- **MENU 连续快速按 4 次**（含 2 次快速点击间隔 ~343ms release-to-press）→ 第4轮实机验证 (**8f8c511**) 已通过
- 结果：仅生成 **1 个 event_id**，非 IDLE 状态时后续 3 次按下**正确抑制**
- 参见 TEST_LOG.md "快速连续按键验证" 详细记录

### ✅ 已修复的编译问题
- **`main.c:370`** — `help_event` 先使用后声明：将 `help_event_t help_event` / `help_button_fsm_t btn_fsm` 声明移到 `sensor_ctx_t ctx` 初始化之前
- **`help_event.c`** — `help_status_name()` 函数体损坏修复：将孤立 case 标签移回 switch 内，删除末尾孤儿代码
- **`http_server.c`** — `HTTPD_500` → `HTTPD_500_INTERNAL_SERVER_ERROR`；注释块缺失 `/*` 修复；双重 `/*` 修复；添加 help URI 路由定义与注册（消除 unused-function warning）

### 文件修改状态
```
modified:   main/main.c             (变量声明顺序修复)
modified:   main/help_event.c       (函数体结构修复)
modified:   main/http_server.c      (编译错误修复 + URI 路由注册)
```
### ✅ 按键扫描解耦 (2026-10-09)
- **方案**: 独立 FreeRTOS 任务 `button_scan_task` (~30 Hz) 运行去抖 FSM，通过 `xQueueSend` 通知主循环
- **接口变更**: `help_event.h` 移除 `help_button_fsm_t` 类型/`help_button_fsm_init`/`help_button_fsm_update`，新增 `help_button_service_start`/`help_button_get_pending`
- **`main.c`**: 移除 `btn_fsm` 变量和主循环 ADC 读/FSM 更新，改为 `help_button_get_pending()` 非阻塞收队列
- **`help_event.c`**: FSM 改为 `static` 内部类型，新增 `button_scan_task`、Queue、两个公开 API
- **`idf.py build`**: ✅ PASS