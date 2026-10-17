"""Static checks on greenstove/ui/present.js, the presenter layer loaded by the single-page UI."""

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "greenstove" / "ui" / "present.js"


@pytest.fixture(scope="module")
def src() -> str:
    assert JS.is_file(), f"missing {JS}"
    return JS.read_text(encoding="utf-8")


def _strip_comments_and_strings(text: str) -> str:
    """Drop comments and string bodies so bracket counting sees only code."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            assert end >= 0, "unterminated block comment"
            i = end + 2
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                assert text[j] != "\n" or c == "`", f"unterminated string at offset {i}"
                j += 2 if text[j] == "\\" else 1
            assert j < n, f"unterminated string at offset {i}"
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _code(src: str) -> str:
    return _strip_comments_and_strings(src)


def test_file_exists_and_is_an_iife(src):
    code = _code(src).strip()
    assert code.startswith("(function(){"), "present.js must be one IIFE"
    assert code.endswith("})();")
    assert "'use strict'" in src


def test_brackets_balance(src):
    code = _code(src)
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for pos, ch in enumerate(code):
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            assert stack and stack[-1] == pairs[ch], f"unbalanced {ch!r} near code offset {pos}"
            stack.pop()
    assert not stack, f"unclosed {stack}"


def test_only_global_is_gspresenter(src):
    assigned = set(re.findall(r"window\.(\w+)\s*=(?!=)", src))
    assert assigned == {"fetch", "GSPresenter"}


def test_wraps_fetch_without_consuming_the_original(src):
    assert "var orig=window.fetch" in src and "window.fetch=wrapped" in src
    assert ".clone()" in src
    assert "copy.json()" in src
    assert not re.search(r"\bres\.(json|text|blob|arrayBuffer|formData)\(", src), "never read the page's body"
    assert "return res;" in src, "the original response is handed back to the caller"
    assert "throw err;" in src, "network errors still reach the caller"


def test_uses_text_content_never_html_sinks(src):
    assert "textContent" in src
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in src, f"{sink} is not allowed"


@pytest.mark.parametrize("hook", ["gs-presenter-toggle", "gs-hero", "gs-steps", "gs-step", "gs-tile",
                                  "gs-flash", "gs-kbd", "presenter", "done", "current"])
def test_defines_style_hooks(src, hook):
    assert f"'{hook}'" in src


def test_hero_states_and_tiles(src):
    for text in ("Ready · pick a scenario and press Run", "PLAN_VERIFIED", "NO_VERIFIED_PLAN", "INFEASIBLE",
                 "ERROR", "attempts", "model calls", "tokens", "cost (session)", "households booked",
                 "Decisions: ", "Forecast: ", "Keeper: "):
        assert text in src, text
    for kind in ("'ok'", "'bad'", "'warn'"):
        assert kind in src


def test_guide_steps_and_their_api_calls(src):
    for label in ("Run with memory off", "Run another day with curated memory", "Try a fabricated note",
                  "Ask a household agent", "Fetch a You.com forecast", "NemoClaw low-battery event"):
        assert label in src
    for path in ("/api/run", "/api/brain/note", "/api/household/ask", "/api/forecast", "/api/runner/drain",
                 "/api/tools", "/api/reset"):
        assert f"'{path}'" in src


def test_local_storage_only_inside_try_catch(src):
    lines = [ln for ln in src.splitlines() if "localStorage" in ln]
    assert lines, "presenter choice is remembered"
    for ln in lines:
        assert "try{" in ln and "catch(" in ln, ln


def test_keyboard_shortcuts_ignore_typing(src):
    assert "addEventListener('keydown'" in src
    for tag in ("'INPUT'", "'SELECT'", "'TEXTAREA'"):
        assert tag in src
    assert "#btn-run" in src and "togglePresenter" in src


def test_every_entry_point_is_guarded(src):
    assert src.count("try{") >= 25
    assert re.search(r"\}catch\(e\)\{\}\s*\}\)\(\);\s*$", src), "the whole IIFE body sits in one try/catch"
