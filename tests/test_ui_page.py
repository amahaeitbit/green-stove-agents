"""Static checks on the single-page UI served by greenstove.ui_server."""

import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "greenstove" / "ui" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert PAGE.is_file(), f"missing {PAGE}"
    return PAGE.read_text(encoding="utf-8")


def test_page_exists_and_is_one_document(html):
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<title>" in html and "</html>" in html


def test_no_external_resources(html):
    assert not re.search(r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//""", html, re.I)
    assert not re.search(r"""url\(\s*["']?\s*(?:https?:)?//""", html, re.I)
    assert "@import" not in html
    # the only linked files are the same-origin presenter layer the server serves from its allow-list
    links = re.findall(r"<link[^>]+stylesheet[^>]*>", html, re.I)
    scripts = re.findall(r"<script[^>]+src=[^>]*>", html, re.I)
    assert [re.search(r'href="([^"]+)"', x).group(1) for x in links] in ([], ["/present.css"])
    assert [re.search(r'src="([^"]+)"', x).group(1) for x in scripts] in ([], ["/present.js"])


@pytest.mark.parametrize("panel", ["panel-households", "panel-agent", "panel-brain", "panel-tools"])
def test_panels_and_tabs_present(html, panel):
    assert f'id="{panel}"' in html
    assert f'aria-controls="{panel}"' in html


def test_titles_and_controls(html):
    for text in ("Green-Stove Agents · interactive", "Run Green-Stove agent", "Reset session",
                 "Households", "Shared Brain", "Sponsor tools", "Ask household agent",
                 "Offer a note to the Keeper", "Try a fabricated note",
                 "Keeper decisions from the last run", "Crusoe Managed Inference",
                 "You.com", "NemoClaw / OpenShell", "Show live model catalog", "Fetch forecast",
                 "Use this forecast for the day", "Send day_start event",
                 "Send low-battery telemetry", "Process events", "Asmara, Eritrea"):
        assert text in html, text


@pytest.mark.parametrize("path", [
    "/api/state", "/api/scenario", "/api/household/update", "/api/household/ask", "/api/run",
    "/api/brain", "/api/brain/explain?id=", "/api/evidence", "/api/brain/note", "/api/reset",
    "/api/tools", "/api/crusoe/models", "/api/forecast", "/api/runner/event",
    "/api/runner/drain", "/api/runner/status",
])
def test_calls_each_api_path(html, path):
    assert f"'{path}'" in html, path


def test_run_can_send_use_forecast(html):
    assert "use_forecast" in html


def test_css_variables_defined_on_root(html):
    root = re.search(r":root\s*\{(.*?)\}", html, re.S)
    assert root
    for var in ("--c1", "--c2", "--c3", "--c4", "--limit", "--ink", "--muted", "--grid"):
        assert re.search(re.escape(var) + r"\s*:", root.group(1)), var


def test_dark_mode(html):
    assert re.search(r"prefers-color-scheme:\s*dark", html)


def test_phone_layout(html):
    assert "max-width:760px" in html.replace(" ", "")
    assert 'name="viewport"' in html


def test_inner_html_only_for_trusted_chart_svg(html):
    # Server text goes through textContent; the only innerHTML use parses chart SVG via <template>.
    assert html.count("innerHTML") == 1
    assert "createElement('template')" in html
    assert "textContent" in html


def test_top_slot_is_the_last_child_of_the_header(html):
    # present.js mounts its status pill and the Presenter toggle here.
    header = re.search(r"<header class=\"top\">(.*?)</header>", html, re.S)
    assert header
    assert re.search(r'<div id="top-slot"></div>\s*$', header.group(1))
    for hook in ('id="btn-run"', 'id="btn-reset"', 'id="sel-scenario"', 'id="sel-memory"'):
        assert hook in header.group(1), hook
    assert re.search(r"#top-slot\s*\{[^}]*display:\s*flex", html)
    assert re.search(r"min-width:\s*1100px", html), "one-row bar breakpoint"


def test_household_cards_collapse_with_an_expand_all_control(html):
    assert 'id="btn-hh-expand"' in html and "Expand all" in html and "Collapse all" in html
    assert "'hh-toggle'" in html and "'aria-expanded':open?'true':'false'" in html
    # present.js finds cards and buttons by these
    assert "'aria-label':'Household '+id" in html
    assert "'aria-label':'Ask the household agent for '+id" in html
    # keyboard focus survives a re-render: Save/Cancel return focus to the card's Edit button
    assert "'data-fk':'edit:'+id" in html and "renderHouseholds('edit:'+id)" in html
    assert "n.focus({preventScroll:true})" in html


def test_desktop_columns_fit_the_viewport(html):
    flat = html.replace(" ", "")
    assert "max-height:calc(100vh-var(--top-h" in flat
    assert "position:sticky;top:0" in flat
    assert "--top-h" in html and "ResizeObserver" in html


def test_seq_guard_against_stale_replies(html):
    assert "const S={seq:0," in html
    assert "const seq=++S.seq" in html, "scenario change bumps the sequence"
    assert "S.seq++" in html, "reset bumps the sequence"
    captures = html.count("const seq=S.seq")
    checks = html.count("seq!==S.seq")
    assert captures >= 10, captures
    assert checks >= captures, (captures, checks)
    for fn in ("async function loadScenario", "async function askHousehold", "async function saveHousehold",
               "async function runAgent", "async function refreshBrain", "async function refreshState",
               "async function refreshTools", "async function fetchForecast", "async function openExplain"):
        body = html[html.index(fn):]
        body = body[:body.index("\nasync function", 1) if "\nasync function" in body[1:] else len(body)]
        assert "seq!==S.seq" in body, fn


def test_tables_and_lists_wrap_instead_of_clipping(html):
    flat = html.replace(" ", "")
    assert "td{white-space:normal;overflow-wrap:anywhere}" in flat
    assert ".tw{overflow-x:auto" in flat
    assert ".entries>li{min-width:0" in flat
    # evidence options drop the current scenario's prefix and keep the full text in the title
    assert "r.scenario_id===S.scenarioName?rest:full,title:full" in html


def test_runner_status_never_renders_an_empty_value(html):
    assert "simple[k]='—'" in html
    assert "simple[k]=v==null?'—':v" in html
    assert "ks.length?ks.map(k=>k+': '+fmtAny(v[k])).join(' · '):'—'" in html
