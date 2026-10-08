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

## 待完成

- 完整闭环链路测试（按键 → 去抖 → event_id → LED 反馈 → HTTP 发送 → 远端确认/取消 → 物理反馈更新）
- 实机验证（按键去抖、LED 反馈、HTTP 远端确认/取消）