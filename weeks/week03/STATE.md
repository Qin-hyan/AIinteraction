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

### 仍待完成
- 快速点击无异常重复测试
- LED 物理闪烁观察
- 远端确认/取消 API 测试
- HTTP 远端确认后 LED 常亮
- `[W3-FEEDBACK]` 日志考虑添加（可选改进，非阻塞）
