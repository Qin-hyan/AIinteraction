"""
trigger_collect.py — Week 04 trigger_collect 受限工具

职责：
  1. 从环境变量 ESP32_BASE_URL 读取 ESP32 基础地址（复用 query_last 配置）
  2. 向固定路径 /api/collect 发起 HTTP POST 请求
  3. 取得 request_id 后轮询 /api/collect/status?request_id=xxx
  4. 只有确认本次请求真实完成、结果与 request_id 对应且包含有效的新观测时，才报告采集成功

约束：
  - 基础地址仅从环境变量读取，禁止用户或模型提供任意 URL
  - POST/轮询路径硬编码，不可修改
  - 轮询有上限（MAX_POLL_ATTEMPTS），避免无限等待
  - 失败、超时、未知状态、请求不匹配、无效响应均明确返回错误，不得伪造成功
  - 纯 Python 标准库，无外部依赖
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .query_last import ENV_KEY, get_base_url


# ──────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────

FIXED_COLLECT_PATH = "/api/collect"
"""POST 采集请求的固定路径。"""

FIXED_STATUS_PATH = "/api/collect/status"
"""轮询采集状态的固定路径。"""

DEFAULT_TIMEOUT_SECONDS = 5
"""默认 HTTP 超时时间（秒）。"""

DEFAULT_POLL_INTERVAL_SECONDS = 0.5
"""轮询间隔（秒）。"""

MAX_POLL_ATTEMPTS = 20
"""最大轮询次数（默认 20 次 × 0.5s = 最多 10s 轮询）。"""


# ──────────────────────────────────────────────
# 结果数据类
# ──────────────────────────────────────────────


@dataclass
class CollectResult:
    """trigger_collect 工具的一次执行结果。

    Fields:
        success:      是否成功完成采集（完成 + 请求匹配 + 有效观测）
        request_id:   设备分配的请求 ID
        status:       最终状态字符串（completed / failed / timeout 等）
        status_code:  HTTP 状态码（0 表示未收到响应）
        error:        错误描述（success=True 时为 None）
        observation:  当次采集的有效观测字典（success=True 时有效）
                      含 valid / source / seq / accel_x/y/z / button
                      / observed_ms / received_ms / data_age_ms / time_quality 等字段
    """

    success: bool
    request_id: Optional[str] = None
    status: str = ""
    status_code: int = 0
    error: Optional[str] = None
    observation: Optional[Dict[str, Any]] = None
# ──────────────────────────────────────────────
# 内部轮询函数
# ──────────────────────────────────────────────


def _poll_status(
    base_url: str,
    request_id: str,
    timeout: int,
    poll_interval: float,
    max_attempts: int,
) -> CollectResult:
    """轮询 /api/collect/status 直到采集完成或达到上限。

    Args:
        base_url:      ESP32 基础地址。
        request_id:    需要轮询的请求 ID。
        timeout:       HTTP 超时秒数。
        poll_interval: 轮询间隔秒数。
        max_attempts:  最大轮询次数。

    Returns:
        CollectResult: 见 trigger_collect docstring。
    """
    status_url = f"{base_url}{FIXED_STATUS_PATH}?request_id={request_id}"

    for attempt in range(1, max_attempts + 1):
        time.sleep(poll_interval)

        try:
            req = urllib.request.Request(status_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            return CollectResult(
                success=False,
                request_id=request_id,
                status="",
                status_code=e.code,
                error=f"轮询 HTTP {e.code}: {e.reason}",
            )
        except urllib.error.URLError as e:
            return CollectResult(
                success=False,
                request_id=request_id,
                status="",
                status_code=0,
                error=f"轮询失败: {e.reason}",
            )
        except TimeoutError:
            return CollectResult(
                success=False,
                request_id=request_id,
                status="",
                status_code=0,
                error=f"轮询超时 (>{timeout}s)",
            )

        # 解析 JSON
        try:
            data: Dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return CollectResult(
                success=False,
                request_id=request_id,
                status="",
                status_code=0,
                error=f"轮询返回无效 JSON: {e}",
            )

        status = data.get("status", "")
        linked = data.get("linked", False)
        obs = data.get("observation")

        if status == "completed":
            if linked and isinstance(obs, dict) and obs.get("valid") is True:
                return CollectResult(
                    success=True,
                    request_id=request_id,
                    status=status,
                    observation=obs,
                )
            elif not linked:
                return CollectResult(
                    success=False,
                    request_id=request_id,
                    status=status,
                    error=(
                        f"任务完成但请求不匹配 (linked=false): "
                        f"观测不属于本次请求 {request_id}"
                    ),
                    observation=obs,
                )
            else:
                return CollectResult(
                    success=False,
                    request_id=request_id,
                    status=status,
                    error="任务完成但观测无效 (observation.valid != true)",
                    observation=obs,
                )
        elif status == "failed":
            return CollectResult(
                success=False,
                request_id=request_id,
                status=status,
                error="设备执行失败",
            )
        elif status == "timeout":
            return CollectResult(
                success=False,
                request_id=request_id,
                status=status,
                error="设备端采集超时",
            )
        elif status in ("submitted", "received", "idle"):
            continue
        else:
            return CollectResult(
                success=False,
                request_id=request_id,
                status=status,
                error=f"未知状态: {status}",
            )

    # 达到最大轮询次数仍未完成
    return CollectResult(
        success=False,
        request_id=request_id,
        status="timeout",
        error=f"轮询超时: 达到最大尝试次数 ({max_attempts})",
    )
# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────


def trigger_collect(
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_attempts: int = MAX_POLL_ATTEMPTS,
) -> CollectResult:
    """向 ESP32 发起一次采集请求并轮询结果。

    1. POST /api/collect → 获得 request_id
    2. 轮询 /api/collect/status?request_id=xxx → 直到完成/失败/超时

    Args:
        timeout:       HTTP 超时秒数（默认 5）。
        poll_interval: 轮询间隔秒数（默认 0.5）。
        max_attempts:  最大轮询次数（默认 20）。

    Returns:
        CollectResult:
          - success=True:  采集成功（完成 + 请求匹配 + 有效观测）
          - success=False: 配置缺失、HTTP 错误、超时、请求不匹配或无效观测
    """
    base = get_base_url()
    if base is None:
        return CollectResult(
            success=False,
            status_code=0,
            error=f"环境变量 {ENV_KEY} 未设置，无法发起请求",
        )

    collect_url = f"{base}{FIXED_COLLECT_PATH}"

    # 第一步：POST /api/collect
    try:
        req = urllib.request.Request(collect_url, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
    except urllib.error.HTTPError as e:
        return CollectResult(
            success=False,
            status_code=e.code,
            error=f"HTTP {e.code}: {e.reason}",
        )
    except urllib.error.URLError as e:
        return CollectResult(
            success=False,
            status_code=0,
            error=f"请求失败: {e.reason}",
        )
    except TimeoutError:
        return CollectResult(
            success=False,
            status_code=0,
            error=f"请求超时 (>{timeout}s)",
        )

    # 解析 POST 响应
    try:
        data: Dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return CollectResult(
            success=False,
            status_code=resp.status,
            error=f"无效 JSON 响应: {e}",
        )

    request_id = data.get("request_id")
    if not request_id:
        return CollectResult(
            success=False,
            status_code=resp.status,
            error="POST 响应缺少 request_id",
        )

    # 第二步：轮询采集状态
    return _poll_status(base, request_id, timeout, poll_interval, max_attempts)