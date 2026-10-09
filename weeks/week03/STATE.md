# Week 03 State — 实体按键与物理反馈闭环

**状态**: 🔧 开发中  
**分支**: `feature/week03-physical-feedback`

## 本次修改 — 按键扫描解耦 (2026-10-09)

将按键扫描从 1Hz 主循环解耦为独立 FreeRTOS 任务 (30 Hz)，
使用 FreeRTOS Queue 传递按键按下事件，主循环不再负责 ADC 采样和 FSM 更新。

### 文件变更
| 文件 | 变更 | 说明 |
|------|------|------|
| `main/help_event.h` | 修改 | 移除 `help_button_fsm_t`、`help_button_fsm_init()`、`help_button_fsm_update()`；新增 `help_button_service_start()`、`help_button_get_pending()` |
| `main/help_event.c` | 修改 | FSM 结构/函数改为 `static` 内部；新增 FreeRTOS Queue + 30Hz `button_scan_task`；实现两个新公开 API |
| `main/main.c` | 修改 | 移除 `btn_fsm` 变量和主循环中 `adc_button_read()` / `help_button_fsm_update()`；改为 `help_button_get_pending()` 非阻塞接收；启动时调用 `help_button_service_start()` |

### 架构
```
button_scan_task (30 Hz)         main loop (1 Hz)
  ├─ adc_button_read()              ├─ help_button_get_pending()
  ├─ help_button_fsm_update()       ├─ help_event_trigger()
  ├─ xQueueSend() ──────────►       ├─ event FSM...
  └─ vTaskDelay(33ms)               └─ help_led_update()
```

### 构建验证
- `idf.py build` ✅ PASS (week02-stable-14-gaafc520-dirty)

### 实机验证（2026-10-09 第3轮 — `65b001f`）

**硬件**: COM10 | **固件**: week02-stable-15-g65b001f | **捕获**: Python 串口 120s

| 测试项 | 结果 | 分析 |
|--------|------|------|
| MENU 短按 ~294ms | ✅ PASS | btn pressed→released 294ms, created help-141745-0001, 仅1个 event_id |
| MENU 长按 ~6s | ✅ PASS | held 5978ms, 因前事件活跃正确抑制, 无新事件 |
| 30s 超时自动重置 | ✅ PASS | help-141745-0001 stale after 30.5s → reset to IDLE |
| Brownout | ✅ PASS | 118s+ 连续运行，无 BOD |
| [W3-BTN]/[W3-EVENT] 日志 | ✅ PASS | 完整记录 press→release→create→send→sent 各阶段 |
| MENU 连续快速按 3 次 | ⚠️ 未捕获 | 日志仅含短按+长按，无第3组快速连按 |

**结论**: 独立 30Hz 按键扫描任务功能正常，去抖/防重/超时均通过验证。

---

## 新增模块 — help_uplink (2026-10-09)

**提交**: `ae89be8` feat(week03): add help_uplink module for VPS HTTP POST  
`5fe738a` fix(help_uplink): correct protocol semantics for JSON timestamps and config

### 新增文件
| 文件 | 说明 |
|------|------|
| `main/help_uplink.c` | 独立 HTTP POST 模块，使用 `esp_http_client` 将 `help_event_t` 序列化为 JSON 推送到外部 VPS |
| `main/help_uplink.h` | 公开 API：`help_uplink_is_configured()`、`help_uplink_send_event()` |

### 被修改文件
| 文件 | 变更 |
|------|------|
| `main/CMakeLists.txt` | 添加 `help_uplink.c` 到源文件列表 |
| `main/Kconfig.projbuild` | 新增 `CONFIG_HELP_VPS_URL`（string）和 `CONFIG_HELP_UPLINK_TIMEOUT_MS`（int，默认 10000）配置项 |
| `main/help_event.h` | `sent_at_us` 注释澄清为"设备端 HTTP 发送完成时间（非 VPS 上传时间）" |

### API 说明
- `help_uplink_is_configured()` — 检查 `CONFIG_HELP_VPS_URL` 是否非空
- `help_uplink_send_event(const help_event_t *event)` — 构造 JSON（含 help_id、状态、按键、四个时间戳、远端确认/备注）→ HTTP POST → 检查 2xx 响应；不修改 event 本身

### 时间戳语义（`5fe738a` 修正）
| 字段 | 语义 |
|------|------|
| `triggered_at_us` | 按键触发时刻（μs since boot） |
| `sent_at_us` | **设备端** HTTP POST 完成时刻 |
| `remote_received_at_us` | VPS 确认接收时刻（由远端 API 设置） |
| `cancelled_at_us` | 取消操作时刻 |
| 值为 0 | 序列化为 JSON `null`（表示"尚未发生"） |

### 构建验证
- `idf.py build` ✅ PASS (HEAD `5fe738a`)

### ⚠️ 已知限制
- **尚未接入 `main.c`**：`help_uplink_send_event()` 未被任何模块调用，当前仅作为独立模块存在
- **未验证真实网络**：`CONFIG_HELP_VPS_URL` 需在 menuconfig 中配置真实 VPS 地址并进行实机 HTTP POST 测试
- **无超时/重试策略**：当前仅一次 HTTP POST 尝试，超时由 `CONFIG_HELP_UPLINK_TIMEOUT_MS` 控制

---

### 仍待完成
- [x] MENU 短按触发 + 单 event_id ← 已完成
- [x] MENU 长按不重复触发 ← 已完成
- [x] Brownout 稳定性 ← 两轮连续 PASS
- [x] MENU 连续快速按 3 次 — 无异常 ← 第4轮验证通过
- [ ] **`help_uplink` 接入 `main.c`** — 将 `help_uplink_send_event()` 调用集成到事件发送路径
- [ ] **实机网络验证** — 配置真实 VPS URL，验证 HTTP POST 成功/失败/超时路径
- [ ] LED 物理闪烁观察（实机）
- [ ] 远端确认/取消 API 测试（需 VPS 配合）
- [ ] HTTP 远端确认后 LED 常亮
