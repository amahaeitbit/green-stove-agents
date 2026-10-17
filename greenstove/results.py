"""Results report for judges: one markdown page built from an e2e folder (`gsa report`).

`build_report(root)` reads only files that `gsa e2e` (and, when present, a later `compare`
step) wrote under `data/e2e-<stamp>/` and returns concise, honestly labeled markdown:
mode, world and coverage first, then the definition of done, the headline comparison,
Version N+1, Shared Brain health, the track scorecard, Crusoe usage and the forecast, and
what the numbers do not prove. Paths are repo-relative; lines mentioning API_KEY are dropped.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from greenstove.llm import ROOT

MOCK_LABEL = "these numbers test the mechanisms, not model quality"
_ABS = re.compile(r"(?<![\w.:/])/(?:Users|home|private|tmp|var|mnt|opt|root|Volumes)/[^\s`|)\]'\"]+")


# ---- small helpers ---------------------------------------------------------------

def rel(p: Path | str) -> str:
    """Repo-relative path when under the repo; otherwise only the last two components."""
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(Path(*p.parts[-2:])) if len(p.parts) >= 2 else p.name


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _latest(root: Path, pattern: str) -> Path | None:
    files = sorted(root.glob(pattern), key=lambda p: (p.stat().st_mtime, p.name))
    return files[-1] if files else None


def _table_lines(text: str) -> list[str]:
    """The first markdown table in text (consecutive lines starting with '|')."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            out.append(line.strip())
        elif out:
            break
    return out


def _parse_table(lines: list[str]) -> list[dict[str, str]]:
    if len(lines) < 2:
        return []
    cells = lambda s: [c.strip() for c in s.strip().strip("|").split("|")]  # noqa: E731
    head = cells(lines[0])
    return [dict(zip(head, cells(x))) for x in lines[2:]]


def _md(rows: list[dict[str, Any]], cols: list[str]) -> list[str]:
    fmt = lambda v: "" if v is None else str(v).replace("|", "/").replace("\n", " ")  # noqa: E731
    return (["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
            + ["| " + " | ".join(fmt(r.get(c)) for c in cols) + " |" for r in rows])


def _frac(s: str) -> tuple[int, int] | None:
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _demote(text: str) -> list[str]:
    return [("##" + x) if x.startswith("#") else x for x in text.strip().splitlines()]


def sanitize(text: str, e2e_root: Path | None = None) -> str:
    """Strip absolute prefixes (repo-relative instead) and drop any line mentioning API_KEY."""
    for prefix in {str(ROOT.resolve()), str(ROOT)}:
        text = text.replace(prefix + "/", "").replace(prefix, ".")
    text = _ABS.sub(lambda m: rel(m.group(0)), text)
    return "\n".join(x for x in text.splitlines() if "API_KEY" not in x) + "\n"


def latest_e2e(base: Path | None = None) -> Path | None:
    if base is None:
        from greenstove import cli
        base = cli.data_dir()
    runs = sorted(p for p in Path(base).glob("e2e-*") if (p / "e2e-summary.md").exists())
    return runs[-1] if runs else None


# ---- header ----------------------------------------------------------------------

def run_info(root: Path) -> dict[str, Any]:
    summary = _read(root / "e2e-summary.md")
    manifest = (_json(root / "eval" / "manifest-partner.json") or {}).get("partner", {})
    llm = manifest.get("llm") or {}
    m = re.search(r"^Model:\s*(.+?)\s*·\s*world:\s*(.+?)\s*·", summary, re.M)
    model_line, world = (m.group(1), m.group(2)) if m else ("", manifest.get("world", "unknown"))
    mode = (llm.get("mode") or "").lower()
    live = ("LIVE" in model_line) or mode in ("live", "crusoe")
    mock = not live
    model_id = llm.get("model") or ("mock-rules" if mock else "unknown")
    pricing = (llm.get("models_by_role") or {}).get("pricing")
    cov = re.search(r"^World coverage:.*$", summary, re.M)
    stamp = root.name.removeprefix("e2e-")
    return {"live": live, "mock": mock, "model": model_id, "pricing_model": pricing, "world": world,
            "stub": "stub" in world.lower(), "coverage": cov.group(0) if cov else "World coverage: not recorded",
            "stamp": stamp, "run_commit": manifest.get("commit") or (_json(root / "freeze" / "freeze.json") or {}).get("commit"),
            "summary": summary}


def _header(info: dict, root: Path) -> list[str]:
    from greenstove.agent_eval import git_commit
    mode = "LIVE Crusoe" if info["live"] else "MOCK (rule-based stand-in)"
    model = info["model"] + (f" (pricing role pinned to {info['pricing_model']})"
                             if info["live"] and info["pricing_model"] and info["pricing_model"] != info["model"] else "")
    lines = ["# Green-Stove Agents: results", "",
             f"- Generated: {time.strftime('%Y-%m-%d %H:%M')} from `{rel(root)}` (e2e run {info['stamp']})",
             f"- Commit: `{git_commit()}`" + (f" (e2e ran at `{info['run_commit']}`)" if info["run_commit"] else ""),
             f"- Model mode: **{mode}** · model id `{model}`",
             f"- World: {info['world']}", ""]
    labels = []
    if info["mock"]:
        labels.append("MOCK model")
    if info["stub"]:
        labels.append("STUB world")
    if labels:
        lines += [f"**{' · '.join(labels)}: {MOCK_LABEL}.**", ""]
    return lines


# ---- sections --------------------------------------------------------------------

def _coverage(info: dict) -> list[str]:
    steps = _parse_table(_table_lines(info["summary"]))
    ok = sum(r.get("Status") == "ok" for r in steps)
    lines = ["## 1. Run status", "", info["coverage"], "",
             f"{ok}/{len(steps)} steps ok (required steps stop the run; optional ones warn).", ""]
    lines += _table_lines(info["summary"]) or ["(no step table in e2e-summary.md)"]
    return lines + [""]


def _spec(root: Path) -> list[str]:
    path = _latest(root, "spec/spec-*.json")
    lines = ["## 2. Definition of done (Done Means Done)", ""]
    spec = _json(path) if path else None
    if not spec:
        return lines + ["No spec record in this run.", ""]
    lines += [f"Goal: \"{spec.get('goal', '')}\"", "",
              f"Spec Agent restated it as: \"{spec.get('goal_restated', '')}\" (model `{spec.get('model', '?')}`, "
              f"record `{rel(path)}`).", ""]
    rows = [{"Metric": c.get("metric"), "Check": f"{c.get('op')} {c.get('threshold')}", "Source": c.get("source"),
             "Why": c.get("why")} for c in spec.get("checks", [])]
    lines += _md(rows, ["Metric", "Check", "Source", "Why"])
    lines += ["", "Sources: `agent` = written by the Spec Agent; `code_tightened` = code raised a threshold the "
              "model set too loosely; `code_added` = a committee requirement the model omitted, added by code. "
              "Code may only tighten." + (f" Dropped checks: {spec['dropped']}." if spec.get("dropped") else ""), ""]
    return lines


def _abc_notes(rows: list[dict[str, str]], mock: bool) -> list[str]:
    by = {r.get("Condition", "")[:1]: r for r in rows}
    notes = ["Failed days stay in every denominator; memory is frozen and held-out results never update it."]
    a, b, c = by.get("A"), by.get("B"), by.get("C")
    if a and c:
        fa, fc = _frac(a.get("First candidate", "")), _frac(c.get("First candidate", ""))
        n = fa[1] if fa else "?"
        notes.append(f"C (curated brain) vs A (facts only): first-candidate pass {c.get('First candidate')} vs "
                     f"{a.get('First candidate')}, calls/day {c.get('Calls/day')} vs {a.get('Calls/day')}, "
                     f"tokens/day {c.get('Tokens/day')} vs {a.get('Tokens/day')} over {n} held-out days.")
        if fa and fc and abs(fa[0] - fc[0]) <= 1:
            notes.append("A difference of one day or less is not significant at this sample size.")
    if b and c:
        notes.append(f"B (raw history) vs C isolates curation (same source evidence): first-candidate "
                     f"{b.get('First candidate')} vs {c.get('First candidate')}, tokens/day {b.get('Tokens/day')} "
                     f"vs {c.get('Tokens/day')}.")
    if mock:
        notes.append("MOCK: the agent is a rule-based stand-in, so these rows test the harness, not the value of memory.")
    return [f"- {x}" for x in notes]


def _comparison(root: Path, mock: bool) -> list[str]:
    lines = ["## 3. Headline comparison (Shared Brain)", ""]
    compare = root / "compare" / "results-table.md"
    if compare.exists():
        lines += [f"Six conditions A–F from `{rel(compare)}` (official table):", ""]
        lines += _demote(_read(compare))
        lines += ["", f"Partner-side raw A/B/C records: `{rel(root / 'eval' / 'agent-runs.jsonl')}`.", ""]
        return lines
    summary = _read(root / "eval" / "agent-summary.md")
    if not summary:
        return lines + ["No held-out comparison in this run.", ""]
    table = _table_lines(summary)
    m = re.search(r"(\d+) held-out days", summary)
    lines += [f"Partner-side A/B/C on {m.group(1) if m else 'the'} held-out days (`{rel(root / 'eval' / 'agent-summary.md')}`;"
              " the six-condition table A–F is not in this run).", ""]
    lines += table + ["", "Interpretation:", ""] + _abc_notes(_parse_table(table), mock) + [""]
    return lines


def _version_n1(root: Path) -> list[str]:
    lines = ["## 4. Version N+1 (playbook before/after on unseen days)", ""]
    ba = _read(root / "playbook" / "before-after.md")
    if ba:
        lines += _table_lines(ba) + [""]
        held = re.search(r"^Held-out check.*$", ba, re.M)
        if held:
            lines += [held.group(0), ""]
        lines += [f"Source: `{rel(root / 'playbook' / 'before-after.md')}` (condition A, memory off; a separate "
                  "experiment from the headline comparison).", ""]
    else:
        lines += ["No before/after record in this run.", ""]
    index = _json(root / "playbook" / "index.json") or {}
    versions = index.get("versions") or []
    log = [json.loads(x) for x in _read(root / "playbook" / "evolve-log.jsonl").splitlines() if x.strip().startswith("{")]
    if versions or log:
        lines += [f"Playbook versions (active: **{index.get('active', '?')}**):", ""]
        lines += _md([{"Version": v.get("id"), "Author": v.get("author"), "Status": v.get("status"),
                       "Decision": v.get("reason")} for v in versions], ["Version", "Author", "Status", "Decision"])
        kept = sum(r.get("decision") == "kept" for r in log)
        rejected = sum(r.get("decision") == "rejected" for r in log)
        lines += ["", f"Evolve rounds: {len(log)} ({kept} kept, {rejected} rejected by the keep rule, "
                      f"{len(log) - kept - rejected} with no candidate)."]
        lines += [f"- round {r.get('round')}: {r.get('decision')} {r.get('candidate') or '(no candidate)'}: "
                  f"{r.get('reason', '')}" for r in log]
        lines += [""]
    return lines


def _brain(root: Path) -> list[str]:
    lines = ["## 5. Shared Brain health", ""]
    if not (root / "brain").exists() or not (root / "evidence").exists():
        return lines + ["No brain or evidence folder in this run.", ""]
    from greenstove.brain import insight
    from greenstove.brain.store import BrainStore
    from greenstove.evidence import EvidenceStore
    brain = BrainStore(root / "brain", frozen=True)             # read-only: never writes the run folder
    s = insight.stats(brain, EvidenceStore(root / "evidence"))
    e = s["entries"]
    freeze = _json(root / "freeze" / "freeze.json") or {}
    rows = [
        ("entries", f"{e.get('current', 0)} current / cap {e.get('cap')}"
                    + "".join(f", {k} {v}" for k, v in e.items() if k not in ("current", "cap"))),
        ("current outcomes", f"{s['current_outcomes']['pass']} pass, {s['current_outcomes']['fail']} fail"),
        ("conflict groups (kept separate, not overwritten)", s["conflict_groups"]),
        ("confirmations", s["confirmations"]),
        ("Keeper admissions", ", ".join(f"{k} {v}" for k, v in sorted(s["admissions"].items())) or "none"),
        ("Keeper rejections by code", ", ".join(f"{k} {v}" for k, v in sorted(s["rejections_by_code"].items())) or "none"),
        ("evicted for cap / expired", f"{s['evictions']} / {s['expired']}"),
        ("runs with a curated brief", s["runs_with_curated_brief"]),
        ("mean brief tokens / entries supplied", f"{s['mean_brief_tokens']} / {s['mean_entries_supplied']}"),
        ("proposals citing memory", s["proposals_citing_memory"]),
        ("snapshot", s["snapshot"]),
    ]
    if freeze:
        rows.append(("frozen for evaluation", f"snapshot {str(freeze.get('brain_snapshot', ''))[:8]}, "
                     f"{freeze.get('brain_entries')} entries, {freeze.get('history_records')} raw history records"))
    lines += _md([{"Measure": k, "Value": v} for k, v in rows], ["Measure", "Value"])
    if s["most_cited"]:
        lines += ["", "Most-cited entries (development runs; descriptions are model words, not trusted facts):", ""]
        cited = []
        for oid, n in s["most_cited"]:
            ent = brain.get(oid)
            cited.append({"Entry": oid, "Cited": n,
                          "Outcome": ("PASS" if ent.outcome_passed else "FAIL") if ent else "?",
                          "Tariff": ", ".join(f"{k}={v:g}" for k, v in sorted(ent.tariff.items())) if ent else "",
                          "Codes": ", ".join(ent.feedback_codes) if ent else "",
                          "Note": (ent.description[:90] + ("…" if len(ent.description) > 90 else "")) if ent else ""})
        lines += _md(cited, ["Entry", "Cited", "Outcome", "Tariff", "Codes", "Note"])
    lines += ["", f"Explain any entry: `gsa brain explain <id>` with `GREENSTOVE_DATA_DIR={rel(root)}`.", ""]
    return lines


def _tracks(root: Path) -> list[str]:
    lines = ["## 6. Track scorecard", ""]
    card = _json(root / "tracks" / "tracks-scorecard.json")
    if not card:
        return lines + ["No track scorecard in this run.", ""]
    rows = card.get("rows") or []
    per: dict[str, list[int]] = {}
    for r in rows:
        t = per.setdefault(r.get("track", "?"), [0, 0])
        t[0] += r.get("result") == "PASS"
        t[1] += 1
    lines += [f"**{card.get('passed')}/{card.get('total')} requirement rows PASS** · "
              + " · ".join(f"{k} {p}/{n}" for k, (p, n) in per.items())
              + f" · full table `{rel(root / 'tracks' / 'tracks-scorecard.md')}`", ""]
    if card.get("header"):
        lines += [f"Scorecard label: {card['header']}", ""]
    bad = [r for r in rows if r.get("result") != "PASS"]
    lines += [f"- not passing: row {r.get('id')} {r.get('track')} {r.get('requirement')}: {r.get('result')}" for r in bad]
    return lines + ([""] if bad else [])


def _crusoe(root: Path) -> list[str]:
    from greenstove.crusoe import usage_rows
    lines = ["## 7. Crusoe usage, model choice and forecast", ""]
    usage = root / "usage.jsonl"
    if usage.exists() and usage.stat().st_size:
        rows = usage_rows(usage)
        lines += [f"Calls by mode, role and model (`{rel(usage)}`):", ""]
        lines += _md(rows, ["mode", "role", "model", "calls", "outcomes", "prompt_tokens", "completion_tokens",
                            "cache_hit", "p50_ms", "p95_ms", "cost_usd"])
        if all(r["mode"] == "mock" for r in rows):
            lines += ["", "All calls are MOCK: no Crusoe call was made, so latency and cost are not measured."]
        lines += [""]
    else:
        lines += ["No usage records.", ""]
    bench = _latest(root, "crusoe/bench-*.json")
    data = _json(bench) if bench else None
    if data:
        lines += [f"Model benchmark on the fixed Pricing Agent task (`{rel(bench)}`):", ""]
        lines += _md(data, ["model", "valid", "calls", "p50_latency_ms", "mean_tokens", "cached_tokens", "cost_usd"]) + [""]
    models = _json(root / "crusoe" / "models.json")
    if models:
        ids = [m.get("id", "") for m in models.get("data", [])]
        nem = [i for i in ids if "nemotron" in i.lower()]
        lines += [f"Crusoe catalog fetched {models.get('fetched_at', '?')}: {len(ids)} models, Nemotron: "
                  + (", ".join(f"`{i}`" for i in nem) or "none"), ""]
    for fc in sorted(root.glob("forecast/*.json")):
        f = _json(fc) or {}
        pv, down = f.get("forecast_pv_kw") or [], f.get("downside_pv_kw") or []
        step_h = 24 / len(pv) if pv else 0
        lines += [f"Forecast record `{rel(fc)}`: {f.get('place')} on {f.get('date')}, class **{f.get('weather_class')}**, "
                  f"PV {sum(pv) * step_h:.1f} kWh expected / {sum(down) * step_h:.1f} kWh downside ({f.get('kwp')} kWp), "
                  f"status {f.get('status')}, search mode {f.get('search_mode')}, extractor {f.get('extractor_mode')}, "
                  f"{len(f.get('sources') or [])} source(s)"
                  + (" (MOCK search: not a real forecast)" if f.get("search_mode") == "mock" else "") + ".", ""]
    decisions = sorted(root.glob("evidence/runs/*/decision.md"))
    demo = _latest(root, "demo/demo-*.txt")
    if decisions or demo:
        lines += [f"Other outputs: {len(decisions)} operator decision records (e.g. `{rel(decisions[0])}`)" if decisions
                  else "Other outputs:", ]
        if demo:
            lines[-1] += f"; recorded demo transcript `{rel(demo)}`"
        lines[-1] += "."
        lines += [""]
    return lines


def _limits(info: dict, root: Path) -> list[str]:
    m = re.search(r"(\d+) held-out days", _read(root / "eval" / "agent-summary.md"))
    ba = re.search(r"(\d+) held-out days", _read(root / "playbook" / "before-after.md"))
    out = ["## 8. What this does not prove", ""]
    if info["mock"]:
        out.append("- **Mock model.** Every agent is a rule-based stand-in (the mock Pricing Agent follows playbook "
                   "directives literally). The numbers show the mechanisms work, not how good Nemotron is.")
    if info["stub"]:
        out.append("- **Stub world.** The world is the development stand-in, not Abel's simulator; its numbers are synthetic.")
    if "could not model" in info["coverage"] and not re.search(r"\b0 phases", info["coverage"]):
        out.append("- **Partial coverage.** The world could not model some phases; those results are partial and not evidence.")
    out.append(f"- **Small samples.** {m.group(1) + ' held-out days' if m else 'A handful of held-out days'} per condition"
               + (f" and {ba.group(1)} for before/after" if ba else "") + "; a difference of one day is not significant. Comparisons are reported, not asserted.")
    out.append("- **Forced cases.** Scripted handlers create the looping, drifting and false-success scorecard rows; "
               "they show the Supervisor's response, not how often a real model behaves that way.")
    out.append("- **No deployment claim.** This is an offline harness on simulated mini-grid days, not a field trial.")
    if info["mock"] or info["stub"]:
        out.append("- **Gains need live runs.** Evidence of gains needs `gsa e2e --mode live` (Crusoe Nemotron) on the real world.")
    return out + [""]


def build_report(e2e_root: Path) -> str:
    root = Path(e2e_root)
    info = run_info(root)
    parts = (_header(info, root) + _coverage(info) + _spec(root) + _comparison(root, info["mock"])
             + _version_n1(root) + _brain(root) + _tracks(root) + _crusoe(root) + _limits(info, root))
    return sanitize("\n".join(parts).rstrip() + "\n", root)


# ---- CLI -------------------------------------------------------------------------

def cmd_report(a) -> int:
    root = Path(a.e2e) if a.e2e else latest_e2e()
    if root is not None and not root.is_absolute():
        root = (ROOT / root) if (ROOT / root).exists() else root
    if root is None or not (root / "e2e-summary.md").exists():
        print("no e2e folder found (run `gsa e2e` first or pass --e2e DIR)")
        return 1
    out = Path(a.out)
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    text = build_report(root)
    out.write_text(text)
    print(f"report: {rel(out)} (from {rel(root)}, {len(text.splitlines())} lines)")
    return 0


def register(sub) -> None:
    """Add `report [--e2e DIR] [--out docs/results.md]` to cli.main()'s argparse subparsers object."""
    s = sub.add_parser("report", help="markdown results report for judges from an e2e folder")
    s.add_argument("--e2e", help="e2e folder (default: latest <data dir>/e2e-*)")
    s.add_argument("--out", default="docs/results.md", help="output markdown file (default docs/results.md)")
    s.set_defaults(fn=cmd_report)
