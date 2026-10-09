# Week 04 State — 自然语言查询与请求采集

**状态**: 🔧 分支已建立，尚未开始功能实现
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

## 前置条件

- Week 03 代码已合并至 `main`（PR #3，`ec9c94b`）
- 真实 ESP32→VPS HTTP POST 端到端闭环仍待验收（属于 Week 03 收尾项，非 Week 04 阻塞项）