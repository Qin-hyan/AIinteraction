"""
test_task_pipeline.py — Week 04 任务管道集成测试

测试策略：
  - mock task_pipeline.dispatch_task（避免真实工具/HTTP 调用）
  - 覆盖：查询、采集、澄清、拒绝、非法输入五大场景
  - 禁止真实 HTTP 请求
"""

import json
import unittest
from unittest.mock import patch

from .task_dispatcher import DispatchResult

from . import task_pipeline as pipe


class TestRunTask(unittest.TestCase):
    """run_task 管道集成测试。"""

    maxDiff = None

    # ── query_last —— 查询 ───────────────────

    @patch("service.task_pipeline.dispatch_task")
    def test_query_last_success(self, mock_dispatch):
        """query_last: 成功获取有效观测 → 输出包含观测详情。"""
        mock_dispatch.return_value = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": True, "source": "live", "seq": 42,
                "accel_x": 10.5, "accel_y": -2.3, "accel_z": 1000.2,
                "observed_ms": 12345678, "received_ms": 12345688,
                "data_age_ms": 10, "time_quality": "relative",
            },
        )
        text = pipe.run_task(
            json.dumps({
                "intent": "query_last", "confidence": 0.95,
                "original": "查看最新传感器数据",
            })
        )
        self.assertIn("最近的观测数据", text)
        self.assertIn("数据来源: live", text)
        self.assertIn("观测序号: #42", text)
        self.assertIn("X=10.5mg", text)
        self.assertIn("观测时间", text)
        self.assertIn("ms", text)
        mock_dispatch.assert_called_once()

    @patch("service.task_pipeline.dispatch_task")
    def test_query_last_no_record(self, mock_dispatch):
        """query_last: 无有效记录 → 输出无数据提示。"""
        mock_dispatch.return_value = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": False, "source": "none", "seq": 0,
                "accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0,
            },
        )
        text = pipe.run_task(
            json.dumps({
                "intent": "query_last", "confidence": 0.90,
                "original": "有最新数据吗",
            })
        )
        self.assertIn("无有效观测记录", text)
        self.assertIn("none", text)
        mock_dispatch.assert_called_once()

    # ── trigger_collect —— 采集 ─────────────

    @patch("service.task_pipeline.dispatch_task")
    def test_trigger_collect_success(self, mock_dispatch):
        """trigger_collect: 采集成功 → 输出采集详情。"""
        mock_dispatch.return_value = DispatchResult(
            success=True, intent="trigger_collect",
            request_id="req-12345-0001", collect_status="completed",
            collect_observation={
                "valid": True, "source": "live", "seq": 43,
                "accel_x": 12.3, "accel_y": -1.5, "accel_z": 1001.0,
                "observed_ms": 67890, "received_ms": 67900,
                "data_age_ms": 10, "time_quality": "relative",
            },
        )
        text = pipe.run_task(
            json.dumps({
                "intent": "trigger_collect", "confidence": 0.92,
                "original": "帮我采集一次数据",
            })
        )
        self.assertIn("采集成功", text)
        self.assertIn("req-12345-0001", text)
        self.assertIn("数据来源: live", text)
        self.assertIn("观测序号: #43", text)
        self.assertIn("X=12.3mg", text)
        mock_dispatch.assert_called_once()

    # ── clarify —— 澄清 ─────────────────────

    @patch("service.task_pipeline.dispatch_task")
    def test_clarify(self, mock_dispatch):
        """clarify: 返回澄清问题，不调用任何工具。"""
        mock_dispatch.return_value = DispatchResult(
            success=True, intent="clarify",
            question="你是要查询还是采集？",
        )
        text = pipe.run_task(
            json.dumps({
                "intent": "clarify", "confidence": 0.45,
                "original": "那个", "question": "你是要查询还是采集？",
            })
        )
        self.assertIn("需要澄清", text)
        self.assertIn("查询还是采集", text)
        mock_dispatch.assert_called_once()

    # ── unsupported —— 拒绝 ─────────────────

    @patch("service.task_pipeline.dispatch_task")
    def test_unsupported(self, mock_dispatch):
        """unsupported: 返回拒绝原因，不调用任何工具。"""
        mock_dispatch.return_value = DispatchResult(
            success=True, intent="unsupported",
            reason="当前仅支持查询和采集操作",
        )
        text = pipe.run_task(
            json.dumps({
                "intent": "unsupported", "confidence": 0.99,
                "original": "帮我写首诗",
                "reason": "当前仅支持查询和采集操作",
            })
        )
        self.assertIn("暂不支持", text)
        self.assertIn("仅支持查询和采集", text)
        mock_dispatch.assert_called_once()

    # ── 非法输入 —— 校验失败 ───────────────

    @patch("service.task_pipeline.dispatch_task")
    def test_invalid_json(self, mock_dispatch):
        """非法 JSON: dispatch_task 返回校验失败，不执行任何工具。"""
        mock_dispatch.return_value = DispatchResult(
            success=False, intent="",
            error="非法 JSON: Expecting value",
            validation_errors=["非法 JSON: Expecting value"],
        )
        text = pipe.run_task("这不是合法JSON{")
        self.assertIn("校验失败", text)
        self.assertIn("非法 JSON", text)
        mock_dispatch.assert_called_once()

    @patch("service.task_pipeline.dispatch_task")
    def test_unknown_intent(self, mock_dispatch):
        """未知意图: dispatch_task 返回校验失败，不执行任何工具。"""
        mock_dispatch.return_value = DispatchResult(
            success=False, intent="",
            error="未知意图: 'send_email'，允许: ['clarify', 'query_last', 'trigger_collect', 'unsupported']",
            validation_errors=["未知意图: 'send_email'"],
        )
        text = pipe.run_task(
            json.dumps({
                "intent": "send_email", "confidence": 0.90,
                "original": "发邮件",
            })
        )
        self.assertIn("校验失败", text)
        self.assertIn("send_email", text)
        mock_dispatch.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)