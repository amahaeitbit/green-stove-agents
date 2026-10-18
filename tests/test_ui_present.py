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


@pytest.mark.parametrize("hook", ["gs-presenter-toggle", "gs-mini", "gs-hero", "gs-steps", "gs-strip", "gs-step",
                                  "gs-tile", "gs-flash", "gs-kbd", "presenter", "done", "current"])
def test_defines_style_hooks(src, hook):
    assert f"'{hook}'" in src


def test_toggle_and_mini_mount_in_the_header(src):
    # #gs-mini then #gs-presenter-toggle go into #top-slot, else a .gs-slot row at the end of header.top, else body
    assert "q('#top-slot')" in src
    assert "'header.top'" in src
    assert "mk('div','gs-slot')" in src
    assert "host.appendChild(mini);" in src and "host.appendChild(toggleBtn);" in src
    assert src.index("host.appendChild(mini);") < src.index("host.appendChild(toggleBtn);")
    assert "document.body.appendChild(toggleBtn)" not in src, "no bottom-right button when a header exists"


def test_mini_status_follows_the_hero(src):
    assert "IntersectionObserver" in src
    assert "addEventListener('scroll'" in src, "scroll listener fallback"
    assert "mini.hidden=!hasRun()||heroSeen" in src, "hidden with no run or while the hero is in view"
    assert "renderMini();" in src.split("function renderHero(){")[1].split("function buildHero(){")[0], \
        "the mini re-renders with the hero"
    assert "' calls'" in src and "toFixed(4)" in src and "' · '" in src
    assert "info.body.scenario" in src, "scenario name comes from the /api/run request body"
    assert "'gs-scn'" in src


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
        assert f"label:'{label}'" in src, "full sentence kept for title and aria-label"
    for short in ("Memory off", "Curated memory", "Fake note", "Ask household", "You.com forecast", "NemoClaw event"):
        assert f"short:'{short}'" in src, "short visible label"
    assert "mk('span','gs-step-l',st.short)" in src
    assert "b.title=st.label;" in src
    assert "'Step '+(i+1)+': '+STEPS[i].label" in src
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


# ---------- present.css: the stylesheet the layer relies on ----------

CSS = JS.with_name("present.css")


@pytest.fixture(scope="module")
def css() -> str:
    assert CSS.is_file(), f"missing {CSS}"
    return CSS.read_text(encoding="utf-8")


def _rules(css: str):
    """(selector, body) pairs for every rule, including those nested in @media blocks."""
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    flat = re.sub(r"@media[^{]*\{", "", flat)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", flat)]


def test_css_braces_balance_and_colours_come_from_page_variables(css):
    assert css.count("{") == css.count("}")
    tokens = [ln for ln in css.splitlines() if "--muted:" in ln]
    assert len(tokens) == 2, "one light and one dark presenter token line"
    rest = "\n".join(ln for ln in css.splitlines() if "--muted:" not in ln)
    assert not re.search(r"(?<![\w-])#[0-9a-fA-F]{3,8}(?![\w-])", rest), "hex colours only in the token lines"
    for var in ("--ok", "--bad", "--warn", "--c4", "--ok-bg", "--bad-bg", "--warn-bg", "--panel", "--ink",
                "--muted", "--line", "--accent", "--accent-ink", "--focus"):
        assert f"var({var})" in css, var


def test_css_toggle_is_a_header_pill_with_fixed_fallback(css):
    rules = _rules(css)
    for sel, body in rules:
        if sel == "#gs-presenter-toggle":
            assert "position:fixed" not in body and "border-radius:999px" in body
    fixed = [body for sel, body in rules if sel == "body>#gs-presenter-toggle"]
    assert fixed and "position:fixed" in fixed[0] and "bottom:16px" in fixed[0]
    assert "body:has(>#gs-presenter-toggle) #toasts" in css
    assert ":where(#top-slot),.gs-slot{display:flex" in css


def test_css_mini_pill(css):
    rules = dict(_rules(css))
    assert "border-radius:999px" in rules["#gs-mini"] and "text-overflow:ellipsis" in rules["#gs-mini"]
    for tone, var in (("ok", "--ok"), ("bad", "--bad"), ("warn", "--warn"), ("purple", "--c4")):
        assert f"var({var})" in rules[f"#gs-mini.{tone}"]
    assert "display:none!important" in rules["#gs-mini[hidden],body:not(.presenter) #gs-mini"]


def test_css_steps_scroll_sideways_and_hero_is_compact(css):
    rules = _rules(css)
    strip = [body for sel, body in rules if sel == "body.presenter .gs-strip"]
    assert strip and "overflow-x:auto" in strip[0] and "flex-wrap:nowrap" in strip[0]
    step = [body for sel, body in rules if sel == "body.presenter .gs-step"]
    assert step and "white-space:nowrap" in step[0] and "min-height:44px" in step[0]
    assert "@media (min-width:900px)" in css
    hero = [body for sel, body in rules if sel == "body.presenter #gs-hero"]
    assert hero and "padding:10px 16px 10px 18px" in hero[0]
    assert "body.presenter #gs-hero .gs-word{min-width:0;font-size:clamp(24px,3.4vw,44px)" in css
    tiles = [body for sel, body in rules if sel == "body.presenter .gs-tile"]
    assert tiles and "calc(50% - 3px)" in tiles[-1], "two tiles per row on phones"
    assert "@media (max-width:760px)" in css and "prefers-color-scheme: dark" in css
