"""
test_trigger_collect.py — Week 04 trigger_collect 受限工具单元测试

覆盖场景：
  1. 成功流程：POST → submitted → completed + linked + valid observation
  2. 设备失败：POST → submitted → failed
  3. 设备超时：POST → submitted → timeout
  4. 轮询超时：POST → submitted → 持续 submitted 直到 max_attempts 耗尽
  5. 请求不匹配：POST → submitted → completed 但 linked=false
  6. 观测无效：POST → submitted → completed + linked=true 但 observation.valid=false
  7. 缺失观测：POST → submitted → completed + linked=true 但 observation 缺失
  8. POST HTTP 错误：POST 返回 500
  9. 轮询 HTTP 错误：POST 成功 → 轮询返回 500
  10. 无效 JSON 响应：POST 成功 → 轮询返回非 JSON
  11. 配置缺失：环境变量未设置
  12. POST 缺少 request_id

不发起真实设备请求。所有 HTTP 调用通过 mock 模拟。
"""

import json
import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from service.trigger_collect import (
    ENV_KEY,
    FIXED_COLLECT_PATH,
    FIXED_STATUS_PATH,
    MAX_POLL_ATTEMPTS,
    CollectResult,
    trigger_collect,
    _poll_status,
)
from service.query_last import get_base_url


class TestTriggerCollect(unittest.TestCase):
    """测试 trigger_collect() 核心函数。"""

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

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_success_flow(self, mock_urlopen, mock_sleep):
        """完整成功流程：POST → submitted → 轮询直至 completed+linked+valid。"""
        post_response = self._make_mock_response({
            "request_id": "req-12345-0001",
            "status": "submitted",
            "task_seq": 1,
            "timeout_ms": 5000,
            "message": "submitted",
        })

        poll_submitted = self._make_mock_response({
            "request_id": "req-12345-0001",
            "task_seq": 1,
            "timeout_ms": 5000,
            "matches_current": True,
            "from_history": False,
            "status": "submitted",
            "submitted_ms": 1000.0,
            "received_ms": -1.0,
            "completed_ms": 0.0,
            "linked": False,
            "observation": {
                "valid": False,
                "source": "none",
                "data_age_ms": -1,
            },
            "note": "已受理，等待设备执行与回执…",
        })

        poll_completed = self._make_mock_response({
            "request_id": "req-12345-0001",
            "task_seq": 1,
            "timeout_ms": 5000,
            "matches_current": True,
            "from_history": False,
            "status": "completed",
            "submitted_ms": 1000.0,
            "received_ms": 1100.0,
            "completed_ms": 1250.0,
            "elapsed_ms": 250.0,
            "linked": True,
            "observation": {
                "valid": True,
                "record_id": "obs-00048",
                "request_id": "req-12345-0001",
                "source": "live",
                "seq": 48,
                "accel_x": 123,
                "accel_y": 456,
                "accel_z": 789,
                "button": "none",
                "observed_ms": 1249.0,
                "received_ms": 1250.0,
                "data_age_ms": 1.0,
                "time_quality": "relative",
            },
            "note": "完成：已收到与本请求号关联的新观测。",
        })

        mock_urlopen.side_effect = [post_response, poll_submitted, poll_completed]

        result = trigger_collect(timeout=3, poll_interval=0.1, max_attempts=10)

        self.assertTrue(result.success)
        self.assertEqual(result.request_id, "req-12345-0001")
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.observation)
        self.assertTrue(result.observation["valid"])
        self.assertEqual(result.observation["record_id"], "obs-00048")
        self.assertEqual(result.observation["request_id"], "req-12345-0001")
        self.assertEqual(result.observation["source"], "live")
        self.assertEqual(result.observation["seq"], 48)

    # ============ 设备失败场景 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_device_failed(self, mock_urlopen, mock_sleep):
        """设备执行失败：status= failed。"""
        post_response = self._make_mock_response({
            "request_id": "req-11111-0001",
            "status": "submitted",
            "task_seq": 1,
            "timeout_ms": 5000,
            "message": "submitted",
        })

        poll_failed = self._make_mock_response({
            "request_id": "req-11111-0001",
            "status": "failed",
            "matches_current": True,
            "from_history": False,
            "linked": False,
            "observation": {
                "valid": False,
                "source": "none",
                "data_age_ms": -1,
            },
            "note": "失败：设备执行了但传感器读取未成功，本次未产生新观测。",
        })

        mock_urlopen.side_effect = [post_response, poll_failed]

        result = trigger_collect(timeout=3, poll_interval=0.1, max_attempts=10)

        self.assertFalse(result.success)
        self.assertEqual(result.request_id, "req-11111-0001")
        self.assertEqual(result.status, "failed")
        self.assertIsNotNone(result.error)
        self.assertIn("失败", result.error)
# ============ 设备端超时场景 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_device_timeout(self, mock_urlopen, mock_sleep):
        """设备端返回 timeout 状态。"""
        post_response = self._make_mock_response({
            "request_id": "req-22222-0001",
            "status": "submitted",
            "task_seq": 1,
        })

        poll_timeout = self._make_mock_response({
            "request_id": "req-22222-0001",
            "status": "timeout",
            "matches_current": True,
            "from_history": False,
            "linked": False,
            "observation": {
                "valid": False,
                "source": "none",
                "data_age_ms": -1,
            },
            "note": "超时：窗口内未收到设备结果。",
        })

        mock_urlopen.side_effect = [post_response, poll_timeout]

        result = trigger_collect(timeout=3, poll_interval=0.1)

        self.assertFalse(result.success)
        self.assertEqual(result.request_id, "req-22222-0001")
        self.assertEqual(result.status, "timeout")

    # ============ 轮询超时场景 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_poll_timeout_exhausted(self, mock_urlopen, mock_sleep):
        """轮询耗尽 max_attempts 仍未完成。"""
        post_response = self._make_mock_response({
            "request_id": "req-33333-0001",
            "status": "submitted",
            "task_seq": 1,
        })

        poll_pending = self._make_mock_response({
            "request_id": "req-33333-0001",
            "status": "submitted",
            "matches_current": True,
            "from_history": False,
            "linked": False,
            "observation": {
                "valid": False,
                "source": "none",
                "data_age_ms": -1,
            },
            "note": "已受理，等待设备执行与回执…",
        })

        # POST + 3 rounds of polling (max_attempts=3)
        mock_urlopen.side_effect = [post_response, poll_pending, poll_pending, poll_pending]

        result = trigger_collect(timeout=3, poll_interval=0.1, max_attempts=3)

        self.assertFalse(result.success)
        self.assertEqual(result.request_id, "req-33333-0001")
        self.assertEqual(result.status, "timeout")
        self.assertIn("达到最大尝试次数", result.error or "")

    # ============ 请求不匹配场景 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_request_id_mismatch(self, mock_urlopen, mock_sleep):
        """任务完成但 linked=false，观测不属于本次请求。"""
        post_response = self._make_mock_response({
            "request_id": "req-44444-0001",
            "status": "submitted",
        })

        poll_mismatch = self._make_mock_response({
            "request_id": "req-44444-0001",
            "status": "completed",
            "matches_current": True,
            "from_history": False,
            "linked": False,
            "observation": {
                "valid": True,
                "record_id": "obs-00047",
                "request_id": "req-00000-0000",
                "source": "live",
                "seq": 47,
                "accel_x": 111,
                "accel_y": 222,
                "accel_z": 333,
                "button": "none",
            },
            "note": "完成但观察来自旧请求。",
        })

        mock_urlopen.side_effect = [post_response, poll_mismatch]

        result = trigger_collect(timeout=3, poll_interval=0.1, max_attempts=10)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.error)
        self.assertIn("linked=false", result.error)
# ============ 观测无效场景 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_invalid_observation(self, mock_urlopen, mock_sleep):
        """任务完成、请求匹配但观测 valid=false。"""
        post_response = self._make_mock_response({
            "request_id": "req-55555-0001",
            "status": "submitted",
        })

        poll_invalid = self._make_mock_response({
            "request_id": "req-55555-0001",
            "status": "completed",
            "matches_current": True,
            "from_history": False,
            "linked": True,
            "observation": {
                "valid": False,
                "source": "none",
                "data_age_ms": -1,
            },
            "note": "完成但观测无效。",
        })

        mock_urlopen.side_effect = [post_response, poll_invalid]

        result = trigger_collect(timeout=3, poll_interval=0.1, max_attempts=10)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIn("valid != true", result.error or "")

    # ============ 缺失观测场景 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_missing_observation(self, mock_urlopen, mock_sleep):
        """任务完成但响应中缺少 observation 字段。"""
        post_response = self._make_mock_response({
            "request_id": "req-66666-0001",
            "status": "submitted",
        })

        poll_missing = self._make_mock_response({
            "request_id": "req-66666-0001",
            "status": "completed",
            "matches_current": True,
            "from_history": False,
            "linked": True,
            "note": "完成但无观测字段。",
        })

        mock_urlopen.side_effect = [post_response, poll_missing]

        result = trigger_collect(timeout=3, poll_interval=0.1, max_attempts=10)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIn("valid != true", result.error or "")

    # ============ HTTP 错误场景 ============

    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_http_error_on_post(self, mock_urlopen):
        """POST 请求返回 HTTP 500 错误。"""
        mock_urlopen.side_effect = HTTPError(
            url="http://192.168.1.100/api/collect",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )

        result = trigger_collect(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 500)
        self.assertIn("500", result.error or "")

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_http_error_on_poll(self, mock_urlopen, mock_sleep):
        """POST 成功但轮询返回 HTTP 500 错误。"""
        post_response = self._make_mock_response({
            "request_id": "req-77777-0001",
            "status": "submitted",
        })
        poll_error = HTTPError(
            url="http://192.168.1.100/api/collect/status?request_id=req-77777-0001",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )

        mock_urlopen.side_effect = [post_response, poll_error]

        result = trigger_collect(timeout=3, poll_interval=0.1)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 500)
        self.assertIn("轮询", result.error or "")

    # ============ 无效 JSON 响应 ============

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_invalid_json_response(self, mock_urlopen, mock_sleep):
        """POST 成功但轮询返回非 JSON 内容。"""
        post_response = self._make_mock_response({
            "request_id": "req-88888-0001",
            "status": "submitted",
        })

        m = MagicMock()
        m.status = 200
        m.read.return_value = b"<html>not json</html>"
        m.__enter__.return_value = m
        m.__exit__.return_value = None

        mock_urlopen.side_effect = [post_response, m]

        result = trigger_collect(timeout=3, poll_interval=0.1)

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertIn("无效 JSON", result.error or "")

    # ============ 配置缺失 ============

    def test_config_missing(self):
        """环境变量未设置时应立即返回错误，不发起任何请求。"""
        os.environ.pop(ENV_KEY, None)

        result = trigger_collect(timeout=3)

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 0)
        self.assertIn(ENV_KEY, result.error or "")

    # ============ POST 缺少 request_id ============

    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_post_missing_request_id(self, mock_urlopen):
        """POST 返回缺少 request_id 字段的响应。"""
        post_response = self._make_mock_response({
            "status": "submitted",
            "message": "submitted",
        })
        mock_urlopen.return_value = post_response

        result = trigger_collect(timeout=3)

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertIn("request_id", result.error or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)