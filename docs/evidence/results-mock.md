# Green-Stove Agents: results

- Generated: 2026-10-17 15:41 from `data/e2e-20261017-154107` (e2e run 20261017-154107)
- Commit: `3c0e656` (e2e ran at `3c0e656`)
- Model mode: **MOCK (rule-based stand-in)** · model id `mock-rules`
- World: stub-env (development stand-in, not Abel's simulator)

**MOCK model · STUB world: these numbers test the mechanisms, not model quality.**

## 1. Run status

World coverage: 574 simulations checked, 0 phases the world could not model

18/18 steps ok (required steps stop the run; optional ones warn).

| Step | Status | Shows |
| --- | --- | --- |
| preflight | ok | readiness: keys, spend flag, model id, world, tests |
| smoke | ok | one structured Crusoe call recorded |
| spec | ok | Done Means Done: one-line goal -> definition of done |
| bench | ok | Nemotron model chosen by measurement |
| forecast | ok | You.com + Nemotron forecast -> solar profile |
| decide-forecast | ok | Done Means Done: one day planned on the forecast, decision.md |
| warmup | ok | Shared Brain written from development days |
| freeze | ok | brain snapshot + raw history hashed |
| eval-agents | ok | Shared Brain: A/B/C on held-out days |
| compare | ok | six conditions: A/B/C + last-tariff reuse, nearest reuse, search |
| evolve | ok | Version N+1: playbook kept only if it helps |
| before-after | ok | Version N+1: held-out before/after |
| runner-event | ok | always-on: enqueue a day_start event |
| runner | ok | always-on: event processed, zero idle calls |
| demo | ok | recorded demo transcript |
| tracks | ok | scorecard: every requirement of Done Means Done, Shared Brain, Version N+1 |
| usage | ok | calls, tokens, cache hits, latency, cost |
| acceptance | ok | Abel's joint acceptance run through the app (admission ledger) |

## 2. Definition of done (Done Means Done)

Goal: "Every household cooks on electricity at a time it accepts and can afford, the mini-grid never overloads, and the essential load is never left unserved."

Spec Agent restated it as: "Book every cooking request it can, close to preferred times, within grid limits." (model `mock-rules`, record `data/e2e-20261017-154107/spec/spec-15c2533e0403ac0d.json`).

| Metric | Check | Source | Why |
| --- | --- | --- | --- |
| served_fraction | >= 0.75 | code_tightened | 'every household cooks on electricity' |
| max_shift_hours | <= 4.0 | agent | 'at a time it accepts' |
| overload_steps | <= 0.0 | agent | 'never overloads' |
| unserved_cooking_kwh | <= 0.0 | agent | 'cooks on electricity': a started meal must not be cut off |
| unserved_essential_kwh | <= 0.0 | code_added | committee requirement |

Sources: `agent` = written by the Spec Agent; `code_tightened` = code raised a threshold the model set too loosely; `code_added` = a committee requirement the model omitted, added by code. Code may only tighten.

## 3. Headline comparison (Shared Brain)

Six conditions A–F from `data/e2e-20261017-154107/compare/results-table.md` (official table):

### Six-condition results (MOCK · STUB world)

Model: **mock-rules (MOCK)** · world: **stub-env (development stand-in, not Abel's simulator)** · commit 3c0e656 · brain snapshot 2293a63e · definition of done 6bf9dbe9 · 8 held-out days

Per-day rows: `six-conditions.jsonl` (A/B/C raw records also in `agent-runs.jsonl`). One candidate check = allocate one tariff and verify both forecast phases; simulator runs include the actual-day replay for every condition. Served is the actual-weather served fraction; a day with no dispatched plan counts as 0 served (nothing cooks). D/E/F are the partner's labeled implementation for Abel to review.

| Condition | Verified | First candidate | Calls/day | Tokens/day | Candidate checks/day | Simulator runs/day | Exec pass | Mean served (actual) | Worst-day served |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A facts only (LLM) | 7/8 | 4/8 | 1.88 | 2144 | 1.88 | 4.62 | 7/8 | 0.66 | 0.00 |
| B raw history (LLM) | 7/8 | 4/8 | 1.88 | 3115 | 1.88 | 4.62 | 7/8 | 0.66 | 0.00 |
| C curated brain (LLM) | 7/8 | 4/8 | 1.88 | 3235 | 1.75 | 4.38 | 7/8 | 0.66 | 0.00 |
| D last verified tariff | 4/8 | 4/8 | 0.00 | 0 | 1.00 | 2.50 | 4/8 | 0.38 | 0.00 |
| E nearest-context reuse | 4/8 | 4/8 | 0.00 | 0 | 1.00 | 2.50 | 4/8 | 0.38 | 0.00 |
| F3 lattice search, budget 3 | 6/8 | 4/8 | 0.00 | 0 | 1.75 | 4.25 | 6/8 | 0.56 | 0.00 |
| Ffull lattice search, full | 7/8 | 4/8 | 0.00 | 0 | 5.38 | 11.62 | 7/8 | 0.66 | 0.00 |

**MOCK mode:** A/B/C use a rule-based stand-in for the model. These numbers test the harness, not Nemotron and not the value of memory.

**STUB world:** development stand-in, not Abel's simulator.

#### Interpretation

- C ties A on verified plans (7/8 vs 7/8); ties it on actual execution (7/8 vs 7/8); candidate checks/day 1.75 vs 1.88.
- C ties B on verified plans (7/8 vs 7/8); ties it on actual execution (7/8 vs 7/8); candidate checks/day 1.75 vs 1.88.
- C beats D on verified plans (7/8 vs 4/8); beats it on actual execution (7/8 vs 4/8); candidate checks/day 1.75 vs 1.00.
- C beats E on verified plans (7/8 vs 4/8); beats it on actual execution (7/8 vs 4/8); candidate checks/day 1.75 vs 1.00.
- C beats F3 on verified plans (7/8 vs 6/8); beats it on actual execution (7/8 vs 6/8); candidate checks/day 1.75 vs 1.75.
- C ties Ffull on verified plans (7/8 vs 7/8); ties it on actual execution (7/8 vs 7/8); candidate checks/day 1.75 vs 5.38.
- Full deterministic search over the same lattice verified 7/8 days with 0 model calls (5.38 checks/day): this run gives no evidence that the LLM is necessary to find a verified plan.
- D/E/F make 0 inference calls, so fewer LLM calls is not a claim against them; compare candidate checks, simulator runs and service instead. Warm-up/curation overhead for C is not in these rows.
- Small single-run sample: descriptive, not statistical significance. Failed days stay in every denominator.

Partner-side raw A/B/C records: `data/e2e-20261017-154107/eval/agent-runs.jsonl`.

## 4. Version N+1 (playbook before/after on unseen days)

| Version | Verified | First candidate | Calls/day | Tokens/day | Checks/day | Exec pass |
| --- | --- | --- | --- | --- | --- | --- |
| before v001 (human-seed) | 7/8 | 4/8 | 1.88 | 2144 | 1.88 | 7/8 |
| after v002 (reviewer-agent) | 7/8 | 6/8 | 1.38 | 1651 | 1.38 | 7/8 |

Held-out check under the same keep rule: HELPED (first-candidate pass 4/8 -> 6/8, verified 7/8 held).

Source: `data/e2e-20261017-154107/playbook/before-after.md` (condition A, memory off; a separate experiment from the headline comparison).

Playbook versions (active: **v002**):

| Version | Author | Status | Decision |
| --- | --- | --- | --- |
| v001 | human-seed | retired | human seed (empty playbook: the unchanged Pricing Agent) |
| v002 | reviewer-agent | active | kept: first-candidate pass 3/5 -> 4/5, verified 4/5 held |

Evolve rounds: 2 (1 kept, 0 rejected by the keep rule, 1 with no candidate).
- round 1: kept v002: kept: first-candidate pass 3/5 -> 4/5, verified 4/5 held
- round 2: no_change (no candidate): reviewer proposed no change; nothing to test

## 5. Shared Brain health

| Measure | Value |
| --- | --- |
| entries | 10 current / cap 20 |
| current outcomes | 5 pass, 5 fail |
| conflict groups (kept separate, not overwritten) | 0 |
| confirmations | 1 |
| Keeper admissions | added 10, confirmed 1 |
| Keeper rejections by code | none |
| evicted for cap / expired | 0 / 0 |
| runs with a curated brief | 5 |
| mean brief tokens / entries supplied | 317.0 / 4.8 |
| proposals citing memory | 5/9 |
| snapshot | f0cb9316 |
| frozen for evaluation | snapshot 2293a63e, 10 entries, 25 raw history records |

Most-cited entries (development runs; descriptions are model words, not trusted facts):

| Entry | Cited | Outcome | Tariff | Codes | Note |
| --- | --- | --- | --- | --- | --- |
| obs-006 | 3 | PASS | peak=13, standard=8, sun=3 |  | Prices peak=13 standard=8 sun=3 passed; min battery 1.26 kWh, served 0.75. |
| obs-003 | 1 | PASS | peak=13, standard=8, sun=4 |  | Prices peak=13 standard=8 sun=4 passed; min battery 1.97 kWh, served 0.75. |
| obs-002 | 1 | PASS | peak=13, standard=8, sun=4 |  | Prices peak=13 standard=8 sun=4 passed; min battery 1.98 kWh, served 0.75. |

Explain any entry: `gsa brain explain <id>` with `GREENSTOVE_DATA_DIR=data/e2e-20261017-154107`.

## 6. Track scorecard

**17/17 requirement rows PASS** · Done Means Done 7/7 · Shared Brain 6/6 · Version N+1 4/4 · full table `data/e2e-20261017-154107/tracks/tracks-scorecard.md`

Scorecard label: MOCK model · STUB world · these rows test the mechanisms, not model quality; live Nemotron runs are needed for evidence of gains

## 7. Crusoe usage, model choice and forecast

Calls by mode, role and model (`data/e2e-20261017-154107/usage.jsonl`):

| mode | role | model | calls | outcomes | prompt_tokens | completion_tokens | cache_hit | p50_ms | p95_ms | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mock | forecast_extractor | mock-rules | 2 | ok=2 | 470 | 132 | 0.0 | 1 | 1 |  |
| mock | note_writer | mock-rules | 14 | ok=14 | 17682 | 3662 | 0.0 | 0 | 1 |  |
| mock | playbook_reviewer | mock-rules | 2 | ok=2 | 1644 | 121 | 0.0 | 0 | 0 |  |
| mock | pricing | mock-rules | 192 | ok=192 | 255290 | 9073 | 0.0 | 0 | 1 |  |
| mock | spec_writer | mock-rules | 4 | ok=4 | 1216 | 536 | 0.0 | 0 | 0 |  |

All calls are MOCK: no Crusoe call was made, so latency and cost are not measured.

Model benchmark on the fixed Pricing Agent task (`data/e2e-20261017-154107/crusoe/bench-20261017-154128.json`):

| model | valid | calls | p50_latency_ms | mean_tokens | cached_tokens | cost_usd |
| --- | --- | --- | --- | --- | --- | --- |
| nvidia/Nemotron-3-Super-120B-A12B | 2/2 | 2 | 0 | 946 | 0 |  |

Forecast record `data/e2e-20261017-154107/forecast/asmara-eritrea-2026-10-18-mock.json`: Asmara, Eritrea on 2026-10-18, class **rainy_afternoon**, PV 13.4 kWh expected / 10.4 kWh downside (3.0 kWp), status VERIFIED, search mode mock, extractor mock, 1 source(s) (MOCK search: not a real forecast).

Other outputs: 7 operator decision records (e.g. `data/e2e-20261017-154107/evidence/runs/run-demo-handoff-overcast-curated-ef05eb/decision.md`); recorded demo transcript `data/e2e-20261017-154107/demo/demo-mock-20261017-154136.txt`.

## 8. What this does not prove

- **Mock model.** Every agent is a rule-based stand-in (the mock Pricing Agent follows playbook directives literally). The numbers show the mechanisms work, not how good Nemotron is.
- **Stub world.** The world is the development stand-in, not Abel's simulator; its numbers are synthetic.
- **Small samples.** 8 held-out days per condition and 8 for before/after; a difference of one day is not significant. Comparisons are reported, not asserted.
- **Forced cases.** Scripted handlers create the looping, drifting and false-success scorecard rows; they show the Supervisor's response, not how often a real model behaves that way.
- **No deployment claim.** This is an offline harness on simulated mini-grid days, not a field trial.
- **Gains need live runs.** Evidence of gains needs `gsa e2e --mode live` (Crusoe Nemotron) on the real world.
