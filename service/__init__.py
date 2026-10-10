# service — 结构化任务契约与校验模块 (Week 04)
# 纯 Python 标准库实现，无外部依赖。
# 不接入语言模型，不调用设备接口。

from .task_schema import (
    IntentEnum,
    TaskContract,
    INTENT_ALLOWED_FIELDS,
    TASK_SCHEMA,
)
from .validator import (
    ValidationResult,
    ParsedTask,
    parse_and_validate,
)

from .trigger_collect import (
    FIXED_COLLECT_PATH,
    FIXED_STATUS_PATH,
    MAX_POLL_ATTEMPTS,
    CollectResult,
    trigger_collect,
    _poll_status,
)
from .query_last import (
    ENV_KEY,
    FIXED_PATH,
    QueryLastResult,
    get_base_url,
    query_last,
)
from .task_dispatcher import (
    DispatchResult,
    dispatch_task,
)

__all__ = [
    "IntentEnum",
    "TaskContract",
    "INTENT_ALLOWED_FIELDS",
    "TASK_SCHEMA",
    "ValidationResult",
    "ParsedTask",
    "parse_and_validate",
    "ENV_KEY",
    "FIXED_PATH",
    "FIXED_COLLECT_PATH",
    "FIXED_STATUS_PATH",
    "MAX_POLL_ATTEMPTS",
    "QueryLastResult",
    "CollectResult",
    "get_base_url",
    "query_last",
    "trigger_collect",
    "_poll_status",
    "DispatchResult",
    "dispatch_task",
]