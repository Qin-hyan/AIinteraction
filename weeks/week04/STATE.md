# Week 04 State — 自然语言查询与请求采集

**状态**: ✅ Step 1-6 全部完成（102 PASS）
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

---

### Week 04 Step 6 集成验证 — 本地结构化样例链路验证 (`service/test_task_pipeline_integration.py`)

**新增文件**: `service/test_task_pipeline_integration.py`

**测试策略**:
- 使用结构化 JSON 样例作为模型输出的替代输入
- 调用**真实处理链路**：`parse_and_validate → dispatch_task → 真实工具代码 → format_result`
- 只 mock HTTP 网络边界 (`urllib.request.urlopen`) 和 `time.sleep`
- 不 mock 分发器和业务工具（validator/dispatcher/formatter/工具函数体全部走真实代码）
- 不发起真实 HTTP 请求

**10 个集成测试覆盖**:

| # | 测试项 | 结果 | 核验点 |
|---|--------|------|--------|
| 1 | query_last 有效观测 | ✅ PASS | 来源/序号/相对时间保留，不伪装日历时间 |
| 2 | query_last 无记录 | ✅ PASS | 正确提示无数据 |
| 3 | trigger_collect 采集成功 | ✅ PASS | 输出采集详情，相对时间保留 |
| 4 | trigger_collect 设备失败 | ✅ PASS | 输出失败信息，不反馈成功 |
| 5 | trigger_collect 设备超时 | ✅ PASS | 输出超时信息，不反馈成功 |
| 6 | clarify 澄清 | ✅ PASS | HTTP 未被调用 |
| 7 | unsupported 拒绝 | ✅ PASS | HTTP 未被调用 |
| 8 | 非法 JSON | ✅ PASS | 校验失败，HTTP 未被调用 |
| 9 | 未知意图 | ✅ PASS | 校验失败，HTTP 未被调用 |
| 10 | 额外字段拒绝 | ✅ PASS | 校验失败，HTTP 未被调用 |

**总计**: **102 PASS / 0 FAIL**（原有 92 + 集成验证新增 10）

**已验证事项**:
- `parse_and_validate` → `dispatch_task` → `query_last`/`trigger_collect` 真实工具代码 → `format_result` 完整真实链路
- 以上链路中仅 HTTP 网络调用被 mock（工具函数体、validator、dispatcher、formatter 全部执行真实逻辑）
- clarify、unsupported、非法输入路径均不触发 HTTP 请求
- 设备端失败/超时场景不反馈成功

**仍待补验**:
- ⚠️ 真实语言模型输出 → 管道输入接口对接
- ⚠️ 真实 ESP32 设备 HTTP 调用（query_last 和 trigger_collect 的真实网络请求）

---

### Web 仪表盘 localStorage 持久化

**提交**: `8ca4677` — feat(web): persist valid observations locally

**变更摘要**（仅 `main/http_server.c`，+5/-3 行）:
- `persistObs(d)` — 新增函数：仅在 `valid===true` 且 `source==='live'|'cadence'` 时写入 `localStorage`（键 `__dashObs_v1__`）
- `restoreLatest()` — 新增函数：页面加载时从 `localStorage` 恢复最新有效观测
- `refreshStored()` — 获取成功时调用 `persistObs(d)`
- `renderNewObs()` — 采集完成获取新观测时调用 `persistObs(o)`
- `init()` — 启动时先 `restoreLatest()` 再 `refreshStored()`

**去重策略**: `record_id` 作去重键（退化 `'s'+seq`），同键覆盖更新；最多保留 50 条，超出后裁剪最旧条目。

**容错策略**: 全部 `try/catch` 包裹；`localStorage` 不可用/超出容量/数据损坏均静默跳过，不阻塞页面正常功能。

### CSV 导出功能

**提交**: `5f1fb4a` — feat(web): export persisted observations to CSV

**变更摘要**（仅 `main/http_server.c`，+2 行）:
- 新增 `exportCSV()` JS 函数：从 `localStorage` 读取有效记录，按 `received_ms` 降序排列
- 新增 "📥 导出 CSV" 按钮（`act-btn ghost` 样式）
- 导出列：`record_id, request_id, source, seq, accel_x, accel_y, accel_z, button, observed_ms, received_ms, data_age_ms, time_quality`（12 列）
- UTF-8 + BOM 编码，兼容 Excel 中文
- 文件名格式：`esp32_obs_YYYY-MM-DD_hh-mm-ss.csv`
- 字段缺失留空，不伪造数值
- 无有效数据/存储不可用/导出失败时清晰提示，不假报成功

**不涉及**: 传感器采集逻辑、现有快照/陈旧标记语义、localStorage 持久化逻辑、新增依赖、其他导出格式。

### 浏览器持久化人工检查（4 项）

**验证方式**: 人工在浏览器开发者工具中操作，观察 localStorage 内容及 CSV 输出
**版本**: HEAD `5f1fb4a`

| # | 测试项 | 结果 | 观察记录 |
|---|--------|------|---------|
| 1 | 页面加载后 localStorage 自动恢复最新有效观测 | ✅ PASS | 刷新后最新观测在 `restoreLatest()` 后正确显示 |
| 2 | 多次采集后 localStorage 保留多条记录，不超过 50 条 | ✅ PASS | 记录正确持久化，超出 50 条后最旧条目被裁剪 |
| 3 | CSV 导出文件包含全部有效字段，12 列对齐 | ✅ PASS | 导出的 CSV 含表头与 12 列数据，字段值正确 |
| 4 | localStorage 不可用/数据损坏时页面不崩溃 | ✅ PASS | 删除 `__dashObs_v1__` 后页面正常运行，无 JS 报错 |

### CSV 核验结果（用户提供）

| 项目 | 值 |
|------|-----|
| 记录总数 | 29 条 |
| 列数 | 12 列 |
| 来源类型 | 全部为 `cadence` |
| 结构完整性 | ✅ 正常，无缺失列或格式异常 |
| 时间字段 | 相对运行时间（ms），`time_quality: "relative"`，无日历时间伪装 |
| 核验结论 | 导出格式规范，数据字段完整，结构无异常 |

---

## 仍待补验（Week 04）

| 项目 | 说明 |
|------|------|
| ⚠️ **真实语言模型输出 → 管道输入接口对接** | 当前使用结构化 JSON 样例替代 LLM 输出，尚未接入真实语言模型 |
| ⚠️ **真实 ESP32 设备 HTTP 调用** | query_last 和 trigger_collect 工具仅通过 mock 测试，未在真实设备上验证 |
| ⚠️ **浏览器持久化与 CSV 功能的实机端到端测试** | localStorage 和 CSV 导出在浏览器开发工具中验证通过，但尚未在真实 ESP32 设备+浏览器组合下完整跑通 |

## 前置条件

- Week 03 代码已合并至 `main`（PR #3，`ec9c94b`）
- 运行 `query_last()` 前需设置环境变量 `ESP32_BASE_URL`（如 `http://192.168.1.100`）
- 真实 ESP32→VPS HTTP POST 端到端闭环仍待验收（属于 Week 03 收尾项，非 Week 04 阻塞项）