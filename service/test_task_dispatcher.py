"""
test_task_dispatcher.py — Week 04 受限任务分发器单元测试

测试策略：
  - mock task_dispatcher._query_last 和 task_dispatcher._trigger_collect
  - 覆盖四种意图、非法输入、校验失败、工具异常
  - 验证 clarify / unsupported / 非法 JSON / 校验失败 路径没有副作用（不调工具）
  - 纯 mock 测试，不发起真实 HTTP 请求
"""

import json
import unittest
from unittest.mock import patch

from .query_last import QueryLastResult
from .trigger_collect import CollectResult

# 导入被测模块
from . import task_dispatcher as disp


class TestDispatchTask(unittest.TestCase):
    """dispatch_task 主入口测试。"""

    maxDiff = None

    # ── query_last 意图 ──────────────────────

    @patch("service.task_dispatcher._query_last")
    def test_query_last_success(self, mock_query_last):
        """query_last: 成功获取有效观测数据。"""
        mock_query_last.return_value = QueryLastResult(
            success=True,
            status_code=200,
            data={
                "valid": True,
                "source": "live",
                "seq": 42,
                "accel_x": 10.5,
                "accel_y": -2.3,
                "accel_z": 1000.2,
                "button": "none",
                "observed_ms": 12345,
                "received_ms": 12350,
                "data_age_ms": 5,
                "time_quality": "relative",
            },
        )

        result = disp.dispatch_task(
            json.dumps({
                "intent": "query_last",
                "confidence": 0.95,
                "original": "查看最近一次传感器数据",
            })
        )

        self.assertTrue(result.success)
        self.assertEqual(result.intent, "query_last")
        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation["source"], "live")
        self.assertEqual(result.observation["seq"], 42)
        self.assertIsNone(result.error)
        mock_query_last.assert_called_once()
    @patch("service.task_dispatcher._query_last")
    def test_query_last_no_record(self, mock_query_last):
        """query_last: 设备无有效记录 (valid=false, source=none)。"""
        mock_query_last.return_value = QueryLastResult(
            success=True, status_code=200,
            data={"valid": False, "source": "none", "seq": 0,
                  "accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0,
                  "observed_ms": 0, "received_ms": 0, "data_age_ms": 0},
        )
        result = disp.dispatch_task(
            json.dumps({"intent": "query_last", "confidence": 0.90, "original": "有最新数据吗"})
        )
        self.assertTrue(result.success)
        self.assertEqual(result.intent, "query_last")
        self.assertIsNotNone(result.observation)
        self.assertFalse(result.observation["valid"])
        mock_query_last.assert_called_once()

    @patch("service.task_dispatcher._query_last")
    def test_query_last_tool_failure(self, mock_query_last):
        """query_last: 工具本身失败（如 HTTP 404）。"""
        mock_query_last.return_value = QueryLastResult(
            success=False, status_code=404, error="HTTP 404: Not Found",
        )
        result = disp.dispatch_task(
            json.dumps({"intent": "query_last", "confidence": 0.80, "original": "查数据"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "query_last")
        self.assertIn("404", result.error)
        mock_query_last.assert_called_once()

    @patch("service.task_dispatcher._query_last")
    def test_query_last_exception(self, mock_query_last):
        """query_last: 工具抛出异常。"""
        mock_query_last.side_effect = ConnectionError("Network unreachable")
        result = disp.dispatch_task(
            json.dumps({"intent": "query_last", "confidence": 0.99, "original": "查询"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "query_last")
        self.assertIn("ConnectionError", result.error)
        mock_query_last.assert_called_once()
# ── trigger_collect 意图 ────────────────

    @patch("service.task_dispatcher._trigger_collect")
    def test_trigger_collect_success(self, mock_trigger):
        """trigger_collect: 完整成功流程。"""
        mock_trigger.return_value = CollectResult(
            success=True, request_id="req-12345-0001", status="completed",
            observation={"valid": True, "source": "live", "seq": 43,
                         "accel_x": 12.3, "accel_y": -1.5, "accel_z": 1001.0,
                         "observed_ms": 67890, "received_ms": 67900, "data_age_ms": 10},
        )
        result = disp.dispatch_task(
            json.dumps({"intent": "trigger_collect", "confidence": 0.92, "original": "采集一次"})
        )
        self.assertTrue(result.success)
        self.assertEqual(result.intent, "trigger_collect")
        self.assertEqual(result.request_id, "req-12345-0001")
        self.assertEqual(result.collect_status, "completed")
        self.assertTrue(result.collect_observation["valid"])
        self.assertEqual(result.collect_observation["seq"], 43)
        mock_trigger.assert_called_once()

    @patch("service.task_dispatcher._trigger_collect")
    def test_trigger_collect_tool_failure(self, mock_trigger):
        """trigger_collect: 工具返回失败。"""
        mock_trigger.return_value = CollectResult(
            success=False, request_id="req-12345-0002", status="failed",
            error="设备执行失败",
        )
        result = disp.dispatch_task(
            json.dumps({"intent": "trigger_collect", "confidence": 0.85, "original": "采集"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "trigger_collect")
        self.assertEqual(result.request_id, "req-12345-0002")
        self.assertEqual(result.collect_status, "failed")
        self.assertIn("设备执行失败", result.error)
        mock_trigger.assert_called_once()

    @patch("service.task_dispatcher._trigger_collect")
    def test_trigger_collect_exception(self, mock_trigger):
        """trigger_collect: 工具抛出异常。"""
        mock_trigger.side_effect = TimeoutError("Timeout >5s")
        result = disp.dispatch_task(
            json.dumps({"intent": "trigger_collect", "confidence": 0.70, "original": "采集"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "trigger_collect")
        self.assertIn("TimeoutError", result.error)
        mock_trigger.assert_called_once()
# ── clarify 意图（不应调用任何工具）────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_clarify_no_tool_call(self, mock_trigger, mock_query):
        """clarify: 返回澄清问题，不调用工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "clarify", "confidence": 0.60, "original": "嗯",
                        "question": "您是想查询数据还是执行采集？"})
        )
        self.assertTrue(result.success)
        self.assertEqual(result.intent, "clarify")
        self.assertEqual(result.question, "您是想查询数据还是执行采集？")
        self.assertIsNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── unsupported 意图（不应调用任何工具）─

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_unsupported_no_tool_call(self, mock_trigger, mock_query):
        """unsupported: 返回拒绝原因，不调用工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "unsupported", "confidence": 0.95, "original": "发邮件",
                        "reason": "仅支持查询和采集"})
        )
        self.assertTrue(result.success)
        self.assertEqual(result.intent, "unsupported")
        self.assertEqual(result.reason, "仅支持查询和采集")
        self.assertIsNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 非法 JSON（不应调用任何工具）───────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_invalid_json_no_tool_call(self, mock_trigger, mock_query):
        """非法 JSON: 校验失败，不调用任何工具。"""
        result = disp.dispatch_task("这不是 JSON{{}")
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        self.assertIsNotNone(result.validation_errors)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 校验失败：未知意图 ─────────────────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_unknown_intent_no_tool_call(self, mock_trigger, mock_query):
        """未知意图: 校验失败，不调用工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "send_email", "confidence": 0.90, "original": "发邮件"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 校验失败：额外字段 ─────────────────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_extra_field_no_tool_call(self, mock_trigger, mock_query):
        """query_last 带额外字段: 校验失败，不调工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "query_last", "confidence": 0.95, "original": "查数据",
                        "device_address": "http://evil.com"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        self.assertIn("不允许", result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 校验失败：缺少必需字段 ─────────────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_missing_required_no_tool_call(self, mock_trigger, mock_query):
        """缺少必需字段: 校验失败，不调工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "query_last"})  # 缺 confidence, original
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 校验失败：clarify 缺少 question ────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_clarify_missing_question_no_tool_call(self, mock_trigger, mock_query):
        """clarify 缺 question: 校验失败，不调工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "clarify", "confidence": 0.50, "original": "嗯"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 校验失败：unsupported 缺少 reason ──

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_unsupported_missing_reason_no_tool_call(self, mock_trigger, mock_query):
        """unsupported 缺 reason: 校验失败，不调工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "unsupported", "confidence": 0.90, "original": "发邮件"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()

    # ── 校验失败：confidence 越界 ───────────

    @patch("service.task_dispatcher._query_last")
    @patch("service.task_dispatcher._trigger_collect")
    def test_confidence_out_of_range_no_tool_call(self, mock_trigger, mock_query):
        """confidence 越界: 校验失败，不调工具。"""
        result = disp.dispatch_task(
            json.dumps({"intent": "query_last", "confidence": 1.5, "original": "查数据"})
        )
        self.assertFalse(result.success)
        self.assertEqual(result.intent, "")
        self.assertIsNotNone(result.error)
        mock_query.assert_not_called()
        mock_trigger.assert_not_called()


if __name__ == "__main__":
    unittest.main()