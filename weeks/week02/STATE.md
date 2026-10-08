# Week 02 State — 远程采集指令与执行反馈

**状态**: ✅ 完成 (tag: `week02-stable`)  
**日期**: 2026-10-06

## 交付物

- 远程采集指令：POST /api/collect → request_id 追踪
- 状态链：SUBMITTED → RECEIVED → COMPLETED/FAILED/TIMEOUT (5s)
- 已存观测 vs 新观测对比（`GET /api/observation/last` vs POST 采集）
- 周期上报开关（`POST /api/auto_refresh`）
- 请求—回执—观测记录（环形缓冲 6 条）
- 摄像头 JPEG 抓拍（PSRAM 存储，15 次连续验证通过）
- OV2640 驱动模块 `camera_app.c/h`

## 实机验证

12 PASS / 0 FAIL（详见 TEST_LOG.md）

## 已知限制

- 环形缓冲仅 6 条
- `collect_task_t` 无互斥锁
- 超时窗口固定 5s
- 摄像头与传感器采集无互斥同步