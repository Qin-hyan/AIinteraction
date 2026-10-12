# Week 03 State — 实体按键与物理反馈闭环

**状态**: ✅ 已完成并合并至 `main`（PR #3）
**合并**: `ec9c94b` — Merge pull request #3 from Qin-hyan/feature/week03-physical-feedback（2026-10-09）
**分支**: `feature/week03-physical-feedback`（已合并的**历史分支**，HEAD `cc34961`，为 `ec9c94b` 的祖先；分支保留未删除）
**后续开发**: 已切换至 `feature/week05-voice-interaction`（基线 `7dd788b`）

> 本次仅更新分支与合并状态；以下技术细节、构建/实机验证记录与未验证事项均保持原样。

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

> 📌 **以下为「模块刚添加时」的阶段性状态存档**：当时 `help_uplink_send_event()` 尚未被任何模块调用，该描述**已不代表当前状态**。后续提交 `29dd72d` 已完成异步接口接入 `main.c`，详见下文「后续接入 — help_uplink 集成至 `main.c`」。

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
- **（阶段性状态，已解决）尚未接入 `main.c`**：`help_uplink_send_event()` 未被任何模块调用，模块刚添加时仅作为独立模块存在；后续由 `29dd72d` 完成接入（见下文）
- **未验证真实网络**：`CONFIG_HELP_VPS_URL` 需在 menuconfig 中配置真实 VPS 地址并进行实机 HTTP POST 测试
- **无超时/重试策略**：当前仅一次 HTTP POST 尝试，超时由 `CONFIG_HELP_UPLINK_TIMEOUT_MS` 控制

---

## 后续接入 — help_uplink 集成至 `main.c`（提交 `29dd72d`）

**提交**: `29dd72d` feat(week03): integrate help_uplink async task into event state machine
（`main/help_uplink.c` +110 行、`main/help_uplink.h` +42 行、`main/main.c` +49 行；该提交位于 PR #3 合并 `ec9c94b` 的历史中，并随 PR #4 进入 `main` `7dd788b`）

### 变更内容
| 文件 | 变更 |
|------|------|
| `main/help_uplink.h/.c` | 新增异步接口 `help_uplink_start()`、`help_uplink_request_send()`、`help_uplink_poll()`；由后台 FreeRTOS 任务执行 `help_uplink_send_event()`，避免阻塞 1 Hz 主循环 |
| `main/main.c` | 初始化阶段调用 `help_uplink_start()`；事件状态机接入 `help_uplink_is_configured()` / `help_uplink_request_send()` / `help_uplink_poll()` |

### 当前集成状态（源码证据，当前 `main` 分支代码）
| 位置 | 调用 |
|------|------|
| `main/main.c:34` | `#include "help_uplink.h"` |
| `main/main.c:364` | `help_uplink_start();`（启动异步 VPS 上行推送任务） |
| `main/main.c:595` | `help_uplink_is_configured()`（未配置则 `help_event_fail()`） |
| `main/main.c:601` | `help_uplink_request_send(&help_event)`（请求未被接受则 `help_event_fail()`） |
| `main/main.c:612` | `help_uplink_poll(&uplink_res)`（`ESP_OK` → `help_event_mark_sent()`，否则 `help_event_fail()`） |

- ✅ **软件集成已完成**：以上调用存在于 `main` 分支代码中，并经 PR #3（`ec9c94b`）/ PR #4（`7dd788b`）合并。
- ⛔ **仍未验证**：真实 ESP32 → VPS 端到端实机验收未完成——需在 menuconfig 配置真实 `CONFIG_HELP_VPS_URL` 后实测 HTTP POST 成功/失败/超时路径。**不得据此声称端到端链路已通过。**
- ⚠️ `TEST_LOG.md` 中目前没有 `29dd72d` 的构建验证条目（本次为文档事实核对，未运行固件构建，故不新增构建结论）。

---

### 仍待完成
- [x] MENU 短按触发 + 单 event_id ← 已完成
- [x] MENU 长按不重复触发 ← 已完成
- [x] Brownout 稳定性 ← 两轮连续 PASS
- [x] MENU 连续快速按 3 次 — 无异常 ← 第4轮验证通过
- [x] **`help_uplink` 接入 `main.c`** ← 已完成（`29dd72d`：异步接口接入事件状态机，见上文「后续接入」）
- [ ] **实机网络验证（仍未完成）** — 配置真实 VPS URL，验证 HTTP POST 成功/失败/超时路径
- [ ] LED 物理闪烁观察（实机）
- [ ] 远端确认/取消 API 测试（需 VPS 配合）
- [ ] HTTP 远端确认后 LED 常亮
