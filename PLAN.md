# Green-Stove Agents: build plan for the coding agent

Read this whole file, then [docs/architecture.md](docs/architecture.md) and
[docs/simulator-and-evaluation.md](docs/simulator-and-evaluation.md), before writing any
code. Build one milestone at a time, run its acceptance check, commit, report the result,
and wait for me before starting the next.

## 1. What we are building

An agent team that runs a village's solar cooking mini-grid without a person on site. From
one standing goal ("every family cooks electric, no firewood, the clinic never loses
power") it writes a machine-checkable definition of done, sets the day's three-tier
community price, books cooking slots, texts households, rewards tree planting, and proves
every plan in a grid simulator before acting. A code Supervisor keeps it honest.

The demo village has 160 households on four 15 kW stations, one of which also powers a
clinic. A second config reproduces Green-Stove's real pilot (one 1.8 kW station, six
households) so the simulator can be checked against what the pilot already does.

Demo story (one cloudy rainy-season day, mock or live model):

1. The standing goal is on screen. The Spec Agent writes the definition of done; code
   shows which conditions it wrote and which code had to add.
2. The forecast says: clear morning, clouds after 13:00. First-come-first-served on this
   day overloads a station at dinner time and darkens the clinic. The audience sees that
   first, in the simulator.
3. The Pricing Agent proposes a tariff. The Verifier fails it and says why (where, when,
   by how much). The agent revises. The Supervisor detects a stall and relaxes revenue
   first, exactly as the committee ordered. The plan passes: "done with 1 relaxation".
4. Households get SMS in Tigrinya, Amharic, Afaan Oromo or English. One model-written
   message quotes a time the plan does not contain; the checker blocks it and the template
   goes out instead.
5. Replies arrive: `book 15:00`, `stove broken`, `PLANT 3`. Each is parsed, re-verified
   and confirmed or countered.
6. The day runs. At 14:00 metered solar falls behind the forecast; the Supervisor
   replans the evening without the model, moves bookings earlier by SMS, and sends the
   only human ping of the day: approve the genset at the clinic station.
7. End-of-day report: honest status, metrics against both baselines on the same day, and
   one lesson written to federation memory that the next village can use.

## 2. Hard constraints

- **The model proposes; code decides.** The model writes four things: the draft
  definition of done, the tariff, SMS wording, and an interpretation of free-text replies
  the grammar could not parse. Safety limits, bookings, grading, status, ledger entries,
  federation lessons and human pings are code.
- **The model never grades itself.** The day's status comes only from the Verifier's
  verdict. No model text can set `DONE`.
- **Hard conditions are never relaxed by the agent**: station overload, battery floor,
  clinic tier-1 (vaccine fridge) uptime. Human-class conditions change only through an
  approved ping.
- **Prices stay inside the committee's bounds.** Out-of-bounds proposals are rejected
  with reasons and sent back, not silently clamped. The only clamp is the documented
  fallback in section 9 (Supervisor), and it is recorded as such.
- **No SMS leaves without passing the checker.** The template fallback is checked too.
- **Humans are pinged only for money and safety**: genset start, clinic tier-2 load
  shedding, deferring a priority household. A plan never depends on an action that has
  not been approved.
- **Live model calls are opt-in.** Billable Crusoe requests need both
  `GREENSTOVE_LLM=crusoe` and `GREENSTOVE_LIVE_SPEND_OK=1`; a key alone never spends.
  Per-day call and token budgets are enforced in code.
- **Mock mode is labeled everywhere** (CLI banner, report header, `docs/results.md`).
  Mock numbers measure the harness, not Nemotron.
- Secrets come from `.env` only. Never print, log or commit them. Ship `.env.example`.
- Deterministic runs: seeded weather and households, temperature 0 (or the lowest the
  model accepts), model id pinned by env var. Every model call records model id,
  request id, tokens, latency and the prompt hash.
- All households, names and phone numbers are synthetic. The SMS gateway is a file
  outbox; no real messages are sent in this hackathon.
- Every number in [docs/assumptions.md](docs/assumptions.md) lives in a config file, never
  in code.
- Python 3.12, one repo, no Docker, no web backend, no auth. Files under ~250 lines.

## 3. Ask me before coding

1. Exact submission deadline with time zone, and what the form needs (video length and
   host, repo link, demo link). Quarantine was marked late because the deadline was 5:00
   PM, not 6:00.
2. Is a Crusoe key available, and what is the spend cap for live runs? Default model is
   `nvidia/Nemotron-3-Nano-30B-A3B` (section 4).
3. Is any data from the 1.8 kW pilot available (meter logs, cooking times, household
   count per day)? It calibrates the simulator.
4. Demo village: country, currency (default ETB), languages (default ti, am, om, en), and
   whether SMS times use the Ethiopian or the international clock.
5. Are the committee defaults in `docs/assumptions.md` acceptable (price floor, ceiling,
   lifeline, relaxation order)?

Fallbacks if an answer is "no" or "unknown": build and evaluate in mock mode; use the
placeholders and say so in every result; international 24-hour clock with the period
word ("morning", "evening") in the local language.

## 4. Stack

| Piece | Choice |
| --- | --- |
| Language | Python 3.12, `uv` virtualenv at `.venv` |
| Model API | Crusoe Managed Inference, OpenAI-compatible `POST /v1/chat/completions` at `https://api.inference.crusoecloud.com/v1`, called with `httpx` (no SDK) |
| Local model (station controller) | Same client pointed at any OpenAI-compatible local server (`LOCAL_LLM_BASE_URL`) |
| Validation | `pydantic` v2 for every model input and output |
| Storage | SQLite (ledger, bookings, run log); JSONL (federation lessons, SMS outbox) |
| Simulator | Plain Python, 15-minute steps (96 per day × 4 stations is tiny) |
| Report | Markdown plus SVG charts written by Python into `docs/results.md` |
| Console (optional) | Streamlit, one page |
| Tests | `pytest` |

| Role | Default model | Why |
| --- | --- | --- |
| Spec Agent, Pricing Agent, SMS composer, reply fallback | `nvidia/Nemotron-3-Nano-30B-A3B` | Cheap, and the same open weights could run on a station controller |
| Pricing Agent override (optional) | `nvidia/Nemotron-3-Super-120B-A12B` | Stronger reasoning if Nano's proposals stall |

Both ids were on Crusoe's serverless model list on 2026-10-17. Confirm with
`GET /v1/models` before a live demo. For JSON tasks send
`chat_template_kwargs: {"enable_thinking": false}` (this worked for a Nemotron model on
the ThermalDesk project; verify it for these two) and strip `<think>…</think>` and code
fences defensively before parsing.

## 5. Repo layout

```
green-stove-agents/
  PLAN.md  README.md  .env.example  pyproject.toml
  config/
    goal.md                 # the standing goal, one paragraph
    village-demo.json       # 4 stations, 160 households, clinic, committee bounds
    pilot-6.json            # 1.8 kW, 6 households
    sms-templates/          # one file per language; native-speaker review required
  greenstove/
    config.py  models.py    # pydantic models (section 7)
    llm.py                  # mock | crusoe | local, budgets, call log
    weather.py              # day classes, forecast, downside scenario
    households.py           # synthetic households and demand response
    simulator.py            # 15-minute grid simulation (docs/simulator-and-evaluation.md)
    limits.py               # code-derived safety limits per station
    spec_agent.py           # goal -> definition of done
    pricing_agent.py        # tariff proposals
    planner.py              # bookings inside the limits
    verifier.py             # simulate + grade + explain
    supervisor.py           # planning loop, relaxations, pings, status, drift replans
    execution.py            # runs the actual day with hourly telemetry checks
    sms/  composer.py  checker.py  parser.py  outbox.py
    ledger.py               # credits, debits, planting claims, hash chain
    federation.py           # lessons, offline queue, HQ sync
    baselines.py            # fcfs, llm_oneshot, agents_no_verifier
    evaluate.py  report.py  cli.py
  console/app.py            # optional
  scripts/set_crusoe_key.py # native dialog that writes the key into .env
  tests/
  data/                     # git-ignored: runs, outbox, ledger.db, federation/
  docs/
```

## 6. Environment variables

```
GREENSTOVE_LLM=mock                 # mock | crusoe | local
GREENSTOVE_LIVE_SPEND_OK=0          # must be 1 for billable Crusoe calls
CRUSOE_API_KEY=
CRUSOE_BASE_URL=https://api.inference.crusoecloud.com/v1
GREENSTOVE_MODEL=nvidia/Nemotron-3-Nano-30B-A3B
GREENSTOVE_MODEL_PRICING=           # optional override for the Pricing Agent
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1
GREENSTOVE_MAX_CALLS_PER_DAY=12     # per simulated village-day
GREENSTOVE_MAX_TOKENS_PER_DAY=40000
GREENSTOVE_PRICE_IN_PER_MTOK=       # USD, from crusoe.ai/cloud/pricing
GREENSTOVE_PRICE_OUT_PER_MTOK=
GREENSTOVE_VILLAGE=config/village-demo.json
```

## 7. Data model

Pydantic models in `greenstove/models.py`. Times are `HH:MM` on the 15-minute grid in the
village's time zone; money is in the village currency.

```
Station        id, pv_kwp, inverter_kw, battery_kwh, initial_soc, hw_lvd_soc,
               genset_kw, has_clinic
ClinicLoad     name, kw, tier (1 fridge | 2 maternity lights | 3 non-essential), hours
Committee      price_floor, price_ceiling, lifeline_price, lifeline_kwh_per_day,
               max_daily_price_change, revenue_target_per_day, battery_floor_soc,
               max_meal_delay_min, max_household_spend_per_day, min_household_service,
               default_tariff, relax_order [{condition_id, steps[]}],
               approvers [ids], verifiers [ids]
Household      id, station_id, language, priority, phone (synthetic),
               meals [{kind: lunch|dinner, preferred, earliest, latest}],
               delay_cost_per_hour, price_sensitivity, patience_min, compliance,
               stove_ok
SpecCondition  id, metric, op (<= | >=), threshold, scenario (expected|downside|both),
               klass (hard | human | relaxable), relax_rank, rationale,
               source (model | code_added | code_tightened)
Tariff         sun {start, end, price}, peak {start, end, price}, standard_price,
               rationale            # lifeline price comes from the committee, not the model
Limits         per station, per step: stove_cap; per station: cooking_reserve_soc
Plan           date, tariff, limits, bookings[], human_requests[], relaxations[]
Booking        household_id, meal, requested_start, granted_start, station_id, tier,
               status (granted | offered | deferred | cancelled)
Verdict        per scenario: metrics{}, conditions [{id, pass, value, margin}],
               violation_score, explanations[] (code-written, top 3)
Attempt        n, proposal, validation_errors[], verdict, tokens, latency_ms, proposal_hash
DayReport      status (DONE | DONE_WITH_RELAXATIONS | NOT_DONE), relaxations[],
               failing[], pings[], planned_metrics, actual_metrics, attempts[],
               replans[], model_calls, tokens, cost_usd, mode (mock | crusoe | local)
SmsMessage     id, household_id, language, text, facts{times[], numbers[]},
               source (model | template), checker {pass, reasons[]}, segments
Reply          raw, from, intent, args, parser (grammar | model), action
LedgerEntry    seq, ts, household_id, kind, amount, ref, prev_hash, hash
PlantingClaim  id, household_id, trees, claimed_on, verifications [{verifier_id, alive, ts}],
               status, credited_trees
Lesson         id, village, station, date, weather_class, forecast_kwh, tariff,
               attempts, relaxations, outcome{}, note
HumanRequest   id, kind (genset | clinic_shed | priority_defer), station_id, window,
               reason, est_cost, status (pending | approved | denied | expired), decided_by
```

## 8. Definition-of-done vocabulary

The Spec Agent may only use these metrics; the Verifier computes all of them from the
simulator trace. Defaults come from the committee config.

| Metric | Meaning | Default condition | Class |
| --- | --- | --- | --- |
| `station_overload_minutes` | Minutes any station's load exceeds its inverter limit | `<= 0`, both scenarios | hard |
| `battery_min_soc` | Lowest state of charge on any station | `>= battery_floor_soc`, both | hard |
| `clinic_tier1_outage_minutes` | Minutes the vaccine fridge is unpowered | `<= 0`, both | hard |
| `priority_wood_meals` | Meals of priority households not cooked electric | `<= 0`, expected | human |
| `revenue_ratio` | Day revenue ÷ revenue target | `>= 1.0`, expected | relaxable, rank 1 |
| `max_meal_delay_minutes` | Largest gap between requested and granted start | `<= 60`, expected | relaxable, rank 2 |
| `min_household_service` | Lowest 7-day share of a household's meals cooked electric | `>= 0.9`, expected | relaxable, rank 3 |
| `wood_meals_share` | Share of all meals cooked on wood | `<= 0.0`, expected | relaxable, rank 4 |
| `max_household_spend` | Highest household spend for the day | `<= max_household_spend_per_day`, expected | committee-only |

Relaxation steps per condition are in the committee config (for example revenue
1.0 → 0.9 → 0.8, max delay 60 → 90 → 120). "Committee-only" conditions are never relaxed
by the agent; when they conflict with revenue, revenue gives.

## 9. Component behavior

### `llm.py`

`complete_json(role, system, user, schema) -> (obj, CallRecord)`. Modes: `mock` returns a
deterministic proposal from `greenstove/mock_models.py` (rule-based, documented as such);
`crusoe` and `local` POST to `/chat/completions`. Parse: strip `<think>` blocks and
fences, take the first JSON object, validate against the pydantic schema. One repair
retry with the validation error; then raise `ModelOutputInvalid`. Budgets checked before
every call. The call log goes to `data/runs/<run>/calls.jsonl` without the key.

### Spec Agent (`spec_agent.py`)

Input: `config/goal.md`, a short village summary, the vocabulary table, committee bounds.
Output: `{"conditions": [SpecCondition…]}`. Code then:

1. drops conditions whose metric is not in the vocabulary (recorded);
2. adds any missing hard, human or committee condition (`source: code_added`);
3. replaces any threshold weaker than the committee's with the committee's
   (`source: code_tightened`);
4. stores the spec keyed by the hash of goal plus committee config. It is regenerated only
   when either changes, so it costs one call per village, not per day.

The CLI prints the spec as a table with the source column, so the audience sees what the
model wrote and what code enforced.

### Pricing Agent (`pricing_agent.py`)

Input (JSON): date, weather class, hourly forecast kWh per station, downside factor,
meals by preferred hour per station, code-derived limits, committee bounds, yesterday's
tariff, up to 3 federation lessons, and previous attempts with the Verifier's failures and
explanations. Output: `Tariff`.

Validation (all reasons returned together):

- times on the 15-minute grid; sun window inside 07:00–17:00 and 2–7 hours long; peak
  window inside 16:00–22:00 and 1–4 hours long; no overlap;
- `price_floor <= sun < standard < peak <= price_ceiling`;
- each price within `max_daily_price_change` of yesterday's;
- no fields beyond the schema (the model cannot set limits, lifeline or bookings).

### Limits (`limits.py`, code only)

- `stove_cap[station][step] = floor((inverter_kw × (1 − margin) − clinic_kw[step]) /
  stove_kw_p95)`, so the cap already leaves room for the clinic and for draw variation.
- `cooking_reserve_soc[station] = max(battery_floor_soc, hw_lvd_soc + night clinic tier-1
  and tier-2 energy until next sunrise under the downside forecast ÷ battery_kwh + margin)`.
  New cooking sessions are not admitted below it; sessions already running finish.

### Planner (`planner.py`, code only)

Each household's requested slot comes from the demand-response model under the proposed
tariff. Process requests in this order: priority households, then lowest 7-day service
share, then request time. Grant the requested slot if every step of the session fits under
`stove_cap` and the projected SoC (forecast PV minus booked load) stays above the reserve.
Otherwise offer the nearest feasible slot inside the household's window. Otherwise defer:
an ordinary household's meal becomes a planned wood meal; a priority household's meal
creates a `priority_defer` request instead of being deferred silently.

### Verifier (`verifier.py`, code only)

Runs the simulator on the plan twice: expected weather with mean behavior, and the
downside scenario (downside PV, lower compliance, p95 stove draw). Computes every
vocabulary metric, grades each condition on its scenario, and returns a `Verdict`.
`violation_score` sums normalized shortfalls, with hard conditions weighted 100× and
relaxable ones by rank. `explanations` are written by code from the trace, for example
"S2 18:00–19:30: 14 dinner requests over cap; queue beyond patience". These go back to the
Pricing Agent.

### Supervisor (`supervisor.py`, code only)

```
spec = SpecAgent.load_or_create()
level = 0; seen = set(); invalid = 0; best = None
for attempt in 1..budget:
    tariff, errors = PricingAgent.propose(context, feedback)
    if errors: feedback = errors; invalid += 1
        if invalid == 2: tariff = committee.default_tariff   # recorded as fallback
        else: continue
    if hash(tariff) in seen: loop = True                     # repeat or A-B-A oscillation
    plan = Planner.build(tariff, limits); verdict = Verifier.grade(plan, spec, level)
    best = better_of(best, plan)    # hard conditions first, then violation_score
    if verdict.passed: break
    if loop or stalled(last 2 scores improved < 5%):
        if next relaxation exists: level += 1; log which failures triggered it
        else: break
status = DONE if passed and level == 0
         DONE_WITH_RELAXATIONS(level) if passed
         NOT_DONE otherwise
```

On `NOT_DONE` the Supervisor keeps the best plan whose hard conditions pass; if none
passes, it raises the reserve step by step until the clinic is safe at the cost of
cooking, and files human requests: `genset` for an energy shortfall at a station with a
genset, `clinic_shed` if tier-1 is still at risk without shedding tier 2, and
`priority_defer` for each priority meal it cannot fit. Requests expire at their window
start; an expired request is treated as denied. Status is never upgraded by a ping that
has not been approved.

### Execution (`execution.py`)

In the simulator, the "actual" day uses actual weather, sampled compliance and draw
jitter (see the evaluation doc). The controller admits booked sessions, admits walk-ins
under the cap and above the reserve, and queues the rest until their patience runs out.
Every hour the Supervisor compares metered PV, load and SoC per station with the plan's
expected trace. Drift is SoC more than 8 points below plan, or PV under 80% of forecast for
two hours in a row. On drift it replans the rest of the day in code only, without the
model: rescale the forecast, recompute limits, re-run the planner for meals not yet
started (keeping granted bookings where they still fit), re-verify, send change SMS, and
file pings if hard conditions now fail. This path works with no internet.

### SMS (`sms/`)

- **Composer.** Message kinds: daily cheapest-window broadcast, booking confirmation,
  change notice, counter-offer, ping to an approver. Each has a `facts` set: the only times
  and numbers it may contain. The model writes the text in the household's language; mock
  mode uses the templates.
- **Checker** (deterministic). Pass only if: every time token (`15:00`, `3pm`, `3.00`) is in
  `facts.times`; every other number is in `facts.numbers` (±0.01); digits are Arabic
  numerals (no Ge'ez numerals, to keep checking exact); the reply keyword is present in
  Latin letters (`BOOK`, `PLANT`, `HELP`); the script matches the language (Ge'ez for ti
  and am, Latin for om and en); the message fits in 2 segments (GSM-7: 160/153 characters,
  UCS-2: 70/67, so Ge'ez text is short); and no phrase from the language's banned-promise
  list appears ("guaranteed", "free", "always" …; lists need native review). On failure the
  template with the same facts is checked and sent; both are logged.
- **Parser.** Grammar first, case-insensitive: `BOOK [lunch|dinner] H[:MM][am|pm]`,
  `CANCEL [lunch|dinner]`, `BROKEN` / `STOVE BROKEN`, `PLANT n` (1–20),
  `VERIFY <claim> <alive>` (registered verifiers only), `YES|NO <request>` (registered
  approvers only), `HELP`, `STOP`. Unmatched text goes to the model with the intent
  schema; accept only `confidence >= 0.8`. The model fallback can never produce
  `VERIFY`, `YES` or `NO`: authority actions need the exact grammar from a registered
  number. A booking reply re-enters the planner and gets a quick verify before the
  confirmation or counter-offer goes out. `BROKEN` cancels the household's bookings, opens
  a maintenance item, and files a `priority_defer` request for a priority household.

### Ledger (`ledger.py`, code only)

SQLite, append-only, each entry hash-chained to the previous one. Kinds: `cooking_debit`
(kWh × tier price at session start), `lifeline_debit` (priority households pay the
lifeline price up to `lifeline_kwh_per_day`), `topup` (simulated mobile money),
`planting_credit`, `survival_credit`, `adjustment` (human only). Planting: `PLANT n`
opens a claim; two different registered verifiers, neither from the claimant's household,
must each reply `VERIFY`; the credited trees are the smaller of their two counts; half the
credit posts then, the other half after `survival_check_days` when two verifiers confirm
the trees are alive. Monthly caps per household on trees and on credit. Planting credit can
only be spent on cooking. `greenstove ledger verify` recomputes the chain and fails on any
edit.

### Federation memory (`federation.py`, code only)

After each day the Supervisor writes one `Lesson` from verified outcomes: weather class,
forecast kWh, the tariff that passed, attempts, relaxations and actual metrics. Lessons
are built by code, never written by the model, so one village's model cannot poison
another village's memory. Stations append to
`data/federation/<village>/<station>.jsonl`, queue unsynced lessons while offline, and
`greenstove federation sync` merges them into an HQ file and back (the QR code per station
carries the station id). Retrieval for the Pricing Agent: same weather class, nearest
forecast kWh, newest first, top 3, at most 600 tokens.

### CLI (`cli.py`)

`smoke`, `sim`, `spec`, `plan`, `sms`, `reply`, `run-day`, `eval`, `demo`,
`ledger verify`, `federation sync`. Every command prints the mode (mock, crusoe or local)
first.

## 10. Milestones

| # | Build | Acceptance check |
| --- | --- | --- |
| 0 | Scaffold, configs, `models.py`, `llm.py` (mock and crusoe), `.env.example`, `scripts/set_crusoe_key.py` | `greenstove smoke` prints the village summary and a mock JSON reply. With a key and `GREENSTOVE_LIVE_SPEND_OK=1`, it lists the Nemotron models from `/v1/models` and gets one schema-valid reply |
| 1 | `weather.py`, `households.py`, `simulator.py`, `limits.py` | `pytest tests/test_simulator.py`: energy balance closes within 0.1%, SoC stays in bounds, overload and clinic outage are counted when forced. `greenstove sim --policy fcfs --day rainy-afternoon-1` shows the dinner overload and the clinic outage on the demo village; the pilot config runs without overload |
| 2 | Spec Agent, `verifier.py` grading | Spec is schema-valid. A mock spec that omits the clinic condition and loosens the battery floor comes back with both fixed and marked `code_added` / `code_tightened` (unit test) |
| 3 | Pricing Agent, `planner.py`, `supervisor.py` | `greenstove plan --day rainy-afternoon-1` shows each attempt with its verdict and ends in one of the three statuses. Unit tests: relaxation follows committee order; a repeated proposal is detected as a loop; an out-of-bounds price is rejected with reasons; status cannot be `DONE` while a hard condition fails |
| 4 | SMS composer, checker, parser, outbox | Tests: a message with a time outside `facts` is blocked and the template is sent; `book 3pm`, `BOOK 15:00`, `plant 3`, `stove broken` parse; model fallback cannot emit `VERIFY` or `YES`; Ge'ez message segment count is correct |
| 5 | Ledger and planting | Tests: one verifier is not enough; the claimant's household cannot verify; monthly cap holds; editing any row makes `ledger verify` fail |
| 6 | `execution.py`, hourly drift replans, human requests | `greenstove run-day --day rainy-afternoon-1` shows a drift replan in the afternoon, change SMS in the outbox, and pings only of the three allowed kinds |
| 7 | Federation memory | Two stations' lessons merge through `federation sync`. Measured: model calls to reach a verified plan on repeat weather classes, with and without lessons |
| 8 | `baselines.py`, `evaluate.py`, `report.py` | `greenstove eval --days 20 --seeds unseen` prints the metric table for `fcfs`, `llm_oneshot`, `agents_no_verifier` and `agents`, and writes `docs/results.md` with SVG charts, mode labeled |
| 9 | Live Nemotron run | The same eval for 3 unseen days with `GREENSTOVE_LLM=crusoe`: JSON-valid rate, attempts per day, tokens and cost per day recorded in `docs/results.md` |
| 10 | `greenstove demo`, README update, submission text | The demo tells the section 1 story end to end; README covers setup, the demo and what is simulated |
| 11 | Console (optional) | Every demo step visible in the browser without a terminal |

Stop after each milestone and show me the acceptance output. An acceptance check that
involves a live model must pass 3 runs in a row.

### Time boxes

Fill in clock times once the deadline is known (section 3, question 1).

| Milestone | Minutes |
| --- | --- |
| 0 | 30 |
| 1 | 75 |
| 2 | 40 |
| 3 | 75 |
| 4 | 50 |
| 5 | 40 |
| 6 | 50 |
| 7 | 30 |
| 8 | 50 |
| 9 | 30 |
| 10 | 30 |
| 11 | only if ahead |

- Tell me when a milestone reaches 80% of its time box without passing, and propose the
  smallest cut that still passes. Do not silently overrun.
- Cut order if behind: console; survival credit (keep planting credit); federation HQ
  sync (keep local lessons); Afaan Oromo and English templates beyond one each; drift
  replans (keep the hourly check and the ping).
- Feature freeze one hour before the deadline. After that, bug fixes, results and the demo
  recording only.

### Speed targets for the live demo

- `greenstove demo` in mock mode: under 60 seconds. Live: under 3 minutes.
- One Pricing Agent call on Nano: under 10 seconds; print elapsed time per call.
- Verifier (both scenarios, 160 households): under 1 second.
- `greenstove eval --days 20` in mock mode, all four policies: under 2 minutes.

## 11. Docs to check before using an API

- Crusoe Managed AI overview: https://docs.crusoecloud.com/managed-ai/overview/
- Serverless models (ids, context): https://docs.crusoecloud.com/serverless-inference/available-models
- Usage and billing: https://docs.crusoecloud.com/serverless-inference/usage-billing-models
  and https://crusoe.ai/cloud/pricing
- Rate limits: https://docs.crusoecloud.com/serverless-inference/rate-limits
- API reference: https://docs.crusoecloud.com/api/managed-ai/
- Nemotron model cards on Hugging Face, for the chat template and the thinking switch.
- SMS encoding: 3GPP TS 23.038 (GSM-7 alphabet; UCS-2 for Ge'ez and Arabic script).
