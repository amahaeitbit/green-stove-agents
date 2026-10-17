"""Static HTML replay of one recorded demo run (`gsa replay`).

Reads the JSON snapshot `gsa demo` writes (<data_dir>/demo/demo-<mode>-<stamp>.json) and, optionally,
an end-to-end folder (data/e2e-*/), and renders ONE self-contained page that walks through the eight
demo acts. It explains what the agents did in that run; every number on the page comes from the run
records. No server, no network, no external CSS/JS/fonts.
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

BLOCKS = " ▁▂▃▄▅▆▇█"
ROOT = Path(__file__).resolve().parents[1]
_ABS = re.compile(r"(?:/Users|/home|/private|/tmp|/var|[A-Za-z]:\\\\?Users)[^\s'\"<>,;)\]]*")


# ---------------------------------------------------------------- text helpers

def _scrub(text: str) -> str:
    """Make any absolute path repo-relative (or drop it) so the page never leaks a local path."""
    def rel(m: re.Match) -> str:
        p = m.group(0)
        for marker in ("green-stove-agents/",):
            if marker in p:
                tail = p.split(marker, 1)[1]
                if tail.startswith(".claude/worktrees/"):
                    tail = tail.split("/", 3)[-1] if tail.count("/") >= 3 else ""
                return tail or "."
        return "<local path>"
    return _ABS.sub(rel, text)


def esc(x) -> str:
    return html.escape(_scrub("" if x is None else str(x)), quote=True)


def num(x, digits: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, (int, float)):
        return f"{x:.{digits}f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)
    return str(x)


def hhmm(step: int) -> str:
    step = int(step) % 96 if step != 96 else 96
    return f"{(step * 15) // 60:02d}:{(step * 15) % 60:02d}"


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{esc(text)}</span>'


def verdict(passed) -> str:
    if passed is None:
        return chip("-", "muted")
    return chip("PASS", "ok") if passed else chip("FAIL", "bad")


class Raw(str):
    """A table header that is already HTML (built only from escaped/constant parts)."""


def table(head: list[str], rows: list[list[str]], cls: str = "") -> str:
    """rows hold pre-escaped HTML cells."""
    th = "".join(f"<th>{h if isinstance(h, Raw) else esc(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    if not rows:
        body = f'<tr><td colspan="{len(head)}" class="muted">none recorded</td></tr>'
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def facts(pairs: list[tuple[str, str]]) -> str:
    """pairs hold (label, pre-escaped value)."""
    return '<dl class="facts">' + "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in pairs) + "</dl>"


def prices(p: dict | None) -> str:
    if not p:
        return chip("invalid", "bad")
    return " ".join(f'<code>{esc(k)}={esc(num(v))}</code>' for k, v in sorted(p.items()))


def tokens_of(run: dict | None) -> int:
    return sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in (run or {}).get("calls") or [])


def calls_of(run: dict | None) -> int:
    return len((run or {}).get("calls") or [])


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- markdown (tables, headings, paragraphs)

def _inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_table_rows(md: str) -> list[list[str]]:
    """Raw cell text of every markdown table row, separator rows dropped."""
    out = []
    for ln in (md or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        out.append(cells)
    return out


def md_to_html(md: str, base_level: int = 3) -> str:
    lines = (md or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                block.append(lines[j]); j += 1
            out.append(f"<pre class=\"code\">{esc(chr(10).join(block))}</pre>")
            i = j + 1
            continue
        if s.startswith("|"):
            j = i
            block = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j]); j += 1
            rows = md_table_rows("\n".join(block))
            if rows:
                head, body = rows[0], rows[1:]
                out.append(table(head, [[_inline(c) for c in r] for r in body]))
            i = j
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            lvl = min(6, base_level + len(m.group(1)) - 1)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
        elif s.startswith(("- ", "* ")):
            j = i
            items = []
            while j < len(lines) and lines[j].strip().startswith(("- ", "* ")):
                items.append(f"<li>{_inline(lines[j].strip()[2:])}</li>"); j += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            i = j
            continue
        elif s:
            out.append(f"<p>{_inline(s)}</p>")
        i += 1
    return "\n".join(out)


# ---------------------------------------------------------------- charts (replay_charts if present)

def _charts():
    import importlib
    try:
        return importlib.import_module("greenstove.replay_charts")  # optional chart module
    except ImportError:
        return None


def _spark_text(values: list[float], top: float | None = None) -> str:
    vals = [float(v or 0) for v in values or []]
    if len(vals) >= 48:
        vals = [sum(vals[i:i + 4]) / len(vals[i:i + 4]) for i in range(0, len(vals), 4)]
    top = top if top else (max(vals) if vals else 0)
    return "".join(BLOCKS[min(8, max(0, round(8 * v / top)))] if top > 0 else " " for v in vals)


def _fallback_lines(series: dict[str, list[float]], limits: dict | None, title: str, unit: str) -> str:
    rows = []
    for name, vals in series.items():
        if not vals:
            continue
        rows.append(f"{name[:22]:22} |{_spark_text(vals)}|  max {max(vals):.2f}")
    for name, v in (limits or {}).items():
        rows.append(f"{name[:22]:22}  limit {v:g}")
    return (f'<figure class="chart"><figcaption>{esc(title)} ({esc(unit)}, 00-24 h, hourly means)</figcaption>'
            f'<pre class="spark">{esc(chr(10).join(rows))}</pre></figure>')


def line_chart(series: dict[str, list[float]], *, limits=None, title: str = "", y_unit: str = "kW",
               emphasis=None) -> str:
    rc = _charts()
    if rc is not None:
        try:
            return f'<figure class="chart">{rc.line_chart(series, limits=limits, title=title, y_unit=y_unit, x_hours=True, emphasis=emphasis)}</figure>'
        except Exception:  # noqa: BLE001 - a chart bug must not break the replay
            pass
    return _fallback_lines(series, limits, title, y_unit)


def bar_chart(rows: list[tuple[str, float]], *, unit: str = "", highlight=None, title: str = "") -> str:
    rc = _charts()
    if rc is not None:
        try:
            return f'<figure class="chart">{rc.bars(rows, unit=unit, highlight=highlight, title=title)}</figure>'
        except Exception:  # noqa: BLE001
            pass
    top = max([v for _, v in rows] + [0]) or 1
    body = "".join(
        f'<div class="hbar"><span class="hl">{esc(lbl)}</span><span class="track"><span class="fill{" hi" if lbl == highlight else ""}" '
        f'style="width:{max(1.5, 100 * v / top):.1f}%"></span></span><span class="hv">{esc(num(v))} {esc(unit)}</span></div>'
        for lbl, v in rows)
    return f'<figure class="chart"><figcaption>{esc(title)}</figcaption>{body}</figure>'


# ---------------------------------------------------------------- sections

def _act(scene: str, body: str, takeaway: str | None = None) -> str:
    from greenstove.demo import ACTS
    n, title, question, default = ACTS[scene]
    tk = takeaway if takeaway is not None else default
    return (f'<section class="act" id="act-{esc(n)}" data-act="{esc(scene)}">'
            f'<header><span class="actno">Act {esc(n)}</span><h2>{esc(title)}</h2>'
            f'<p class="question">{esc(question)}</p></header>{body}'
            f'<p class="takeaway"><span aria-hidden="true">&#10003;</span> {esc(tk)}</p></section>')


def _missing(what: str) -> str:
    return f'<p class="muted">{esc(what)} was not recorded in this run.</p>'


def act_goal(snap: dict) -> str:
    spec = snap.get("spec") or {}
    if not spec:
        return _act("goal", _missing("The definition of done"))
    kinds = {"agent": "info", "code_tightened": "warn", "code_added": "accent"}
    rows = [[f"<code>{esc(c.get('metric'))} {esc(c.get('op'))} {esc(num(c.get('threshold')))}</code>",
             chip(c.get("source", "?"), kinds.get(c.get("source"), "")), esc(c.get("why"))]
            for c in spec.get("checks") or []]
    dropped = spec.get("dropped") or []
    body = (f'<blockquote class="goal">{esc(spec.get("goal") or snap.get("meta", {}).get("goal"))}</blockquote>'
            + (f'<p class="muted">Spec Agent restated it as: {esc(spec.get("goal_restated"))}</p>' if spec.get("goal_restated") else "")
            + f'<h3>Definition of done <span class="muted">· spec {esc(str(spec.get("spec_hash", ""))[:8])} · written by {esc(spec.get("model"))}</span></h3>'
            + table(["check the agents run without us", "source", "why"], rows)
            + (f'<p class="muted">Dropped by code: {esc(", ".join(map(str, dropped)))}</p>' if dropped else "")
            + '<p class="legend">' + chip("agent", "info") + " written by the Spec Agent · "
            + chip("code_tightened", "warn") + " the agent's threshold was loosened, code tightened it · "
            + chip("code_added", "accent") + " the agent missed a committee requirement, code added it</p>")
    return _act("goal", body)


def _window_bar(r: dict) -> str:
    e, lf = r.get("earliest_start", 0), r.get("latest_finish", 0)
    p, d = r.get("preferred_start", 0), r.get("duration_steps", 0)
    return (f'<span class="day" title="window {hhmm(e)}-{hhmm(lf)}, preferred {hhmm(p)}">'
            f'<span class="win" style="left:{100 * e / 96:.2f}%;width:{max(0.8, 100 * (lf - e) / 96):.2f}%"></span>'
            f'<span class="pref" style="left:{100 * p / 96:.2f}%;width:{max(0.8, 100 * d / 96):.2f}%"></span></span>')


def _tier_band(ctx: dict) -> str:
    lattice = ctx.get("lattice") or {}
    segs = "".join(
        f'<span class="tier t-{esc(w.get("name"))}" style="left:{100 * w.get("start_step", 0) / 96:.2f}%;'
        f'width:{100 * (w.get("end_step", 0) - w.get("start_step", 0)) / 96:.2f}%">{esc(w.get("name"))}</span>'
        for w in ctx.get("windows") or [])
    legend = " · ".join(f'{esc(w.get("name"))} {hhmm(w.get("start_step", 0))}-{hhmm(w.get("end_step", 0))} '
                        f'price options {esc(", ".join(num(x) for x in lattice.get(w.get("name"), [])))}'
                        for w in ctx.get("windows") or [])
    return f'<div class="band">{segs}</div><p class="muted small">{legend}</p>'


def _hours_axis() -> str:
    return '<span class="axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></span>'


def act_world(snap: dict) -> str:
    ctx = (snap.get("world") or {}).get("context") or (snap.get("proposal") or {}).get("context") or {}
    snap_world = (snap.get("meta") or {}).get("world", "")
    if not ctx:
        return _act("world", _missing("The day's context"))
    fpv = sum(ctx.get("forecast_pv_kw") or []) / 4
    dpv = sum(ctx.get("downside_pv_kw") or []) / 4
    station = facts([
        ("scenario", f"<code>{esc(ctx.get('scenario_id'))}</code>"),
        ("weather forecast", esc(ctx.get("weather_class"))),
        ("inverter limit", f"{esc(num(ctx.get('inverter_kw')))} kW"),
        ("battery", f"{esc(num(ctx.get('battery_capacity_kwh')))} kWh, starts {esc(num(ctx.get('initial_battery_kwh')))}"),
        (("battery floor" if "admission" in str(snap_world) else "cooking reserve"),
         f"{esc(num(ctx.get('reserve_kwh')))} kWh"),
        ("forecast PV", f"{fpv:.2f} kWh (downside {dpv:.2f})"),
    ])
    rows = [[f"<code>{esc(r.get('household_id'))}</code>", esc(num(r.get("power_kw"))),
             esc(int(r.get("duration_steps", 0)) * 15), esc(hhmm(r.get("preferred_start", 0))),
             f"{esc(hhmm(r.get('earliest_start', 0)))}-{esc(hhmm(r.get('latest_finish', 0)))}",
             _window_bar(r), esc(num(r.get("max_cost"))),
             chip("auto-book", "ok") if r.get("auto_book") else chip("asks first", "muted")]
            for r in ctx.get("requests") or []]
    assumptions = "".join(f"<li>{esc(a)}</li>" for a in ctx.get("assumptions") or [])
    body = (station + "<h3>Price windows</h3>" + _tier_band(ctx)
            + f"<h3>Households ({len(ctx.get('requests') or [])} cooking requests)</h3>"
            + table(["household", "kW", "min", "preferred", "window", Raw(f"window across the day {_hours_axis()}"),
                     "budget", "booking"], rows, "hh")
            + '<p class="legend"><span class="sw win"></span> acceptable window <span class="sw pref"></span> preferred slot</p>'
            + (f'<ul class="assume">{assumptions}</ul>' if assumptions else ""))
    return _act("world", body)


def _decision_trail(run: dict) -> str:
    ds = run.get("decisions") or []
    if not ds:
        return ""
    kinds = {"continue": "ok", "re-plan": "warn", "stop": "bad", "ping_human": "accent"}
    trail = ' <span class="arrow">&rarr;</span> '.join(chip(d.get("decision"), kinds.get(d.get("decision"), "")) for d in ds)
    reasons = "".join(f"<li><b>attempt {esc(d.get('attempt'))} · {esc(d.get('decision'))}</b>: {esc(d.get('reason'))}</li>"
                      for d in ds)
    return f'<h3>Supervisor decisions</h3><p class="trail">{trail}</p><ol class="reasons">{reasons}</ol>'


def _status_line(run: dict) -> str:
    st = run.get("status") or "-"
    kind = "ok" if st == "PLAN_VERIFIED" else "bad"
    ex = run.get("execution") or {}
    exec_s = (f" · {chip('EXECUTION_COMPLETE', 'info')} on actual simulated weather: outcome {verdict(ex.get('outcome_passed'))}"
              if ex else "")
    return (f'<p class="status">{chip(st, kind)} {esc(run.get("reason"))} · {esc(plural(calls_of(run), "model call"))} · '
            f'{esc(f"{tokens_of(run):,}")} tokens · {esc(plural(run.get("candidate_evaluations") or 0, "candidate check"))} · '
            f'{esc(run.get("simulator_runs", 0))} simulator runs{exec_s}</p>')


def _attempts(run: dict) -> str:
    rows = []
    for a in run.get("attempts") or []:
        p = a.get("proposal") or {}
        fb = a.get("feedback") or a.get("validation_errors") or []
        v = verdict(a.get("passed")) if a.get("evidence_ids") else chip("not checked", "muted")
        rows.append([esc(a.get("n")), prices(p.get("prices")),
                     esc(", ".join(p.get("cited_observation_ids") or [])) or '<span class="muted">-</span>',
                     v, esc(len(a.get("evidence_ids") or [])),
                     " ".join(chip(f, "bad") for f in fb) or '<span class="muted">-</span>',
                     f'<span class="small">{esc(p.get("rationale"))}</span>'])
    return table(["#", "prices", "cited memory", "verifier: expected + downside", "evidence", "feedback codes",
                  "agent's rationale"], rows)


def _bookings(plan: dict | None, ctx: dict) -> str:
    if not plan:
        return _missing("A booked plan")
    who = {q.get("request_id"): q.get("household_id") for q in ctx.get("requests") or []}
    rows = [[f"<code>{esc(who.get(r.get('request_id'), r.get('request_id')))}</code>", esc(hhmm(r.get("start_step", 0))),
             esc(hhmm(r.get("end_step", 0))), esc(num(r.get("power_kw"))), esc(num(r.get("locked_cost"))),
             chip("accepted", "ok") if r.get("accepted") else chip("pending", "warn")]
            for r in plan.get("reservations") or []]
    rows += [[f"<code>{esc(u.get('request_id'))}</code>", "-", "-", "-", "-", chip(f"unserved: {u.get('reason')}", "bad")]
             for u in plan.get("unserved") or []]
    return (f"<h3>Bookings under tariff {prices(plan.get('tariff'))}</h3>"
            + table(["household", "start", "end", "kW", "locked cost", "status"], rows))


def _trace_chart(traces: dict, ctx: dict) -> str:
    tr, label = traces.get("forecast_downside"), "downside forecast"
    if not tr:
        tr, label = traces.get("forecast_expected"), "expected forecast; downside not modeled"
    if not tr:
        return _missing("A simulator trace")
    series = {"PV (kW)": tr.get("pv_kw") or [], "demand (kW)": tr.get("demand_kw") or []}
    if tr.get("battery_kwh"):
        series["battery (kWh)"] = tr["battery_kwh"]
    floor_world = "admission" in str(tr.get("simulator_version") or "")    # Abel's ledger: a hard floor
    reserve_label = (f"battery floor {num(ctx.get('reserve_kwh'))} kWh" if floor_world else
                     f"cooking reserve {num(ctx.get('reserve_kwh'))} kWh")
    limits = {f"inverter {num(ctx.get('inverter_kw'))} kW": ctx.get("inverter_kw"),
              reserve_label: ctx.get("reserve_kwh")}
    limits = {k: float(v) for k, v in limits.items() if v is not None}
    note = "" if tr.get("battery_kwh") else '<p class="muted">battery: not modeled by this world</p>'
    if tr.get("battery_kwh") and not floor_world:
        note += ('<p class="muted small">Stand-in world: below the cooking reserve no new cooking may start; '
                 'the essential load may still draw on the battery at night. Abel\'s admission world uses a hard '
                 'floor instead.</p>')
    mb = f" · battery minimum {min(tr['battery_kwh']):.2f} kWh" if tr.get("battery_kwh") else ""
    return (line_chart(series, limits=limits, title=f"Selected plan on the {label}", y_unit="kW / kWh",
                       emphasis="demand (kW)")
            + f'<p class="muted small">Simulator {esc(tr.get("simulator_version"))} · overload steps '
              f'{len(tr.get("overload_steps") or [])}{mb}</p>' + note)


def act_proposal(snap: dict) -> str:
    pr = snap.get("proposal") or {}
    run, ctx, traces = pr.get("run") or {}, pr.get("context") or {}, pr.get("traces") or {}
    if not run:
        return _act("proposal", _missing("The proposal run"))
    body = (f'<p>A Pricing Agent with no memory proposes window prices; the verifier simulates each on the expected and '
            f'the downside forecast and returns reason codes. Scenario <code>{esc(run.get("scenario_id"))}</code>.</p>'
            + _attempts(run) + _decision_trail(run) + _status_line(run)
            + _trace_chart(traces, ctx) + _bookings(traces.get("plan"), ctx))
    return _act("proposal", body)


def act_memory(snap: dict) -> str:
    mem = snap.get("memory") or {}
    if not mem:
        return _act("memory", _missing("The Shared Brain"))
    note_rows = [[chip(n.get("action"), "ok" if n.get("action") == "added" else "warn"),
                  f"<code>{esc(n.get('observation_id') or '-')}</code>", esc("; ".join(n.get("reasons") or [])) or "-"]
                 for n in mem.get("notes") or []]
    fab = mem.get("fabricated") or {}
    fab_html = ""
    if fab:
        fab_html = (f'<div class="card bad-card"><div class="card-h">Fabricated note offered to the Keeper '
                    f'{chip(fab.get("action", "?"), "bad" if fab.get("action") == "rejected" else "warn")}'
                    f'{chip(fab.get("code") or "-", "muted")}</div>'
                    f'<blockquote>{esc(fab.get("description"))}</blockquote>'
                    f'<p>Keeper reasons: {" ".join(chip(r, "bad") for r in fab.get("reasons") or []) or "-"}</p></div>')
    ent = []
    for e in mem.get("entries") or []:
        sit = e.get("situation") or {}
        sit_s = " · ".join(f"{k} {num(v)}" for k, v in sit.items())
        ent.append(f'<li class="entry"><div><code>{esc(e.get("id"))}</code> v{esc(e.get("version"))} '
                   f'{verdict(e.get("outcome_passed"))} {chip(e.get("phase") or "-", "muted")} {prices(e.get("tariff"))} '
                   f'{" ".join(chip(c, "bad") for c in e.get("feedback_codes") or [])}</div>'
                   f'<div class="small muted">{esc(sit_s)}</div><div class="small">{esc(e.get("description"))}</div></li>')
    body = ("<h3>Keeper decisions on the notes written after Act 3</h3>"
            + table(["keeper decision", "entry", "reasons"], note_rows) + fab_html
            + f'<h3>Shared Brain entries <span class="muted">· snapshot {esc(str(mem.get("snapshot", ""))[:8])}</span></h3>'
            + (f'<ul class="entries">{"".join(ent)}</ul>' if ent else _missing("A brain entry")))
    tk = None
    if fab and fab.get("action") != "rejected":
        tk = f"The fabricated note was {fab.get('action')}, not rejected; this run does not show the Keeper stopping it."
    elif not fab:
        tk = "Shared Brain: the Keeper rebuilds every fact from evidence (no fabricated-note test was recorded in this run)."
    return _act("memory", body, tk)


def handoff_takeaway(off: dict, cur: dict, cited: list[str]) -> str:
    oc, cc = calls_of(off), calls_of(cur)
    read = len(((cur.get("brief") or {}).get("entry_ids")) or [])
    cite = f"cited {', '.join(cited)}" if cited else "cited no entry"
    if cc < oc:
        tail = f"and needed fewer model calls ({oc} → {cc})"
    elif cc == oc:
        tail = f"and needed the same number of model calls ({oc}); no saving on this day"
    else:
        tail = f"and needed more model calls ({oc} → {cc}); no saving on this day"
    entries = "1 memory entry" if read == 1 else f"{read} memory entries"
    return (f"Shared Brain: a new agent read {entries}, {cite}, {tail}. "
            "One example; the held-out table is the evidence.")


def act_handoff(snap: dict) -> str:
    h = snap.get("handoff") or {}
    off, cur = h.get("off") or {}, h.get("curated") or {}
    if not off or not cur:
        return _act("handoff", _missing("The handoff comparison"), "The handoff was not recorded in this run.")
    brief = cur.get("brief") or {}
    cited = sorted({c for a in cur.get("attempts") or [] for c in ((a.get("proposal") or {}).get("cited_observation_ids") or [])})
    supplied = " ".join(f"<code>{esc(i)}</code>" for i in brief.get("entry_ids") or []) or "none"
    body = (f'<p>Same day (<code>{esc(cur.get("scenario_id"))}</code>), two fresh agents: one with no memory, one that '
            f'reads the Shared Brain brief first.</p>'
            + facts([("brief entries supplied", supplied),
                     ("brief", f"{esc(brief.get('mode'))} · {esc(brief.get('tokens'))}/{esc(brief.get('budget'))} tokens"),
                     ("cited by the agent", " ".join(f"<code>{esc(c)}</code>" for c in cited) or "none")])
            + (f'<details><summary>Brief text the agent read</summary><pre class="code">{esc(brief.get("text"))}</pre></details>'
               if brief.get("text") else "")
            + '<div class="two">'
            + bar_chart([("No memory", calls_of(off)), ("Shared Brain", calls_of(cur))], unit="",
                        highlight="Shared Brain", title="Model calls")
            + bar_chart([("No memory", tokens_of(off)), ("Shared Brain", tokens_of(cur))], unit="tokens",
                        highlight="Shared Brain", title="Tokens")
            + "</div>"
            + table(["agent", "status", "attempts", "model calls", "tokens"],
                    [[esc(lbl), chip(r.get("status") or "-", "ok" if r.get("status") == "PLAN_VERIFIED" else "bad"),
                      esc(len(r.get("attempts") or [])), esc(calls_of(r)), esc(f"{tokens_of(r):,}")]
                     for lbl, r in (("No memory", off), ("Shared Brain", cur))]))
    return _act("handoff", body, handoff_takeaway(off, cur, cited))


def act_tomorrow(snap: dict) -> str:
    t = snap.get("tomorrow") or {}
    f = t.get("forecast") or {}
    if not f:
        return _act("tomorrow", _missing("A real-world forecast"), "No forecast was recorded in this run.")
    src = (f.get("sources") or [{}])[0] if f.get("sources") else {}
    acc = f.get("accepted") or {}
    card = (f'<div class="card">' + facts([
        ("place", esc(f.get("place"))), ("date", esc(f.get("date"))),
        ("weather class", esc(f.get("weather_class"))),
        ("PV", f"{esc(num(f.get('pv_kwh')))} kWh"), ("downside PV", f"{esc(num(f.get('downside_pv_kwh')))} kWh"),
        ("status", chip(f.get("status") or "-", "ok" if f.get("status") == "VERIFIED" else "bad")),
        ("source", f"{esc(src.get('title'))} <span class=\"muted small\">{esc(src.get('url'))}</span>" if src else "-"),
        ("search / extractor", f"{chip(f.get('search_mode') or '-', 'warn' if f.get('search_mode') == 'mock' else 'ok')} "
                               f"{chip(f.get('extractor_mode') or '-', 'warn' if f.get('extractor_mode') == 'mock' else 'ok')}"),
    ]) + (f'<p>Quote checked by code: <q>{esc(acc.get("quote"))}</q> · {esc(acc.get("condition"))}</p>' if acc else "")
        + "</div>")
    chart = ""
    if f.get("forecast_pv_kw"):
        chart = line_chart({"forecast PV (kW)": f.get("forecast_pv_kw") or [], "downside PV (kW)": f.get("downside_pv_kw") or []},
                           title=f"Solar profile for {f.get('place', '')} {f.get('date', '')}", y_unit="kW",
                           emphasis="forecast PV (kW)")
    assumptions = "".join(f"<li>{esc(a)}</li>" for a in f.get("assumptions") or [])
    run = t.get("run") or {}
    body = (card + chart + (f'<ul class="assume">{assumptions}</ul>' if assumptions else "")
            + ("<h3>Tomorrow's plan</h3>" + _status_line(run) + _decision_trail(run) if run else _missing("Tomorrow's plan")))
    return _act("tomorrow", body, tomorrow_takeaway(f, run))


def tomorrow_takeaway(f: dict, run: dict) -> str:
    """ACTS' default names You.com and Nemotron; say so only when this run actually used them."""
    search = "You.com found the forecast" if f.get("search_mode") == "live" else \
        "a mock search result stood in for You.com"
    extract = "Nemotron extracted it" if f.get("extractor_mode") not in (None, "mock") else \
        "a mock extractor stood in for Nemotron"
    checked = "code checked the quote" if f.get("status") == "VERIFIED" else f"the forecast status is {f.get('status')}"
    if not run:
        plan = "no plan for tomorrow was recorded"
    else:
        who = "a fresh agent with the Shared Brain" if run.get("memory_mode") == "curated" else "a fresh agent"
        plan = (f"{who} planned tomorrow on it ({run.get('status')})" if run.get("status") == "PLAN_VERIFIED"
                else f"{who} did not verify a plan for tomorrow ({run.get('status')})")
    return f"{search[0].upper()}{search[1:]}, {extract}, {checked}, and {plan}."


def act_infeasible(snap: dict) -> str:
    inf = snap.get("infeasible") or {}
    run = inf.get("run") or {}
    proof = run.get("infeasibility") or {}
    if not run:
        return _act("infeasible", _missing("The infeasible day"))
    body = (f'<p>Scenario <code>{esc(run.get("scenario_id"))}</code>.</p>'
            + (f'<div class="proof"><div><span class="big">&ge; {esc(num(proof.get("lower_bound_kwh"), 3))}</span> kWh needed</div>'
               f'<div class="vs">vs</div><div><span class="big">&le; {esc(num(proof.get("available_kwh"), 3))}</span> kWh available</div></div>'
               if proof else _missing("An energy bound"))
            + (f'<ul class="assume">{"".join(f"<li>{esc(a)}</li>" for a in proof.get("assumptions") or [])}</ul>' if proof else "")
            + _status_line(run) + _decision_trail(run))
    pinged = any(d.get("decision") == "ping_human" for d in run.get("decisions") or [])
    tk = None
    if run.get("status") != "INFEASIBLE" or calls_of(run) or not pinged:
        tk = (f"Recorded outcome: {run.get('status')} after {plural(calls_of(run), 'model call')}"
              f"{', human pinged' if pinged else ', no human ping recorded'}.")
    return _act("infeasible", body, tk)


def act_improve(snap: dict) -> str:
    imp = snap.get("improve") or {}
    idx = imp.get("index") or {}
    versions = idx.get("versions") or []
    if not versions and not imp.get("before_after_md"):
        return _act("improve", _missing("Version N+1 history"), "Version N+1 was not run before this demo.")
    vrows = [[f"<code>{esc(v.get('id'))}</code>", esc(v.get("parent") or "-"), esc(v.get("author")),
              chip(v.get("status") or "-", "ok" if v.get("status") == "active" else "muted"),
              esc(v.get("chars")), esc(v.get("reason"))] for v in versions]
    ba = imp.get("before_after_md") or ""
    rows = md_table_rows(ba)
    ba_html = table(rows[0], [[_inline(c) for c in r] for r in rows[1:]]) if rows else _missing("A before/after table")
    held = [ln for ln in ba.splitlines() if ln.startswith("Held-out check")]
    code = re.search(r"```\n(.*?)```", ba, re.S)
    body = (f'<h3>Playbook versions <span class="muted">· active {esc(idx.get("active"))}</span></h3>'
            + table(["version", "parent", "author", "status", "chars", "keep / reject decision"], vrows)
            + "<h3>Before / after on held-out days</h3>" + ba_html
            + "".join(f"<p>{_inline(h)}</p>" for h in held)
            + (f'<p class="muted small">Final active playbook</p><pre class="code">{esc(code.group(1).strip())}</pre>' if code else ""))
    return _act("improve", body)


def results_section(e2e_root: Path | None) -> str:
    if not e2e_root:
        return ""
    e2e_root = Path(e2e_root)
    parts = []
    rt = e2e_root / "compare" / "results-table.md"
    if rt.exists():
        parts.append(md_to_html(rt.read_text(encoding="utf-8"), base_level=3))
    sc = e2e_root / "tracks" / "tracks-scorecard.json"
    if sc.exists():
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
            ok = data.get("passed", 0) == data.get("total", -1)
            rows = [[esc(r.get("id")), esc(r.get("track")), esc(r.get("requirement")),
                     chip(r.get("result") or "-", "ok" if r.get("result") == "PASS" else "bad")]
                    for r in data.get("rows") or []]
            parts.append(f'<h3>Track scorecard {chip(f"{data.get("passed")}/{data.get("total")}", "ok" if ok else "bad")}</h3>'
                         f'<p class="muted">{esc(data.get("header"))}</p>'
                         f'<details><summary>{len(rows)} requirement checks</summary>'
                         + table(["#", "track", "requirement", "result"], rows) + "</details>")
        except (ValueError, OSError):
            pass
    summ = e2e_root / "e2e-summary.md"
    if summ.exists():
        for ln in summ.read_text(encoding="utf-8").splitlines():
            if ln.startswith("World coverage"):
                parts.append(f'<p class="coverage">{esc(ln)}</p>')
    if not parts:
        return ""
    rel = _scrub(str(e2e_root.resolve().relative_to(ROOT) if e2e_root.resolve().is_relative_to(ROOT) else e2e_root.name))
    return (f'<section class="act results" id="results"><header><span class="actno">Evidence</span><h2>Results</h2>'
            f'<p class="question">What do the held-out runs say, beyond this one replay?</p></header>'
            f'<p class="muted small">From <code>{esc(rel)}</code></p>{"".join(parts)}</section>')


def receipt_section(snap: dict) -> str:
    rc = snap.get("receipt") or {}
    rows = [[esc(r[0] if len(r) > 0 else ""), esc(r[1] if len(r) > 1 else ""), esc(r[2] if len(r) > 2 else "")]
            for r in rc.get("rows") or []]
    cost = rc.get("cost_usd")
    return (f'<section class="act receipt" id="receipt"><header><span class="actno">Receipt</span>'
            f'<h2>What the run proved</h2><p class="question">Each track requirement, and what this run recorded for it.</p></header>'
            + table(["track", "requirement", "what this run shows"], rows)
            + f'<p class="muted">{esc(plural(rc.get("calls") or 0, "model call"))} · {esc(f"{rc.get("tokens") or 0:,}")} tokens · '
              f'cost {esc("$" + format(cost, ".4f") if isinstance(cost, (int, float)) else "not reported")}</p></section>')


# ---------------------------------------------------------------- page

CSS = """
:root{--bg:#f7f8f6;--panel:#fff;--ink:#1c2321;--muted:#5f6b67;--line:#dfe4e1;--grid:#e8ece9;
--accent:#1f7a55;--c1:#d48a00;--c2:#2f6fb5;--c3:#1f7a55;--c4:#9a4fb0;--limit:#c0392b;
--ok-bg:#e3f4ea;--ok:#17633f;--bad-bg:#fbe6e3;--bad:#a8321f;--warn-bg:#fdf1d6;--warn:#8a5a00;
--info-bg:#e4eefa;--info:#1f5592;--acc-bg:#f1e6f6;--acc:#743d87;--mut-bg:#eef1ef;--code:#f0f3f1}
@media (prefers-color-scheme:dark){:root{--bg:#121614;--panel:#1a201d;--ink:#e6ebe8;--muted:#9aa7a2;--line:#2c3531;
--grid:#26302b;--accent:#5cc394;--c1:#f0ad3a;--c2:#6fa8e8;--c3:#5cc394;--c4:#c48ad6;--limit:#ff7a66;
--ok-bg:#163a28;--ok:#8fe0b5;--bad-bg:#3d1d18;--bad:#ffab9c;--warn-bg:#3a2e12;--warn:#f4cf7d;--info-bg:#172c45;
--info:#a9cdf6;--acc-bg:#33203b;--acc:#ddb2ec;--mut-bg:#232a27;--code:#222a26}}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:56px}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:1000px;margin:0 auto;padding:0 16px 48px}
header.top{padding:28px 0 12px}
header.top h1{margin:0 0 6px;font-size:26px;letter-spacing:-.01em}
.meta{color:var(--muted);font-size:13px}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.banner{background:var(--warn-bg);color:var(--warn);border:1px solid var(--line);border-radius:10px;padding:10px 14px;
margin:12px 0;font-size:14px}
.banner p{margin:2px 0}
nav.acts{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
display:flex;gap:4px;overflow-x:auto;padding:8px 0;scrollbar-width:thin}
nav.acts a{flex:0 0 auto;text-decoration:none;color:var(--ink);font-size:13px;padding:4px 10px;border-radius:999px;
border:1px solid var(--line);background:var(--panel);white-space:nowrap}
nav.acts a:hover,nav.acts a:focus{border-color:var(--accent);color:var(--accent)}
section.act{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 18px 8px;margin:18px 0}
section.act>header{margin-bottom:10px}
.actno{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--accent)}
section.act h2{margin:2px 0 4px;font-size:21px}
.question{margin:0;color:var(--muted);font-style:italic}
h3{font-size:15px;margin:18px 0 8px}
.takeaway{border-top:1px solid var(--line);margin:16px -18px 0;padding:12px 18px;color:var(--ok);font-weight:600}
.tw{overflow-x:auto;margin:8px 0;border:1px solid var(--line);border-radius:10px;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--mut-bg);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:0}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:1px 5px;border-radius:5px;
white-space:nowrap}
pre{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);border-radius:10px;
padding:10px 12px;overflow-x:auto;margin:8px 0}
pre.code{white-space:pre-wrap;word-break:break-word}
.chip{display:inline-block;font-size:11.5px;font-weight:600;padding:1px 8px;border-radius:999px;background:var(--mut-bg);
color:var(--muted);white-space:nowrap;margin:1px 0;letter-spacing:.02em}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.bad{background:var(--bad-bg);color:var(--bad)}
.chip.warn{background:var(--warn-bg);color:var(--warn)}.chip.info{background:var(--info-bg);color:var(--info)}
.chip.accent{background:var(--acc-bg);color:var(--acc)}
.badge{font-size:12px;padding:3px 10px}
.muted{color:var(--muted)}.small{font-size:12.5px}
.legend{font-size:12.5px;color:var(--muted)}
blockquote{margin:8px 0;padding:8px 14px;border-left:3px solid var(--accent);background:var(--mut-bg);border-radius:0 8px 8px 0}
blockquote.goal{font-size:16px}
dl.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin:8px 0}
dl.facts div{background:var(--mut-bg);border-radius:10px;padding:8px 10px;min-width:0;overflow-wrap:anywhere}
dt{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
dd{margin:2px 0 0;font-weight:600}
.day{position:relative;display:block;min-width:140px;height:12px;background:var(--grid);border-radius:6px}
.day .win{position:absolute;top:0;bottom:0;background:var(--c2);opacity:.35;border-radius:6px}
.day .pref{position:absolute;top:2px;bottom:2px;background:var(--c1);border-radius:4px}
.axis{display:flex;justify-content:space-between;font-weight:400;font-size:10.5px;color:var(--muted);min-width:140px}
.sw{display:inline-block;width:14px;height:9px;border-radius:3px;vertical-align:middle;margin-left:6px}
.sw.win{background:var(--c2);opacity:.35}.sw.pref{background:var(--c1)}
.band{position:relative;height:26px;background:var(--grid);border-radius:8px;overflow:hidden;margin:6px 0}
.tier{position:absolute;top:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:11.5px;
font-weight:600;color:var(--ink);border-right:2px solid var(--panel)}
.t-sun{background:var(--warn-bg)}.t-peak{background:var(--bad-bg)}.t-standard{background:var(--info-bg)}
.trail{line-height:2}.arrow{color:var(--muted)}
ol.reasons,ul.assume{font-size:13.5px;padding-left:20px}
ul.assume{color:var(--muted)}
.status{line-height:1.9}
figure.chart{margin:10px 0;max-width:100%;overflow-x:auto}
figure.chart svg{max-width:100%;height:auto;display:block}
figcaption{font-size:13px;font-weight:600;margin-bottom:4px}
pre.spark{white-space:pre}
.hbar{display:grid;grid-template-columns:96px 1fr auto;gap:8px;align-items:center;font-size:13px;margin:4px 0}
.hbar .track{background:var(--grid);border-radius:6px;height:14px;overflow:hidden}
.hbar .fill{display:block;height:100%;background:var(--muted)}.hbar .fill.hi{background:var(--accent)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.card{border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:10px 0}
.bad-card{border-color:var(--bad)}
.card-h{font-weight:600;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
ul.entries{list-style:none;padding:0;margin:0}
li.entry{border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin:8px 0;overflow-wrap:anywhere}
.proof{display:flex;flex-wrap:wrap;gap:16px;align-items:baseline;margin:10px 0}
.proof .big{font-size:28px;font-weight:700}.proof .vs{color:var(--muted)}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;font-size:13.5px}
.coverage{font-weight:600}
footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:24px}
@media (max-width:600px){section.act{padding:14px 12px 6px}.takeaway{margin:14px -12px 0;padding:10px 12px}
header.top h1{font-size:21px}}
@media print{nav.acts{display:none}body{background:#fff;color:#000}section.act{break-inside:avoid-page;border-color:#bbb}
.tw{overflow:visible}details>*{display:block}a{color:inherit}}
"""


def build_replay(snapshot: dict, e2e_root: Path | None = None) -> str:
    """One self-contained HTML page replaying the recorded demo run in `snapshot`."""
    from greenstove.demo import ACTS
    meta = snapshot.get("meta") or {}
    mode = str(meta.get("mode") or "?").lower()
    is_mock = mode == "mock"
    world = str(meta.get("world") or "")
    stub = "stub" in world.lower() or "stand-in" in world.lower()
    abel = "abel" in world.lower() and not stub
    mode_badge = chip("MOCK model", "warn badge") if is_mock else chip(f"LIVE model ({mode})", "ok badge")
    world_badge = (chip("stand-in world", "warn badge") if stub else
                   chip("Abel's world", "ok badge") if abel else chip(world or "world not recorded", "muted badge"))
    banner = []
    if is_mock:
        banner.append("<p><b>MOCK:</b> a rule-based stand-in plays the model. This replay shows the mechanism "
                      "(verifier, Supervisor, Keeper, Shared Brain), not Nemotron's quality or the value of memory.</p>")
    if stub:
        banner.append("<p><b>STUB WORLD:</b> development stand-in, not Abel's simulator.</p>")
    nav = "".join(f'<a href="#act-{esc(v[0])}">{esc(v[0])} · {esc(v[1])}</a>' for v in ACTS.values())
    results = results_section(e2e_root)
    if results:
        nav += '<a href="#results">Results</a>'
    nav += '<a href="#receipt">Receipt</a>'
    acts = (act_goal(snapshot) + act_world(snapshot) + act_proposal(snapshot) + act_memory(snapshot)
            + act_handoff(snapshot) + act_tomorrow(snapshot) + act_infeasible(snapshot) + act_improve(snapshot))
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Green-Stove run replay</title><meta name="description" content="Replay of one recorded Green-Stove Agents demo run">
<style>{CSS}</style></head>
<body><div class="wrap">
<header class="top"><h1>Green-Stove Agents · run replay</h1>
<div class="meta">Run recorded {esc(meta.get("stamp"))} · replay generated {esc(generated)} · model <code>{esc(meta.get("model"))}</code></div>
<div class="badges">{mode_badge}{world_badge}{chip("RECORDED run", "info badge")}</div>
<div class="meta">World: {esc(world or "not recorded")}</div>
{f'<div class="banner">{"".join(banner)}</div>' if banner else ""}
<p class="muted">An agent team that runs a community solar cooking mini-grid unattended, proves every plan before acting,
and shares what it learns. This page replays one recorded run act by act; everything shown comes from the run records.</p>
</header>
<nav class="acts" aria-label="Acts">{nav}</nav>
{acts}{results}{receipt_section(snapshot)}
<footer>Static replay · no server, no network · generated by <code>gsa replay</code></footer>
</div></body></html>
"""


# ---------------------------------------------------------------- CLI

def _latest(paths) -> Path | None:
    paths = [p for p in paths if p.exists()]
    return max(paths, key=lambda q: q.stat().st_mtime) if paths else None


def latest_snapshot(data: Path) -> Path | None:
    return _latest((data / "demo").glob("demo-*.json"))


def latest_e2e(*bases: Path) -> Path | None:
    found = [p for b in bases for p in b.glob("e2e-*") if (p / "e2e-summary.md").exists()]
    return _latest(found)


def _show(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def cmd_replay(a) -> int:
    from greenstove.cli import data_dir
    data = data_dir()
    snap_path = Path(a.snapshot) if a.snapshot else latest_snapshot(data)
    if not snap_path or not snap_path.exists():
        print(f"no demo snapshot found (looked in {_show(data / 'demo')}); run `gsa demo` first or pass --snapshot")
        return 1
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    e2e = Path(a.e2e) if a.e2e else latest_e2e(data, ROOT / "data")
    if e2e is not None and not e2e.exists():
        e2e = None
    stamp = (snapshot.get("meta") or {}).get("stamp") or time.strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) if a.out else data / "replay" / f"replay-{stamp}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_replay(snapshot, e2e), encoding="utf-8")
    print(_show(out))
    return 0


def register(sub) -> None:
    """Add `replay [--snapshot FILE] [--e2e DIR] [--out FILE]` to cli.main()'s subparsers."""
    s = sub.add_parser("replay", help="static HTML replay of a recorded demo run (eight acts + results)")
    s.add_argument("--snapshot", help="demo snapshot JSON (default: latest <data dir>/demo/demo-*.json)")
    s.add_argument("--e2e", help="e2e folder for the results section (default: latest data/e2e-* with e2e-summary.md)")
    s.add_argument("--out", help="output HTML (default <data dir>/replay/replay-<stamp>.html)")
    s.set_defaults(fn=cmd_replay)
