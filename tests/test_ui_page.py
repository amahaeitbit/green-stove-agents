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
    assert not re.search(r"<link[^>]+stylesheet", html, re.I)
    assert not re.search(r"<script[^>]+src=", html, re.I)


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
