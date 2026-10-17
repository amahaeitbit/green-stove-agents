# Non-LLM comparators D, E, F3, Ffull

Partner's labeled implementation (`greenstove/comparators.py`) of the D/E/F rows from
`docs/simulator-and-evaluation.md` ("Six conditions"). Abel owns baselines and evaluation per
PLAN.md. This is a reference he can review, replace or adopt. A/B/C are unchanged.

## Same inputs as A/B/C

- **Environment:** the same `allocate -> simulate -> verify` calls the Supervisor makes, on
  `forecast_expected` and `forecast_downside`. The selected plan is replayed once on the day's
  actual weather. Like the Supervisor, a comparator first runs `env.lower_bound` and stops as
  `INFEASIBLE` when the energy bound proves the day cannot work.
- **Requirements:** the frozen definition of done. The world verifies against
  `dod.requirements(DEFAULT_REQUIREMENTS)`, and every forecast verdict must also pass
  `dod.evaluate(metrics)`.
- **Pass rule:** `supervisor.complete_pass` on both forecast phases, no unmodeled phase, and no
  definition-of-done failure. An incomplete-coverage world stops with "not a verified plan".
- **Memory:** `history.jsonl` in the freeze dir, the same development evidence condition B
  reads. A *verified library entry* is a (config, scenario, day, tariff) group whose records
  passed both forecast phases and the definition of done on their recorded metrics. Like the
  history brief, only same-`config_hash` records from days before today count.
- **Cost:** 0 model calls and 0 tokens. One candidate check is one tariff allocated and verified
  on both forecasts. Simulator runs include the actual-day replay, counted the same way for A-F.

## Conditions

| ID | Rule | Candidates |
| --- | --- | --- |
| D | Newest compatible verified entry, ordered by (day, run_id) | 1 |
| E | Compatible verified entry nearest to today's `situation_of(context)`. Ties go to the lower run_id | 1 |
| F3 | Frozen lattice order; stop at the first pass | at most 3 |
| Ffull | Same order over all 27 lattice tariffs; stop at the first pass | at most 27 |

**E distance** (fixed before evaluation): 1.0 x weather mismatch
+ |Δbattery_frac| / 0.3 + |Δdemand_kwh| / max(1, demand) / 0.3
+ |Δforecast_pv_kwh| / max(1, pv) / 0.3 + |Δflex_hours| / 3, where demand and pv are
today's values. The protocol also lists peak requested power. In the stub that value scales
with demand, so it is left out. Abel can add it before evaluation.

**Compatibility** (applies to D and E): same `config_hash`, a day before today, tariff windows
equal to today's, every price on today's lattice, tier order respected. If no entry is
compatible, or the one candidate fails, the comparator records that failure ("no compatible
candidate", 0 checks). It never falls back to anything else.

**F order** (frozen from development data, no held-out or actual-weather input):
1. Number of distinct development days on which the tariff verified, highest first.
2. Cheapest tier ascending.
3. Most expensive tier descending.
4. Middle tiers ascending.

With no development data, the first candidate is sun 3 / standard 6 / peak 16. The order hash
is in each F row's `source`.

## Run

```bash
gsa compare --freeze data/freeze-... [--out DIR] [--days 8] [--mode mock] [--stub]
```

`compare(freeze_dir, out_dir, days, env, llm, dod)` runs A/B/C through
`agent_eval.run_conditions(..., dod=dod)`, then D/E/F3/Ffull on the same held-out days. It
writes these files:

- `six-conditions.jsonl`: one row per condition and day, with the full record.
- `results-table.md`: one row per condition, MOCK/STUB labels and computed interpretation notes.
- `comparators-manifest.json`
- `agent-runs.jsonl`

`register(sub)` adds the subcommand to the CLI. The lead wires it into `cli.main()`.

## Reading the table honestly

The notes are computed from the counts. They state which baselines C beats, ties or does not
beat on verified plans and actual execution. When Ffull verifies at least as many days as C,
the table says the run gives no evidence the LLM is necessary. Fewer LLM calls is never
claimed against D/E/F, which use none. C's warm-up and curation cost is not in the rows. Mock
mode and the stub world test only the harness.
