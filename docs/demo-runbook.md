# Demo runbook

The commands, order, timing and fallbacks for the three-minute live demo and the
one-minute video. Segment owners follow [submission.md](submission.md). Commands marked
*(Abel)* depend on Abel's evaluation code; everything else is in the agent layer.

## Tools used on stage

| Tool | What it shows | Command |
| --- | --- | --- |
| Crusoe Managed Inference (live model) | Real Pricing Agent and Note-writer calls, with request ids, tokens and latency | `smoke --mode crusoe`, `demo --mode crusoe` |
| Nemotron (exact id from `/v1/models`) | The model behind both agent roles | printed in the banner of every command |
| Crusoe catalog, benchmark, usage | Exact Nemotron ids and pricing; model chosen by measured valid outputs, latency and cost; total cost, cache hit rate and latency after the run | `gsa models`, `gsa bench --mode live`, `gsa usage` (see [crusoe.md](crusoe.md)) |
| Terminal demo (`rich`) | World, attempts and verdicts, reservations, demand and battery strips, Keeper decisions, brief, fresh-agent comparison, infeasibility proof | `python -m greenstove demo` |
| Shared Brain tools | Entries, versions, expiry, conflict groups; audit log; current/lean/conflict-free numbers and citation rate; full provenance of one entry | `brain show`, `brain audit`, `brain stats`, `brain explain <id>` |
| Setup and readiness | Install, tests, mock smoke; PASS/WARN/FAIL table for key, spend flag, model id, world, brain, tests, git | `scripts/setup.sh`, `preflight` |
| Weather + solar forecast (You.com + Nemotron) | Cited forecast for the site and date, verified quote, PV and downside profiles in kWh | `gsa forecast --mode live`, then `gsa decide --forecast <file>` |
| Operator decision record | Today's tariff and why, memory relied on, both checks, bookings, who must confirm, rejected alternatives, cost | `gsa decide --scenario demo-handoff-overcast --condition C` |
| Version N+1 | Reviewer agent's playbook change kept only if it scores better; held-out before/after | `gsa evolve --rounds 2`, `gsa playbook before-after` |
| Always-on runner (NemoClaw-ready) | Events wake the agents; telemetry below plan raises a human alert with zero model calls | `gsa event day_start …`, `gsa runner --once`, `gsa runner status`; sandbox kit in `deploy/nemoclaw/` |
| End-to-end runner | every step (preflight, Crusoe, forecast, decide, warm-up, freeze, A/B/C, Version N+1, runner, demo, usage) in one folder with `e2e-summary.md` | `e2e` |
| Recorded transcripts | Every demo run is saved and can be replayed, labeled RECORDED | `demo --replay data/demo/<file>.txt` |
| Results table *(Abel)* | Held-out comparison of all six conditions | `docs/results.md` from `eval` |

All commands run as `.venv/bin/gsa <command>` (or `.venv/bin/python -m greenstove <command>`) from the repo root.
Q&A props: `gsa decide` shows the full decision an operator would get for one day; `gsa brain explain obs-…` answers "how do you know this memory is true?"; `gsa brain stats` answers "is it current, lean and used?".

## T−30 minutes: preflight

1. `git pull` on the branch you will demo, then `scripts/setup.sh` (install, tests, mock
   smoke, readiness table).
2. Live key: `.venv/bin/python scripts/set_crusoe_key.py --enable-live` (native dialog;
   the key goes straight into the ignored `.env`).
3. `python -m greenstove preflight --mode crusoe`: every row PASS or an understood WARN.
   It checks the key (length only), the spend flag, the model id in Crusoe's catalog, which
   world is loaded, the brain, the tests and git.
4. `python -m greenstove smoke --mode crusoe`: one valid proposal, recorded under
   `data/smoke/` with request id, tokens and latency. Then `gsa models` and
   `gsa bench --mode live --n 3`, and pin the winning Nemotron id in `GREENSTOVE_MODEL`.
5. Optional: `python -m greenstove warmup --mode crusoe` and `brain show`. The demo itself
   starts from a fresh memory workspace, so the observation the Keeper admits in the
   memory scene is exactly what the fresh agent reads in the handoff scene.
   `demo --dev-brain` uses the warm-up brain instead.
6. Record a full fallback run: `python -m greenstove demo --mode crusoe`. Note the
   transcript path it prints (`data/demo/demo-crusoe-<time>.txt`).
7. Check the banner says which world is running. If it says `stub-env (development
   stand-in, not Abel's simulator)`, Abel's world is not merged yet: say so on stage.
8. Close other apps, set the terminal font large, widen the window to at least 120 columns,
   and keep a phone hotspot ready.

The full pipeline (warm-up, freeze, held-out A/B/C, demo) is one command:
`python -m greenstove e2e --mode crusoe`. See [end-to-end.md](end-to-end.md).

## Live sequence (3:00): eight acts

Before the demo: `gsa evolve --rounds 2` and `gsa playbook before-after` (Act 8 reads them).
Then run `gsa demo --mode live --pause` (Enter moves to the next act). Every act opens with
the question it answers and closes with a green ✓ line saying what it proved, computed from
the run (it never claims a saving that did not happen). The closing card, "What the demo
proved", maps each claim to its track requirement and evidence, including the sponsor tools.

| Time | Act | Speaker | Say |
| --- | --- | --- | --- |
| 0:00–0:10 | Title card | You | "An agent team that runs a community solar cooking grid unattended, proves every plan before acting, and shares what it learns. Built today on Crusoe." |
| 0:10–0:25 | 1 · One line becomes checks | You | "One line of goal; the Spec Agent wrote the checks; code tightened one and added one. It can never loosen them." |
| 0:25–0:35 | 2 · The day ahead | Abel | "Households cook at home with different appliances, times and budgets; fixed station limits." |
| 0:35–1:00 | 3 · Agents propose, code decides | You | "Nemotron proposes prices; code books and checks two forecasts; failures come back as reasons: re-plan, re-plan, continue." |
| 1:00–1:20 | 4 · Only proven knowledge enters | You | "The Keeper rebuilds every fact from evidence; this fabricated note is rejected." |
| 1:20–1:45 | 5 · A fresh agent starts where the last ended | You | Point at the bars: calls and tokens, no memory vs Shared Brain. "One example; the table is the evidence." |
| 1:45–2:05 | 6 · Tomorrow on a real forecast | You | "You.com finds tomorrow's forecast, Nemotron extracts it, code checks the quote; the agents plan tomorrow on it." |
| 2:05–2:20 | 7 · Knowing when to stop | Abel | "Impossible days are proven impossible before spending a call, and a human is pinged." |
| 2:20–2:35 | 8 · Version N+1 | Abel | "A playbook change is kept only if it helps, then checked on unseen days." |
| 2:35–3:00 | Closing card + results table | Both | Track by track, what was shown; Crusoe calls and cost; NemoClaw kit status; the held-out table. |

Single acts: `demo --scene goal|world|proposal|memory|handoff|tomorrow|infeasible|improve`.
Shorter cut if time is tight: skip acts 2 and 8 (`--scene` one by one).

## Fallbacks

| Problem | Do this | Label on screen |
| --- | --- | --- |
| Crusoe error, 429 or 503 mid-demo | The client already backs off; if it still fails, rerun the scene with `--mode mock` | Banner shows MOCK (rule-based stand-in) |
| No network | `demo --replay data/demo/demo-crusoe-<time>.txt` from preflight step 6 | RECORDED |
| Abel's world not merged | Run as is | Banner shows stub-env; say it out loud |
| Handoff brief empty | The proposal scene did not run in the same process; rerun `demo` with all scenes, or use `--dev-brain` after `warmup` | — |

Never present mock output as model quality, and never present a replay as live.

## One-minute video

Record the screen (QuickTime → New Screen Recording) while running
`demo --mode crusoe --pause`, then cut to these shots:

| Seconds | Shot |
| --- | --- |
| 0–10 | Problem and today's scope: agent layer and Shared Brain built today; Green-Stove hardware is earlier work |
| 10–25 | `proposal`: proposal, failed check, passing attempt, reservations |
| 25–40 | `memory` and `handoff`: note admitted, fabricated note rejected, fresh agent reading the brief |
| 40–55 | Results table *(Abel)* |
| 55–60 | Tools actually used and limits |

Upload unlisted (YouTube, Loom or Google Drive), check the link in a private window, and
put it in the submission form with the public repo link.

## Evidence to keep

Runs, evidence and the brain live in `data/` (git-ignored). Before submitting, copy the
smoke record, the live demo transcript and the frozen brain snapshot
(`brain freeze --out docs/evidence/brain-frozen`) into `docs/evidence/` and check them
for secrets: they contain no keys by design, but look anyway.
