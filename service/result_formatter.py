"""
result_formatter.py — Week 04 工具结果格式化模块

职责：
  将 DispatchResult 转换为用户可读的反馈文本。
  区分五种场景：查询结果、采集成功、失败/超时、需要澄清、请求不支持。

约束：
  - 设备时间为相对运行时间（ms since boot），不得伪装为日历时间
  - 采集成功仅当本次请求关联成功且包含有效的新观测
  - 保留真实数据来源（source）、时间、状态
  - 纯 Python 标准库，零外部依赖
  - 不接入语言模型，不修改 ESP32 业务代码
"""

from __future__ import annotations

from typing import Any, Dict

from .task_dispatcher import DispatchResult


def _fmt_ms(value, label="设备运行"):
    """Format millisecond value as device-relative time."""
    if value is None:
        return f"{label}: 未知"
    try:
        ms = int(value)
        return f"{label} {ms} ms"
    except (ValueError, TypeError):
        return f"{label}: {value}"


def _fmt_accel(obs):
    """Extract and format acceleration values from observation."""
    x, y, z = obs.get("accel_x"), obs.get("accel_y"), obs.get("accel_z")
    if x is None and y is None and z is None:
        return "加速度: 无数据"
    parts = []
    for val, lb in [(x, "X"), (y, "Y"), (z, "Z")]:
        if val is not None:
            try:
                parts.append(f"{lb}={float(val):.1f}mg")
            except (ValueError, TypeError):
                parts.append(f"{lb}={val}")
        else:
            parts.append(f"{lb}=N/A")
    return f"加速度: {', '.join(parts)}"


def _fmt_source(obs):
    """Extract data source from observation."""
    return f"数据来源: {obs.get('source', 'unknown')}"


def _fmt_seq(obs):
    """Extract observation sequence number."""
    seq = obs.get("seq")
    if seq is not None:
        return f"观测序号: #{seq}"
    return "观测序号: 未知"


def _fmt_button(obs):
    """Extract button state from observation."""
    btn = obs.get("button", "none")
    if btn and btn != "none":
        return f"按键状态: {btn}"
    return ""


def _fmt_data_age(obs):
    """Extract data age from observation."""
    age = obs.get("data_age_ms")
    if age is not None:
        return _fmt_ms(age, label="数据年龄")
    return ""


def _fmt_observed_time(obs):
    """Format observation/received time as device-relative time."""
    parts = []
    ov = obs.get("observed_ms")
    if ov is not None:
        parts.append(_fmt_ms(ov, label="观测时间"))
    rv = obs.get("received_ms")
    if rv is not None:
        parts.append(_fmt_ms(rv, label="接收时间"))
    if obs.get("time_quality") == "relative":
        parts.append("(设备相对运行时间)")
    return "\n  ".join(parts) if parts else ""


def format_result(result):
    """将 DispatchResult 转换为用户可读的文本反馈。"""
    if not result.success and result.intent == "":
        return f"请求校验失败: {result.error or '未知错误'}"

    intent = result.intent
    if intent == "query_last":
        return _fmt_query_last(result)
    if intent == "trigger_collect":
        return _fmt_trigger_collect(result)
    if intent == "clarify":
        q = result.question or "请提供更多信息"
        return f"需要澄清: {q}"
    if intent == "unsupported":
        r = result.reason or "当前不支持该操作"
        return f"暂不支持: {r}"
    return f"未知错误: 意图 {intent!r} 无法处理"


def _fmt_query_last(result):
    """Format query_last result."""
    if not result.success:
        return f"查询失败: {result.error or '未知错误'}"

    obs = result.observation
    if obs is None:
        return "查询成功，但设备未返回有效数据"

    valid = obs.get("valid", False)
    source = obs.get("source", "unknown")
    if not valid or source == "none":
        return f"当前无有效观测记录 | 来源: {source} | 序号: #{obs.get('seq', '?')}"

    lines = ["最近的观测数据:"]
    lines.append(f"  {_fmt_source(obs)}")
    lines.append(f"  {_fmt_seq(obs)}")

    acc = _fmt_accel(obs)
    if acc:
        lines.append(f"  {acc}")

    btn = _fmt_button(obs)
    if btn:
        lines.append(f"  {btn}")

    tm = _fmt_observed_time(obs)
    if tm:
        lines.append(f"  {tm}")

    age = _fmt_data_age(obs)
    if age:
        lines.append(f"  {age}")

    tq = obs.get("time_quality")
    if tq and tq != "relative":
        lines.append(f"  时间类型: {tq}")

    return "\n".join(lines)


def _fmt_trigger_collect(result):
    """Format trigger_collect result. Only report success with valid new observation."""
    rid = result.request_id or "未知"
    st = result.collect_status or "未知"

    if not result.success:
        return (f"采集失败\n"
                f"  请求ID: {rid}\n"
                f"  状态: {st}\n"
                f"  原因: {result.error or '未知错误'}")

    obs = result.collect_observation
    if obs is None:
        return (f"采集请求已受理，无有效观测数据\n"
                f"  请求ID: {rid}\n"
                f"  状态: {st}")

    obs_valid = obs.get("valid", False)
    src = obs.get("source", "unknown")
    if not obs_valid or src == "none":
        return (f"采集已完成但观测无效\n"
                f"  请求ID: {rid}\n"
                f"  状态: {st}\n"
                f"  原因: valid={obs_valid}, source={src}")

    lines = ["采集成功!"]
    lines.append(f"  请求ID: {rid}")
    lines.append(f"  {_fmt_source(obs)}")
    lines.append(f"  {_fmt_seq(obs)}")

    acc = _fmt_accel(obs)
    if acc:
        lines.append(f"  {acc}")

    btn = _fmt_button(obs)
    if btn:
        lines.append(f"  {btn}")

    tm = _fmt_observed_time(obs)
    if tm:
        lines.append(f"  {tm}")

    age = _fmt_data_age(obs)
    if age:
        lines.append(f"  {age}")

    return "\n".join(lines)
