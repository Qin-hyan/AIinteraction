"""
test_dashboard_vision_ui.py — Week 08 W8-5: ESP32 Dashboard 视觉反馈面板中文化回归

范围：只静态检查固件内嵌页面（`main/http_server.c` 的 `INDEX_HTML` 字符串），
不启动设备、不启动服务、不访问网络，**不改变** API、底层枚举、模型算法与存储格式。

覆盖：
  1. 状态中文映射覆盖真实枚举值（decision / rule / verdict / source / reject_reason）；
  2. 未知状态安全回退：保留原值，不抛错、不猜测；
  3. 主区域为「图像质量分析结果」，主要分数四位小数，完整原始分数保留；
  4. 「查看详细信息」区域含完整分数 / 模型版本 / 图片来源 / 字节数 / SHA-256 / 记录 ID；
  5. 未校准说明：分数不代表准确率，阈值未校准时不能自动判定图片可用；
  6. 反馈保存与历史关联（保存后刷新历史，历史列按 verdict 中文映射）；
  7. 内嵌 JS 通过 `node --check`（未安装 Node.js 时跳过）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from .image_quality import IMAGE_STATUSES
from .vision_feedback import FEEDBACK_VERDICTS, SOURCE_DEVICE_GALLERY, SOURCE_LOCAL_UPLOAD
from .vision_result import DecisionEnum, RejectReason

REPO_ROOT = Path(__file__).resolve().parents[1]
HTTP_SERVER_C = REPO_ROOT / "main" / "http_server.c"

_C_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "'": "'",
    "\\": "\\",
}


def _decode_c_string(body: str) -> str:
    """把 C 字符串字面量的转义序列还原为真实字符。"""
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            following = body[index + 1]
            out.append(_C_ESCAPES.get(following, following))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _c_literals(region: str) -> list[str]:
    """按出现顺序提取区域内的 C 字符串字面量内容。"""
    return [
        _decode_c_string(match.group(1))
        for match in re.finditer(r'"((?:[^"\\]|\\.)*)"', region)
    ]


@lru_cache(maxsize=1)
def _page() -> str:
    """返回固件内嵌的完整页面（INDEX_HTML 的 C 相邻字面量拼接结果）。"""
    text = HTTP_SERVER_C.read_text(encoding="utf-8")
    start_marker = "static const char INDEX_HTML[] ="
    end_marker = '"init();</script></body></html>";'
    start = text.index(start_marker)
    end = text.index(end_marker) + len(end_marker)
    return "".join(_c_literals(text[start:end]))


@lru_cache(maxsize=1)
def _html() -> str:
    return _page().split("<script>", 1)[0]


@lru_cache(maxsize=1)
def _javascript() -> str:
    return _page().split("<script>", 1)[1].split("</script>", 1)[0]


def _js_map(name: str) -> dict[str, str]:
    match = re.search(
        r"var " + re.escape(name) + r"=\{([^}]*)\}", _javascript()
    )
    assert match is not None, f"未在内嵌 JS 中找到映射表 {name}"
    return dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'([^']*)'", match.group(1)))



# ──────────────────────────────────────────────
# 1. 状态中文映射（覆盖真实枚举值）
# ──────────────────────────────────────────────


def test_decision_labels_cover_contract_enum() -> None:
    labels = _js_map("VISION_DECISION_ZH")

    assert set(labels) == set(DecisionEnum.all_values())
    assert labels["needs_review"] == "待人工确认（未校准）"
    assert labels["ok"] == "可采纳（规则检查未发现明显问题）"
    assert labels["unknown"] == "无法判断"


def test_rule_labels_cover_image_statuses() -> None:
    labels = _js_map("VISION_RULE_ZH")

    assert set(labels) == set(IMAGE_STATUSES)
    assert labels["ok"] == "规则检查未发现明显问题"
    assert labels["low_detail"] == "画面细节不足"
    assert labels["invalid"] == "图像无效"


def test_verdict_labels_cover_feedback_enum() -> None:
    labels = _js_map("VISION_VERDICT_ZH")

    assert set(labels) == set(FEEDBACK_VERDICTS)
    assert labels == {"usable": "可用", "unusable": "不可用", "undetermined": "无法判断"}


def test_reject_reason_labels_cover_contract_enum() -> None:
    labels = _js_map("VISION_REASON_ZH")

    assert set(labels) == set(RejectReason.all_values())
    assert labels["threshold_not_configured"] == "阈值未配置"


def test_source_labels_cover_service_sources() -> None:
    labels = _js_map("VISION_SOURCE_ZH")

    assert labels == {
        SOURCE_LOCAL_UPLOAD: "本地图片",
        SOURCE_DEVICE_GALLERY: "设备照片",
    }


def test_unknown_values_fall_back_to_raw_text() -> None:
    js = _javascript()

    assert "function zhMap(map,v)" in js
    # 未命中映射时返回原值（不猜测、不抛错）
    assert "hasOwnProperty.call(map,v)?map[v]:String(v)" in js


def test_legacy_english_labels_are_no_longer_rendered() -> None:
    js = _javascript()

    assert "if(a.decision==='ok')return 'ok';" not in js
    assert "if(a.decision==='needs_review')return 'needs_review（未校准）';" not in js
    assert "return r.label||'—';" not in js


# ──────────────────────────────────────────────
# 2. 主区域：图像质量分析结果（四位小数 + 保留完整分数）
# ──────────────────────────────────────────────


def test_main_area_title_and_four_decimal_score() -> None:
    html = _html()
    js = _javascript()

    assert "<h4>图像质量分析结果</h4>" in html
    assert "function score4(v)" in js
    assert "v.toFixed(4)" in js
    assert "txt('visionScore',score4(a.score));" in js
    # 主区域只展示四位小数，完整原始分数单独保留
    assert "txt('visionScoreRaw',(a.score!=null?a.score:'—'));" in js
    assert "h+='<td>'+score4(a.score)+'</td>';" in js


def test_details_section_keeps_traceable_raw_values() -> None:
    html = _html()

    start = html.index('<details class="vision-details">')
    end = html.index("</details>", start)
    details = html[start:end]

    assert "查看详细信息" in details
    for element_id in (
        "visionScoreRaw",
        "visionModelVer",
        "visionSource",
        "visionBytes",
        "visionSha",
        "visionRecordIdDisplay",
    ):
        assert f'id="{element_id}"' in details, f"详细信息区域缺少 {element_id}"

    # 完整值写入：分数原值、SHA-256、记录 ID 不做截断
    js = _javascript()
    assert "txt('visionSha',ir.sha256||'—');" in js
    assert "txt('visionRecordIdDisplay',visionRecordId||'—');" in js
    assert "txt('visionBytes',(ir.bytes_len!=null?ir.bytes_len+' B':'—'));" in js


# ──────────────────────────────────────────────
# 3. 未校准说明（不得写成准确率，不得自动判定可用）
# ──────────────────────────────────────────────


def test_uncalibrated_wording_is_explicit() -> None:
    html = _html()

    assert "质量分数不代表准确率或正确概率" in html
    assert "阈值未校准，不能自动判定图片是否可用" in html
    assert "待人工确认（未校准）" in html


def test_health_status_is_translated_without_raw_enum() -> None:
    js = _javascript()

    assert "'待人工确认（未校准）':((th.status==='configured')?'已配置阈值':'—')" in js
    assert "'needs_review（未校准）'" not in js


# ──────────────────────────────────────────────
# 4. 反馈保存与历史关联
# ──────────────────────────────────────────────


def test_feedback_save_refreshes_linked_history() -> None:
    js = _javascript()

    # 反馈必须绑定当前分析记录 ID，提交后刷新同一批历史
    assert "visionRecordId=rec.record_id||null;" in js
    assert (
        "var payload={record_id:visionRecordId,verdict:visionVerdict,reason:reason,note:note};"
        in js
    )
    assert "反馈已保存（revision " in js
    assert "loadVisionHistory();})" in js

    # 历史列同样使用中文映射与四位小数
    assert "zhMap(VISION_VERDICT_ZH,fb.verdict)" in js
    assert "decisionLabel(a)" in js
    assert "ruleLabel(a.rule_diagnostics||{})" in js

    # 提交按钮保留真实 verdict 枚举值（不改底层契约）
    for verdict in FEEDBACK_VERDICTS:
        assert f"setVisionVerdict('{verdict}')" in _html()


# ──────────────────────────────────────────────
# 5. 内嵌 JS 语法（真实执行 node --check）
# ──────────────────────────────────────────────


@pytest.mark.skipif(
    shutil.which("node") is None, reason="未安装 Node.js，跳过内嵌 JS 语法检查"
)
def test_embedded_javascript_passes_node_check(tmp_path: Path) -> None:
    script = tmp_path / "dashboard.js"
    script.write_text(_javascript(), encoding="utf-8")

    result = subprocess.run(
        ["node", "--check", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


_JS_HELPER_HARNESS = """
const out = {
  decisions: {
    needs_review: decisionLabel({decision: 'needs_review'}),
    ok: decisionLabel({decision: 'ok'}),
    low_score: decisionLabel({decision: 'low_score'}),
    unknown: decisionLabel({decision: 'unknown', reject_reason: 'score_below_threshold'}),
    unknown_reason: decisionLabel({decision: 'unknown', reject_reason: 'future_reason'}),
    missing: decisionLabel({}),
  },
  rules: {
    ok: ruleLabel({available: true, label: 'ok'}),
    low_detail: ruleLabel({available: true, label: 'low_detail'}),
    unavailable: ruleLabel({available: false, label: 'ok'}),
    future: ruleLabel({available: true, label: 'future_status'}),
  },
  scores: [score4(0.34175166487693787), score4(0.5), score4(null)],
  verdict_future: zhMap(VISION_VERDICT_ZH, 'future_verdict'),
};
console.log(JSON.stringify(out));
"""


def _js_helpers() -> str:
    """截取纯函数片段（映射表 + zhMap/score4/decisionLabel/ruleLabel）。"""
    js = _javascript()
    start = js.index("var VISION_DECISION_ZH=")
    end = js.index("function renderVisionRecord(")
    return js[start:end]


@pytest.mark.skipif(
    shutil.which("node") is None, reason="未安装 Node.js，跳过内嵌 JS 行为检查"
)
def test_mapping_helpers_behave_in_node(tmp_path: Path) -> None:
    import json

    script = tmp_path / "vision_labels.js"
    script.write_text(_js_helpers() + _JS_HELPER_HARNESS, encoding="utf-8")

    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["decisions"] == {
        "needs_review": "待人工确认（未校准）",
        "ok": "可采纳（规则检查未发现明显问题）",
        "low_score": "低分（建议重新采集）",
        "unknown": "无法判断（分数低于阈值）",
        "unknown_reason": "无法判断（future_reason）",
        "missing": "—",
    }
    assert payload["rules"] == {
        "ok": "规则检查未发现明显问题",
        "low_detail": "画面细节不足",
        "unavailable": "规则检查不可用",
        "future": "future_status",
    }
    assert payload["scores"] == ["0.3418", "0.5000", "—"]
    assert payload["verdict_future"] == "future_verdict"
