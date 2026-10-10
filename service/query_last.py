"""
query_last.py — Week 04 query_last 受限工具

职责：
  1. 从环境变量 ESP32_BASE_URL 读取 ESP32 基础地址
  2. 向固定路径 /api/observation/last 发起 HTTP GET 请求
  3. 解析 JSON 响应，返回标准化的 QueryLastResult

约束：
  - 基础地址仅从环境变量读取，禁止用户或模型提供任意 URL
  - 请求路径硬编码，不可修改
  - 原样保留设备返回的数据来源、时间和状态（valid / source / seq 等）
  - 无有效记录、HTTP 错误、超时及无效 JSON 均须明确报告，不得伪造数据
  - 纯 Python 标准库，无外部依赖
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


# ──────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────

ENV_KEY = "ESP32_BASE_URL"
"""环境变量名，用于读取 ESP32 基础地址（如 http://192.168.1.100）。"""

FIXED_PATH = "/api/observation/last"
"""固定的请求路径，不可被外部修改。"""

DEFAULT_TIMEOUT_SECONDS = 5
"""默认 HTTP 超时时间（秒）。"""


# ──────────────────────────────────────────────
# 结果数据类
# ──────────────────────────────────────────────


@dataclass
class QueryLastResult:
    """query_last 工具的一次执行结果。

    Fields:
        success:      是否成功从设备获取到有效响应
        status_code:  HTTP 状态码（0 表示未收到响应）
        error:        错误描述（success=True 时为 None）
        data:         设备返回的完整 JSON 字典（success=True 时有效）
                     原样保留 valid / source / seq / accel_x/y/z / button
                     / observed_ms / received_ms / data_age_ms / time_quality 等字段
    """

    success: bool
    status_code: int = 0
    error: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────
# 核心函数
# ──────────────────────────────────────────────


def get_base_url() -> Optional[str]:
    """从环境变量读取 ESP32 基础地址。

    Returns:
        基础地址字符串（末尾不含斜杠），或 None（未设置）。
    """
    raw = os.environ.get(ENV_KEY)
    if not raw or not raw.strip():
        return None
    return raw.strip().rstrip("/")


def query_last(timeout: int = DEFAULT_TIMEOUT_SECONDS) -> QueryLastResult:
    """向 ESP32 设备发起 GET /api/observation/last 请求。

    Args:
        timeout: HTTP 超时秒数（默认 5）。

    Returns:
        QueryLastResult:
          - success=True:  成功收到设备响应且为合法 JSON
          - success=False: 未配置、HTTP 错误、超时或无效 JSON
    """
    base = get_base_url()
    if base is None:
        return QueryLastResult(
            success=False,
            status_code=0,
            error=f"环境变量 {ENV_KEY} 未设置，无法发起请求",
        )

    url = f"{base}{FIXED_PATH}"

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.status
            raw_bytes = resp.read()

    except urllib.error.HTTPError as e:
        return QueryLastResult(
            success=False,
            status_code=e.code,
            error=f"HTTP {e.code}: {e.reason}",
        )
    except urllib.error.URLError as e:
        return QueryLastResult(
            success=False,
            status_code=0,
            error=f"请求失败: {e.reason}",
        )
    except TimeoutError:
        return QueryLastResult(
            success=False,
            status_code=0,
            error=f"请求超时 (>{timeout}s)",
        )

    # 解析 JSON
    try:
        data: Dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return QueryLastResult(
            success=False,
            status_code=status_code,
            error=f"无效 JSON 响应: {e}",
        )

    return QueryLastResult(
        success=True,
        status_code=status_code,
        data=data,
    )