# Green-Stove Agents: notes for Claude Code sessions

Hackathon project (Crusoe Recursive Agents Hackathon, Oct 17, 2026; submissions due 5:00 PM
PT, team target 4:50). Primary track **Shared Brain**, with **Done Means Done** and
**Version N+1** also covered. Read `README.md`, `PLAN.md`, `docs/person-b-plan.md` and
`docs/contracts.md` before changing anything.

## Team and ownership

| Person | Branch | Owns |
| --- | --- | --- |
| Abel (environment and evidence) | `environment-evidence` | `greenstove/models.py`, `households.py`, `allocator.py`, `simulator.py`, `verifier.py`, `admission*.py`, `billing.py`, `tariff.py`, `io.py`, `config/`, `fixtures/`, `schemas/`, baselines, evaluation, `docs/contracts.md` |
| Isaac (partner: agents and memory) | `agents-memory` | `llm.py`, `crusoe.py`, `spec_agent.py`, `pricing_agent.py`, `note_writer.py`, `supervisor.py`, `brain/*`, `agent_eval.py`, `decision.py`, `demo.py`, `cli.py`, `runner.py`, `evolve.py`, `playbook.py`, `tracks.py`, `forecast/*`, `stub_env.py`, `scenarios.py`, `world_adapter.py`, `scripts/setup.sh`, `deploy/nemoclaw/` |

Change only the files your person owns; ask the other person (or write it in a doc) for changes
to theirs. `agents-memory` already contains everything from `environment-evidence` up to the
last merge; merge Abel's branch into it, never the other way without both agreeing.

## Run and test

```bash
scripts/setup.sh                          # venv (.venv, Python 3.12 via uv), install, .env, tests, mock smoke
.venv/bin/python -m pytest -q             # all tests
.venv/bin/python scripts/c0_check.py      # Abel's checks (also allocator_check.py, admission_check.py)
.venv/bin/gsa e2e                         # every step, mock model, stand-in world (16 steps)
GREENSTOVE_WORLD=abel .venv/bin/gsa e2e   # same on Abel's world (C0: power only)
.venv/bin/gsa tracks                      # 17 requirement checks for the three tracks
.venv/bin/gsa demo --pause                # stage demo; see docs/demo-runbook.md
```

Always read the **World coverage** line of `gsa e2e`: "ok" steps on a world that could not
model a phase are partial and are not evidence.

## Live mode

Live Crusoe/Nemotron and You.com calls need keys in the git-ignored `.env`:
`CRUSOE_API_KEY`, `YDC_API_KEY`, `GREENSTOVE_LLM=crusoe`, `GREENSTOVE_LIVE_SPEND_OK=1`.
On macOS `scripts/set_crusoe_key.py [--enable-live] [--var YDC_API_KEY]` opens a native dialog;
on Linux/WSL edit `.env` directly. Never print, log or commit keys. Then:
`gsa preflight --mode live`, `gsa smoke --mode live`, `gsa models`, `gsa bench --mode live`,
`gsa e2e --mode live`.

## Rules for any session

- Label everything honestly: MOCK vs live, stub world vs Abel's world, recorded vs live demo.
  Never present mock or stub numbers as evidence of model quality or memory benefit.
- The model proposes; code decides. Never let model text set a status, relax a requirement,
  write approved memory directly or change committee bounds. The Spec Agent may only tighten.
- A pass with incomplete coverage (Abel's C0 world) is never `PLAN_VERIFIED`.
- Evaluation memory is frozen; held-out results never update the brain or prompts.
- Commits end with a `Co-Authored-By` line for the assistant that wrote them; push only to
  your own branch unless both people agree.

## Where things are

`docs/end-to-end.md` (timed pipeline), `docs/demo-runbook.md` (stage script and fallbacks),
`docs/decisions.md` (open questions between the two branches), `docs/crusoe.md`,
`docs/tracks.md`, `docs/always-on.md`, `docs/version-n-plus-1.md`, `deploy/nemoclaw/README.md`
(sandbox on a WSL2 + NVIDIA laptop). Run outputs go to `data/` (git-ignored).
