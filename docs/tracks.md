# Track conformance: problem statement → mechanism → proof

Run all of it with one command (mock model, stub world, isolated work folder, about 2 s):

```
python -m greenstove tracks [--out DIR]      # writes DIR/tracks-scorecard.md and .json; exit 0 only if every row passes
```

Every row runs the real code path. Scripted mock handlers force the cases the default mock never
produces: a model that claims success on a failing tariff, a repeated tariff, proposals that get worse.
Test: `tests/test_tracks.py`. Code: `greenstove/tracks.py` (`run_scorecard(out_dir)`).

## Done Means Done

| # | Problem statement says | Mechanism | File | Command |
| --- | --- | --- | --- | --- |
| 1 | "writes its own definition of done" | Spec Agent turns the one-line goal into checks. Code keeps only metrics the verifier computes, raises a threshold that is looser than the committee's (`code_tightened`) and adds any check the model left out (`code_added`). The spec is hashed, cached and the same for A/B/C | `spec_agent.py` | `tracks` row 1 |
| 2 | "checks its work with tests or independent verifier agents" | The status comes from the verifier on the expected and downside forecasts plus the definition of done. The model's rationale is never read | `supervisor.py`, `verifier.py` | row 2 |
| 3 | "catches itself … looping" | A repeated tariff hash leads to `stop` ("looping") | `supervisor.py` | row 3 |
| 4 | "catches itself drifting" | Each attempt gets a score. A worse attempt adds a `drift_warning` to the feedback. Two worse attempts in a row lead to `stop` ("drifting") | `supervisor.py` | row 4 |
| 5 | "decides whether to continue, re-plan, stop or … ping a human" | `RunRecord.decisions` holds each decision with its reason, and `decision.md` is written for every run | `supervisor.py`, `decision.py` | row 5 |
| 6 | "(rarely) ping a human" | `ping_human` happens only on NO_VERIFIED_PLAN or INFEASIBLE (energy proof) days | `supervisor.py` | row 6 |
| 7 | "checks they can run without you, so you can actually walk away" | Always-on runner: a `day_start` event plans the day with no human input. Telemetry below plan raises an alert with zero model calls | `runner.py` | `runner --once`, row 7 |

## Shared Brain

| # | Problem statement says | Mechanism | File | Command |
| --- | --- | --- | --- | --- |
| 8 | "every agent reads before it works" | A fresh Pricing Agent and Note-writer each get a compiled brief. Each `CallRecord.memory_hash` is the brief snapshot | `supervisor.py`, `brain/briefs.py` | row 8 |
| 9 | "updates afterward" | The Note-writer proposes notes. The Keeper rebuilds every fact from evidence and admits the note (audited) | `note_writer.py`, `brain/keeper.py` | `warmup`, row 9 |
| 10 | "current" | Entries carry a TTL and a config hash. A brief serves only entries with `created_at <= clock < expires_at` | `brain/store.py`, `brain/briefs.py` | row 10 |
| 11 | "lean" | Every brief has a token budget, and the store has a cap with eviction for the soonest-expiring entry | `brain/briefs.py`, `brain/keeper.py` | `brain stats`, row 11 |
| 12 | "conflict-free" | Keeper stages (`evidence_unknown`, `result_mismatch`, …) reject fabricated notes. An opposite outcome for the same situation and tariff is `kept_separate` in a conflict group and nothing is overwritten | `brain/keeper.py` | `brain audit`, row 12 |
| 13 | "measurable gains in output quality" | Development memory is frozen, then conditions A (facts), B (raw history) and C (curated) run on held-out days. Stale entries served must be 0 | `agent_eval.py` | `freeze`, `eval-agents`, row 13 |

## Version N+1

| # | Problem statement says | Mechanism | File | Command |
| --- | --- | --- | --- | --- |
| 14 | "reviews what worked and what didn't" | The reviewer reads compact summaries of development runs (attempts, failure codes, status). Held-out runs are dropped | `evolve.py` | row 14 |
| 15 | "rewrites its own … playbooks" | The reviewer writes a candidate playbook version, which is validated: no rule changes, no scenario ids | `evolve.py`, `playbook.py` | `evolve --rounds N`, row 15 |
| 16 | "keeps a change only if it measurably helps" | The gate is `keep_decision`: the verified rate must not drop, and the first-candidate pass rate must rise or calls must drop. Ties reject | `evolve.py` | `playbook history`, row 16 |
| 17 | "before-and-after score on tasks it hasn't seen" | `before_after` compares the seed playbook with the active one on HELDOUT scenarios only and refuses development scenarios | `evolve.py` | `playbook before-after`, row 17 |

## What this does not prove

- **Mock model.** The agents are rule-based stand-ins (the mock Pricing Agent follows playbook directives literally). The rows show that the mechanisms work, not how good Nemotron is at writing specs, prices, notes or playbooks.
- **Stub world.** The world is the development stand-in, not Abel's simulator. Its numbers are synthetic.
- **Small samples.** There are a handful of development days and 3 held-out days. A difference of one day is not significant. The A/B/C and before/after rows report the comparison but do not assert a gain.
- **Forced cases.** Scripted handlers create the looping, drifting and false-success cases. They show the Supervisor's response, not how often a real model behaves that way.
- **Gains need live runs.** Evidence of gains needs live Crusoe Nemotron runs (`e2e --mode live`, `evolve --mode live`, then `playbook before-after --mode live`) on the real world.
