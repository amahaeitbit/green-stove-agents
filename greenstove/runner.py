"""Event-driven, always-on runner: agents wake only on events; idle time costs zero model calls.

Files under the data dir (cli.data_dir(), default data/):
  events/inbox.jsonl   append-only events (gsa event ... or any process that appends a JSON line)
  events/state.json    processed ids, inbox offset, model calls/tokens per date, each date's plan summary
  events/log.jsonl     one line per handled event: id, type, started/finished, outcome, calls, tokens, outputs
  events/alerts.jsonl  telemetry alerts with a human-ping flag (no automatic re-plan: intra-day
                       rebooking is outside MVP scope, docs/contracts.md)

Event handling:
  day_start        run one agent day (supervisor.run_agent_day), write decision.md, remember the plan
  forecast_update  re-plan only if the weather class changed or downside PV kWh moved > 15%
  telemetry        compare battery with the plan's expected trace; alert below plan or reserve
  shutdown         stop the watch loop

Only day_start and a material forecast_update ever call a model. A daily model-call budget per
date (GREENSTOVE_RUNNER_MAX_CALLS_PER_DAY, default 20) is checked before a day runs and caps it.
The lead wires the CLI with `runner.register(sub)` inside cli.main().
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from greenstove.env_protocol import STEP_HOURS, STEPS, Environment, Requirements
from greenstove.llm import ROOT

FORECAST_TOLERANCE = 0.15         # relative change in downside PV kWh that triggers a re-plan
BATTERY_TOLERANCE_KWH = 0.5       # telemetry below the plan's expected battery by more than this -> alert
DEFAULT_MAX_CALLS_PER_DAY = 20
CONDITIONS = {"A": "off", "B": "history", "C": "curated"}


# ---- events ----------------------------------------------------------------------------
class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=120)
    created_at: str | None = None


class _Dated(_Event):
    date: str

    @field_validator("date")
    @classmethod
    def _iso(cls, v: str) -> str:
        _date.fromisoformat(v)
        return v


class DayStart(_Dated):
    type: Literal["day_start"]
    scenario: str = "demo-handoff-overcast"      # named scenario used as the base (station, households)
    forecast_path: str | None = None
    condition: Literal["A", "B", "C"] = "C"


class ForecastUpdate(_Dated):
    type: Literal["forecast_update"]
    forecast_path: str


class Telemetry(_Dated):
    type: Literal["telemetry"]
    step: int = Field(ge=0, lt=STEPS)
    battery_kwh: float = Field(ge=0)
    pv_kw: float = Field(ge=0)


class Shutdown(_Event):
    type: Literal["shutdown"]


Event = Annotated[Union[DayStart, ForecastUpdate, Telemetry, Shutdown], Field(discriminator="type")]
EVENT = TypeAdapter(Event)


# ---- context and files -----------------------------------------------------------------
@dataclass
class Paths:
    root: Path

    @property
    def dir(self) -> Path:
        return self.root / "events"

    @property
    def inbox(self) -> Path:
        return self.dir / "inbox.jsonl"

    @property
    def state(self) -> Path:
        return self.dir / "state.json"

    @property
    def log(self) -> Path:
        return self.dir / "log.jsonl"

    @property
    def alerts(self) -> Path:
        return self.dir / "alerts.jsonl"


@dataclass
class RunnerContext:
    """Everything a handler needs; tests inject a mock LLM, the stub world and a tmp data dir."""
    data_dir: Path
    env: Environment
    llm: Any                                     # greenstove.llm.LLMClient
    evidence: Any                                # greenstove.evidence.EvidenceStore
    brain: Any                                   # greenstove.brain.store.BrainStore
    requirements: Requirements
    max_calls_per_day: int = DEFAULT_MAX_CALLS_PER_DAY
    say: Callable[[str], None] = field(default=lambda s: None)

    @property
    def paths(self) -> Paths:
        return Paths(self.data_dir)


def max_calls_from_env() -> int:
    return int(os.environ.get("GREENSTOVE_RUNNER_MAX_CALLS_PER_DAY", str(DEFAULT_MAX_CALLS_PER_DAY)))


def build_context(mode: str | None = None, stub: bool = False, say: Callable[[str], None] | None = None) -> RunnerContext:
    """Production context: cli.data_dir(), cli.make_llm(mode), cli.stores() (the curated brain)."""
    from greenstove import cli
    from greenstove.env_protocol import load_environment
    from greenstove.scenarios import DEFAULT_REQUIREMENTS
    evidence, brain = cli.stores()
    return RunnerContext(data_dir=cli.data_dir(), env=load_environment(prefer_stub=stub), llm=cli.make_llm(mode),
                         evidence=evidence, brain=brain, requirements=DEFAULT_REQUIREMENTS,
                         max_calls_per_day=max_calls_from_env(), say=say or print)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _rel(p: Path | str) -> str:
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


def load_state(paths: Paths) -> dict[str, Any]:
    if paths.state.exists():
        st = json.loads(paths.state.read_text())
    else:
        st = {}
    st.setdefault("inbox_offset", 0)
    st.setdefault("processed", [])
    st.setdefault("calls_per_day", {})
    st.setdefault("tokens_per_day", {})
    st.setdefault("days", {})
    return st


def save_state(paths: Paths, st: dict[str, Any]) -> None:
    paths.dir.mkdir(parents=True, exist_ok=True)
    tmp = paths.state.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, paths.state)


def _append(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---- emitting events ---------------------------------------------------------------------
def emit(event: dict[str, Any], data_dir: Path | None = None) -> dict[str, Any]:
    """Validate and append one event to the inbox; assigns id/created_at when missing."""
    from greenstove import cli
    paths = Paths(Path(data_dir) if data_dir else cli.data_dir())
    ev = dict(event)
    ev.setdefault("id", "-".join(str(x) for x in (ev.get("type", "event"), ev.get("date"), uuid.uuid4().hex[:8]) if x))
    ev.setdefault("created_at", _now())
    for key in ("forecast_path",):
        if ev.get(key):
            ev[key] = _rel(ev[key])
    EVENT.validate_python(ev)                    # never write a malformed event ourselves
    _append(paths.inbox, {k: v for k, v in ev.items() if v is not None})
    return ev


# ---- handlers ----------------------------------------------------------------------------
@dataclass
class Outcome:
    outcome: str                                 # "planned: ..." | "skipped: ..." | "alert: ..." | "ok: ..." | ...
    model_calls: int = 0
    tokens: int = 0
    outputs: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    stop: bool = False


def _downside_kwh(pv: list[float]) -> float:
    return round(sum(pv) * STEP_HOURS, 3)


def plan_day(date: str, scenario: str, condition: str, forecast_path: str | None, ctx: RunnerContext,
             st: dict[str, Any], why: str) -> Outcome:
    """Run one agent day and remember the plan (shared by day_start and a material forecast_update)."""
    from greenstove import supervisor
    from greenstove.cli import context_for
    from greenstove.decision import brief_lines, render_decision
    from greenstove.scenarios import DEMO_SCENARIOS, DEV_SCENARIOS, make_actual

    used = int(st["calls_per_day"].get(date, 0))
    remaining = ctx.max_calls_per_day - used
    if remaining <= 0:
        return Outcome(f"skipped: daily model-call budget exhausted ({used}/{ctx.max_calls_per_day} for {date})")
    if scenario != "infeasible" and scenario not in {**DEV_SCENARIOS, **DEMO_SCENARIOS}:
        return Outcome(f"rejected: unknown scenario {scenario!r}")
    base = context_for(scenario)
    fc_info: dict[str, Any] = {}
    if forecast_path:
        from greenstove.forecast.pipeline import context_from_forecast, load
        rec = load(_resolve(forecast_path))
        ctx_day = context_from_forecast(rec, base)
        fc_info = {"forecast_path": _rel(_resolve(forecast_path)), "forecast_status": rec.status,
                   "downside_pv_kwh": rec.downside_pv_kwh}
    else:
        ctx_day = base
        fc_info = {"forecast_path": None, "downside_pv_kwh": _downside_kwh(base.downside_pv_kw)}

    llm = ctx.llm
    saved_cap = llm.s.max_calls
    llm.s.max_calls = min(saved_cap, remaining)  # the run itself cannot overrun the day's budget
    try:
        run = supervisor.run_agent_day(ctx_day, CONDITIONS[condition], ctx.requirements, env=ctx.env, llm=llm,
                                       evidence=ctx.evidence, brain=ctx.brain,
                                       actual=make_actual(ctx_day, seed=ctx_day.day + 7))
    finally:
        llm.s.max_calls = saved_cap
    calls = [c for c in run.calls if c.outcome != "budget"]
    tokens = sum(c.prompt_tokens + c.completion_tokens for c in calls)
    st["calls_per_day"][date] = used + len(calls)
    st["tokens_per_day"][date] = int(st["tokens_per_day"].get(date, 0)) + tokens

    text = render_decision(ctx_day, run, ctx.env, ctx.requirements, brief_lines(run.brief.text))
    decision = ctx.evidence.run_dir(run.agent_run_id) / "decision.md"
    decision.write_text(text)

    expected_batt: list[float] | None = None
    expected_pv: list[float] | None = None
    if run.selected_tariff:
        plan = ctx.env.allocate(ctx.env.household_requests(ctx_day), run.selected_tariff, ctx_day,
                                run.selected_proposal_id or "")
        trace = ctx.env.simulate(plan, ctx_day, "forecast_expected")
        expected_batt = [round(x, 3) for x in trace.battery_kwh]
        expected_pv = [round(x, 3) for x in trace.pv_kw]
    prev = st["days"].get(date, {})
    st["days"][date] = {
        "run_id": run.agent_run_id, "status": run.status, "reason": run.reason, "scenario": scenario,
        "condition": condition, "context_scenario_id": ctx_day.scenario_id, "weather_class": ctx_day.weather_class,
        **fc_info, "selected_tariff": run.selected_tariff, "reserve_kwh": ctx_day.reserve_kwh,
        "expected_battery_kwh": expected_batt, "expected_pv_kw": expected_pv,
        "decision_path": _rel(decision), "planned_at": _now(), "plans": int(prev.get("plans", 0)) + 1,
        "why": why}
    tariff = " ".join(f"{k}={v:g}" for k, v in sorted((run.selected_tariff or {}).items())) or "no tariff"
    verb = "replanned" if prev else "planned"
    return Outcome(f"{verb}: {run.status} ({tariff})", model_calls=len(calls), tokens=tokens,
                   outputs=[_rel(decision), _rel(ctx.evidence.run_dir(run.agent_run_id) / "run.json")],
                   detail={"run_id": run.agent_run_id, "reason": run.reason, "why": why,
                           "calls_today": st["calls_per_day"][date], "budget": ctx.max_calls_per_day})


def handle_day_start(event: DayStart, ctx: RunnerContext, st: dict[str, Any]) -> Outcome:
    return plan_day(event.date, event.scenario, event.condition, event.forecast_path, ctx, st, why="day_start")


def handle_forecast_update(event: ForecastUpdate, ctx: RunnerContext, st: dict[str, Any]) -> Outcome:
    from greenstove.forecast.pipeline import load
    day = st["days"].get(event.date)
    if not day:
        return Outcome(f"skipped: no plan for {event.date} yet (send day_start first)")
    try:
        rec = load(_resolve(event.forecast_path))
    except (OSError, ValidationError, ValueError) as exc:
        return Outcome(f"rejected: unreadable forecast ({exc.__class__.__name__})")
    old_kwh = float(day.get("downside_pv_kwh") or 0.0)
    change = abs(rec.downside_pv_kwh - old_kwh) / old_kwh if old_kwh > 0 else (1.0 if rec.downside_pv_kwh else 0.0)
    detail = {"old_weather_class": day.get("weather_class"), "new_weather_class": rec.weather_class,
              "old_downside_pv_kwh": old_kwh, "new_downside_pv_kwh": rec.downside_pv_kwh,
              "downside_change": round(change, 3), "tolerance": FORECAST_TOLERANCE}
    reasons = []
    if rec.weather_class != day.get("weather_class"):
        reasons.append(f"weather class {day.get('weather_class')} -> {rec.weather_class}")
    if change > FORECAST_TOLERANCE:
        reasons.append(f"downside PV {old_kwh:g} -> {rec.downside_pv_kwh:g} kWh ({change:+.0%})")
    if not reasons:
        return Outcome("skipped: forecast within tolerance", detail=detail)
    out = plan_day(event.date, day["scenario"], day["condition"], event.forecast_path, ctx, st,
                   why="forecast_update: " + "; ".join(reasons))
    out.detail.update(detail)
    return out


def handle_telemetry(event: Telemetry, ctx: RunnerContext, st: dict[str, Any]) -> Outcome:
    from greenstove.pricing_agent import hhmm
    day = st["days"].get(event.date) or {}
    reserve = day.get("reserve_kwh")
    if reserve is None:
        from greenstove.cli import context_for
        reserve = context_for(day.get("scenario", "demo-handoff-overcast")).reserve_kwh
    trace = day.get("expected_battery_kwh")
    expected = trace[event.step] if trace else None
    deficit = round(expected - event.battery_kwh, 3) if expected is not None else None
    reasons = []
    if event.battery_kwh < reserve:
        reasons.append(f"battery {event.battery_kwh:g} kWh below reserve {reserve:g} kWh")
    if deficit is not None and deficit > BATTERY_TOLERANCE_KWH:
        reasons.append(f"battery {deficit:g} kWh below plan ({expected:g} kWh expected)")
    detail = {"step": event.step, "at": hhmm(event.step), "battery_kwh": event.battery_kwh,
              "expected_battery_kwh": expected, "deficit_kwh": deficit, "reserve_kwh": reserve, "pv_kw": event.pv_kw,
              "expected_pv_kw": (day.get("expected_pv_kw") or [None] * STEPS)[event.step],
              "run_id": day.get("run_id")}
    if not reasons:
        note = "within plan" if expected is not None else "no verified plan to compare; reserve check only"
        return Outcome(f"ok: {note}", detail=detail)
    alert = {"id": f"alert-{event.id}", "event_id": event.id, "date": event.date, "created_at": _now(), **detail,
             "severity": "high" if event.battery_kwh < reserve else "warn", "reasons": reasons, "human_ping": True,
             "action": "ping the operator; intra-day rebooking is outside MVP scope, so no automatic re-plan",
             "decision_path": day.get("decision_path")}
    _append(ctx.paths.alerts, alert)
    return Outcome("alert: " + "; ".join(reasons), outputs=[_rel(ctx.paths.alerts)], detail=detail)


HANDLERS: dict[str, Callable[[Any, RunnerContext, dict[str, Any]], Outcome]] = {
    "day_start": handle_day_start,
    "forecast_update": handle_forecast_update,
    "telemetry": handle_telemetry,
    "shutdown": lambda e, ctx, st: Outcome("shutdown: stop requested", stop=True),
}


# ---- the loop ----------------------------------------------------------------------------
def _log(ctx: RunnerContext, ev_id: str, ev_type: str, started: str, out: Outcome, date: str | None) -> dict:
    row = {"id": ev_id, "type": ev_type, "date": date, "started_at": started, "finished_at": _now(),
           "outcome": out.outcome, "model_calls": out.model_calls, "tokens": out.tokens, "outputs": out.outputs,
           "detail": out.detail}
    _append(ctx.paths.log, row)
    ctx.say(f"{ev_type:15} {ev_id:40} {out.outcome}  calls={out.model_calls}")
    return row


def process_line(line: str, ctx: RunnerContext, st: dict[str, Any], position: int) -> Outcome:
    started = _now()
    try:
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError("event must be a JSON object")
    except ValueError as exc:
        out = Outcome(f"rejected: malformed JSON ({str(exc)[:80]})")
        _log(ctx, f"line@{position}", "unknown", started, out, None)
        return out
    raw_id = str(raw.get("id") or f"line@{position}")
    try:
        event = EVENT.validate_python(raw)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
        out = Outcome(f"rejected: invalid event ({errs[:160]})")
        _log(ctx, raw_id, str(raw.get("type", "unknown")), started, out, raw.get("date"))
        return out
    if event.id in set(st["processed"]):
        out = Outcome("ignored: duplicate event id (already processed)")
        _log(ctx, event.id, event.type, started, out, getattr(event, "date", None))
        return out
    try:
        out = HANDLERS[event.type](event, ctx, st)
    except Exception as exc:  # noqa: BLE001 - one bad event never kills the always-on loop
        out = Outcome(f"error: {exc.__class__.__name__}: {str(exc)[:160]}")
    st["processed"].append(event.id)
    _log(ctx, event.id, event.type, started, out, getattr(event, "date", None))
    return out


def drain(ctx: RunnerContext) -> dict[str, Any]:
    """Process every new inbox line once. Returns a summary; stops early on a shutdown event."""
    paths = ctx.paths
    st = load_state(paths)
    summary = {"handled": 0, "model_calls": 0, "stopped": False}
    if not paths.inbox.exists():
        return summary
    size = paths.inbox.stat().st_size
    if size < st["inbox_offset"]:                 # inbox was truncated or replaced: start over (ids dedupe)
        st["inbox_offset"] = 0
    with paths.inbox.open("rb") as f:
        f.seek(st["inbox_offset"])
        while True:
            position = f.tell()
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):           # half-written line: wait for the writer to finish it
                break
            st["inbox_offset"] = f.tell()
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            out = process_line(line, ctx, st, position)
            summary["handled"] += 1
            summary["model_calls"] += out.model_calls
            save_state(paths, st)                 # after each event: a crash never re-runs a finished day
            if out.stop:
                summary["stopped"] = True
                break
    save_state(paths, st)
    return summary


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        s = path.stat()
        return s.st_size, s.st_mtime_ns
    except FileNotFoundError:
        return None


def watch(ctx: RunnerContext, interval: float = 30.0, max_polls: int | None = None,
          sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Poll the inbox's size/mtime; drain only when it changed. Idle polls make no model calls."""
    totals = {"polls": 0, "drains": 0, "handled": 0, "model_calls": 0, "stopped": False}
    last: object = object()                      # sentinel: the first poll always drains (catch-up)
    try:
        while max_polls is None or totals["polls"] < max_polls:
            totals["polls"] += 1
            sig = _signature(ctx.paths.inbox)
            if sig != last and sig is not None:
                s = drain(ctx)
                totals["drains"] += 1
                totals["handled"] += s["handled"]
                totals["model_calls"] += s["model_calls"]
                last = sig                       # a write during the drain shows up as a change next poll
                if s["stopped"]:
                    totals["stopped"] = True
                    break
            sleep(interval)
    except KeyboardInterrupt:
        ctx.say("runner: interrupted (Ctrl-C); state is saved after every event")
    return totals


def status(data_dir: Path | None = None, last: int = 10) -> dict[str, Any]:
    from greenstove import cli
    paths = Paths(Path(data_dir) if data_dir else cli.data_dir())
    st = load_state(paths)
    days = {d: {k: v for k, v in info.items() if k not in ("expected_battery_kwh", "expected_pv_kw")}
            for d, info in st["days"].items()}
    log = _read_jsonl(paths.log)
    return {"inbox": _rel(paths.inbox), "processed": len(st["processed"]), "calls_per_day": st["calls_per_day"],
            "tokens_per_day": st["tokens_per_day"], "days": days, "last_events": log[-last:],
            "alerts": _read_jsonl(paths.alerts)[-last:], "max_calls_per_day": max_calls_from_env()}


# ---- CLI ---------------------------------------------------------------------------------
def cmd_runner(a) -> int:
    if a.action == "status":
        s = status(last=a.last)
        print(f"inbox {s['inbox']} · processed {s['processed']} events · budget {s['max_calls_per_day']} calls/day")
        for d, n in sorted(s["calls_per_day"].items()):
            print(f"  {d}: {n} model calls, {s['tokens_per_day'].get(d, 0)} tokens")
        for d, info in sorted(s["days"].items()):
            print(f"  day {d}: {info['status']} {info.get('selected_tariff')} · {info['weather_class']} · "
                  f"plans {info['plans']} · {info['decision_path']}")
        print("last events:")
        for r in s["last_events"]:
            print(f"  {r['finished_at']} {r['type']:15} {r['id']:40} {r['outcome']} (calls {r['model_calls']})")
        print(f"alerts ({len(s['alerts'])} shown):")
        for al in s["alerts"]:
            print(f"  {al['date']} {al['at']} {al['severity'].upper()} human_ping={al['human_ping']}: "
                  f"{'; '.join(al['reasons'])}")
        return 0
    ctx = build_context(a.mode, a.stub)
    mode = "MOCK (rule-based stand-in)" if ctx.llm.s.mode == "mock" else f"LIVE Crusoe · {ctx.llm.s.model}"
    print(f"runner: model {mode} · world {ctx.env.name} · inbox {_rel(ctx.paths.inbox)} · "
          f"budget {ctx.max_calls_per_day} calls/day")
    if a.watch:
        t = watch(ctx, interval=a.interval, max_polls=a.max_polls)
        print(f"runner stopped: {t['polls']} polls, {t['drains']} drains, {t['handled']} events, "
              f"{t['model_calls']} model calls" + (" (shutdown event)" if t["stopped"] else ""))
    else:
        s = drain(ctx)
        print(f"drained: {s['handled']} events, {s['model_calls']} model calls"
              + (" (stopped at shutdown event)" if s["stopped"] else ""))
    return 0


def cmd_event(a) -> int:
    ev: dict[str, Any] = {"type": a.event_type}
    if a.id:
        ev["id"] = a.id
    if a.event_type == "day_start":
        ev.update(date=a.date, scenario=a.scenario, condition=a.condition)
        if a.forecast:
            ev["forecast_path"] = a.forecast
    elif a.event_type == "forecast_update":
        ev.update(date=a.date, forecast_path=a.forecast)
    elif a.event_type == "telemetry":
        ev.update(date=a.date, step=a.step, battery_kwh=a.battery_kwh, pv_kw=a.pv_kw)
    try:
        out = emit(ev)
    except ValidationError as exc:
        print(f"invalid event: {exc.errors()[0]['msg']}")
        return 1
    print(f"queued {out['type']} {out['id']} -> {_rel(Paths(_data_dir()).inbox)}")
    return 0


def _data_dir() -> Path:
    from greenstove import cli
    return cli.data_dir()


def register(sub) -> None:
    """Add `runner` and `event` subcommands to cli.main()'s argparse subparsers object."""
    s = sub.add_parser("runner", help="event-driven always-on runner: --once | --watch | status")
    s.add_argument("action", nargs="?", default="run", choices=["run", "status"])
    g = s.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="drain the inbox and exit (default)")
    g.add_argument("--watch", action="store_true", help="poll the inbox; stop on a shutdown event or Ctrl-C")
    s.add_argument("--interval", type=float, default=30.0, help="seconds between inbox polls (--watch)")
    s.add_argument("--max-polls", type=int, default=None, help=argparse_suppress())
    s.add_argument("--last", type=int, default=10, help="status: how many log lines and alerts")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.set_defaults(fn=cmd_runner)

    e = sub.add_parser("event", help="append an event to the runner inbox")
    es = e.add_subparsers(dest="event_type", required=True)
    d = es.add_parser("day_start"); d.add_argument("--date", required=True)
    d.add_argument("--scenario", default="demo-handoff-overcast"); d.add_argument("--forecast")
    d.add_argument("--condition", choices=list(CONDITIONS), default="C")
    f = es.add_parser("forecast_update"); f.add_argument("--date", required=True)
    f.add_argument("--forecast", required=True)
    t = es.add_parser("telemetry"); t.add_argument("--date", required=True)
    t.add_argument("--step", type=int, required=True); t.add_argument("--battery-kwh", type=float, required=True)
    t.add_argument("--pv-kw", type=float, required=True)
    es.add_parser("shutdown")
    for p in (d, f, t, es.choices["shutdown"]):
        p.add_argument("--id", help="explicit event id (idempotency key)")
    e.set_defaults(fn=cmd_event)


def argparse_suppress() -> str:
    import argparse
    return argparse.SUPPRESS


def main(argv: list[str] | None = None) -> int:
    """Standalone entry (python -m greenstove.runner ...) until the lead wires register() into cli.py."""
    import argparse
    import sys
    p = argparse.ArgumentParser(prog="greenstove-runner")
    register(p.add_subparsers(dest="cmd", required=True))
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
