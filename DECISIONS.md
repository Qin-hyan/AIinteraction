# Technical Decisions — 已确认决策记录

> 只记录已确认的技术决策。不猜测、不编造。

---

## D001 — 构建系统：ESP-IDF 原生 CMake（非 PlatformIO）

- **日期**: Week 01
- **决定**: 使用 ESP-IDF v5.4.3 原生 CMake 构建系统，不使用 PlatformIO
- **原因**: ESP32-S3-EYE 官方示例基于 ESP-IDF；项目无 `platformio.ini`
- **影响**: 使用 `idf.py build/flash/monitor`；需要先 `export.bat` 激活环境

---

## D002 — 时间基准：esp_timer 相对时间

- **日期**: Week 02
- **决定**: 所有时间戳使用 `esp_timer_get_time()`（µs since boot），标记 `time_quality: "relative"`
- **原因**: 板端未启用 SNTP，不伪造墙钟时间
- **影响**: 前端显示"相对时间"（ms since boot），不做绝对时间转换

---

## D003 — request_id 格式

- **日期**: Week 02
- **决定**: 手动采集请求 `req-{uptime_ms}-{counter:04d}`，观测 `obs-{seq:05d}`
- **影响**: 前端通过 request_id 关联请求与观测；counter 逐次递增，可区分重复点击

---

## D004 — 采集任务超时窗口 5s

- **日期**: Week 02
- **决定**: `COLLECT_TIMEOUT_MS = 5000`，硬编码在 `http_server.h`
- **原因**: 课程演示需要可预期的等待时间；未做可配置化
- **影响**: 超时后不接受迟到结果；旧观测不改标为本次完成

---

## D005 — JPEG 缓冲区迁移到 PSRAM

- **日期**: Week 02
- **决定**: 摄像头抓拍的 JPEG buffer 使用 `MALLOC_CAP_SPIRAM` 分配到 PSRAM
- **原因**: 大分辨率下内部 DRAM 不足
- **影响**: 15 次连续抓拍验证通过

---

## D006 — 教学测试消息事件命名

- **日期**: Week 03
- **决定**: 事件 ID 格式 `help-{ms}-{counter:04d}`；状态枚举 `HELP_IDLE → HELP_TRIGGERED → ...`
- **原因**: 区别于采集任务（`req-`）和观测（`obs-`）
- **影响**: 前端按 help_id 追踪教学测试消息生命周期

---

## D007 — LED 反馈引脚

- **日期**: Week 03
- **决定**: 使用 GPIO38（RGB LED）和 GPIO3（模组电源 LED）作为物理反馈输出
- **原因**: ESP32-S3-EYE 板载 LED
- **影响**: `help_led_update()` 同时驱动两个 LED