"""
test_result_formatter.py — result_formatter 单元测试

测试策略：
  - 验证 format_result() 对各种 DispatchResult 的正确格式化输出
  - 覆盖 query_last/trigger_collect 成功/失败/无数据，clarify/unsupported/校验失败
  - 验证时间格式不伪装为日历时间
  - 纯单元测试，不调用任何设备工具
"""

import unittest
from .task_dispatcher import DispatchResult
from .result_formatter import format_result


class TestFormatResult(unittest.TestCase):
    """format_result 格式化输出测试。"""
    maxDiff = None

    # ── query_last ──────────────────────────

    def test_query_last_success_valid(self):
        """query_last 成功，有效观测。"""
        result = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": True, "source": "live", "seq": 42,
                "accel_x": 10.5, "accel_y": -2.3, "accel_z": 1000.2,
                "button": "none",
                "observed_ms": 12345678, "received_ms": 12345688,
                "data_age_ms": 10, "time_quality": "relative",
            },
        )
        text = format_result(result)
        self.assertIn("最近的观测数据", text)
        self.assertIn("数据来源: live", text)
        self.assertIn("观测序号: #42", text)
        self.assertIn("X=10.5mg", text)
        self.assertIn("观测时间", text)
        self.assertIn("ms", text)
        self.assertNotIn("202", text)
        self.assertNotIn("月", text)
    def test_query_last_success_no_record(self):
        """query_last 成功但无有效记录。"""
        result = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": False, "source": "none", "seq": 0,
                "accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0,
                "observed_ms": 0, "received_ms": 0, "data_age_ms": 0,
            },
        )
        text = format_result(result)
        self.assertIn("无有效观测记录", text)
        self.assertIn("none", text)

    def test_query_last_observation_none(self):
        """query_last observation 为 None。"""
        result = DispatchResult(
            success=True, intent="query_last", observation=None,
        )
        text = format_result(result)
        self.assertIn("未返回有效数据", text)

    def test_query_last_failure(self):
        """query_last 工具失败（HTTP 404）。"""
        result = DispatchResult(
            success=False, intent="query_last",
            error="HTTP 404: Not Found",
        )
        text = format_result(result)
        self.assertIn("查询失败", text)
        self.assertIn("404", text)

    # ── trigger_collect ─────────────────────

    def test_trigger_collect_success(self):
        """trigger_collect 成功，有效新观测。"""
        result = DispatchResult(
            success=True, intent="trigger_collect",
            request_id="req-12345-0001", collect_status="completed",
            collect_observation={
                "valid": True, "source": "live", "seq": 43,
                "accel_x": 12.3, "accel_y": -1.5, "accel_z": 1001.0,
                "observed_ms": 67890, "received_ms": 67900,
                "data_age_ms": 10, "time_quality": "relative",
            },
        )
        text = format_result(result)
        self.assertIn("采集成功", text)
        self.assertIn("req-12345-0001", text)
        self.assertIn("数据来源: live", text)
        self.assertIn("观测序号: #43", text)
        self.assertIn("X=12.3mg", text)
        self.assertIn("观测时间", text)
        self.assertNotIn("月", text)

    def test_trigger_collect_failure(self):
        """trigger_collect 工具失败。"""
        result = DispatchResult(
            success=False, intent="trigger_collect",
            request_id="req-12345-0002", collect_status="failed",
            error="设备执行失败",
        )
        text = format_result(result)
        self.assertIn("采集失败", text)
        self.assertIn("req-12345-0002", text)
        self.assertIn("设备执行失败", text)

    def test_trigger_collect_no_observation(self):
        """trigger_collect success but obs=None。"""
        result = DispatchResult(
            success=True, intent="trigger_collect",
            request_id="req-12345-0003", collect_status="completed",
            collect_observation=None,
        )
        text = format_result(result)
        self.assertIn("无有效观测数据", text)
        self.assertIn("req-12345-0003", text)

    def test_trigger_collect_invalid_observation(self):
        """trigger_collect 完成但观测无效。"""
        result = DispatchResult(
            success=True, intent="trigger_collect",
            request_id="req-12345-0004", collect_status="completed",
            collect_observation={
                "valid": False, "source": "live", "seq": 44,
                "accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0,
            },
        )
        text = format_result(result)
        self.assertIn("观测无效", text)
        self.assertIn("req-12345-0004", text)
        self.assertIn("valid=False", text)

    def test_trigger_collect_source_none(self):
        """trigger_collect 完成但 source=none。"""
        result = DispatchResult(
            success=True, intent="trigger_collect",
            request_id="req-12345-0005", collect_status="completed",
            collect_observation={
                "valid": True, "source": "none", "seq": 0,
            },
        )
        text = format_result(result)
        self.assertIn("观测无效", text)
        self.assertIn("source=none", text)
    def test_clarify_with_question(self):
        """clarify 带澄清问题。"""
        result = DispatchResult(
            success=True, intent="clarify",
            question="您是想要查询还是采集？",
        )
        text = format_result(result)
        self.assertIn("需要澄清", text)
        self.assertIn("您是想要查询还是采集？", text)

    def test_clarify_no_question(self):
        """clarify 无 question（fallback）。"""
        result = DispatchResult(
            success=True, intent="clarify", question=None,
        )
        text = format_result(result)
        self.assertIn("需要澄清", text)
        self.assertIn("请提供更多信息", text)

    # ── unsupported ─────────────────────────

    def test_unsupported_with_reason(self):
        """unsupported 带原因。"""
        result = DispatchResult(
            success=True, intent="unsupported",
            reason="仅支持查询和采集操作",
        )
        text = format_result(result)
        self.assertIn("暂不支持", text)
        self.assertIn("仅支持查询和采集操作", text)

    def test_unsupported_no_reason(self):
        """unsupported 无 reason（fallback）。"""
        result = DispatchResult(
            success=True, intent="unsupported", reason=None,
        )
        text = format_result(result)
        self.assertIn("暂不支持", text)
        self.assertIn("不支持", text)

    # ── 校验失败 ────────────────────────────

    def test_validation_failure(self):
        """校验失败。"""
        result = DispatchResult(
            success=False, intent="",
            error="非法 JSON: Expecting value",
            validation_errors=["非法 JSON: Expecting value"],
        )
        text = format_result(result)
        self.assertIn("校验失败", text)
        self.assertIn("非法 JSON", text)

    def test_validation_failure_no_error(self):
        """校验失败但 error 为 None。"""
        result = DispatchResult(
            success=False, intent="", error=None,
        )
        text = format_result(result)
        self.assertIn("校验失败", text)

    # ── 未知意图 ────────────────────────────

    def test_unknown_intent(self):
        """未知意图（防御性）。"""
        result = DispatchResult(
            success=False, intent="unknown_intent",
            error="未知意图: unknown_intent",
        )
        text = format_result(result)
        self.assertIn("未知错误", text)

    # ── 字段缺失容错 ────────────────────────

    def test_query_last_missing_accel(self):
        """query_last 观测缺少加速度字段。"""
        result = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": True, "source": "live", "seq": 50,
                "observed_ms": 1000, "time_quality": "relative",
            },
        )
        text = format_result(result)
        self.assertIn("加速度: 无数据", text)
        self.assertIn("观测序号: #50", text)

    def test_query_last_partial_accel(self):
        """query_last 观测部分加速度字段。"""
        result = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": True, "source": "live", "seq": 51,
                "accel_x": 5.0, "accel_z": 999.0,
            },
        )
        text = format_result(result)
        self.assertIn("X=5.0mg", text)
        self.assertIn("Y=N/A", text)
        self.assertIn("Z=999.0mg", text)

    def test_trigger_collect_missing_time(self):
        """trigger_collect 观测缺少时间字段。"""
        result = DispatchResult(
            success=True, intent="trigger_collect",
            request_id="req-00001-0001", collect_status="completed",
            collect_observation={
                "valid": True, "source": "live", "seq": 60,
                "accel_x": 1.0, "accel_y": 2.0, "accel_z": 1000.0,
            },
        )
        text = format_result(result)
        self.assertIn("采集成功", text)
        self.assertIn("req-00001-0001", text)

    def test_valid_with_button(self):
        """query_last 观测包含非 none 的按键状态。"""
        result = DispatchResult(
            success=True, intent="query_last",
            observation={
                "valid": True, "source": "live", "seq": 70,
                "accel_x": 0.0, "accel_y": 0.0, "accel_z": 100.0,
                "button": "MENU", "observed_ms": 5000,
            },
        )
        text = format_result(result)
        self.assertIn("按键状态: MENU", text)


if __name__ == "__main__":
    unittest.main()