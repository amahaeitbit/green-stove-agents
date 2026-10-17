# Green-Stove Agents: build plan for the coding agent

Read this whole file, then [docs/architecture.md](docs/architecture.md) and
[docs/simulator-and-evaluation.md](docs/simulator-and-evaluation.md), before writing any
code. Build one milestone at a time, run its acceptance check, commit, report the result,
and keep to the clock in section 11. Section 12 splits the work across parallel coding agents.

## 0. The hackathon (from the Cerebral Valley participant guide)

| Item | Value |
| --- | --- |
| Event | The Recursive Agents Hackathon (Cerebral Valley × Crusoe), Shack15, San Francisco |
| Date | Saturday, October 17, 2026 |
| Hacking | 10:45 AM – **5:00 PM PT submissions due** (lunch 1:00 PM) |
| Judging | Round 1 from 5:00 PM: ~3 min live demo + 1–2 min Q&A per team. Final round 7:00 PM: top six on stage, same format. Winners 8:15 PM |
| Criteria (round 1) | Technicality 40%, Creativity and originality 25%, Live demo 20%, Future potential and AI impact 15% (equal weights in the final) |
| Submission | Form with a **one-minute demo video** of what we built today; public repo; accessible demo link; every teammate added on the submission page |
| Rules | Public repo. Max 4 per team. **New work only**: the demo may show only what was built today, and judges must be able to tell it apart from earlier work |
| Banned | Streamlit apps; projects where a dashboard is the main feature; basic RAG; AI medical advice (and the rest of the guide's anti-project list) |
| Resources | Crusoe Managed Inference with $50 credits (Nemotron and other open models); NVIDIA Nemotron; NemoClaw/OpenShell; You.com search |
| Prizes | 1st $14,000, 2nd $8,000, 3rd $3,000 (+ You.com credits); **Best Use of Nemotron: RTX 5090**; Best Use of NemoClaw & OpenShell: Brev credits |

Problem statements: **Done Means Done** (agents write their own definition of done,
verify, catch drift and loops, decide to continue, re-plan, stop or ping a human),
**Shared Brain** (a shared memory layer every agent reads before it works and updates
afterward, kept current, lean and conflict-free, with measurable quality gains) and
**Version N+1** (self-improvement proven on unseen tasks).

**Our entry: Shared Brain**, built on a Done Means Done loop. The verifier is what keeps
the brain honest: only knowledge backed by a verified result gets in. Prize targets: the
top three and Best Use of Nemotron (every agent runs on Nemotron).

## 1. What we are building

An agent team that runs a village's solar cooking mini-grid without a person on site, and
that shares one brain. Before every model call, each agent reads a short brief compiled
from the Shared Brain: the village's facts, yesterday's state, the pricing playbook for
today's weather, verified lessons, SMS wording that passed the checker, recent human
decisions. After it works, the agent proposes notes, and code writes what the run proved.
A code Keeper admits a note only if it is backed by evidence (a verifier verdict, a
metered day, a checker result), resolves conflicts by evidence, and keeps every section
capped and fresh. Stations and villages sync their brains, so a new village starts from
what others proved, not from zero.

Underneath is the Done Means Done loop from the pitch: from one standing goal the team
writes a machine-checkable definition of done, sets the day's three-tier price, books
cooking slots, texts households, rewards tree planting, and proves every plan in a grid
simulator before acting. A code Supervisor decides continue, re-plan, stop or ping.

The demo village has 160 households on four 15 kW stations, one of which also powers a
clinic. A second config reproduces Green-Stove's pilot (one 1.8 kW station, six
households). A third village ("village B", different size and households) tests a cold
start from another village's brain.

Demo story (live, about 3 minutes; the video is a one-minute cut of it):

1. One line on screen: the standing goal. The Spec Agent turns it into a definition of
   done; code shows which conditions it wrote and which code had to add.
2. A cloudy rainy-season day. First-come-first-served in the simulator: a station trips at
   dinner and the clinic fridge goes dark.
3. **Brain off**: the Pricing Agent starts from zero, fails verification, revises, gets
   relaxed by the Supervisor, burns calls and tokens.
4. **Brain on**: the same day. The Pricing Agent reads the playbook entry for
   rainy afternoons and two verified lessons, cites them, and passes on the first or second
   attempt. Show calls, tokens and outcome side by side.
5. After the run, the Keeper admits one note (with its verdict id), rejects one (claim does
   not match the evidence), and supersedes a stale lesson.
6. SMS go out; one model-written message is blocked by the checker. Replies `book 15:00`,
   `stove broken`, `PLANT 3` are parsed and re-verified.
7. Village B, day 1, with village A's brain versus an empty one.
8. The results table on unseen days: brain on, brain off, naive history dump, and both
   baselines. Mode labeled (live Nemotron or mock).

## 2. Hard constraints

- **New work only.** Commit `9f27565` (10:19 AM PT, before hacking began) contains plans
  only. No code is written before 10:45 AM PT. The README gets a "Built at the hackathon"
  section listing what was built today; Green-Stove's hardware and pilot are named as
  pre-existing and are not demoed as our work.
- **No Streamlit and no dashboard-first demo.** The demo is the agents working, shown in
  the terminal (`rich` output) plus `docs/results.md`. Charts support the story; they are
  not the product.
- **The model proposes; code decides.** The model writes the draft definition of done,
  the tariff, SMS wording, interpretations of free-text replies, and brain notes. Safety
  limits, bookings, grading, status, ledger entries, brain admission and human pings are
  code.
- **Nothing enters the Shared Brain without evidence.** Every entry carries an evidence
  reference that code re-checks. Models can never write `facts` or `decisions`, and no
  entry can loosen a committee bound.
- **The model never grades itself.** The day's status comes only from the verifier's
  verdict.
- **Hard conditions are never relaxed by the agent**: station overload, battery floor,
  clinic tier-1 (vaccine fridge) uptime. Human-class conditions change only through an
  approved ping.
- **Prices stay inside the committee's bounds.** Out-of-bounds proposals are rejected
  with reasons, not silently clamped (except the recorded fallback in section 9).
- **No SMS leaves without passing the checker.** The template fallback is checked too.
- **Humans are pinged only for money and safety**: genset start, clinic tier-2 shedding,
  deferring a priority household.
- **Live calls are opt-in**: `GREENSTOVE_LLM=crusoe` and `GREENSTOVE_LIVE_SPEND_OK=1`.
  Per-day call and token budgets are enforced in code. Mock mode is labeled everywhere.
- Secrets only in `.env` (git-ignored); ship `.env.example`. Export the key in single
  quotes (keys can contain `$`).
- Deterministic runs: seeded weather and households; model ids pinned by env var; each
  model call records model, request id, tokens, latency, prompt hash and the brain entries
  it cited.
- Synthetic households and phone numbers only; the SMS gateway is a file outbox.
- Every number in [docs/assumptions.md](docs/assumptions.md) lives in config, not code.
- Python 3.12, one repo, no Docker, no web backend. Files under ~250 lines.

## 3. Ask me before coding (answer by 10:50)

1. Team: who is on it (max 4)? Abel (`abeltesfa`) has a pending collaborator invite.
   Who owns which milestones?
2. Crusoe: has someone created the org, claimed the $50 credits
   (https://forms.gle/JdEvMGSb4TURH1Tq8) and made a key that expires October 18? Spend
   cap for today?
3. Any data from the 1.8 kW pilot (meter logs, cooking times)? If not, placeholders,
   labeled.
4. SMS languages (default ti, am, om, en) and clock (default international 24-hour).

Fallbacks: build in mock mode until the key exists; placeholders labeled in every result.

## 4. Stack

| Piece | Choice |
| --- | --- |
| Language | Python 3.12, `uv` virtualenv at `.venv` |
| Model API | Crusoe Managed Inference, OpenAI-compatible, `https://api.inference.crusoecloud.com/v1`, header `Authorization: Bearer $CRUSOE_API_KEY`, called with `httpx` |
| Shared Brain | SQLite (`entries` + append-only `log`), JSONL for sync bundles |
| Validation | `pydantic` v2 for every model input and output |
| Other storage | SQLite (ledger, bookings, runs); JSONL (SMS outbox) |
| Simulator | Plain Python, 15-minute steps |
| Terminal demo | `rich` |
| Report | Markdown + SVG written by Python into `docs/results.md` |
| Tests | `pytest` |

| Role | Model | Why |
| --- | --- | --- |
| Spec Agent, Pricing Agent | `nvidia/Nemotron-3-Super-120B-A12B` | The reasoning-heavy calls |
| SMS composer, reply fallback, brain notes | `nvidia/Nemotron-3-Nano-30B-A3B` | High volume, cheap; same open weights a station controller could run |

Copy exact ids from `GET /v1/models` (case-sensitive; Crusoe's own hackathon notes mark
some Nemotron ids "to confirm"; `nvidia/nemotron-3.5-lightning-30b-a3b` is the fallback
for either role). Request notes from Crusoe's hackathon skill file:

- Thinking off for JSON tasks: `chat_template_kwargs: {"enable_thinking": false}`; strip
  `<think>` blocks and fences anyway.
- Empty content with `finish_reason == "length"` means the budget was eaten: use
  `max_tokens >= 1024`.
- 429 (rate) and 503 (load) are transient: exponential backoff from 1 s with jitter, cap
  30 s. Without a payment method the limit is 30 requests per minute per model, so the
  eval runs sequentially and caches.
- Check `supported_parameters` before using `response_format`; otherwise parse JSON from
  text.

## 5. Repo layout

```
green-stove-agents/
  PLAN.md  README.md  .env.example  pyproject.toml
  config/
    goal.md  village-a.json  village-b.json  pilot-6.json
    sms-templates/            # per language; native-speaker review required
  greenstove/
    config.py  models.py      # pydantic models (section 7)
    llm.py                    # mock | crusoe | local; budgets; backoff; call log
    brain/
      store.py                # SQLite entries + log, current view
      keeper.py               # admission, evidence checks, conflicts, caps, expiry
      briefs.py               # role briefs under token budgets
      sync.py                 # station -> village -> HQ merge, offline queue
    weather.py  households.py
    simulator.py  limits.py
    spec_agent.py  pricing_agent.py
    planner.py  verifier.py  supervisor.py  execution.py
    sms/  composer.py  checker.py  parser.py  outbox.py
    ledger.py                 # credits, debits, planting claims
    baselines.py  evaluate.py  report.py  demo.py  cli.py
  scripts/set_crusoe_key.py   # native dialog that writes the key into .env
  tests/
  data/                       # git-ignored: brain.db, runs, outbox, ledger.db
  docs/
```

## 6. Environment variables

```
GREENSTOVE_LLM=mock                 # mock | crusoe | local
GREENSTOVE_LIVE_SPEND_OK=0          # must be 1 for billable Crusoe calls
CRUSOE_API_KEY=
CRUSOE_BASE_URL=https://api.inference.crusoecloud.com/v1
GREENSTOVE_MODEL_REASON=nvidia/Nemotron-3-Super-120B-A12B
GREENSTOVE_MODEL_FAST=nvidia/Nemotron-3-Nano-30B-A3B
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1
GREENSTOVE_MAX_CALLS_PER_DAY=12
GREENSTOVE_MAX_TOKENS_PER_DAY=40000
GREENSTOVE_BRAIN=on                 # on | off | dump (evaluation modes)
GREENSTOVE_VILLAGE=config/village-a.json
```

## 7. Data model

Pydantic models in `greenstove/models.py`. Times are `HH:MM` on the 15-minute grid;
money in the village currency.

```
BrainEntry     key, section (facts|state|playbook|lessons|sms|parser|decisions),
               scope (station|village|federation), value{}, evidence {kind, ref},
               author (code | agent role), version, status (current|superseded|
               rejected|expired), supersedes, created_at, expires_at, confirmations
BrainNote      section, key, claim{}, evidence_ref, rationale      # agent proposal
Brief          role, entries [{key, version, text}], tokens
Station        id, pv_kwp, inverter_kw, battery_kwh, initial_soc, hw_lvd_soc,
               genset_kw, has_clinic
ClinicLoad     name, kw, tier (1 fridge | 2 maternity lights | 3 non-essential), hours
Committee      price_floor, price_ceiling, lifeline_price, lifeline_kwh_per_day,
               max_daily_price_change, revenue_target_per_day, battery_floor_soc,
               max_meal_delay_min, max_household_spend_per_day, min_household_service,
               default_tariff, relax_order [{condition_id, steps[]}], approvers, verifiers
Household      id, station_id, language, priority, phone (synthetic),
               meals [{kind, preferred, earliest, latest}], delay_cost_per_hour,
               price_sensitivity, patience_min, compliance, stove_ok
SpecCondition  id, metric, op, threshold, scenario (expected|downside|both),
               klass (hard|human|relaxable), relax_rank, rationale,
               source (model|code_added|code_tightened)
Tariff         sun {start, end, price}, peak {start, end, price}, standard_price,
               rationale, brain_refs []
Limits         per station, per step: stove_cap; per station: cooking_reserve_soc
Plan           date, tariff, limits, bookings[], human_requests[], relaxations[]
Booking        household_id, meal, requested_start, granted_start, station_id, tier, status
Verdict        id, per scenario: metrics{}, conditions [{id, pass, value, margin}],
               violation_score, explanations[]
Attempt        n, proposal, validation_errors[], verdict_id, tokens, latency_ms, hash
DayReport      status (DONE|DONE_WITH_RELAXATIONS|NOT_DONE), decision trail
               (continue|re-plan|relax|stop|ping per step), relaxations[], pings[],
               metrics, model_calls, tokens, brain {cited, notes_admitted, rejected,
               superseded, brief_tokens}, mode
SmsMessage     id, household_id, language, text, facts{times, numbers}, source,
               checker {pass, reasons}, segments
Reply          raw, from, intent, args, parser (grammar|model), action
LedgerEntry    seq, ts, household_id, kind, amount, ref, prev_hash, hash
PlantingClaim  id, household_id, trees, claimed_on, verifications[], status, credited_trees
HumanRequest   id, kind (genset|clinic_shed|priority_defer), station_id, window, reason,
               est_cost, status (pending|approved|denied|expired), decided_by
```

## 8. Definition-of-done vocabulary

The Spec Agent may only use these metrics; the verifier computes all of them.

| Metric | Default condition | Class |
| --- | --- | --- |
| `station_overload_minutes` | `<= 0`, both scenarios | hard |
| `battery_min_soc` | `>= battery_floor_soc`, both | hard |
| `clinic_tier1_outage_minutes` | `<= 0`, both | hard |
| `priority_wood_meals` | `<= 0`, expected | human |
| `revenue_ratio` | `>= 1.0`, expected | relaxable, rank 1 |
| `max_meal_delay_minutes` | `<= 60`, expected | relaxable, rank 2 |
| `min_household_service` (7-day) | `>= 0.9`, expected | relaxable, rank 3 |
| `wood_meals_share` | `<= 0.0`, expected | relaxable, rank 4 |
| `max_household_spend` | `<= max_household_spend_per_day` | committee-only |

Relaxation steps per condition come from the committee config (revenue 1.0 → 0.9 → 0.8,
max delay 60 → 90 → 120, …). When a committee-only condition conflicts with revenue,
revenue gives.

## 9. Component behavior

### Shared Brain (`brain/`)

**Sections.** Keys are stable, so writes are upserts, not appends.

| Section | Example key | Written by | Kept current by |
| --- | --- | --- | --- |
| `facts` | `facts/S1/inverter_kw` | config loader only | reload on config change |
| `state` | `state/S2/end_soc`, `state/open_requests`, `state/low_service_households` | code after each day | expires after 2 days |
| `playbook` | `playbook/rainy_afternoon` | code, from the verified plan | one per weather class; replaced only by a verified plan with a better score |
| `lessons` | `lessons/rainy_afternoon/S2_dinner_cap` | agent notes, admitted by the Keeper | expire in 30 days unless re-confirmed; max 5 per class |
| `sms` | `sms/am/booking_confirmation` | code, from checker passes | one per language × kind |
| `parser` | `parser/misses` | code | top 10 unparsed patterns with counts |
| `decisions` | `decisions/genset/S1` | code, from ping outcomes | latest 5 per kind |

**Read, before every model call.** `briefs.compile(role, situation)` picks entries by role
and situation (weather class, station, language), newest verified first, under a token
budget: Spec 400, Pricing 900, SMS 250, reply fallback 150. Each line carries its key and
version. Agents must return `brain_refs` (the keys they used); the call log records them,
so we can measure which knowledge was used.

**Write, after every run.** Code writes `state`, `playbook`, `sms`, `parser` and
`decisions` from evidence. Agents may return up to 3 `BrainNote`s (only `lessons`). The
Keeper:

1. **Evidence check.** The note must cite a verdict id, day report id or checker result,
   and every number in the claim must match that evidence (±1%). Otherwise `rejected`,
   with the reason logged.
2. **Conflicts.** Same key: the entry with newer, equal-or-better verified evidence wins;
   the loser becomes `superseded` (kept in the log, never shown in briefs). Two verified
   but contradictory claims are split by refining the key (for example adding the station)
   instead of one overwriting the other.
3. **Lean.** Section caps; expiry; a later verdict that agrees adds a confirmation and
   extends expiry; nightly compaction removes expired and superseded entries from the
   current view. Brief and store sizes in tokens are reported every day.
4. **Guards.** Agents can't write `facts` or `decisions`; no entry may contain a price
   outside the committee bounds; notes from free-text replies are never admitted
   (prevents poisoning through SMS).

**Sync** (`brain/sync.py`). Station brains merge into the village brain, villages into an
HQ brain, by `(key, version, evidence)` with the same conflict rules; offline stations
queue bundles. A lesson confirmed in two villages is promoted to `federation` scope and
appears in other villages' briefs. Village B's cold start reads from it.

### `llm.py`

`complete_json(role, system, user, schema) -> (obj, CallRecord)`. Modes: `mock` returns a
deterministic rule-based proposal that does use the brief (for example, it warm-starts
from the playbook entry), so mock runs exercise the brain mechanics honestly; `crusoe` and
`local` POST to `/chat/completions`. One repair retry on schema failure, then
`ModelOutputInvalid`. Budgets are checked before every call; backoff as in section 4.

### Spec Agent (`spec_agent.py`)

Input: `config/goal.md`, its brief (facts and committee bounds), the vocabulary. Output:
conditions. Code drops unknown metrics, adds any missing hard, human or committee
condition (`code_added`), tightens anything weaker than the committee's
(`code_tightened`). Cached by the hash of goal plus committee config, so it costs one call
per village.

### Pricing Agent (`pricing_agent.py`)

Input: date, weather class, hourly forecast per station, downside factor, demand by
preferred hour, code-derived limits, committee bounds, yesterday's tariff, its brief, and
previous attempts with verifier failures and explanations. Output: `Tariff` with
`brain_refs`. Validation: 15-minute grid; sun window inside 07:00–17:00, 2–7 h; peak
inside 16:00–22:00, 1–4 h; no overlap; `floor <= sun < standard < peak <= ceiling`; each
price within the daily change cap; no extra fields.

### Limits, Planner, Verifier (code only)

- `stove_cap = floor((inverter_kw × (1 − margin) − clinic_kw[step]) / stove_kw_p95)`;
  `cooking_reserve_soc` covers the clinic's night energy under the downside forecast,
  never below the committee floor.
- Planner: requests from the demand-response model; priority households first, then
  lowest 7-day service; grant if under the cap and projected SoC stays above the reserve;
  else offer the nearest feasible slot; else defer (a priority meal files a
  `priority_defer` request instead).
- Verifier: simulate expected and downside scenarios, compute the vocabulary, grade,
  `violation_score` (hard ×100), and code-written explanations ("S2 18:00–19:30: 14
  dinner requests over cap").

### Supervisor (`supervisor.py`, code only)

Each step records one decision: **continue** (next attempt with feedback), **re-plan**
(drift during execution), **relax** (next condition in committee order), **stop**, or
**ping**.

```
spec = SpecAgent.load_or_create()
for attempt in 1..budget:
    tariff, errors = PricingAgent.propose(context, brief, feedback)
    if errors: feedback = errors; after 2 invalid -> committee default tariff (recorded)
    loop = hash(tariff) seen before (repeat or A-B-A)
    verdict = Verifier.grade(Planner.build(tariff, limits), spec, level)
    if verdict.passed: stop
    if loop or stalled (score improved < 5% over 2 attempts):
        relax next condition if one exists, else stop
status = DONE | DONE_WITH_RELAXATIONS(n) | NOT_DONE
```

On `NOT_DONE`: keep the best plan whose hard conditions pass, or raise the reserve until
the clinic is safe; file `genset`, `clinic_shed` or `priority_defer` requests. Expired
requests count as denied. Then write the day's evidence to the brain.

### Execution (`execution.py`)

The actual day uses actual weather, sampled compliance and draw jitter. Booked sessions
are admitted; walk-ins only under the cap and above the reserve; the rest queue until
patience runs out. Hourly, the Supervisor compares metered PV, load and SoC with the plan;
drift (SoC 8 points under plan, or PV under 80% of forecast for 2 hours) triggers a
code-only re-plan of unstarted meals, change SMS and, if needed, pings. Works offline.

### SMS (`sms/`)

- Composer: the model writes in the household's language from a `facts` set (the only
  allowed times and numbers) and its brief (the last checker-passed wording).
- Checker (deterministic): times and numbers must be in `facts`; Arabic digits only;
  reply keyword in Latin (`BOOK`, `PLANT`, `HELP`); script matches language; at most 2
  segments (GSM-7 160/153, UCS-2 70/67); no banned-promise phrases. On failure the
  template goes out (checked too). Passes and block reasons go to the brain.
- Parser: grammar first (`BOOK [lunch|dinner] H[:MM][am|pm]`, `CANCEL`, `BROKEN`,
  `PLANT n`, `VERIFY <claim> <alive>` and `YES|NO <request>` from registered numbers
  only, `HELP`, `STOP`); unmatched text goes to the fast model, accepted only at
  confidence ≥ 0.8 and never as `VERIFY`, `YES` or `NO`. Misses go to `parser/misses`.

### Ledger and planting (`ledger.py`, code only)

SQLite, append-only, hash-chained. Cooking debits at the tier price (lifeline for
priority households up to the daily lifeline kWh). `PLANT n` opens a claim; two different
registered verifiers, neither from the claimant's household, must reply `VERIFY`; credit
the smaller count; monthly caps per household; credit spendable on cooking only.

### CLI (`cli.py`)

`smoke`, `sim`, `spec`, `plan`, `brain show|diff|sync`, `sms`, `reply`, `run-day`,
`eval`, `demo`. Every command prints the mode and whether the brain is on, off or dump.

## 10. Measuring the Shared Brain

This is the headline result. Details in
[docs/simulator-and-evaluation.md](docs/simulator-and-evaluation.md).

- **Modes:** `off` (each day starts from facts only), `dump` (naive memory: previous
  attempts and reports appended up to the same token budget), `on` (Keeper-curated
  briefs).
- **Protocol:** warm up the brain on development days 0–19 of village A; evaluate on
  unseen days 1000–1019; then village B days 1–5 with village A's federated brain versus
  an empty one.
- **Quality metrics:** first-attempt verified pass rate; attempts and model calls to a
  verified plan; relaxations needed; wood meals and max delay; SMS checker block rate;
  parser fallback rate.
- **Cost and leanness:** tokens per day (context and output); brief tokens; store size;
  stale or superseded entries served (must be 0); notes admitted, rejected, superseded.
- **Live proof:** at least 3 unseen days with Nemotron in `on` and `off` modes. Mock
  numbers show the mechanism, not model quality, and are labeled.

## 11. Milestones and clock (today, PT)

| # | Window | Build | Acceptance check |
| --- | --- | --- | --- |
| 0 | 10:45–11:00 | Scaffold, configs, `models.py`, `llm.py` (mock + crusoe + backoff), `.env.example`, key dialog | `greenstove smoke` prints the village and a mock JSON reply; with a key, lists Nemotron ids from `/v1/models` and gets one schema-valid reply |
| 1 | 11:00–11:55 | `weather.py`, `households.py`, `simulator.py`, `limits.py` | Energy balance closes within 0.1%; SoC in bounds; forced overload and clinic outage are counted. `greenstove sim --policy fcfs --day rainy-afternoon-1` shows the dinner trip and clinic outage |
| 2 | 11:55–12:20 | Spec Agent, verifier grading | A mock spec missing the clinic condition and loosening the battery floor comes back fixed and marked (test) |
| 3 | 12:20–1:20 | Pricing Agent, planner, Supervisor | `greenstove plan --day rainy-afternoon-1` prints each attempt, verdict and decision and ends in one of the three statuses. Tests: committee relaxation order; loop detection; out-of-bounds rejection; no `DONE` with a hard failure |
| 4 | 1:20–2:05 | Shared Brain: store, Keeper, briefs; wired into Spec, Pricing, Supervisor | Tests: note without evidence rejected; note whose number disagrees with its verdict rejected; newer better evidence supersedes; caps and expiry hold; agents can't write `facts`. `greenstove plan` with brain on cites keys and needs fewer attempts on a repeat weather class |
| 5 | 2:05–2:40 | SMS composer, checker, parser; minimal ledger with planting rule | Tests: wrong time blocked → template sent; `book 3pm`, `plant 3`, `stove broken` parse; fallback can't emit `VERIFY`/`YES`; one verifier is not enough |
| 6 | 2:40–3:00 | Execution with hourly drift, pings; brain sync between stations and villages | `greenstove run-day` shows an afternoon re-plan and pings of allowed kinds only; `brain sync` merges two stations with one conflict resolved |
| 7 | 3:00–3:30 | Baselines, eval (brain on/off/dump, village B cold start), `docs/results.md` | `greenstove eval --days 20 --seeds unseen` writes the tables and charts, mode labeled |
| 8 | 3:30–3:50 | Live Nemotron run | 3 unseen days, brain on and off, recorded in `docs/results.md` with tokens and calls |
| 9 | 3:50–4:10 | `greenstove demo`, README "Built at the hackathon", submission text | Demo tells the section 1 story in under 3 minutes |
| — | 4:10 | **Feature freeze** | Bug fixes only |
| — | 4:10–4:35 | Record the one-minute video | Shows only today's work |
| — | 4:35–4:50 | Submit: video link, public repo, every teammate added | Submitted by **4:50 PM**, ten minutes before the deadline |

- Tell me when a milestone reaches 80% of its window without passing, and propose the
  smallest cut that still passes. Do not silently overrun.
- Cut order if behind: ledger hash chain; village B cold start (keep station sync);
  Afaan Oromo and English templates; drift re-plans (keep the hourly check and the ping);
  live run on more than 3 days.
- Do not cut: the brain on/off measurement, the verifier, the Keeper's evidence check,
  the live Nemotron run.
- An acceptance check involving a live model must pass 3 runs in a row.

### Speed targets for the live demo

- `greenstove demo` in mock mode: under 60 s; live: under 3 min.
- One Pricing Agent call: under 10 s; print elapsed time per call.
- Verifier (both scenarios, 160 households): under 1 s. Brief compile: under 50 ms.

## 12. Build team: multiple coding agents

The code is written by a lead agent plus parallel coding agents. Each coding agent owns
its files, works in its own git worktree on its own branch, and runs its own tests. The
lead owns the shared files and merges. Because no two agents own the same file, merges
are conflict-free by construction.

**Phase A, lead alone (10:45–11:05): the contract.** `pyproject.toml`, `.env.example`,
configs, all pydantic models in `models.py`, `llm.py` with mock mode, and every module as
a stub with its final signature and docstring (`simulate(plan, scenario) -> Trace`,
`grade(plan, spec, level) -> Verdict`, `compile(role, situation) -> Brief`,
`admit(note) -> BrainEntry`, `check(msg) -> CheckResult`, …). Tests import the stubs.
Commit and push; every coding agent branches from this commit.

**Phase B, four agents in parallel (11:05–2:05).**

| Agent | Owns | Delivers (milestones) |
| --- | --- | --- |
| `grid` | `weather.py`, `households.py`, `simulator.py`, `limits.py`, `execution.py`, `tests/test_simulator.py`, `tests/test_execution.py` | 1, then 6 (execution and drift) |
| `loop` | `spec_agent.py`, `pricing_agent.py`, `planner.py`, `verifier.py`, `supervisor.py`, `mock_models.py`, their tests | 2, 3 (against the simulator stub until `grid` merges) |
| `brain` | `brain/*`, `tests/test_brain*.py` | 4 (store, Keeper, briefs, sync) |
| `comms` | `sms/*`, `ledger.py`, `config/sms-templates/*`, their tests | 5 |

**Phase C, three agents in parallel (2:05–3:30).** `grid` finishes execution; `loop` and
`brain` together wire briefs and notes into the Spec Agent, Pricing Agent and Supervisor
(`loop` edits the agents, `brain` edits `brain/*`); `comms` moves on to `baselines.py`,
`evaluate.py`, `report.py`.

**Phase D, lead (3:30–4:10).** Live Nemotron run, `demo.py`, `cli.py`, README, results.

Rules for every coding agent:

- Read `PLAN.md` and the contract commit first. Change only the files you own. If you need
  a change in `models.py`, `llm.py`, `cli.py` or another agent's file, write it in your
  report; the lead makes it.
- No live model calls; mock mode only. The lead runs the live checks.
- Run `pytest` for your files before every commit; commit small, with clear messages; the
  lead merges at 12:20, 1:20, 2:05, 3:00 and 3:30 and re-runs the full suite after each
  merge.
- Report at each merge point: what passes, what is stubbed, what you need from others.
- Stop and report when a window reaches 80% without the acceptance check passing.

The lead keeps the clock in section 11, merges, resolves interface questions within
minutes, and never lets a broken merge sit on `main`.

## 13. Judging map and Q&A

| Criterion | What we show |
| --- | --- |
| Technicality (40%) | Simulator-verified loop; evidence-checked shared memory with conflict rules and federation; SMS checker; live Nemotron |
| Creativity (25%) | Shared memory for unattended grid agents; Ge'ez-script SMS with a deterministic checker; planting credit |
| Live demo (20%) | Same day, brain off versus on, in the terminal, under 3 minutes, with a mock fallback that is labeled |
| Future potential (15%) | Open-weight models operating infrastructure within human bounds; villages learning from each other without trusting unverified memory |

Likely questions: What did you build today versus before? (Agent layer and brain today;
Green-Stove hardware and pilot earlier.) How do you know the simulator is right? (Pilot
config, invariant tests, placeholders labeled.) What stops a bad memory spreading?
(Evidence check, no notes from SMS, supersede rules.) Why not a dashboard? (Nobody is on
site to look at it.)

## 14. Docs to check before using an API

- Crusoe hackathon guide and skill file:
  https://github.com/crusoecloud/crusoe-developer-hub/tree/main/examples/hackathons/crusoe-cv-recursive-agents-hackathon
- Sign-up: https://console.crusoecloud.com/request-foundry; credits form:
  https://forms.gle/JdEvMGSb4TURH1Tq8
- Serverless models: https://docs.crusoecloud.com/serverless-inference/available-models;
  rate limits: https://docs.crusoecloud.com/serverless-inference/rate-limits
- Nemotron: https://developer.nvidia.com/topics/ai/nemotron and the model cards on
  Hugging Face (chat template, thinking switch)
- SMS encoding: 3GPP TS 23.038 (GSM-7; UCS-2 for Ge'ez and Arabic script)
