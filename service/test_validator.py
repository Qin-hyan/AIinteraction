"""
test_validator.py — Week 04 结构化任务契约校验单元测试

覆盖场景：
  1. 合法意图：query_last, trigger_collect, clarify, unsupported
  2. 歧义（clarify 缺少 question / unsupported 缺少 reason）
  3. 非法 JSON
  4. 未知意图
  5. 越界字段（额外字段、query_last 带设备地址等）
  6. 类型错误与边界值
  7. TaskContract 序列化/反序列化
  8. IntentEnum 辅助方法

不接入语言模型。不调用设备接口。
"""

import json
import unittest

from service.task_schema import IntentEnum, TaskContract
from service.validator import parse_and_validate


class TestParseAndValidate(unittest.TestCase):

    # ============ 合法意图 ============

    def test_query_last_valid(self):
        raw = json.dumps({
            "intent": "query_last",
            "confidence": 0.87,
            "original": "看看最新的数据",
        })
        result = parse_and_validate(raw)
        self.assertTrue(result.valid, msg=str(result.errors))
        self.assertIsNotNone(result.contract)
        self.assertEqual(result.contract.intent, "query_last")
        self.assertAlmostEqual(result.contract.confidence, 0.87)
        self.assertEqual(result.contract.original, "看看最新的数据")

    def test_trigger_collect_valid(self):
        raw = json.dumps({
            "intent": "trigger_collect",
            "confidence": 0.92,
            "original": "帮我采集一下",
        })
        result = parse_and_validate(raw)
        self.assertTrue(result.valid, msg=str(result.errors))
        self.assertEqual(result.contract.intent, "trigger_collect")
        self.assertIsNone(result.contract.question)
        self.assertIsNone(result.contract.reason)

    def test_clarify_valid(self):
        raw = json.dumps({
            "intent": "clarify",
            "confidence": 0.45,
            "original": "那个",
            "question": "你是指查询最近一次观测，还是重新采集？",
        })
        result = parse_and_validate(raw)
        self.assertTrue(result.valid, msg=str(result.errors))
        self.assertEqual(result.contract.intent, "clarify")
        self.assertEqual(result.contract.question,
                         "你是指查询最近一次观测，还是重新采集？")

    def test_unsupported_valid(self):
        raw = json.dumps({
            "intent": "unsupported",
            "confidence": 0.99,
            "original": "帮我写首诗",
            "reason": "当前仅支持查询和采集意图",
        })
        result = parse_and_validate(raw)
        self.assertTrue(result.valid, msg=str(result.errors))
        self.assertEqual(result.contract.intent, "unsupported")
        self.assertEqual(result.contract.reason, "当前仅支持查询和采集意图")

    # ============ 歧义（契约层拒绝） ============

    def test_clarify_missing_question(self):
        raw = json.dumps({
            "intent": "clarify",
            "confidence": 0.60,
            "original": "我不确定你说什么",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.contract)
        self.assertTrue(
            any("clarify" in e and "question" in e for e in result.errors),
        )

    def test_clarify_empty_question(self):
        raw = json.dumps({
            "intent": "clarify",
            "confidence": 0.50,
            "original": "嗯？",
            "question": "",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.contract)

    def test_unsupported_missing_reason(self):
        raw = json.dumps({
            "intent": "unsupported",
            "confidence": 0.90,
            "original": "我不理解",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.contract)
        self.assertTrue(
            any("unsupported" in e and "reason" in e for e in result.errors),
        )

    # ============ 非法 JSON ============

    def test_invalid_json(self):
        result = parse_and_validate("这不是 JSON 内容")
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("非法 JSON", result.errors[0])

    def test_empty_input(self):
        result = parse_and_validate("")
        self.assertFalse(result.valid)
        self.assertIn("非法 JSON", result.errors[0])

    # ============ 未知意图 ============

    def test_unknown_intent(self):
        raw = json.dumps({
            "intent": "turn_on_light",
            "confidence": 0.80,
            "original": "开灯",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertTrue(any("未知意图" in e for e in result.errors))
# ============ 额外字段（越界） ============

    def test_query_last_extra_field_device(self):
        raw = json.dumps({
            "intent": "query_last",
            "confidence": 0.85,
            "original": "查一下传感器",
            "device_address": "0x12",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("不允许的额外字段" in e or "不允许字段" in e
                for e in result.errors),
        )

    def test_trigger_collect_extra_field_interface(self):
        raw = json.dumps({
            "intent": "trigger_collect",
            "confidence": 0.91,
            "original": "采集数据",
            "interface": "I2C",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("不允许的额外字段" in e or "不允许字段" in e
                for e in result.errors),
        )

    def test_unsupported_extra_device_id(self):
        raw = json.dumps({
            "intent": "unsupported",
            "confidence": 0.70,
            "original": "xxxx",
            "reason": "无法处理",
            "device_id": "esp32s3-01",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertTrue(any("device_id" in e for e in result.errors))

    # ============ 类型错误与边界值 ============

    def test_confidence_out_of_range_above(self):
        raw = json.dumps({
            "intent": "query_last",
            "confidence": 1.5,
            "original": "查询",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)
        self.assertTrue(any("confidence" in e for e in result.errors))

    def test_confidence_out_of_range_below(self):
        raw = json.dumps({
            "intent": "query_last",
            "confidence": -0.1,
            "original": "查询",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)

    def test_original_empty_string(self):
        raw = json.dumps({
            "intent": "query_last",
            "confidence": 0.50,
            "original": "",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)

    def test_missing_intent_field(self):
        raw = json.dumps({
            "confidence": 0.80,
            "original": "测试",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)

    def test_missing_confidence_field(self):
        raw = json.dumps({
            "intent": "query_last",
            "original": "测试",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)

    def test_intent_not_string(self):
        raw = json.dumps({
            "intent": 123,
            "confidence": 0.80,
            "original": "测试",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)

    def test_confidence_not_numeric(self):
        raw = json.dumps({
            "intent": "query_last",
            "confidence": "high",
            "original": "测试",
        })
        result = parse_and_validate(raw)
        self.assertFalse(result.valid)

    # ============ TaskContract 数据类 ============

    def test_task_contract_to_dict_excludes_none(self):
        contract = TaskContract(
            intent="query_last", confidence=0.90, original="查询",
        )
        d = contract.to_dict()
        self.assertIn("intent", d)
        self.assertIn("confidence", d)
        self.assertIn("original", d)
        self.assertNotIn("question", d)
        self.assertNotIn("reason", d)

    def test_task_contract_to_json_roundtrip(self):
        contract = TaskContract(
            intent="clarify", confidence=0.40, original="什么意思",
            question="你是要查数据还是采集？",
        )
        s = contract.to_json()
        parsed = json.loads(s)
        self.assertEqual(parsed["intent"], "clarify")
        self.assertEqual(parsed["question"], "你是要查数据还是采集？")
        self.assertNotIn("reason", parsed)

    def test_task_contract_from_dict(self):
        d = {
            "intent": "unsupported",
            "confidence": 0.99,
            "original": "帮我做点别的",
            "reason": "功能不支持",
        }
        contract = TaskContract.from_dict(d)
        self.assertEqual(contract.intent, "unsupported")
        self.assertEqual(contract.reason, "功能不支持")
        self.assertIsNone(contract.question)


class TestIntentEnum(unittest.TestCase):

    def test_all_values_count(self):
        values = IntentEnum.all_values()
        self.assertEqual(len(values), 4)
        self.assertIn("query_last", values)
        self.assertIn("trigger_collect", values)
        self.assertIn("clarify", values)
        self.assertIn("unsupported", values)


if __name__ == "__main__":
    unittest.main(verbosity=2)