"""静态回归：Dashboard 导航一致性与 PC 服务自动启动入口。

只读取 `main/http_server.c` 的内嵌页面，不启动设备/服务。
覆盖：
  1. 导航链接顺序与页面卡片顺序一致，无占位死链；
  2. 打开页面会调用本机启动器的幂等 ensure 接口；
  3. 服务状态面板、失败提示与轮询存在；
  4. 导航滚动高亮在真实浏览器语义下可用（scroll 监听 + aria-current）；
  5. 内嵌 JS 通过 `node --check`（未安装 Node.js 时跳过）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

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
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            out.append(_C_ESCAPES.get(body[index + 1], body[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _c_literals(region: str) -> list[str]:
    return [
        _decode_c_string(match.group(1))
        for match in re.finditer(r'"((?:[^"\\]|\\.)*)"', region)
    ]


@lru_cache(maxsize=1)
def _page() -> str:
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


def _nav_hrefs() -> list[str]:
    match = re.search(r'<nav class="topnav"[^>]*>(.*?)</nav>', _html(), re.S)
    assert match is not None, "未找到 topnav"
    return re.findall(r'href="#([^"]+)"', match.group(1))


def _card_ids() -> list[str]:
    return re.findall(r'<div class="card" id="([^"]+)"', _html())


def test_nav_order_matches_page_order() -> None:
    hrefs = _nav_hrefs()
    cards = _card_ids()

    assert hrefs == [
        "service-hub",
        "collect-console",
        "live-monitor",
        "camera-console",
        "auto-cap-panel",
        "gallery-panel",
        "voice-panel",
        "vision-panel",
        "evidence-log",
    ]
    assert cards[0] == "service-hub"
    for href in hrefs:
        assert href in cards, f"导航目标 #{href} 不存在"
    positions = [cards.index(href) for href in hrefs]
    assert positions == sorted(positions), "导航顺序与页面卡片顺序不一致"


def test_no_placeholder_or_dead_nav_link() -> None:
    nav = re.search(r'<nav class="topnav"[^>]*>(.*?)</nav>', _html(), re.S)
    assert nav is not None
    assert 'href="#"' not in nav.group(1)
    assert "外场与导出" not in nav.group(1)
    assert "外场与导出" not in _html()


def test_active_nav_follows_scroll() -> None:
    js = _javascript()
    assert "function syncNav()" in js
    assert "addEventListener('scroll',syncNav" in js
    assert "a.setAttribute('aria-current','page')" in js
    assert "scrollIntoView({behavior:'smooth',block:'start'})" in js
    assert "a.classList.toggle('act',on)" in js
    # 页面底部无法把最后一张卡滚到顶部时，仍应高亮最后一项
    assert "document.documentElement.scrollHeight-4" in js
    assert "links[links.length-1].getAttribute('href')" in js


def test_service_hub_calls_launcher_ensure_on_page_load() -> None:
    html = _html()
    js = _javascript()

    assert 'id="service-hub"' in html
    assert 'id="pcLauncherState"' in html
    assert 'id="pcSvcGrid"' in html
    assert 'var PC_LAUNCHER_URL="__PC_LAUNCHER_URL__";' in js
    assert "fetch(PC_LAUNCHER_URL+'/api/services/ensure'" in js
    assert "function pcEsc(v)" in js
    assert "pcEsc(s.detail||'')" in js
    assert "pcEnsureServices(false);pcPollStart();" in js
    assert "pcPollStart();visionHealth();" in js
    assert "setInterval(pcRefreshStatus,15000)" in js
    # 启动器比页面晚就绪时，轮询发现 stopped 会补一次 ensure
    assert "s.state==='stopped'" in js
    assert "pcEnsureServices(false);}}" in js


def test_service_failure_message_tells_user_how_to_start() -> None:
    js = _javascript()

    assert "浏览器不能自行拉起本机进程" in js
    assert "scripts/start_services.cmd" in js
    assert "python -m service.service_launcher" in js
    assert "pcSetNav('err','PC 服务未启动')" in js


def test_template_replacement_covers_all_three_placeholders() -> None:
    source = HTTP_SERVER_C.read_text(encoding="utf-8")
    assert "static const char *placeholders[3]" in source
    assert "const char *values[3]" in source
    # 计算长度与真正替换都必须遍历 3 个占位符；此前只改计算不改变换会让 URL 残留。
    assert source.count("for (int i = 0; i < 3; i++)") >= 2
    assert "__PC_LAUNCHER_URL__" in source


def test_launcher_url_is_loopback_placeholder_only() -> None:
    page = _page()
    assert "__PC_LAUNCHER_URL__" in page
    # 页面不写死任何私有/公网 IP（真实地址由固件 Kconfig 在运行时注入）
    assert re.search(r"\b(?:10|172|192)\.\d+\.\d+\.\d+\b", page) is None


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
