# Project State — 18 周当前进度

> 每次 Agent 窗口开始时应首先读取此文件了解当前状态。

---

## 当前：Week 03 — 实体按键与物理反馈闭环

**分支**: `feature/week03-physical-feedback`
**阶段**: 实机测试中 ⚠️（MENU长按不重复触发确认通过，Brownout未复现）
**Git HEAD**: `a451227` merge: repo-cleanup

---

## 全局进度

| 单元 | 周 | 主题 | 状态 |
|------|-----|------|------|
| 单元1 | Week 01 | 传感数据采集与 Web 展示 | ✅ 完成 |
| | Week 02 | 远程采集指令与执行反馈 | ✅ 完成 (tag: week02-stable) |
| | **Week 03** | **实体按键与物理反馈闭环** | 🔧 **开发中** |
| 单元2 | Week 04-06 | 自然语言与语音任务交互 | ⏳ 未开始 |
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

**待完成**:
- 完整闭环链路测试（按键 → 去抖 → event_id → LED 反馈 → HTTP 发送 → 远端确认/取消 → 物理反馈更新）
- 实机验证（按键去抖、LED 反馈、HTTP 远端确认/取消）

### ⚠️ 实机测试记录 (2026-10-09)

| 测试项 | 结果 | 说明 |
|--------|------|------|
| idf.py build | ✅ PASS | week02-stable-11-g63ab504 |
| 串口检测 COM10 | ✅ PASS | ESP32-S3-EYE (USB VID:PID=303A:1001) |
| idf.py flash | ✅ PASS | 烧录成功 |
| 设备启动 | ✅ PASS | IMU 数据正常输出（cadence 循环） |
| 按键单次按下检测 | ✅ PASS | 日志: `[W3-BTN] pressed PLAY → event help-15919-0001` |
| 事件创建 | ✅ PASS | `W3-EVENT: created id=help-15919-0001 btn=PLAY counter=1 (LOCAL CONFIRMED)` |
| HTTP 发送 | ✅ PASS | `W3-EVENT: sent id=help-15919-0001` |
| 网络 API 可用 | ✅ PASS | `[W3-NET] event help-15919-0001 available via API` |
| `[W3-FEEDBACK]` 日志 | ❌ 不存在 | LED 反馈走 `gpio_set_level()` 未打印单独日志，代码中无 `W3-FEEDBACK` 标签 |
| Brownout 检测触发 | ❌ **FAIL** | `E BOD: Brownout detector was triggered` → 设备重启 |
| 长按不重复触发 | ⛔ 未完成 | Brownout 后设备重启，无法继续测试 |
| 快速点击无异常重复 | ⛔ 未完成 | 同上 |
| LED 物理反馈观察 | ⛔ 未完成 | 同上 |
| | MENU长按 ≥5s 不重复触发 | ✅ **PASS** | [W3-BTN] 仅1次，event help-48904-0002 |
| | Brownout 重测 | ✅ **PASS** | 本轮**无 Brownout**，设备稳定运行25s+ |
| | 30s 超时自动重置 | ✅ PASS | help-16842-0001 stale (>30s), auto-reset |

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
- **第2轮测试备注 (2026-10-09)**: MENU长按≥5s 操作下未复现 Brownout，设备稳定运行 25s+。暂标记为间歇性问题。

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