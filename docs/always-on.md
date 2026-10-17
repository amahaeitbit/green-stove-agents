# Always-on, event-driven runner

`greenstove/runner.py` keeps the agents available all day without spending model calls while
nothing happens. Agents wake only when an event arrives; most events are answered by code.

## Files (under the data dir, `GREENSTOVE_DATA_DIR`, default `data/`)

| File | Written by | Content |
| --- | --- | --- |
| `events/inbox.jsonl` | `gsa event ...` or any process that appends a JSON line | append-only events |
| `events/state.json` | runner | processed event ids, inbox byte offset, model calls and tokens per date, each date's plan summary and expected battery trace |
| `events/log.jsonl` | runner | one line per handled event: id, type, started/finished, outcome, model calls, tokens, output paths |
| `events/alerts.jsonl` | runner | telemetry alerts, each with `human_ping: true` |

## Events and what they cost

| Event | Handling | Model calls |
| --- | --- | --- |
| `day_start {date, scenario, forecast_path?, condition}` | one fresh agent day (`supervisor.run_agent_day`, development phase, curated brain, actual day simulated), `decision.md` written into the run folder, plan remembered | the supervisor's bounded attempts (at most `GREENSTOVE_MAX_CALLS_PER_RUN`), zero if the energy bound proves the day infeasible |
| `forecast_update {date, forecast_path}` | re-plan **only** if the weather class changed or downside PV kWh moved by more than 15% from the forecast the plan used; otherwise `skipped: forecast within tolerance` | zero unless the change is material |
| `telemetry {date, step, battery_kwh, pv_kw}` | compare with the plan's expected battery at that step (stored at plan time from the environment's `forecast_expected` simulation); more than 0.5 kWh below plan or below the reserve writes an alert | always zero |
| `shutdown` | stops the watch loop; later events stay queued for the next start | zero |

Rules that hold for every event:

- **Idempotent.** An event id is processed once; a repeated id is logged as `ignored: duplicate event id`.
- **Malformed input never crashes the loop.** Bad JSON or a schema violation (pydantic, `extra=forbid`) is logged as `rejected` and skipped; an exception inside a handler is logged as `error` and the loop continues.
- **Daily budget.** `GREENSTOVE_RUNNER_MAX_CALLS_PER_DAY` (default 20) is checked before a day runs, and the run's own call cap is lowered to what is left, so one day cannot overrun it. An exhausted budget is logged as `skipped: daily model-call budget exhausted`.
- **Crash-safe.** State is saved after every event; a restart resumes from the inbox offset and never re-runs a finished day.

```bash
gsa event day_start --date 2026-10-18 --scenario demo-handoff-overcast [--forecast data/forecast/x.json] --condition C
gsa event forecast_update --date 2026-10-18 --forecast data/forecast/asmara-eritrea-2026-10-18-live.json
gsa event telemetry --date 2026-10-18 --step 72 --battery-kwh 1.1 --pv-kw 0.2
gsa event shutdown
gsa runner --once                  # drain the inbox and exit
gsa runner --watch --interval 30   # always-on: poll inbox size/mtime, drain only when it changed
gsa runner status                  # last events, alerts, calls per day
```

(Until `cli.py` wires `runner.register(sub)`, the same commands run as `python -m greenstove.runner ...`.)

## Why this is efficient

- **Zero idle calls.** The watch loop compares the inbox's size and modification time; an unchanged file means no read, no parse and no model call. Idle cost is one `stat` per interval.
- **Re-plan only on material change.** A new forecast that keeps the weather class and stays within 15% on downside PV keeps the verified plan. The tolerance is on the downside profile because that is the one the verifier must pass.
- **Telemetry is checked by code.** Comparing a battery reading with the expected trace needs arithmetic, not a model.
- **Deterministic shortcuts stay in front.** The supervisor's energy bound still answers infeasible days with zero calls, also when a forecast update triggers the re-plan.

## Done Means Done

The runner works unattended between events and makes one of four decisions for each, all recorded in `log.jsonl`:

| Decision | When |
| --- | --- |
| **continue** | telemetry within plan, forecast within tolerance, duplicate event |
| **re-plan** | `day_start`, or a forecast update with a weather-class change or a >15% downside PV change |
| **stop** | budget exhausted for that date, `shutdown`, or the supervisor's own honest stops (INFEASIBLE, NO_VERIFIED_PLAN) |
| **ping a human** | telemetry more than 0.5 kWh below plan or below reserve: an alert with `human_ping: true` and the decision record's path |

Telemetry never triggers a re-plan. Intra-day rebooking is outside MVP scope (`docs/contracts.md`), so
the honest action is to alert the operator, not to change bookings households already accepted. Status
still comes only from the verifier, and requirements are never relaxed. This is an operations loop over
the simulated station; it is not a claim of deployed autonomy (README, PLAN.md section 1).

## Runtime boundary: NemoClaw / OpenShell

`deploy/nemoclaw/` runs the same `gsa runner --watch` inside an NVIDIA NemoClaw sandbox (OpenShell
runtime). The sandbox adds what application code cannot enforce on itself:

- **Network:** egress only to the Crusoe inference endpoint, You.com search and GitHub; anything else is denied.
- **Filesystem:** configuration, code, evaluation fixtures and the frozen brain snapshot are read-only; only `data/` is writable.

This **complements the Keeper; it does not replace it.** The Keeper still decides which observations
enter the curated brain (evidence ids, numbers, TTL, conflicts), and pydantic schemas and the verifier
still gate every proposal. The sandbox guarantees that even a misbehaving tool or prompt-injected
model output cannot rewrite the frozen snapshot, the fixtures or the configuration, or send data to an
unlisted host. `deploy/nemoclaw/guardrail-check.py` demonstrates both denials and reports honestly
when it is run outside the sandbox. See `deploy/nemoclaw/README.md` for which steps were verified.
