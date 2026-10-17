# C0 fixture and contract proposal

Owner: Abel (environment and evidence, branch `environment-evidence`). Status: proposed at C0 for
joint sign-off under [contracts.md](contracts.md). Scope authority: [PLAN.md](../PLAN.md).
Built at the hackathon on 2026-10-17. Deterministic, no LLM.

`greenstove/models.py` follows the field names and layout of the agent layer's provisional
`greenstove/env_protocol.py` (branch `agents-memory`), so the agent layer can import these types
instead of its copies. The additions on top come from the C0 code review and are listed below.

## Operating model confirmed by Abel

Cooking happens **at home**. Each household has access to its own cooking appliance and
is connected to one local mini-grid. In this fixture, "station" means the mini-grid's
shared generation/storage and aggregate inverter boundary, not a communal cooking site.

A reservation books electrical capacity for a household's cooking session. Different
homes may cook simultaneously when their combined demand fits the shared limits; the
allocator does not need a shared-cooker availability check. Household agents submit needs
and accept offers; the local coordinator confirms capacity and code enforces modeled limits.

The MVP models cooking as the only household demand. C0 has no essential/background load;
a later increment may add an explicitly configured essential load. Calendar-derived
availability is represented by synthetic time windows, with no calendar integration.
Accepted bookings are assumed to be followed in the simulation. They do not establish
physical control over household appliances. Distribution losses, voltage/phase effects,
and individual household connection limits are omitted initially and must be disclosed.

Decentralized deployment means multiple local mini-grids could each run their own
coordinator. This build models one; cross-grid trading, federation and coordination are
outside the MVP. These clarifications require no C0 fixture, schema or arithmetic changes.

## Run it

```
pip install "pydantic>=2.7" pytest
python scripts/c0_check.py                          # both cases, compared to fixtures/c0/expected.json
python scripts/c0_check.py verify --plan fixtures/c0/plan-simultaneous.json   # one verdict as JSON
python scripts/c0_check.py evidence                 # regenerate trace/verdict fixtures and schemas/
pytest                                              # or: python -m unittest discover -s tests
```

`verify` exit codes: 0 passed, 1 failed verdict, 3 contract error (malformed input). The check
lives in `scripts/` because `greenstove/cli.py` is partner-owned (PLAN.md section 3).

## The scenario (PLAN.md A1 acceptance)

| Household | Power | Duration | Energy |
|---|---|---|---|
| A (`req-A`) | 1 kW | 30 min (2 steps) | 0.5 kWh |
| B (`req-B`) | 2 kW | 30 min (2 steps) | 1.0 kWh |

Inverter limit 2 kW. Both prefer 18:00 (step 72); window 17:00 to 20:00. Windows, lattice and tier
order are the agent layer's (`standard` [0,40), `sun` [40,64), `peak` [64,96); 3 prices each).
Tariff `{standard 8, sun 4, peak 13}`.

| Plan | Steps | Peak load | Energy | Spend | Verdict |
|---|---|---|---|---|---|
| Simultaneous | A 72-74, B 72-74 | 3 kW | 1.5 kWh | 19.50 | FAIL, `INVERTER_LIMIT` intervals [72, 74), value 3, limit 2, `req-A`+`req-B` |
| Sequential | A 72-74, B 74-76 | 2 kW | 1.5 kWh | 19.50 | PASS (B shifted 0.5 h) |

Energy is before system losses. Spend at peak 13/kWh: A = 1 × 0.5 × 13 = 6.50, B = 2 × 0.5 × 13 = 13.00.

**Explicit C0 limits**
- Power only: sufficient stored energy assumed, no essential load, PV and battery not modeled.
  Unmodeled conditions are *absent* from `conditions` (not passed) and listed in
  `coverage.not_modeled`; `Trace.battery_kwh` and `unserved_cooking_kwh` are `null`.
- Plans are hand-built (`accepted: true` stands for manual acceptance). The allocator produces
  plans from C1, and acceptance must then come from `auto_book` or a recorded approval.
- `forecast_expected` only. The C0 simulator refuses `forecast_downside`, `actual`, an
  `ActualScenario`, and non-zero essential load (`UNSUPPORTED_PHASE` / `UNSUPPORTED_PROFILE`).

## Fixtures

`fixtures/c0/`: `context.json`, `request.json`, `requirements.json`, `plan-*.json`, `trace-*.json`,
`verdict-*.json`, `expected.json`. JSON Schemas in `schemas/`. Regenerate with
`python scripts/make_c0_fixtures.py` (never by hand, so the hashes stay correct). Observation
fixtures are partner-owned and should cite a `verdict-*.json` `run_id`.

## Adopted from the agent layer (resolves open items 1 and 2)

- **Money**: float currency; prices per kWh; `max_cost` in currency; `shift_penalty` in currency
  **per hour** of shift. One rounding rule: a quote integrates `kW × 0.25 × interval price` over
  every interval (so cross-window sessions are priced per window) and rounds the total half-up
  to 0.01 with Decimal arithmetic (`greenstove/billing.py`).
- **Price lattice**: explicit allowed prices per window (`lattice`), `tier_order` non-decreasing.
  3 levels per window gives 27 valid tariffs, matching the search baseline.
- **Layout and names**: `ScenarioContext` top-level `inverter_kw`, battery fields, `windows`,
  `lattice`, `tier_order`, `day`, `weather_class`; phases `forecast_expected`,
  `forecast_downside`, `actual`; `Condition(name, passed, value, limit)`;
  `Feedback(code, interval, request_id, value, limit)`; `Requirements(min_service_fraction,
  max_shift_hours, essential_unserved_kwh)`; `verify(trace, requirements, context, plan)`;
  metric names `served_fraction`, `completed_sessions`, `requested_sessions`, `overload_steps`,
  `max_shift_hours`, `total_spend`, `revenue` (plus `peak_load_kw`, `cooking_energy_kwh`).
  `min_battery_kwh` and `unserved_*_kwh` are omitted in C0 because they are not modeled.

## Contract additions to sign off (from the C0 review)

1. **`config_hash` = station compatibility; new `scenario_hash` = the day.** This matches how
   `scenarios.config_hash()` already works. Fixture contexts are hash-checked on load.
2. **`Plan.tariff` (required).** The plan carries the tariff it was quoted on, so `simulate`
   keeps the exact contract signature `simulate(plan, context, phase, actual=None)` and the
   verifier recomputes every quote. `tariff_version` uses the stub's recipe on float values.
3. **`Trace.simulation_id`** (deterministic over station, scenario, full tariff, plan, phase,
   actual inputs, simulator version) plus `config_hash`, `scenario_hash`, `tariff_version`,
   `simulator_version`, `cooking_kw`.
4. **`Verdict.run_id` is deterministic**: over `simulation_id` + requirements + verifier
   version. The evidence store keys on it and refuses duplicates, so identical inputs map to
   one evidence record instead of a random id per call. Verdict also gains `simulation_id`,
   `scenario_hash`, `verifier_version`, `requirements`, `coverage`.
5. **Feedback gains** optional `end_interval`, `request_ids`, `message` (defaults keep existing code working).
6. **Verifier trust boundary**: context, requirements and the plan are authoritative; the trace
   is checked, not trusted (its `simulation_id`, loads and `overload_steps` must equal what
   the verifier recomputes, otherwise `ContractError`).
7. **Helper files** on Abel's side not yet in the PLAN.md ownership table: `billing.py`,
   `tariff.py`, `io.py`.

## What the agent layer changes when it switches to these types

A compatibility probe swapped these types into `agents-memory`'s `env_protocol.py`: the agent
layer (`scenarios.lattice_tariffs`, `agent_models.situation_of`, Keeper, briefs, Pricing Agent,
Note-writer, supervisor, evidence store) imports and runs unchanged. Three dev-stub spots need
small edits:

| File | Change |
|---|---|
| `scenarios.py` `make_context` | pass `scenario_hash=` (e.g. hash of seed, weather, battery, scales, day) |
| `stub_env.py` `allocate` | `Plan(..., tariff=tariff)` |
| `stub_env.py` `simulate` / `verify` | fill the new Trace and Verdict identity fields, or keep the stub on its provisional types until Abel's world adapter lands |

## Still open

3. **Allocator tie-break.** Proposed: adopt the stub's rule. Process requests by
   `(preferred_start, request_id)`; each household ranks starts by quote +
   `shift_penalty × shift hours`, skipping starts over `max_cost`, ties to the earliest start;
   reserve the best start that fits the inverter limit in every interval.

## Not in this revision (deliberately)

`INVALID_TARIFF` as a structured verdict (before C2), allocator and household authority (C1), PV,
battery and essential load, downside and actual phases, infeasibility bound, observation fixtures.
