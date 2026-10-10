# Week 04 State — 自然语言查询与请求采集

**状态**: ✅ Step 1-6 全部完成（92 PASS）
**分支**: `feature/week04-natural-language`
**基线**: `ec9c94b` — Merge pull request #3 (origin/main)
**上游**: 无（已清除 `origin/main` 的跟踪）

## 目标（来自课程计划）

用自然语言查询与请求采集。复用 Week 01-02 的查询和远程采集接口。

## 当前状态

- `feature/week04-natural-language` 已从 `origin/main` (`ec9c94b`) 创建
- 该分支不跟踪任何远程分支（上游已清除）
- 待 Week 04 功能完成并准备合并时，再配置上游跟踪

## 已完成

### Week 04 Step 1 — 结构化任务契约与校验模块 (`service/`)

**文件结构**:
```
service/
├── __init__.py         # 包导出
├── task_schema.py      # IntentEnum, TaskContract, JSON Schema
├── validator.py        # 严格校验函数 (parse_and_validate)
└── test_validator.py   # 单元测试 (24 tests)
```

**设计要点**:
- 纯 Python 标准库实现，零外部依赖
- 四种允许意图：`query_last`, `trigger_collect`, `clarify`, `unsupported`
- `query_last` / `trigger_collect` 不得携带任何设备地址或接口参数（白名单严格约束）
- `clarify` 必须带非空 `question` 字段
- `unsupported` 必须带非空 `reason` 字段
- 拒绝非法 JSON、未知意图、额外字段、类型错误、越界值
- 校验分三步：JSON 解析 → JSON Schema 结构性校验 → 意图级契约校验
- 测试覆盖 24 个用例，全部 PASS

**不接入语言模型，不调用设备接口。**

### Week 04 Step 2 — query_last 受限工具 (`service/query_last.py`)

**新增文件**:
```
service/
├── query_last.py          # query_last 受限工具
└── test_query_last.py     # 单元测试 (13 tests)
```

**设计要点**:
- 纯 Python 标准库（`urllib.request`），零外部依赖
- ESP32 基础地址仅从环境变量 `ESP32_BASE_URL` 读取
- 请求路径固定为 `/api/observation/last`，不可被用户或模型修改
- 原样保留设备返回的 `valid` / `source` / `seq` / `accel_x/y/z` / `button` / `observed_ms` / `received_ms` / `data_age_ms` / `time_quality` 等字段
- 无有效记录、HTTP 错误、超时及无效 JSON 均明确报告，不伪造数据
- 13 个单元测试覆盖：成功有数据、成功无记录、HTTP 404/500、连接失败、超时、无效 JSON、环境变量未设置/空/空白/末尾斜杠
- 测试均 mock HTTP，不发起真实设备请求

### Week 04 Step 3 — trigger_collect 受限工具 (`service/trigger_collect.py`)

**新增文件**:
```
service/
├── trigger_collect.py          # trigger_collect 受限工具
└── test_trigger_collect.py     # 单元测试 (12 tests)
```

**设计要点**:
- 纯 Python 标准库（`urllib.request`），零外部依赖
- ESP32 基础地址仅从环境变量 `ESP32_BASE_URL` 读取（复用 `query_last.get_base_url`）
- 固定路径：`POST /api/collect` → 获取 `request_id` → 轮询 `GET /api/collect/status?request_id=xxx`
- 轮询上限 20 次 × 0.5s 间隔（最多 10s），可通过参数调整
- 成功条件严格：仅当状态 `completed` + `linked=true` + `observation.valid=true` 时报告成功
- 设备失败、超时、轮询超时、请求不匹配、观测无效、缺失观测、HTTP 错误、无效 JSON 均明确返回错误
- 12 个单元测试覆盖：成功流程、设备失败、设备超时、轮询超时、请求不匹配、观测无效、
  缺失观测、POST HTTP 错误、轮询 HTTP 错误、无效 JSON 响应、配置缺失、POST 缺少 request_id
- 测试均 mock HTTP，不发起真实设备请求

### Week 04 Step 4 — 受限任务分发模块 (`service/task_dispatcher.py`)

**新增文件**:
```
service/
├── task_dispatcher.py          # 受限任务分发入口
└── test_task_dispatcher.py     # 单元测试 (16 tests)
```

**设计要点**:
- 单一入口 `dispatch_task(raw_input: str) → DispatchResult`
- 调用 `parse_and_validate()` 完成三步校验（JSON 解析 → Schema → 意图契约）
- 四种意图分发：
  - `query_last`: 真实调用 `query_last()` 工具，返回 `observation`
  - `trigger_collect`: 真实调用 `trigger_collect()` 工具，返回 `request_id`/`collect_status`/`collect_observation`
  - `clarify`: 直接返回 `question`，**不调用任何设备工具**
  - `unsupported`: 直接返回 `reason`，**不调用任何设备工具**
- 非法 JSON、校验失败、未知意图 → `success=False`，**不执行任何工具**
- 工具内部异常 → 捕获并返回错误，不崩溃
- 纯 Python 标准库，零外部依赖

### Week 04 Step 5 — 工具结果格式化模块 (`service/result_formatter.py`)

**新增文件**:
```
service/
├── result_formatter.py          # 结果格式化入口
└── test_result_formatter.py     # 单元测试 (20 tests)
```

**设计要点**:
- 单一入口 `format_result(result: DispatchResult) → str`
- 五种场景格式化：
  - **查询结果**：保留 source、seq、加速度、按键状态、相对时间（"设备运行 XXXX ms"），不伪装日历时间
  - **采集成功**：仅当 `collect_observation.valid==True` 且 `source!="none"` 时报告成功
  - **失败/超时**：包含请求 ID、状态、错误原因
  - **需要澄清**：返回 question 字段
  - **请求不支持**：返回 reason 字段
- 校验失败返回错误列表
- 字段缺失容错（加速度部分缺失、时间字段缺失等均不会崩溃）
- 纯 Python 标准库，零外部依赖

**20 个单元测试覆盖**:
| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 成功有效观测，含相对时间验证 | ✅ PASS | query_last |
| 2 | query_last 无有效记录 (valid=false) | ✅ PASS | query_last |
| 3 | query_last observation=None | ✅ PASS | query_last |
| 4 | query_last 工具失败 (HTTP 404) | ✅ PASS | query_last |
| 5 | trigger_collect 成功有效新观测 | ✅ PASS | trigger_collect |
| 6 | trigger_collect 工具失败 | ✅ PASS | trigger_collect |
| 7 | trigger_collect observation=None | ✅ PASS | trigger_collect |
| 8 | trigger_collect 观测无效 (valid=false) | ✅ PASS | trigger_collect |
| 9 | trigger_collect source=none | ✅ PASS | trigger_collect |
| 10 | clarify 带澄清问题 | ✅ PASS | clarify |
| 11 | clarify 无 question (fallback) | ✅ PASS | clarify |
| 12 | unsupported 带原因 | ✅ PASS | unsupported |
| 13 | unsupported 无 reason (fallback) | ✅ PASS | unsupported |
| 14 | 校验失败 | ✅ PASS | 失败 |
| 15 | 校验失败 error=None | ✅ PASS | 失败 |
| 16 | 未知意图（防御性） | ✅ PASS | 防御 |
| 17 | query_last 缺少加速度字段 | ✅ PASS | 容错 |
| 18 | query_last 部分加速度字段 | ✅ PASS | 容错 |
| 19 | trigger_collect 缺少时间字段 | ✅ PASS | 容错 |
| 20 | query_last 包含按键状态 | ✅ PASS | 容错 |

**总计**: 92 PASS / 0 FAIL（原有 85 + Step 6 新增 7）

---

### Week 04 Step 6 — 本地结构化任务处理入口 (`service/task_pipeline.py`)

**新增文件**:
```
service/
├── task_pipeline.py             # 统一入口：校验→分发→格式化
└── test_task_pipeline.py        # 集成测试 (7 tests)
```

**设计要点**:
- 单一入口 `run_task(raw_input: str) → str`
- 串联 `dispatch_task()`（校验+分发）与 `format_result()`（格式化）两个已有模块
- 不重复实现 validator/dispatcher/formatter 的逻辑
- 纯 Python 标准库，零外部依赖
- 不接入语言模型，不修改 ESP32 业务代码

**7 个集成测试覆盖**:

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 成功获取有效观测 | ✅ PASS | 查询 |
| 2 | query_last 无有效记录 | ✅ PASS | 查询 |
| 3 | trigger_collect 采集成功 | ✅ PASS | 采集 |
| 4 | clarify 返回澄清问题 | ✅ PASS | 澄清 |
| 5 | unsupported 返回拒绝原因 | ✅ PASS | 拒绝 |
| 6 | 非法 JSON | ✅ PASS | 非法输入 |
| 7 | 未知意图 | ✅ PASS | 非法输入 |

**总计**: 92 PASS / 0 FAIL（原有 85 + Step 6 新增 7）

## 前置条件

- Week 03 代码已合并至 `main`（PR #3，`ec9c94b`）
- 运行 `query_last()` 前需设置环境变量 `ESP32_BASE_URL`（如 `http://192.168.1.100`）
- 真实 ESP32→VPS HTTP POST 端到端闭环仍待验收（属于 Week 03 收尾项，非 Week 04 阻塞项）