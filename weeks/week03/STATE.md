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

### 仍待完成
- [x] MENU 短按触发 + 单 event_id ← 本轮完成
- [x] MENU 长按不重复触发 ← 本轮完成
- [x] Brownout 稳定性 ← 两轮连续 PASS
- [ ] MENU 连续快速按 3 次 ← 需要单独安排
- [ ] LED 物理闪烁观察
- [ ] 远端确认/取消 API 测试
- [ ] HTTP 远端确认后 LED 常亮
- [ ] `[W3-FEEDBACK]` 日志考虑添加（可选改进，非阻塞）
