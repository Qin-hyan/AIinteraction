"""Week 09 Dashboard 主动询问面板静态回归（只读内嵌页面）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from .test_dashboard_launcher_ui import _html, _javascript, _page


def test_event_panel_has_actions_and_traceability_fields() -> None:
    html = _html()
    for element_id in (
        "visionEventBox",
        "visionEventBadge",
        "visionEventMessage",
        "visionEventPriority",
        "visionEventRef",
        "visionEventSource",
        "visionEventHistory",
        "visionEventNote",
        "visionEventStatus",
        "veBtn-confirm",
        "veBtn-ignore",
        "veBtn-snooze",
    ):
        assert f'id="{element_id}"' in html, f"缺少 {element_id}"
    for action in ("confirm", "ignore", "snooze"):
        assert f"respondVisionEvent('{action}')" in html


def test_event_panel_states_execution_location_and_no_cloud() -> None:
    html = _html()
    assert "主动询问" in html
    assert "设备 OV2640" in html
    assert "PC 本地 8090 DBCNN" in html
    assert "不使用云端或 VPS" in html
    assert "忽略/稍后按「来源 + 原因」冷却" in html


def test_event_js_loads_on_init_and_after_analysis() -> None:
    js = _javascript()
    assert "function loadVisionEvents()" in js
    assert "fetch(VISION_URL+'/api/vision/events?limit=20')" in js
    assert "loadVisionHistory();loadVisionEvents();" in js
    assert "fs.textContent='';loadVisionEvents();}" in js
    assert "visionEventActive=active;" in js


def test_event_js_posts_response_and_renders_cooldown() -> None:
    js = _javascript()
    assert "fetch(VISION_URL+'/api/vision/events/respond'" in js
    assert "event_id:visionEventActive.event_id" in js
    assert "冷却至 '+ev.cooldown_until" in js
    assert "VISION_PRIORITY_ZH={high:'高',normal:'中',low:'低'}" in js
    assert "VISION_EVENT_STATE_ZH=" in js
    assert "VISION_EVENT_REASON_ZH=" in js


def test_event_panel_escapes_server_text() -> None:
    js = _javascript()
    assert "pcEsc((tr2.record_id||'—')+' / '+zhMap(VISION_EVENT_ACTION_ZH,last))" in js
    assert "pcEsc(zhMap(VISION_EVENT_REASON_ZH,e.reason_code))" in js


def test_event_panel_does_not_promise_human_verification() -> None:
    page = _page()
    assert "已人工验收" not in page
    assert "准确率" not in page or "不代表准确率" in page


def test_voice_panel_has_health_check() -> None:
    html = _html()
    js = _javascript()
    for element_id in ("voiceSvc", "voiceAsrModel", "voiceAudioCfg", "voiceHealthBtn"):
        assert f'id="{element_id}"' in html, f"缺少 {element_id}"
    assert 'onclick="voiceHealth()"' in html
    assert "fetch(VOICE_URL+'/api/voice/health')" in js
    assert "voicePollStart();voiceHealth();" in js
    assert "任务链路 " in js
    assert "TTS " in js


@pytest.mark.skipif(
    shutil.which("node") is None, reason="未安装 Node.js，跳过内嵌 JS 语法检查"
)
def test_embedded_javascript_passes_node_check(tmp_path: Path) -> None:
    script = tmp_path / "dashboard-week09.js"
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
