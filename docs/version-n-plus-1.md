# Version N+1: the team rewrites its own playbook, and keeps only measured gains

Status: built and tested in mock mode on the stub world. This is a **separate, labeled extension**. It does not change the headline A/B/C Shared Brain experiment ([PLAN.md](../PLAN.md) sections 1 and 8, [evaluation protocol](simulator-and-evaluation.md)), and the A/B/C runs never see a playbook. PLAN.md makes no Version N+1 claim from the headline experiment; [the partner-tools notes](llm-learnings-and-partner-tools.md) list it as a later extension that needs evaluation on untouched tasks, which is what this page does.

## The loop

```
development runs (condition A) ──► Playbook Reviewer (LLM role "playbook_reviewer")
        ▲                                │  reads: active playbook + id-free run summaries
        │                                │  (situation, tariffs tried, feedback codes, status, calls)
        │                                ▼
        │                         candidate playbook ── validation (code): ≤ 900 chars, bullets only,
        │                                │              no rule changes, no scenario ids
        │                                ▼
        │                 fresh agent days on the development TUNING set: candidate vs active
        │                                │
        └──── kept: becomes active ◄── keep rule ──► rejected (recorded with its scores)
                                                  
after the last round: seed v001 vs final active on HELDOUT_SCENARIOS (unseen) ──► before-after.md/.json
```

A playbook is short text the Pricing Agent reads as `team_playbook` in its payload. It holds heuristics for the first proposal of a day. The most useful kind is a machine-readable directive: `- when battery_frac < 0.45: start sun=3 standard=8 peak=13`. It can never change rules. Windows, allowed prices, tier order, reserve, inverter limit, requirements and household preferences stay fixed, and only the verifier decides pass or fail. Versions live in `<data_dir>/playbook/` as `v001.md`, `v002.md` and so on, plus `index.json`. Each version records its parent, author (`human-seed` or `reviewer-agent`), text hash, status (active, candidate, rejected or retired), development scores and the reason for the decision. The seed `v001` is empty, so "before" is exactly the unchanged Pricing Agent.

## The keep rule

A candidate is kept only if, on the development tuning set (all of `DEV_SCENARIOS` by default):

- the verified rate does not drop, **and**
- the first-candidate pass rate improves **or** the mean number of model calls per day drops.

Ties are rejected. A reviewer output that makes no change is not evaluated. An output that fails validation never becomes a version. Every round is appended to `evolve-log.jsonl`.

## Why condition A

Every gate and before/after run uses memory mode `"off"` (condition A, facts only), `brain=None`, `write_notes=False`, an empty history, and a separate evidence store under `playbook/evals/`. A playbook gain therefore cannot be explained by a better brief from the Shared Brain, and the experiment cannot write to the brain or to the development evidence library. Tests check both points.

## Development tuning vs held-out

The reviewer sees summaries of **development** runs only. Summaries carry situation descriptors and attempts, not scenario ids, and held-out or evaluation-phase runs are dropped. The gate runs on development scenarios, and `evolve` refuses a tuning set that contains held-out days. The before/after comparison runs only on `HELDOUT_SCENARIOS`. These days were never reviewed or tuned on, and both rows use the same model, settings, attempt budget and condition. If the evolved playbook does not help there, the report says **DID NOT HELP**.

## What mock mode shows and what it does not

The mock reviewer is deterministic. It adds a `start` directive for each day that verified only after a failed first attempt, scoped by the first failure's codes (for example, a battery-reserve failure gives a battery threshold). It removes a directive whose start failed its first check. The mock Pricing Agent obeys a matching `start` directive literally, and only when there is no earlier attempt today and no usable memory. Mock mode therefore shows that the mechanism works end to end: review, validation, gate, versioning and an honest held-out check. It does **not** show that Nemotron writes good playbooks, or that it follows them.

Sample mock run (stub world, labeled MOCK · STUB WORLD):

| Version | Verified | First candidate | Calls/day | Tokens/day | Checks/day | Exec pass |
| --- | --- | --- | --- | --- | --- | --- |
| before v001 (human-seed) | 6/8 | 4/8 | 2.00 | 2058 | 2.00 | 6/8 |
| after v002 (reviewer-agent) | 6/8 | 6/8 | 1.50 | 1655 | 1.50 | 6/8 |

## Commands

The lead wires `evolve.register(sub)` into `greenstove/cli.py`.

```
python -m greenstove evolve --rounds 2 --mode mock [--stub]      # review -> gate -> keep/reject
python -m greenstove playbook show | history
python -m greenstove playbook before-after --mode mock [--stub]   # seed vs active on held-out days
```

Use `--mode live` (with `GREENSTOVE_LIVE_SPEND_OK=1`) for Nemotron. One round costs about 2 × 5 days × up to 3 pricing calls plus 1 reviewer call. The before/after costs 2 × 8 days × up to 3 calls.

## Honest limits

- The samples are small: 5 tuning days and 8 held-out days. A difference of one day is not significant, and repeated live runs are needed to see variance.
- The mock results come from directives that the mock agent obeys by construction. **Real evidence needs a live Nemotron run** for both the reviewer and the Pricing Agent.
- The stub world is a development stand-in. Rerun the experiment on Abel's world when it lands.
- The gate tunes on development days, so a kept change can overfit them. The held-out before/after is the check, and it is reported even when it is unfavourable.
- The playbook adds prompt tokens, so a neutral playbook costs slightly more per call.
- This experiment does not change or support the headline A/B/C claim about shared memory.
