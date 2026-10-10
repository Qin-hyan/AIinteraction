"""
test_query_last.py — Week 04 query_last 受限工具单元测试

覆盖场景：
  1. 成功获取有效观测数据（valid=true，含完整传感器字段）
  2. 设备无有效记录（valid=false，source=none）
  3. HTTP 404 错误
  4. HTTP 500 错误
  5. 连接失败（URLError）
  6. 请求超时（TimeoutError）
  7. 无效 JSON 响应
  8. 环境变量未设置
  9. 环境变量为空字符串
  10. 环境变量末尾斜杠被正确处理

不发起真实设备请求。所有 HTTP 调用通过 mock 模拟。
"""

import json
import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from service.query_last import (
    ENV_KEY,
    FIXED_PATH,
    QueryLastResult,
    get_base_url,
    query_last,
)


class TestGetBaseUrl(unittest.TestCase):
    """测试 get_base_url() 环境变量读取逻辑。"""

    def tearDown(self):
        os.environ.pop(ENV_KEY, None)

    def test_env_var_set(self):
        os.environ[ENV_KEY] = "http://192.168.1.100"
        self.assertEqual(get_base_url(), "http://192.168.1.100")

    def test_env_var_trailing_slash_stripped(self):
        os.environ[ENV_KEY] = "http://192.168.1.100/"
        self.assertEqual(get_base_url(), "http://192.168.1.100")

    def test_env_var_not_set(self):
        os.environ.pop(ENV_KEY, None)
        self.assertIsNone(get_base_url())

    def test_env_var_empty_string(self):
        os.environ[ENV_KEY] = ""
        self.assertIsNone(get_base_url())

    def test_env_var_whitespace(self):
        os.environ[ENV_KEY] = "   "
        self.assertIsNone(get_base_url())
class TestQueryLast(unittest.TestCase):
    """测试 query_last() 核心函数。"""

    def setUp(self):
        os.environ[ENV_KEY] = "http://192.168.1.100"

    def tearDown(self):
        os.environ.pop(ENV_KEY, None)

    @staticmethod
    def _make_mock_response(data: dict, status: int = 200):
        """构造一个模拟的 HTTP 响应对象。"""
        raw = json.dumps(data).encode("utf-8")
        m = MagicMock()
        m.status = status
        m.read.return_value = raw
        m.__enter__.return_value = m
        m.__exit__.return_value = None
        return m

    # ============ 成功场景 ============

    @patch("service.query_last.urllib.request.urlopen")
    def test_success_with_valid_data(self, mock_urlopen):
        """成功获取有效观测数据。"""
        device_response = {
            "action": "refresh_stored",
            "triggered_read": False,
            "fresh_window_ms": 3000,
            "valid": True,
            "record_id": "obs-00047",
            "request_id": "req-75253-0001",
            "source": "live",
            "seq": 47,
            "accel_x": 123,
            "accel_y": 456,
            "accel_z": 789,
            "button": "none",
            "observed_ms": 12345,
            "received_ms": 12346,
            "data_age_ms": 150,
            "time_quality": "relative",
            "stale": False,
            "note": "读取的是板上已保存观测，本次未读取传感器。",
        }
        mock_urlopen.return_value = self._make_mock_response(device_response)

        result = query_last(timeout=3)

        self.assertTrue(result.success)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.data)
        self.assertTrue(result.data["valid"])
        self.assertEqual(result.data["record_id"], "obs-00047")
        self.assertEqual(result.data["source"], "live")
        self.assertEqual(result.data["seq"], 47)
        self.assertEqual(result.data["accel_x"], 123)
        self.assertEqual(result.data["time_quality"], "relative")
        self.assertFalse(result.data["stale"])

        # 确认请求 URL 固定、不可变
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.full_url, "http://192.168.1.100/api/observation/last")

    @patch("service.query_last.urllib.request.urlopen")
    def test_success_no_records(self, mock_urlopen):
        """设备无有效记录 — 原样保留 valid=false 和 source=none。"""
        device_response = {
            "action": "refresh_stored",
            "triggered_read": False,
            "fresh_window_ms": 3000,
            "valid": False,
            "source": "none",
            "data_age_ms": -1,
            "stale": True,
            "note": "读取的是板上已保存观测，本次未读取传感器。",
        }
        mock_urlopen.return_value = self._make_mock_response(device_response)

        result = query_last(timeout=3)

        self.assertTrue(result.success)
        self.assertEqual(result.status_code, 200)
        self.assertIsNotNone(result.data)
        self.assertFalse(result.data["valid"])
        self.assertEqual(result.data["source"], "none")
        self.assertEqual(result.data["data_age_ms"], -1)
        self.assertTrue(result.data["stale"])
# ============ HTTP 错误场景 ============

    @patch("service.query_last.urllib.request.urlopen")
    def test_http_404(self, mock_urlopen):
        """HTTP 404 错误。"""
        mock_urlopen.side_effect = HTTPError(
            url="http://192.168.1.100/api/observation/last",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )

        result = query_last(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 404)
        self.assertIn("404", result.error or "")

    @patch("service.query_last.urllib.request.urlopen")
    def test_http_500(self, mock_urlopen):
        """HTTP 500 错误。"""
        mock_urlopen.side_effect = HTTPError(
            url="http://192.168.1.100/api/observation/last",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )

        result = query_last(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 500)
        self.assertIn("500", result.error or "")

    # ============ 网络错误场景 ============

    @patch("service.query_last.urllib.request.urlopen")
    def test_connection_failure(self, mock_urlopen):
        """连接失败（URLError）。"""
        mock_urlopen.side_effect = URLError(reason("Connection refused"))

        result = query_last(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 0)
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.data)

    @patch("service.query_last.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        """请求超时。"""
        mock_urlopen.side_effect = TimeoutError("timed out")

        result = query_last(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 0)
        self.assertIn("超时", result.error or "")

    # ============ 响应解析错误 ============

    @patch("service.query_last.urllib.request.urlopen")
    def test_invalid_json(self, mock_urlopen):
        """设备返回了非 JSON 内容。"""
        m = MagicMock()
        m.status = 200
        m.read.return_value = b"<html>not json</html>"
        m.__enter__.return_value = m
        m.__exit__.return_value = None
        mock_urlopen.return_value = m

        result = query_last(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 200)
        self.assertIn("无效 JSON", result.error or "")

    # ============ 环境变量未设置 ============

    def test_env_var_not_set_core(self):
        """环境变量未设置时应立即返回错误，不发起请求。"""
        os.environ.pop(ENV_KEY, None)

        result = query_last(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 0)
        self.assertIn(ENV_KEY, result.error or "")


class reason:
    """辅助类：模拟 URLError 的 reason 参数。"""
    def __init__(self, msg: str):
        self.args = (msg,)

    def __str__(self):
        return self.args[0]


if __name__ == "__main__":
    unittest.main(verbosity=2)