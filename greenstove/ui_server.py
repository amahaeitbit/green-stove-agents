"""Local web UI: `gsa ui` serves greenstove/ui/index.html and a small JSON API (stdlib only).

One in-memory session per server with its own workspace <data_dir>/ui-session/<stamp>/
(EvidenceStore + BrainStore + usage + runner inbox), per-scenario household overrides and the
last run per scenario. Model and world calls are serialized with one lock. Binds to 127.0.0.1
only. Status always comes from the supervisor/verifier; the household "ask" agent is
deterministic code, not a model. Key values are never returned or logged.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from greenstove import cli
from greenstove.agent_models import ObservationProposal
from greenstove.billing import quote_for
from greenstove.brain import insight, keeper
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import (STEP_HOURS, STEPS, ContractError, HouseholdRequest, Plan, ScenarioContext,
                                     Trace, load_environment, with_scenario_hash)
from greenstove.evidence import EvidenceStore
from greenstove.household_messages import HouseholdMessage, household_outcomes, render
from greenstove.llm import LLMClient, LLMSettings, ROOT
from greenstove.models import UNSERVED_NOT_OFFERED, step_label
from greenstove.replay_charts import line_chart
from greenstove.scenarios import (DEFAULT_REQUIREMENTS, DEMO_SCENARIOS, DEV_SCENARIOS, HELDOUT_SCENARIOS,
                                  make_actual)

UI_FILE = Path(__file__).resolve().parent / "ui" / "index.html"
# Presenter layer next to the page (fixed allow-list; never a path from the request).
STATIC = {"/present.css": "text/css", "/present.js": "text/javascript"}
PLACEHOLDER = ("<!doctype html><html><head><meta charset='utf-8'><title>Green-Stove UI</title></head>"
               "<body><h1>Green-Stove Agents</h1><p>The UI page (greenstove/ui/index.html) is not built yet. "
               "The JSON API is live at <a href='/api/state'>/api/state</a>.</p></body></html>")
MEMORY_MODES = ["off", "history", "curated"]
SCENARIOS = {**DEV_SCENARIOS, **DEMO_SCENARIOS, **HELDOUT_SCENARIOS}
LIVE_NEEDED = "live mode needs CRUSOE_API_KEY and GREENSTOVE_LIVE_SPEND_OK=1 (mock mode active)"
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _bad(msg: str) -> ApiError:
    return ApiError(400, msg)


def _step(value: Any, field: str) -> int:
    m = _HHMM.match(str(value)) if isinstance(value, str) else None
    if not m:
        raise _bad(f"{field} must be HH:MM")
    minutes = int(m.group(1)) * 60 + int(m.group(2))
    if int(m.group(2)) >= 60 or minutes % 15 or minutes > 24 * 60:
        raise _bad(f"{field} must be on the 15-minute grid between 00:00 and 24:00")
    return minutes // 15


def _number(body: dict, key: str, *, positive: bool = False) -> float:
    v = body[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _bad(f"{key} must be a number")
    if v < 0 or (positive and v == 0):
        raise _bad(f"{key} must be {'positive' if positive else 'non-negative'}")
    return float(v)


def _end_label(step: int) -> str:
    return "24:00" if step >= STEPS else step_label(step)


def household_dict(r: HouseholdRequest) -> dict[str, Any]:
    return {"household_id": r.household_id, "request_id": r.request_id, "power_kw": r.power_kw,
            "minutes": r.duration_steps * 15, "preferred": step_label(r.preferred_start),
            "earliest": step_label(r.earliest_start), "latest_finish": _end_label(r.latest_finish),
            "max_cost": r.max_cost, "shift_penalty": r.shift_penalty, "auto_book": r.auto_book}


def window_of(ctx: ScenarioContext, step: int) -> str:
    return next((w.name for w in ctx.windows if w.start_step <= step < w.end_step), "")


def mid_lattice(ctx: ScenarioContext) -> dict[str, float]:
    return {w.name: sorted(ctx.lattice[w.name])[len(ctx.lattice[w.name]) // 2] for w in ctx.windows}


def household_options(ctx: ScenarioContext, req: HouseholdRequest, prices: dict[str, float]) -> dict[str, Any]:
    """Deterministic household agent: score = quote + shift_penalty * shift_hours over every start."""
    opts = []
    for s in range(req.earliest_start, req.latest_finish - req.duration_steps + 1):
        q = quote_for(req.power_kw, s, s + req.duration_steps, prices, ctx)
        shift = abs(s - req.preferred_start) * STEP_HOURS
        opts.append({"start": step_label(s), "end": _end_label(s + req.duration_steps), "quote": q,
                     "shift_hours": shift, "score": round(q + req.shift_penalty * shift, 2),
                     "within_budget": q <= req.max_cost + 1e-9, "_step": s})
    opts.sort(key=lambda o: (o["score"], o["shift_hours"], o["_step"]))
    top = opts[:5]
    choice = next((o for o in top if o["within_budget"]), None)
    if choice is None:
        best = top[0] if top else None
        message = (f"Nothing in my window fits my budget of {req.max_cost:.2f}"
                   + (f"; the best option is {best['start']} for {best['quote']:.2f}." if best else "."))
    elif choice["_step"] == req.preferred_start:
        message = f"I'd cook at {choice['start']} for {choice['quote']:.2f}, my preferred time."
    else:
        h = choice["shift_hours"]
        way = "earlier" if choice["_step"] < req.preferred_start else "later"
        win, pref_win = window_of(ctx, choice["_step"]), window_of(ctx, req.preferred_start)
        why = (f"because the {win} price is lower" if prices.get(win, 0) < prices.get(pref_win, 0)
               else "because it has the best cost plus inconvenience")
        message = f"I'd cook at {choice['start']} for {choice['quote']:.2f} ({h:g} h {way} than I prefer) {why}."
    for o in top:
        o.pop("_step")
    if choice is not None:
        choice = dict(choice)
    return {"household_id": req.household_id, "prices": prices, "options": top, "choice": choice,
            "message": message}


class Session:
    """Workspace, overrides, last runs and the last forecast; every public method takes the lock."""

    def __init__(self, data_dir: Path, mode: str | None):
        self.data_dir = Path(data_dir)
        self.lock = threading.RLock()
        self.env = load_environment()
        self.llm = LLMClient(LLMSettings.from_env(mode), cli.MOCKS)
        self._guardrail: str | None = None
        self.new_workspace()

    # ---- workspace -------------------------------------------------------------------
    def new_workspace(self) -> str:
        with self.lock:
            self.stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
            self.root = self.data_dir / "ui-session" / self.stamp
            self.evidence = EvidenceStore(self.root / "evidence")
            self.brain = BrainStore(self.root / "brain")
            self.llm.usage_path = self.root / "usage.jsonl"
            self.overrides: dict[str, dict[str, dict[str, Any]]] = {}
            self.last: dict[str, dict[str, Any]] = {}
            self.forecast = None
            self._dod = None
            return self.stamp

    def dod(self):
        if self._dod is None:
            from greenstove.spec_agent import write_spec
            self._dod = write_spec(cli.goal_text(), DEFAULT_REQUIREMENTS, self.llm, cache_dir=self.data_dir / "spec")
        return self._dod

    # ---- scenarios -------------------------------------------------------------------
    def context(self, name: Any) -> ScenarioContext:
        if not isinstance(name, str) or name not in SCENARIOS:
            raise ApiError(404, f"unknown scenario {name!r}")
        base = cli.context_for(name)
        ov = self.overrides.get(name, {})
        if not ov:
            return base
        reqs = [HouseholdRequest.model_validate({**r.model_dump(), **ov.get(r.household_id, {})})
                for r in base.requests]
        return with_scenario_hash(base.model_copy(update={"requests": reqs}))

    @staticmethod
    def request(ctx: ScenarioContext, household_id: Any) -> HouseholdRequest:
        r = next((r for r in ctx.requests if r.household_id == household_id), None)
        if r is None:
            raise ApiError(404, f"unknown household {household_id!r}")
        return r

    def state(self) -> dict[str, Any]:
        with self.lock:
            model = self.llm.s.model if self.llm.s.mode == "crusoe" else f"mock-rules (live: {self.llm.s.model})"
            return {"mode": self.llm.s.mode, "model": model, "world": self.env.name,
                    "session": self.stamp,
                    "scenarios": [{"name": n, "weather_class": s[1], "battery_frac": s[2], "day": s[5]}
                                  for n, s in SCENARIOS.items()],
                    "memory_modes": MEMORY_MODES, "brain": insight.stats(self.brain, self.evidence)}

    def scenario(self, body: dict) -> dict[str, Any]:
        with self.lock:
            ctx = self.context(body.get("name"))
            return {"name": body["name"], "weather_class": ctx.weather_class,
                    "station": {"inverter_kw": ctx.inverter_kw, "battery_capacity_kwh": ctx.battery_capacity_kwh,
                                "initial_battery_kwh": ctx.initial_battery_kwh, "reserve_kwh": ctx.reserve_kwh},
                    "windows": [{"name": w.name, "start": step_label(w.start_step), "end": _end_label(w.end_step),
                                 "prices": list(ctx.lattice[w.name])} for w in ctx.windows],
                    "tier_order": list(ctx.tier_order),
                    "households": [household_dict(r) for r in ctx.requests],
                    "pv": {"expected": list(ctx.forecast_pv_kw), "downside": list(ctx.downside_pv_kw)},
                    "pv_kwh": {"expected": round(sum(ctx.forecast_pv_kw) * STEP_HOURS, 2),
                               "downside": round(sum(ctx.downside_pv_kw) * STEP_HOURS, 2)}}

    def update_household(self, body: dict) -> dict[str, Any]:
        with self.lock:
            name, hid = body.get("scenario"), body.get("household_id")
            ctx = self.context(name)
            req = self.request(ctx, hid)
            upd: dict[str, Any] = {}
            for key, field in (("preferred", "preferred_start"), ("earliest", "earliest_start"),
                               ("latest_finish", "latest_finish")):
                if key in body:
                    upd[field] = _step(body[key], key)
            if "max_cost" in body:
                upd["max_cost"] = _number(body, "max_cost")
            if "power_kw" in body:
                upd["power_kw"] = _number(body, "power_kw", positive=True)
            if "minutes" in body:
                m = _number(body, "minutes", positive=True)
                if m % 15:
                    raise _bad("minutes must be a multiple of 15")
                upd["duration_steps"] = int(m) // 15
            if "auto_book" in body:
                if not isinstance(body["auto_book"], bool):
                    raise _bad("auto_book must be true or false")
                upd["auto_book"] = body["auto_book"]
            try:
                new = HouseholdRequest.model_validate({**req.model_dump(), **upd})
            except ValueError as exc:
                raise _bad(f"invalid household window: {_first_error(exc)}") from None
            prev = self.overrides.setdefault(name, {}).get(hid, {})
            self.overrides[name][hid] = {**prev, **upd}
            return household_dict(new)

    def ask(self, body: dict) -> dict[str, Any]:
        with self.lock:
            name = body.get("scenario")
            ctx = self.context(name)
            req = self.request(ctx, body.get("household_id"))
            prices = body.get("prices") or self.last.get(name, {}).get("tariff") or mid_lattice(ctx)
            if not isinstance(prices, dict) or set(prices) != {w.name for w in ctx.windows}:
                raise _bad(f"prices must name every window {[w.name for w in ctx.windows]}")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in prices.values()):
                raise _bad("prices must be non-negative numbers")
            return household_options(ctx, req, {k: float(v) for k, v in prices.items()})

    # ---- one agent day -----------------------------------------------------------------
    def run(self, body: dict) -> dict[str, Any]:
        from greenstove import supervisor
        with self.lock:
            name = body.get("scenario")
            ctx = self.context(name)
            mode = body.get("memory_mode", "curated")
            if mode not in MEMORY_MODES:
                raise _bad(f"memory_mode must be one of {MEMORY_MODES}")
            n_max = body.get("max_attempts", 3)
            if isinstance(n_max, bool) or not isinstance(n_max, int) or not 1 <= n_max <= 6:
                raise _bad("max_attempts must be an integer from 1 to 6")
            fc_info = None
            if body.get("use_forecast"):
                if self.forecast is None:
                    raise ApiError(409, "no forecast in this session; POST /api/forecast first")
                from greenstove.forecast.pipeline import context_from_forecast
                ctx = context_from_forecast(self.forecast, ctx)
                f = self.forecast
                fc_info = {"place": f.place, "date": f.date, "weather_class": f.weather_class, "pv_kwh": f.pv_kwh}
            dod = self.dod()
            rec = supervisor.run_agent_day(ctx, mode, DEFAULT_REQUIREMENTS, env=self.env, llm=self.llm,
                                           evidence=self.evidence, brain=self.brain, phase="development",
                                           actual=make_actual(ctx, seed=ctx.day + 7), max_attempts=n_max, dod=dod)
            out = self._run_view(ctx, rec, dod)
            if fc_info:
                out["forecast"] = fc_info
            tariff = rec.selected_tariff or next((a.proposal.prices for a in reversed(rec.attempts) if a.proposal),
                                                 None)
            if tariff:
                self.last[name] = {"tariff": dict(tariff), "run_id": rec.agent_run_id}
            return out

    def _load(self, run_id: str, name: str, model):
        path = self.evidence.run_dir(run_id) / f"{name}.json"
        return model.model_validate_json(path.read_text()) if path.exists() else None

    def _run_view(self, ctx: ScenarioContext, rec, dod) -> dict[str, Any]:
        calls = rec.calls
        run = {"agent_run_id": rec.agent_run_id, "status": rec.status, "reason": rec.reason,
               "attempts": [{"n": a.n, "prices": a.proposal.prices if a.proposal else None, "passed": a.passed,
                             "feedback": list(a.feedback),
                             "cited": list(a.proposal.cited_observation_ids) if a.proposal else [],
                             "rationale": a.proposal.rationale if a.proposal else "; ".join(a.validation_errors[:2])}
                            for a in rec.attempts],
               "decisions": [d.model_dump() for d in rec.decisions], "tariff": rec.selected_tariff,
               "calls": len(calls), "tokens": sum(c.prompt_tokens + c.completion_tokens for c in calls),
               "brief": {"mode": rec.brief.mode, "entry_ids": list(rec.brief.entry_ids), "tokens": rec.brief.tokens,
                         "text": rec.brief.text}}
        plan = down = shown = None
        if rec.selected_proposal_id:
            n = rec.selected_proposal_id.rsplit("-p", 1)[-1]
            plan = self._load(rec.agent_run_id, f"reservations_{n}", Plan)
            down = self._load(rec.agent_run_id, f"trace_{n}_forecast_downside", Trace)
            shown = self._load(rec.agent_run_id, "trace_actual", Trace) or down
        owner = {r.request_id: r.household_id for r in ctx.requests}
        bookings, messages = [], []
        if plan is not None and shown is not None:
            outcomes = shown.reservation_outcomes or {}
            bookings = [{"household_id": owner.get(r.request_id, "?"), "start": step_label(r.start_step),
                         "end": _end_label(r.end_step), "kw": r.power_kw, "cost": r.locked_cost,
                         "status": outcomes.get(r.reservation_id) or ("booked" if r.accepted else "approval_needed")}
                        for r in plan.reservations]
            msgs = household_outcomes(ctx, plan, shown)
        else:                                       # nothing verified: nothing is offered or dispatched
            msgs = [HouseholdMessage(household_id=r.household_id, request_id=r.request_id, status="NOT_OFFERED",
                                     reason=UNSERVED_NOT_OFFERED) for r in ctx.requests]
        for m, text in zip(msgs, render(msgs)):
            messages.append({"household_id": m.household_id, "status": m.status, "reason": m.reason, "text": text})
        chart = None
        if down is not None:
            series = {"PV (kW)": down.pv_kw, "demand (kW)": down.demand_kw}
            if down.battery_kwh:
                series["battery (kWh)"] = down.battery_kwh
            limits = {f"inverter {ctx.inverter_kw:g} kW": ctx.inverter_kw,
                      f"reserve {ctx.reserve_kwh:g} kWh": ctx.reserve_kwh}
            chart = line_chart(series, limits=limits, title="Selected plan on the downside forecast",
                               y_unit="kW / kWh", emphasis="demand (kW)")
        return {"run": run, "bookings": bookings, "messages": messages,
                "notes": [{"action": r.action, "code": r.code, "observation_id": r.observation_id,
                           "reasons": list(r.reasons)} for r in rec.notes],
                "chart_svg": chart,
                "spec": {"checks": [{"metric": c.metric, "op": c.op, "threshold": c.threshold, "source": c.source}
                                    for c in (dod.checks if dod else [])]}}

    # ---- brain and evidence -------------------------------------------------------------
    def brain_view(self) -> dict[str, Any]:
        with self.lock:
            return {"stats": insight.stats(self.brain, self.evidence),
                    "entries": [{"id": e.id, "version": e.version, "status": e.status, "phase": e.phase,
                                 "outcome_passed": e.outcome_passed, "tariff": e.tariff, "codes": e.feedback_codes,
                                 "situation": e.situation, "confirmations": e.confirmations,
                                 "conflict_group": e.conflict_group, "expires_at": e.expires_at,
                                 "evidence_ids": e.evidence_ids} for e in self.brain.entries()]}

    def explain(self, obs_id: str | None) -> dict[str, Any]:
        if not obs_id:
            raise _bad("id is required")
        with self.lock:
            out = insight.explain(obs_id, self.brain, self.evidence)
        if out is None:
            raise ApiError(404, f"no brain entry {obs_id!r}")
        return out

    def evidence_view(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{"run_id": r.run_id, "agent_run_id": r.agent_run_id, "scenario_id": r.scenario_id,
                     "phase": r.phase, "passed": r.passed, "tariff": r.tariff,
                     "served_fraction": r.metrics.get("served_fraction"),
                     "min_battery_kwh": r.metrics.get("min_battery_kwh")} for r in self.evidence.all()[-40:]]

    def note(self, body: dict) -> dict[str, Any]:
        ids = body.get("evidence_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise _bad("evidence_ids must be a non-empty list of ids")
        with self.lock:
            try:
                proposal = ObservationProposal(evidence_ids=ids, description=str(body.get("description", "")),
                                               situation_tags=list(body.get("tags") or []),
                                               claimed_passed=body.get("claimed_passed"))
            except ValueError as exc:
                raise _bad(f"invalid note: {_first_error(exc)}") from None
            first = next((r for i in ids if (r := self.evidence.get(i)) is not None), None)
            cfg = keeper.KeeperConfig(config_hash=first.config_hash if first else "unknown",
                                      clock=first.day if first else 0)
            r = keeper.admit(proposal, self.evidence, cfg, self.brain)
            return {"action": r.action, "code": r.code, "observation_id": r.observation_id, "reasons": list(r.reasons)}

    # ---- sponsor tools ---------------------------------------------------------------
    def _live_search(self) -> bool:
        return self.llm.s.mode == "crusoe" and bool(os.environ.get("YDC_API_KEY", "").strip())

    def guardrail(self) -> str:
        if self._guardrail is None:
            from greenstove.demo import guardrail_status
            self._guardrail = guardrail_status()
        return self._guardrail

    def tools(self) -> dict[str, Any]:
        from greenstove import crusoe
        with self.lock:
            s, usage = self.llm.s, self.root / "usage.jsonl"
            return {"crusoe": {"mode": s.mode, "model": s.model, "base_url": s.base_url,
                               "key_present": bool(s.api_key), "spend_ok": bool(s.live_spend_ok),
                               "usage": crusoe.usage_rows(usage) if usage.exists() else []},
                    "youcom": {"key_present": bool(os.environ.get("YDC_API_KEY", "").strip()),
                               "search": "live" if self._live_search() else "mock"},
                    "nemoclaw": {"guardrail": self.guardrail(), "kit": "deploy/nemoclaw/README.md"}}

    def crusoe_models(self) -> list[dict[str, Any]]:
        from greenstove import crusoe
        if self.llm.s.mode != "crusoe":
            raise ApiError(409, LIVE_NEEDED)
        with self.lock:
            return crusoe.catalog_rows(self.llm.list_models())

    def make_forecast(self, body: dict) -> dict[str, Any]:
        from greenstove.forecast import pipeline
        place = body.get("place")
        if not isinstance(place, str) or not place.strip():
            raise _bad("place is required")
        try:
            lat, lon = float(body.get("lat")), float(body.get("lon"))
            day = date.fromisoformat(str(body.get("date")))
        except (TypeError, ValueError):
            raise _bad("lat and lon must be numbers and date YYYY-MM-DD") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise _bad("lat/lon out of range")
        with self.lock:
            rec = pipeline.build_forecast(place.strip(), lat, lon, day, llm=self.llm,
                                          search_mode="live" if self._live_search() else "mock")
            self.forecast_path = pipeline.save(rec, self.root / "forecast")
            self.forecast = rec
            src = rec.sources[0]["url"] if rec.sources else None
            return {"place": rec.place, "date": rec.date, "weather_class": rec.weather_class,
                    "cloud_fraction": rec.cloud_fraction, "precip_prob_pct": rec.precip_prob_pct,
                    "status": rec.status, "search_mode": rec.search_mode, "extractor_mode": rec.extractor_mode,
                    "source_url": (getattr(rec.accepted, "source_url", None) if rec.accepted else None) or src,
                    "quote": getattr(rec.accepted, "quote", None) if rec.accepted else None,
                    "pv_kwh": rec.pv_kwh, "downside_pv_kwh": rec.downside_pv_kwh, "clear_sky_kwh": rec.clear_sky_kwh,
                    "chart_svg": line_chart({"forecast PV": rec.forecast_pv_kw, "downside PV": rec.downside_pv_kw},
                                            y_unit="kW"),
                    "rejected": [str(r.get("reason")) for r in rec.rejected]}

    # ---- always-on runner ----------------------------------------------------------------
    def runner_ctx(self):
        from greenstove import runner
        return runner.RunnerContext(data_dir=self.root, env=self.env, llm=self.llm, evidence=self.evidence,
                                    brain=self.brain, requirements=DEFAULT_REQUIREMENTS,
                                    max_calls_per_day=runner.max_calls_from_env(), dod=self.dod())

    def runner_event(self, body: dict) -> dict[str, Any]:
        from greenstove import runner
        if body.get("type") not in ("day_start", "telemetry", "forecast_update"):
            raise _bad("type must be day_start, telemetry or forecast_update")
        ev = dict(body)
        with self.lock:
            if ev["type"] == "forecast_update" and not ev.get("forecast_path"):
                if self.forecast is None:
                    raise ApiError(409, "no forecast in this session; POST /api/forecast first")
                ev["forecast_path"] = str(self.forecast_path)
            try:
                return runner.emit(ev, self.root)
            except ValueError as exc:
                raise _bad(f"invalid event: {_first_error(exc)}") from None

    def runner_drain(self) -> dict[str, Any]:
        from greenstove import runner
        with self.lock:
            ctx = self.runner_ctx()
            before = len(runner._read_jsonl(ctx.paths.log))
            runner.drain(ctx)
            rows = runner._read_jsonl(ctx.paths.log)[before:]
            return {"handled": [{"id": r.get("id"), "type": r.get("type"), "outcome": r.get("outcome"),
                                 "calls": r.get("model_calls", 0)} for r in rows]}

    def runner_status(self) -> dict[str, Any]:
        from greenstove import runner
        with self.lock:
            return json.loads(json.dumps(runner.status(self.root), default=str))


def _first_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            e = errors()[0]
            return f"{'.'.join(map(str, e.get('loc', ())))}: {e.get('msg')}".strip(": ")[:200]
        except Exception:  # noqa: BLE001
            pass
    return str(exc).splitlines()[0][:200]


class Handler(BaseHTTPRequestHandler):
    server_version = "GreenStoveUI/1"

    def log_message(self, format: str, *args: Any) -> None:   # quiet: never log bodies (or keys)
        return

    @property
    def app(self) -> Session:
        return self.server.app                                    # type: ignore[attr-defined]

    def _send(self, status: int, payload: Any, ctype: str = "application/json") -> None:
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise _bad("body must be JSON") from None
        if not isinstance(body, dict):
            raise _bad("body must be a JSON object")
        return body

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        try:
            if method == "GET" and url.path in ("/", "/index.html"):
                html = UI_FILE.read_text() if UI_FILE.exists() else PLACEHOLDER
                return self._send(200, html, "text/html")
            if method == "GET" and url.path in STATIC:
                asset = UI_FILE.parent / url.path.lstrip("/")
                if not asset.exists():
                    raise ApiError(404, f"no file {url.path}")
                return self._send(200, asset.read_text(), STATIC[url.path])
            route = ROUTES.get((method, url.path))
            if route is None:
                raise ApiError(404, f"no route {method} {url.path}")
            arg = self._body() if method == "POST" else parse_qs(url.query)
            self._send(200, route(self.app, arg))
        except ApiError as exc:
            self._send(exc.status, {"error": exc.message})
        except (ContractError, ValueError) as exc:
            self._send(400, {"error": _first_error(exc)})
        except Exception as exc:  # noqa: BLE001 - report the class, never a traceback or settings
            self._send(500, {"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")


ROUTES = {
    ("GET", "/api/state"): lambda s, q: s.state(),
    ("POST", "/api/scenario"): lambda s, b: s.scenario(b),
    ("POST", "/api/household/update"): lambda s, b: s.update_household(b),
    ("POST", "/api/household/ask"): lambda s, b: s.ask(b),
    ("POST", "/api/run"): lambda s, b: s.run(b),
    ("GET", "/api/brain"): lambda s, q: s.brain_view(),
    ("GET", "/api/brain/explain"): lambda s, q: s.explain((q.get("id") or [None])[0]),
    ("GET", "/api/evidence"): lambda s, q: s.evidence_view(),
    ("POST", "/api/brain/note"): lambda s, b: s.note(b),
    ("POST", "/api/reset"): lambda s, b: {"session": s.new_workspace()},
    ("GET", "/api/tools"): lambda s, q: s.tools(),
    ("GET", "/api/crusoe/models"): lambda s, q: s.crusoe_models(),
    ("POST", "/api/forecast"): lambda s, b: s.make_forecast(b),
    ("POST", "/api/runner/event"): lambda s, b: s.runner_event(b),
    ("POST", "/api/runner/drain"): lambda s, b: s.runner_drain(),
    ("GET", "/api/runner/status"): lambda s, q: s.runner_status(),
}


def make_server(port: int, data_dir: Path, mode: str | None) -> ThreadingHTTPServer:
    """Server bound to 127.0.0.1 only (port 0 picks a free port); serve_forever() to run it."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    srv.app = Session(Path(data_dir), mode)                   # type: ignore[attr-defined]
    return srv


def cmd_ui(a) -> int:
    srv = make_server(a.port, cli.data_dir(), a.mode)
    app: Session = srv.app                                    # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    label = "MOCK (rule-based stand-in, not model quality)" if app.llm.s.mode == "mock" \
        else f"LIVE Crusoe · {app.llm.s.model}"
    print(f"model: {label}   world: {app.env.name}   session: {app.root.relative_to(ROOT) if app.root.is_relative_to(ROOT) else app.root}")
    print(f"Green-Stove UI: {url}  (Ctrl-C to stop)")
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def register(sub) -> None:
    s = sub.add_parser("ui", help="local web UI on 127.0.0.1 (scenarios, households, runs, brain, tools)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--mode", choices=["mock", "live", "crusoe"])
    s.add_argument("--no-open", action="store_true", help="do not open a browser")
    s.set_defaults(fn=cmd_ui)
