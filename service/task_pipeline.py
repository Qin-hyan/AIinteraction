"""
task_pipeline.py — Week 04 本地结构化任务处理入口

职责：
  提供单一函数 run_task()，将结构化任务 JSON 处理的完整流程串联起来：
    1. 调用 dispatch_task() 完成校验与分发
    2. 调用 format_result() 生成用户可读反馈
  不接入语言模型，不发起真实 HTTP 请求（由下游工具决定）。

约束：
  - 纯 Python 标准库，零外部依赖
  - 不重复实现校验、分发或格式化逻辑
  - 不在本模块内发起任何 HTTP 请求
"""

from __future__ import annotations

from .task_dispatcher import dispatch_task
from .result_formatter import format_result


def run_task(raw_input: str) -> str:
    """统一入口：处理结构化任务 JSON，返回用户可读反馈。

    流程：
      1. 调用 dispatch_task(raw_input) 完成校验与任务分发
      2. 调用 format_result(result) 生成用户可读文本

    Args:
        raw_input: 用户原始输入字符串（预期为结构化 JSON）。

    Returns:
        用户可读的反馈文本字符串。
        校验失败、工具失败、澄清、不支持等场景均已包含描述。
    """
    result = dispatch_task(raw_input)
    return format_result(result)