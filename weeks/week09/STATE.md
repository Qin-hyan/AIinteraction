# Week 09 State — 用视觉事件触发一次主动询问

**分支**: `feature/week09-proactive-vision-event`（基于 `feature/week08-service-autostart`；未合并 `main`）
**日期**: 2026-10-22
**阶段**: 🟡 功能实现与自动化/技术冒烟完成；**人工体验验收未完成**

---

## 1. 官方要求（课程页面 Week 09）

- 前 2 学时：事件触发、反馈优先级与主动询问条件；将视觉结果接入原有任务状态；补上确认、忽略与冷却时间。
- 后 2 学时：演示语音、视觉、实体操作协同的中期版本；组织首轮完整任务测试，按模型/端侧/交互分类定位问题；含 1 学时集中排错。
- 当堂验证：误判能够纠正；用户拒绝后不反复打扰；说明模型、设备、服务实际运行位置。
- 交付物：中期可运行原型；完整任务测试原始记录；问题归因与修复清单。
- 备用：联调故障按接口替代定位；已有真实基线保留完整任务，缺项补验。

---

## 2. 实现

### 2.1 事件层 `service/vision_event.py`

- 明确证据才触发：
  - `unknown` / `low_score`；
  - 规则 `invalid` / `too_dark` / `too_bright` / `low_contrast` / `low_detail`；
  - `needs_review` 单独出现（阈值未配置、规则 ok）**不触发**，避免每张图都弹。
- 优先级：高（invalid / too_dark / too_bright / unknown）> 中（low_contrast / low_detail / low_score）。
- 状态：`pending / confirmed / ignored / snoozed / suppressed`。
- 动作：`confirm`（确认，无冷却）、`ignore`（同类来源+原因冷却 10 分钟）、`snooze`（5 分钟）。
- 同一 `record_id` 幂等；事件按 `event_id` 仅追加持久化，读取取最新快照。
- `task_ref` 保留 `record_id / request_id / device_index / source / captured_ms`，与既有分析记录关联。

### 2.2 服务 `service/vision_feedback_server.py`

新增：

- `GET /api/vision/events?limit=20`
- `GET /api/vision/events/active`
- `POST /api/vision/events/respond`

`POST /api/vision/analyze` 返回记录新增 `event` 字段；事件写失败不丢失已保存分析记录（返回 `event_error`）。`health` 增加事件配置/计数。

### 2.3 Dashboard `main/http_server.c`

- 视觉反馈面板新增「⏰ 主动询问」：消息、优先级/原因、关联记录/设备请求号、来源/运行位置、确认/忽略/稍后/重新采集、说明、历史表。
- 页面加载、分析完成、响应后刷新事件。
- 运行位置文案：设备 OV2640 取图或本地 JPEG → PC 本地 8090 DBCNN → 浏览器询问；不使用云端/VPS。
- 导航品牌更新为 Week 09。

---

## 3. 验证证据

### 3.1 自动化（Agent 执行）

| 命令 | 结果 |
|---|---|
| `python -m pytest service/test_vision_event.py -q` | **29 passed** |
| `python -m pytest service/test_vision_event_server.py -q` | **8 passed** |
| `python -m pytest service/test_vision_feedback.py service/test_vision_feedback_server.py service/test_vision_event.py service/test_vision_event_server.py -q` | **137 passed, 1 skipped** |
| `python -m pytest service/test_dashboard_week09_ui.py -q` | **7 passed** |
| `python -m pytest service/ --ignore=service/test_asr_whisper.py -q` | **553 passed, 2 skipped**；退出码 1 为已知 Anaconda torch DLL 问题 |
| `idf.py build` | PASS（`week02-sensor-collect.bin` 0x1179c0） |
| `idf.py -p COM10 app-flash` | Hash verified |

### 3.2 真实设备技术冒烟（Agent，未人工核验）

| 步骤 | 实际结果 |
|---|---|
| 当前环境抓拍一张（COM10 设备，`req-493280-0001`） | `img_status=low_detail`，20,298 B |
| 该照片送本地 DBCNN 推理 | `decision=needs_review`，`score=0.4555`，规则 `low_detail`，事件 `pending`（中） |
| 本地 `occluded.jpg` 送推理 | `score=0.2581`，规则 `too_dark`，事件 `pending`（高） |
| 确认低优先级事件 | HTTP 200，`state=confirmed`；active 自动落到高优先级 `too_dark` |
| 页面加载 | 事件面板显示「画面过暗，可能影响判断，是否重拍？」、高优先级、关联记录、运行位置；无 JS 控制台错误 |

**环境观察**：当前时间用户可能在睡觉，房间较暗/安静；真实抓拍被判 `low_detail`/`too_dark` 与环境光照一致。按用户要求**只记录，不强行修改检测阈值**。未做受控光照实验，不能把该结果当作模型准确率。

### 3.3 未完成 / 未人工核验

- **人工体验验收未完成**：用户暂时无法操作，不能声称“误判能够纠正”“拒绝后不反复打扰”已由用户确认。
- **首轮完整任务测试**：仅 Agent 技术冒烟；没有真实用户按任务卡执行、观察、记录成功/失败/用时/误解。
- **冷却时间**：仅用注入时间和单元测试验证；未真实等待 10 分钟。
- **语音 + 视觉 + 实体操作协同**：语音面板存在，但未做三者联动的真实用户任务；实体按键侧未在本次修改。
- **长期稳定性/并发**：未验证。

---

## 4. 问题归因与修复清单

| 类别 | 问题 | 处理 |
|---|---|---|
| 交互 | 阈值未配置时 `needs_review` 若无条件触发会每张图弹窗 | 只在规则/unknown/low_score 有明确证据时触发；有单测 |
| 交互 | 用户忽略后同类事件重复打扰 | 按来源+原因冷却 10 分钟；有单测/服务测试 |
| 交互 | 多事件同时存在时提示顺序不清 | 优先级排序 + active；确认后自动落到下一条；有单测 |
| 模型/环境 | 暗光导致 `too_dark`/`low_detail` | 记录为环境观察，不调阈值、不伪装通过 |
| 端侧 | 设备照片与本地图片来源不同 | `task_ref.source` 区分；面板显示实际运行位置 |
| 工程 | 事件写失败可能影响分析返回 | 分析记录先保存；事件失败返回 `event_error`，不丢分析 |
| 工程 | 事件状态仅追加导致读取歧义 | 按 `event_id` 取最后快照；有跨实例持久化测试 |

---

## 5. 文件清单

```
service/vision_event.py
service/test_vision_event.py
service/test_vision_event_server.py
service/vision_feedback.py
service/vision_feedback_server.py
service/test_dashboard_week09_ui.py
main/http_server.c
docs/vision-events.md
weeks/week09/STATE.md
README.md
PROJECT_STATE.md
AGENT_MEMORY.md
DECISIONS.md
TEST_LOG.md
```
