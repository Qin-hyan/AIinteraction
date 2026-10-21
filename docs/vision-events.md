# 视觉事件与主动询问（Week 09）

**日期**：2026-10-22
**入口**：`service/vision_event.py`
**服务端点**：`service/vision_feedback_server.py`（`127.0.0.1:8090`）
**Dashboard**：`main/http_server.c` 视觉反馈面板内的「⏰ 主动询问」

---

## 1. 课程目标

把视觉模型/规则结果变成一次**可解释、可纠正、可拒绝**的主动询问：

- 事件触发条件与反馈优先级；
- 确认、忽略、稍后；
- 用户拒绝后进入冷却，不反复打扰；
- 输入（设备照片/本地图片）、结果、事件、响应通过 `record_id` / `task_ref` 关联；
- 允许用户重新采集或纠正。

---

## 2. 触发条件（明确证据才触发）

`derive_trigger(record)` 只读取已有 `analysis`，不编造分数或类别：

| 条件 | 原因码 | 优先级 |
|---|---|---|
| `decision=unknown` | `visual_result_unknown` | 高 |
| `decision=low_score` | `score_below_threshold` | 中 |
| 规则 `invalid` | `invalid` | 高 |
| 规则 `too_dark` / `too_bright` | 同规则名 | 高 |
| 规则 `low_contrast` / `low_detail` | 同规则名 | 中 |

**不触发**：仅有 `needs_review` 且规则为 `ok` 或规则不可用。原因是阈值未校准，`needs_review` 本身不能证明画面有问题；否则每张图都会弹提示。

> 当前环境偏暗时，真实设备照片很可能被规则判为 `too_dark` / `low_detail`，从而触发事件。这是环境导致的检测结果，记录为观察项，不强行修改阈值。

---

## 3. 状态与冷却

```
pending ──confirm──► confirmed（无冷却，同 record 不再重复）
   │
   ├──ignore──► ignored（同类「来源 + 原因」冷却 10 分钟）
   └──snooze──► snoozed（冷却 5 分钟）

冷却期内同类新记录 ──► suppressed（记录但不主动提示）
```

- 冷却键：`source:reason_code`，例如 `local_upload:too_dark`、`device_gallery:low_detail`。
- 冷却参数：`ignore=600s`、`snooze=300s`（可在 `VisionEventService` 构造参数中调整）。
- 同一 `record_id` 只生成一个事件；重复分析幂等返回原事件。
- 多事件同时存在时，`active` 按优先级（高→中→低）与创建时间选择；确认高优先级后自动落到下一条。
- 事件存储：`.workbuddy/vision-feedback/events.jsonl`（gitignored，仅追加，读取时按 `event_id` 取最新快照）。

---

## 4. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/vision/events?limit=20` | 事件列表、当前 active、计数、冷却配置 |
| GET | `/api/vision/events/active` | 当前最高优先级待响应事件 |
| POST | `/api/vision/events/respond` | `{"event_id","action":"confirm|ignore|snooze","note?}` |

错误码：

- 400 `invalid_event_response`：动作/字段/note 非法；
- 404 `event_not_found`；
- 409 `event_state_conflict`：已确认或冷却中事件再次响应；
- 500 `event_storage_error`。

`POST /api/vision/analyze` 的返回记录新增 `event` 字段（可能为 `null`）；事件写入失败时返回 `event_error`，不丢失已保存的分析记录。

---

## 5. Dashboard 行为

- 视觉反馈面板内显示「⏰ 主动询问」：
  - 当前事件的消息、优先级/原因、关联 `record_id` / 设备 `request_id`、来源与运行位置；
  - 确认 / 忽略（10 分钟）/ 稍后（5 分钟）/ 重新采集；
  - 可选说明；事件历史表显示状态与最近响应。
- 页面加载、每次分析完成、每次响应后刷新事件。
- 运行位置明确显示：**设备 OV2640 取图（或本地 JPEG 上传）→ PC 本地 8090 DBCNN → 浏览器主动询问**；不使用云端/VPS。

---

## 6. 验证与边界

**自动测试**：

- `service/test_vision_event.py`：触发条件、优先级、去重、确认/忽略/稍后、冷却、跨进程持久化、错误路径；
- `service/test_vision_event_server.py`：分析生成事件、事件列表/active、响应接口、健康信息、limit 校验；
- `service/test_dashboard_week09_ui.py`：面板字段、动作、运行位置文案、JS `node --check`。

**真实设备（Agent 冒烟，未人工核验）**：

- 设备照片 `low_detail` → `pending`（中）；
- 本地 `occluded.jpg` → `too_dark` → `pending`（高）；
- 通过 API 确认低优先级事件后，active 正确落到高优先级 `too_dark`。

**不能证明**：

- 未人工核验用户体验、误判纠正的实际可用性、拒绝后长期不打扰；
- 冷却时间仅按代码/测试验证，未做 10 分钟真实等待；
- 暗光/安静环境对检测的影响未做受控实验；事件触发不等于模型准确率。
