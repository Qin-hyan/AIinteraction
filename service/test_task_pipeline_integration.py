"""
test_task_pipeline_integration.py — Week 04 结构化样例下的本地集成验证

测试策略：
  - 使用结构化 JSON 样例作为模型输出的替代输入
  - 调用真实处理链路：
      parse_and_validate → dispatch_task → 真实工具代码 → format_result
  - 只 mock HTTP 网络边界 (urllib.request.urlopen) 和 time.sleep
  - 不 mock 分发器和业务工具（validator、dispatcher、formatter、工具函数体）
  - 不发起真实 HTTP 请求

覆盖场景：
  1. query_last 查询成功（有效观测，验证数据来源/序号/相对时间保留）
  2. query_last 无有效记录
  3. trigger_collect 采集成功
  4. trigger_collect 设备执行失败
  5. trigger_collect 设备端超时
  6. clarify 返回澄清问题（验证 HTTP 未被调用）
  7. unsupported 返回拒绝原因（验证 HTTP 未被调用）
  8. 非法 JSON（校验失败，验证 HTTP 未被调用）
  9. 未知意图（校验失败，验证 HTTP 未被调用）
  10. query_last 带额外字段（校验失败，验证 HTTP 未被调用）
"""

import json
import os
import unittest
from unittest.mock import patch, MagicMock

from . import task_pipeline as pipe
from .query_last import ENV_KEY


class TestTaskPipelineIntegration(unittest.TestCase):
    """真实链路集成测试（仅 mock HTTP 网络边界）。

    10 tests covering:
      - 查询成功、无记录
      - 采集成功、失败、超时
      - 澄清、拒绝（不调 HTTP）
      - 非法 JSON、未知意图、额外字段（不调 HTTP）
    """

    maxDiff = None

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

    def setUp(self):
        os.environ[ENV_KEY] = "http://192.168.1.100"

    def tearDown(self):
        os.environ.pop(ENV_KEY, None)

    # ─────────────────────────────────────────────
    # query_last 查询成功
    # ─────────────────────────────────────────────

    @patch("service.query_last.urllib.request.urlopen")
    def test_query_last_success(self, mock_urlopen):
        """查询: 有效观测 → 输出包含来源/序号/相对时间，不伪装日历时间。"""
        device_response = {
            "valid": True,
            "source": "live",
            "seq": 47,
            "accel_x": 10.5,
            "accel_y": -2.3,
            "accel_z": 1000.2,
            "button": "none",
            "observed_ms": 12345678,
            "received_ms": 12345688,
            "data_age_ms": 10,
            "time_quality": "relative",
        }
        mock_urlopen.return_value = self._make_mock_response(device_response)

        text = pipe.run_task(json.dumps({
            "intent": "query_last",
            "confidence": 0.95,
            "original": "查看最新传感器数据",
        }))

        # 数据来源和序号保留
        self.assertIn("数据来源: live", text)
        self.assertIn("观测序号: #47", text)
        self.assertIn("X=10.5mg", text)
        # 相对时间保留（不伪装日历时间）
        self.assertIn("观测时间", text)
        self.assertIn("ms", text)
        self.assertIn("设备相对运行时间", text)
        self.assertNotIn("202", text, msg="不应包含日历年份")
        self.assertNotIn("月", text, msg="不应包含日历月份")

    @patch("service.query_last.urllib.request.urlopen")
    def test_query_last_no_record(self, mock_urlopen):
        """查询: 无有效记录 → 正确提示无数据。"""
        device_response = {
            "valid": False,
            "source": "none",
            "seq": 0,
            "accel_x": 0.0,
            "accel_y": 0.0,
            "accel_z": 0.0,
            "observed_ms": 0,
            "received_ms": 0,
            "data_age_ms": -1,
        }
        mock_urlopen.return_value = self._make_mock_response(device_response)

        text = pipe.run_task(json.dumps({
            "intent": "query_last",
            "confidence": 0.90,
            "original": "有最新数据吗",
        }))

        self.assertIn("无有效观测记录", text)
        self.assertIn("none", text)

    # ─────────────────────────────────────────────
    # trigger_collect 采集成功
    # ─────────────────────────────────────────────

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_trigger_collect_success(self, mock_urlopen, mock_sleep):
        """采集: 成功链路 → 输出采集详情，不含日历时间。"""
        post_response = self._make_mock_response({
            "request_id": "req-12345-0001",
            "status": "submitted",
        })
        poll_completed = self._make_mock_response({
            "request_id": "req-12345-0001",
            "status": "completed",
            "linked": True,
            "observation": {
                "valid": True,
                "source": "live",
                "seq": 48,
                "accel_x": 12.3,
                "accel_y": -1.5,
                "accel_z": 1001.0,
                "observed_ms": 67890,
                "received_ms": 67900,
                "data_age_ms": 10,
                "time_quality": "relative",
            },
        })
        mock_urlopen.side_effect = [post_response, poll_completed]

        text = pipe.run_task(json.dumps({
            "intent": "trigger_collect",
            "confidence": 0.92,
            "original": "帮我采集一次数据",
        }))

        self.assertIn("采集成功", text)
        self.assertIn("req-12345-0001", text)
        self.assertIn("数据来源: live", text)
        self.assertIn("观测序号: #48", text)
        self.assertIn("X=12.3mg", text)
        # 相对时间保留
        self.assertIn("观测时间", text)
        self.assertIn("ms", text)
        self.assertIn("设备相对运行时间", text)
        self.assertNotIn("202", text, msg="不应包含日历年份")
        self.assertNotIn("月", text, msg="不应包含日历月份")

    # ─────────────────────────────────────────────
    # trigger_collect 采集失败
    # ─────────────────────────────────────────────

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_trigger_collect_failed(self, mock_urlopen, mock_sleep):
        """采集: 设备返回 failed → 输出失败信息，不反馈成功。"""
        post_response = self._make_mock_response({
            "request_id": "req-99999-0001",
            "status": "submitted",
        })
        poll_failed = self._make_mock_response({
            "request_id": "req-99999-0001",
            "status": "failed",
            "linked": False,
            "observation": {"valid": False},
        })
        mock_urlopen.side_effect = [post_response, poll_failed]

        text = pipe.run_task(json.dumps({
            "intent": "trigger_collect",
            "confidence": 0.80,
            "original": "采集数据",
        }))

        self.assertIn("采集失败", text)
        self.assertIn("req-99999-0001", text)
        self.assertIn("设备执行失败", text)
        # 采集未完成时不会反馈成功
        self.assertNotIn("采集成功", text)

    # ─────────────────────────────────────────────
    # trigger_collect 设备端超时
    # ─────────────────────────────────────────────

    @patch("service.trigger_collect.time.sleep")
    @patch("service.trigger_collect.urllib.request.urlopen")
    def test_trigger_collect_timeout(self, mock_urlopen, mock_sleep):
        """采集: 设备端超时 → 输出超时信息，不反馈成功。"""
        post_response = self._make_mock_response({
            "request_id": "req-88888-0001",
            "status": "submitted",
        })
        poll_timeout = self._make_mock_response({
            "request_id": "req-88888-0001",
            "status": "timeout",
            "linked": False,
            "observation": {"valid": False},
        })
        mock_urlopen.side_effect = [post_response, poll_timeout]

        text = pipe.run_task(json.dumps({
            "intent": "trigger_collect",
            "confidence": 0.85,
            "original": "采集数据",
        }))

        self.assertIn("采集失败", text)
        self.assertIn("req-88888-0001", text)
        self.assertIn("超时", text)
        # 采集未完成时不会反馈成功
        self.assertNotIn("采集成功", text)
# ─────────────────────────────────────────────
    # clarify 澄清（不调 HTTP）
    # ─────────────────────────────────────────────

    @patch("service.query_last.urllib.request.urlopen")
    def test_clarify_no_http(self, mock_urlopen):
        """澄清: 返回澄清问题，不调用任何 HTTP 工具。"""
        text = pipe.run_task(json.dumps({
            "intent": "clarify",
            "confidence": 0.45,
            "original": "那个",
            "question": "你是要查询还是采集？",
        }))

        self.assertIn("需要澄清", text)
        self.assertIn("查询还是采集", text)
        mock_urlopen.assert_not_called()

    # ─────────────────────────────────────────────
    # unsupported 拒绝（不调 HTTP）
    # ─────────────────────────────────────────────

    @patch("service.query_last.urllib.request.urlopen")
    def test_unsupported_no_http(self, mock_urlopen):
        """拒绝: 返回拒绝原因，不调用任何 HTTP 工具。"""
        text = pipe.run_task(json.dumps({
            "intent": "unsupported",
            "confidence": 0.99,
            "original": "帮我写首诗",
            "reason": "当前仅支持查询和采集操作",
        }))

        self.assertIn("暂不支持", text)
        self.assertIn("仅支持查询和采集", text)
        mock_urlopen.assert_not_called()

    # ─────────────────────────────────────────────
    # 非法输入（不调 HTTP）
    # ─────────────────────────────────────────────

    @patch("service.query_last.urllib.request.urlopen")
    def test_invalid_json_no_http(self, mock_urlopen):
        """非法 JSON: 校验失败，不发起 HTTP 请求。"""
        text = pipe.run_task("这不是合法JSON{")

        self.assertIn("校验失败", text)
        self.assertIn("非法 JSON", text)
        mock_urlopen.assert_not_called()

    @patch("service.query_last.urllib.request.urlopen")
    def test_unknown_intent_no_http(self, mock_urlopen):
        """未知意图: 校验失败，不发起 HTTP 请求。"""
        text = pipe.run_task(json.dumps({
            "intent": "send_email",
            "confidence": 0.90,
            "original": "发邮件",
        }))

        self.assertIn("校验失败", text)
        self.assertIn("send_email", text)
        mock_urlopen.assert_not_called()

    @patch("service.query_last.urllib.request.urlopen")
    def test_extra_field_rejected_no_http(self, mock_urlopen):
        """query_last 带额外字段: 校验失败，不发起 HTTP 请求。"""
        text = pipe.run_task(json.dumps({
            "intent": "query_last",
            "confidence": 0.95,
            "original": "查数据",
            "device_address": "http://evil.com",
        }))

        self.assertIn("校验失败", text)
        self.assertIn("device_address", text)
        self.assertIn("不允许", text)
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)