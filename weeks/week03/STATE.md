# Week 03 State — 实体按键与物理反馈闭环

**状态**: 🔧 开发中  
**分支**: `feature/week03-physical-feedback`

## 目标

实体按键触发 → 去抖 → 生成唯一 event_id → 本地 LED 反馈 →
发送教学测试消息 → Web 接收 → 远端证据返回 → 更新物理反馈 → 支持取消/回应

## 已完成

- `help_event.h` — 完整接口定义
- `help_event.c` — 事件生命周期、按键去抖 FSM、LED 反馈
- `main.c` 主循环集成：按键去抖 + 事件触发 + 网络检测 + 超时保护 + LED 更新
- `http_server.h` sensor_ctx_t 扩展 help 字段
- `http_server.c` — 三个 HTTP API 端点（`GET /api/help/status`、`POST /api/help/confirm`、`POST /api/help/cancel`）
- `main/CMakeLists.txt` 注册 help_event.c

## ✅ 已解决

- **编译通过** ✅ `idf.py build` 成功（2026/10/8）
- 修复内容：
  - `main.c` — `help_event` / `btn_fsm` 声明移到 `sensor_ctx_t ctx` 之前
  - `help_event.c` — `help_status_name()` 函数体修复
  - `http_server.c` — `HTTPD_500` 枚举、注释语法、添加 help URI 路由注册

## 🧪 实机验证结果 (2026-10-09)

**环境**: ESP32-S3-EYE v2.2 | COM10 | ESP-IDF v5.4.3

### 基础流程
| 步骤 | 结果 | 关键日志 |
|------|------|----------|
| 启动初始化 | ✅ | IMU cadence 循环正常 |
| 按键检测（PLAY） | ✅ | `I (16763) W3-EVENT: btn pressed: PLAY` |
| 事件创建 | ✅ | `I (16763) W3-EVENT: created id=help-15919-0001 btn=PLAY counter=1 (LOCAL CONFIRMED)` |
| [W3-BTN] 日志 | ✅ | `I (16763) MAIN: [W3-BTN] pressed PLAY → event help-15919-0001` |
| HTTP 发送 | ✅ | `I (16772) W3-EVENT: sent id=help-15919-0001` |
| Web API 可用 | ✅ | `I (16776) MAIN: [W3-NET] event help-15919-0001 available via API` |
| LED 反馈日志 [W3-FEEDBACK] | ❌ 不存在 | `help_event.c` 中 LED 走 `gpio_set_level()` 无独立日志打印 |

### 第2轮验证：MENU 长按 ≥5s
| 测试项 | 结果 | 关键日志 |
|--------|------|----------|
| MENU 长按检测 | ✅ **PASS** | `[W3-BTN] pressed MENU → event help-48904-0002`，仅1次 |
| 长按不重复触发 | ✅ **PASS** | 全程仅1条 `[W3-BTN]`，去抖 FSM 正常 |
| 事件创建 + ID | ✅ PASS | `id=help-48904-0002 btn=MENU counter=2 (LOCAL CONFIRMED)` |
| HTTP 发送 | ✅ PASS | `W3-EVENT: sent id=help-48904-0002` |
| Web API 可用 | ✅ PASS | `[W3-NET] event help-48904-0002 available via API` |
| 30s 超时自动重置 | ✅ PASS | `help-16842-0001 stale (>30s), auto-reset` |
| Brownout 稳定性 | ✅ **PASS** | 本轮**无 Brownout**，设备稳定运行25s+ |
| 设备持续稳定运行 | ✅ PASS | IMU cadence #46~#66 连续正常输出 |

### 异常
| 问题 | 严重性 | 详情 |
|------|--------|------|
| **Brownout 触发重启** | 🚨 严重 | 第1轮测试中事件发送约 1s 后 `E BOD: Brownout detector was triggered`，设备 `rst:0x3 (RTC_SW_SYS_RST)`。第2轮未复现，暂标记为间歇性。 |

### 已完成项 (本次)
- [x] MENU 长按不重复触发验证
- [x] 30s 超时自动重置验证
- [x] Brownout 重测 (未复现)

### 仍待完成
- 快速点击无异常重复测试
- LED 物理闪烁观察
- 远端确认/取消 API 测试
- HTTP 远端确认后 LED 常亮
- `[W3-FEEDBACK]` 日志考虑添加（可选改进，非阻塞）
