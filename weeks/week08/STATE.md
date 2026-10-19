# Week 08 State — 把视觉推理结果变成可理解反馈

**分支**: `feature/week08-vision-feedback`（基线 `origin/main` `333a697`，PR #8 已合并 Week 07）
**日期**: 2026-10-18（官方要求核实日）；W8-1 实施 2026-10-19
**阶段**: 🟡 **W8-1 完成（契约 + 单元测试）**；W8-2 等待真实模型资源

---

## 1. 课程官方要求（2026-10-18 核实自课程主页）

**本周问题**：模型返回一个标签和分数，系统应该如何表达"看到了什么"和"还有什么不能确定"？

**单元归属**：单元 3「视觉感知与事件反馈」（第 7–9 周），对应课程目标 1、3、4、5、6。

### 前 2 学时（理论 1、实践 1）
- 讲解预测标签、分数、拒识及模型版本；
- 本轮以**画面可用性/遮挡识别**为共同保底任务，场景粗分类作为拓展；
- 接入真实模型推理。

### 后 2 学时（理论 0、实践 2）
- 实现结构化结果解析、**低分/未知反馈**、**重新采集**和**人工纠正**；
- **保留输入、结果和反馈三者对应**，比较高分误判与正常样例。

### 当堂验证
- 未经校准的模型分数不写成现实正确率；
- 场景类别不转换为精确位置或人身安全判断。

### 交付物
- 视觉识别—反馈闭环；
- 正常、低分、错误和未知样例对照；
- 迁移：为个人项目的一项模型判断补上纠错入口。

### 范围控制（备用路径）
- 学生模型未就绪时先调用教师真实基线；**静态标签只能测试界面，不计入真实推理证据**。

### 对应测试用例
- T03（视觉结果与用户纠错）、T04（低置信度/异常结构输出）

---

## 2. 与 Week 07 既有能力的衔接分析

| Week 08 要求 | Week 07 已有基础 | 缺口 |
|---|---|---|
| 画面可用性/遮挡识别（保底） | ✅ 双端 `image_quality`（四态+score+metrics） | 无 |
| 结构化结果解析 | ✅ `img_status`/metrics/score 已结构化 | 需补**拒识/未知**语义 |
| 分数与模型版本 | ⚠️ 有 score（0~1 相对值） | 需补**算法版本标识**（如 `iq_v1`） |
| 低分/未知反馈 | ⚠️ score 存在但未驱动反馈 | 需新增低分/不确定反馈路径 |
| 重新采集 | ✅ `POST /api/camera/capture` | 需与反馈闭环关联 |
| 人工纠正 | ❌ 无 | 需新增纠正接口与记录 |
| 输入-结果-反馈三者对应 | ⚠️ 输入(request_id)+结果(img_status)已关联 | 需补**反馈记录**关联 |
| 正常/低分/错误/未知样例对照 | ⚠️ 已有 too_dark/low_detail 实机样例 | 需系统整理（部分待覃总实机配合） |

## 3. 待办

- [x] 待 Week 07 分支合并后创建本周分支（`origin/main` `333a697`）
- [x] W8-1 结构化视觉结果契约与拒识语义
- [ ] W8-2 接入真实模型推理（等待模型资源，见第 4 节）
- [ ] W8-3 低分/未知反馈、重新采集与人工纠正闭环

---

## 4. W8-1 结构化视觉结果契约与拒识语义（2026-10-19 完成）

**目标**：把「标签 + 分数」变成可追踪、可审计的结构化结果，明确 `ok / low_score / unknown` 三态与拒识原因。

**实现**：
- `service/vision_result.py`：`build_vision_result(request_id, raw_output, score_threshold, model_version="iq_v1", created_at=None)`
  - 契约字段：`request_id / label / score / model_version / decision / reject_reason / created_at`
  - 附带解释性元数据：`time_quality="pc_utc"`、`score_kind="relative_usability"`、`score_threshold`
  - `decision`：`ok`（结构合法、标签受支持、分数达门槛；**不表示画面可用**）/ `low_score` / `unknown`
  - 拒识原因：`no_result`、`parse_error`、`missing_label`、`unsupported_label`、`missing_score`、`invalid_score`、`image_invalid`、`score_below_threshold`
  - `label` 词汇表复用 `image_quality.IMAGE_STATUSES`（新增具名常量），**未新造标签**
  - **阈值不内置**：`score_threshold` 无默认值，调用方必须显式提供且位于 `(0, 1]`；W8-1 未校准，不编造阈值
  - `score` 为原始 0~1 相对可用性，合法时原值保留；**不得称准确率/置信度**
  - 模型侧异常（`None`/坏 JSON/缺字段/非法分数/未知标签/无效图像）→ `unknown` + 原因，不抛异常
  - 调用方错误（`request_id`、阈值、`model_version`）→ `ValueError`，不伪装成模型结果
  - 时间：`created_at` 为 PC 端 UTC ISO-8601（毫秒 + `Z`），不与设备相对时间混用（D002）
- `service/test_vision_result.py`：**52 项**测试（含参数化用例）

**验证证据**：
- 命令：`python -m pytest service/test_vision_result.py service/test_image_quality.py -q`
- 结果：**62 passed in 0.44s**（新增 52 + `image_quality` 回归 10）；日志 `.workbuddy/logs/w8-1-tests.log`（本地忽略，未提交）
- 验证层级：**单元测试**；未编译/未烧录/未接入真实模型

**范围外（未做，不做宣称）**：真实模型推理、模型适配器、HTTP API、持久化、反馈文案/界面、人工纠正、重采闭环。

**W8-2 尚缺资源（阻塞点）**：
1. 教师基线/学生候选模型的**真实推理入口与接口约定**（当前仅有 Week 07 规则型 `image_quality`，不是训练模型）
2. **真实失败样本/验证样本**及标签、分数语义说明（课程要求「模型课提供真实失败样本与分数语义」）
3. 校准后的**低分阈值**（W8-1 刻意不设默认值；无校准时不得擅自给出 `low_score` 门槛）
4. 模型**版本号**正式命名（当前 `iq_v1` 仅标识 Week 07 规则算法）
