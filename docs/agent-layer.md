# Agent layer: integration notes for Abel

**Status 15:03 PT: admission integrated.** `GREENSTOVE_WORLD=abel-admission` runs your
households, allocator (with the day's `max_shift_hours`) and battery-backed admission ledger
through a full day for **every phase** (forecast_expected, forecast_downside, actual): at each
booked start `admission.can_start(...)`, then `advance_interval(...)` with that phase's PV.
The energy verdict (`AdmissionWorld.verify` in `greenstove/world_adapter.py`) is partner glue
over your ledger results: inverter, ledger consistency, battery floor, essential load, every
booked session admitted, service, shift and recomputed quotes (your `billing.quote_for`). Its
coverage says `complete=True` with scope "admission-energy (Abel ledger; partner verdict glue,
pending sign-off)". **Please review it before we call anything PLAN_VERIFIED in the submission.**
`BatterySpec` uses the scenario's capacity and floor (reserve), inverter kW as charge/discharge
power and 0.95 efficiencies (synthetic). A pre-dawn proof (essential load before PV can cover it
must come from storage above the floor) makes impossible days INFEASIBLE with zero model calls.
Your C0 world (`GREENSTOVE_WORLD=abel`) is unchanged.

**Status 13:59 PT:** your branch is merged into `agents-memory` (commit 5f49292). The agent
layer imports your `greenstove/models.py` types (`env_protocol.py` now re-exports them).
`GREENSTOVE_WORLD=abel` runs agents on your C0 simulator and verifier
(`greenstove/world_adapter.py`; since 818bd91 it also uses your households and allocator). What happens there today:
the downside phase and actual execution are recorded as "not modeled", a pass with
`coverage.complete=false` is reported as "checked conditions passed … not a verified plan"
(never PLAN_VERIFIED), and on `c0-fixture` both households have `auto_book=false`, so your
verifier reports `SERVICE_SHORTFALL` until household acceptance exists. Try:
`GREENSTOVE_WORLD=abel gsa decide --scenario c0-fixture --condition A`.
The stub world now emits your full types too (scenario_hash, Plan.tariff, Trace and Verdict
ids, coverage `stub-full`), with deterministic verdict ids.

Branch `agents-memory`. Everything here is the partner's side of [PLAN.md](../PLAN.md)
section 7. It runs today against `greenstove/stub_env.py`, a labeled development
stand-in for your world, so the loop, memory and demo could be built before your code
landed. The stub is not evidence and is not used for results.

## What the agent layer needs from your world

`greenstove/env_protocol.py` holds provisional copies of the contract types and one
protocol. When your `models.py` lands, I replace the provisional types with imports from
it, and a thin `greenstove/world_adapter.py` maps your functions onto this protocol
(`load_environment()` picks it up automatically):

| Protocol method | Your contract function | Notes |
| --- | --- | --- |
| `household_requests(context)` | `household_requests(context)` | as is |
| `allocate(requests, tariff, context, proposal_id)` | `allocate(requests, tariff, context)` | adapter sets `Plan.proposal_id` |
| `simulate(plan, context, phase, actual=None)` | `simulate(plan, context, phase, actual)` | phases `forecast_expected`, `forecast_downside`, `actual` |
| `verify(trace, requirements, context, plan)` | `verify(trace, requirements)` | adapter drops the extra arguments; `Verdict.run_id` must be unique per call |
| `lower_bound(context, requirements)` | your infeasibility bound | return `None` when no proof |

Fields the agent layer reads: `ScenarioContext` (`scenario_id`, `config_hash`, `day`,
`weather_class`, battery fields, `inverter_kw`, the three 96-step profiles, `requests`,
`windows`, `lattice`, `tier_order`), `Verdict` (`run_id`, `phase`, `passed`, `metrics`,
`feedback_codes[].code/interval/request_id/value/limit`). Metric names used in briefs and
notes: `served_fraction`, `min_battery_kwh`, `unserved_cooking_kwh`,
`unserved_essential_kwh`, `overload_steps`, `max_shift_hours`, `total_spend`. If yours
differ, tell me and I map them in the adapter.

## What your evaluation can call

```python
from greenstove.supervisor import run_agent_day
run = run_agent_day(context, memory_mode,            # "off" | "history" | "curated"  (A / B / C)
                    requirements, env=env, llm=llm, evidence=evidence_store,
                    brain=frozen_brain, history=frozen_dev_records,
                    phase="evaluation", actual=actual_scenario)
```

`RunRecord` gives `status` (`PLAN_VERIFIED`, `NO_VERIFIED_PLAN`, `INFEASIBLE`, `ERROR`),
`reason`, `attempts`, `calls` (tokens, latency, request ids), `candidate_evaluations`,
`simulator_runs`, `execution.outcome_passed`, and the `brief` actually supplied (entry
ids, tokens, snapshot hash). In evaluation, notes are disabled by default; if enabled
they are quarantined and never served. Pass the same frozen `brain` and `history` to
every day so memory never learns from held-out results.

## Commands

```text
python -m greenstove smoke --mode mock|crusoe     # catalog check + one structured call, recorded
python -m greenstove plan --scenario <name> --brain off|history|curated [--execute]
python -m greenstove warmup                       # development days -> curated brain
python -m greenstove brain show|audit|freeze
python -m greenstove demo [--scene ...] [--pause] [--replay file]
```

Demo order, timing and fallbacks: [demo-runbook.md](demo-runbook.md). Diagram reconciliation and open questions for you: [decisions.md](decisions.md).
