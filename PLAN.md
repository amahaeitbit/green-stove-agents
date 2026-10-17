# Green-Stove Agents two-person build plan

Status: approved scope for implementation planning; functionality and performance remain unproven. This document supersedes the earlier broad build plan. Read [the contracts](docs/contracts.md), [evaluation protocol](docs/simulator-and-evaluation.md), and [architecture](docs/architecture.md) before implementation.

## 1. One claim and the remaining challenge

> Household agents coordinate cooking at a simulated solar station. A shared brain of scoped successes and failures helps fresh agents find acceptable tariffs with less planning effort, without weakening household service or community constraints.

Primary track: **Shared Brain**. Done Means Done supplies fixed checks, bounded attempts, and honest stopping. Do not claim Version N+1, deployed autonomy, or real-world safety from this experiment.

The strict-judge challenge is: why not reuse a tariff or search the small tariff space? Answer it experimentally, rather than making the task artificially complicated.

Our approach:

1. Store structured observations of successful AND failed development proposals, including the actual situation, action, outcomes, and evidence IDs.
2. Retrieve applicable observations for a fresh agent on an unseen combination of weather, battery, demand, and flexibility.
3. Compare curated memory with no memory, raw history, last-tariff reuse, nearest-context tariff reuse, and deterministic tariff search.
4. Count simulator evaluations, inference cost, and service quality. Fewer LLM calls alone is insufficient, especially against a baseline that uses no LLM.
5. Show the specific observation, proposal, resulting reservations, and machine verdict in the demo. Aggregate results establish the claim; a selected trace only illustrates it.

Memory superiority is a hypothesis. If nearest reuse or deterministic search wins, report that result and narrow the claim. Do not tune held-out cases or hide unfavorable baselines.

## 2. Frozen MVP and cuts

Build one configurable station, one fixed essential load, and six to ten synthetic households with different power, session durations, preferred starts, acceptable windows, budgets, and explicit auto-booking authority. Use 15-minute intervals and fixed tariff windows with three adjustable tier prices drawn from a finite configured lattice. No constraint relaxation in the headline experiment.

The LLM roles are Pricing Agent and Note-writer. Deterministic household agents score offers and accept only within configured authority. Do not claim every household runs an LLM. Pricing reads applicable experiences; Note-writer reads the evidence plus a relevant brief, proposes additions, and flags duplicates/conflicts. Code alone approves memory.

Keep: real Crusoe inference, structured observations, evidence checks, fresh-agent handoff, independent verification, credible baselines, and one infeasibility fixture.

Defer: Household Advocate, generalized-rule promotion, tree planting, financial ledger, generated SMS, federation, local model deployment, adaptive tariff windows, real hardware control, and intra-day multi-stage renegotiation. You.com research, OpenShell, and a Note-writer counterexample proposal are stretch work after the core path passes C3.

## 3. Ownership

| Owner | Files | Deliverable |
| --- | --- | --- |
| Abel | config/, greenstove/households.py, allocator.py, simulator.py, verifier.py | Heterogeneous requests, acceptance, reservations, physical model, machine verdicts |
| Abel | greenstove/baselines.py, evaluate.py, report.py; environment/evaluation tests | Baselines, independent scoring, frozen scenarios, results and limitations |
| Partner | greenstove/llm.py, pricing_agent.py, note_writer.py, supervisor.py | Crusoe/Nemotron calls, proposals, current-run feedback, budgets, loop detection |
| Partner | greenstove/brain/store.py, keeper.py, briefs.py; agent/memory tests | Scoped observations, evidence admission, expiry, shared briefs, audit trail |
| Partner | greenstove/cli.py, demo.py, pyproject.toml, .env.example | Mock/live modes, reproducible commands, demonstration |
| Abel after joint agreement | greenstove/models.py, docs/contracts.md | Shared typed contract and fixtures; partner requests changes rather than editing silently |
| Both | README, submission, assumptions, results review | Accurate scope, measured claims, completed submission |

The verifier does not trust totals supplied by agents or the allocator. It recomputes from the reservation trace. Shared physics helpers are acceptable, but use hand-calculated fixtures to catch common bugs. The split in developer ownership is useful audit discipline, not evidence that the simulator matches real hardware.

## 4. Joint preparation and working agreement

At C0, record the available time, hard submission deadline, model access, measured budget, and branch names. The supplied guide says submissions are due October 17 at 5 PM; confirm the venue timezone from the organizer. Use elapsed times below rather than a machine timezone. Reserve the final hour for evidence, rehearsal, and submission.

Use separate checkouts/branches: environment-evidence and agents-memory. Main must remain runnable. Merge small complete checkpoints; do not force-push over the other person's work. One owner edits shared contracts. Do not direct coding assistants to modify the same files concurrently. No automatic delegation is required by this plan.

At every checkpoint share: commit, command, observed result, next blocker. Use [the joint checkpoint log](docs/checkpoints.md) for both sign-offs. Pair for ten minutes when the integrated path breaks; pause new features until it works. Report at 80 percent of a phase if its exit check is unlikely to pass, and choose a cut together.

## 5. Six-hour reference schedule with joint gates

This is a reference envelope, not an instruction to overrun the real deadline. Shorten scope at C0 if less time remains. Commands are intended interfaces to implement, not commands that work in the plans-only repository today.

| Elapsed | Abel | Partner | Joint gate |
| --- | --- | --- | --- |
| 00:00-00:20 | Agree physical units, bounds, fixtures, metric definitions | Agree schemas, roles, model route, call budget | C0: contracts committed; no unclear ownership; one representative scenario |
| 00:20-01:10 | Tiny energy engine, households, allocator, hand-calculated cases | Runtime scaffold, mock proposal, live model smoke, memory schema | C1, 10 min: fixed tariff -> accepted reservations -> verdict; one real Crusoe response |
| 01:20-02:10 | Trace verifier, last/nearest reuse, infeasibility bound | Proposal/feedback loop, limits, repeat detection, evidence logging | C2, 10 min: live proposal -> verdict -> revised candidate or honest stop; bounds never weaken |
| 02:20-03:10 | Development suite, finite search baseline, common evaluation runner | Keeper, successful/failed observations, bounded briefs, Note-writer | C3, 15 min: supported note admitted; fabricated/stale/mismatched note excluded; fresh agent reads memory |
| 03:25-04:05 | Development ablations, service floors, test manifest | Stabilize prompts and retrieval; build visible trace; optional integration only if green | C4, 15 min: freeze scenarios, prompts, models/settings, memory/history, candidate lattice, metrics and comparison rules |
| 04:20-05:00 | Run held-out evaluation; inspect raw evidence | Monitor calls, collect logs, finalize demo command | C5: actual all-condition results; failures included; feature freeze |
| 05:00-06:00 | Audit numbers, limitations, reproducibility | Record demo, fix packaging, rehearse | C6: clean-checkout smoke, three-minute demo, public repo and submission links checked |

No acceptance gate may require brain-on to outperform brain-off. Gates check implementation and experimental validity; performance is measured afterward.

## 6. Abel's environment and evidence work

### A1. Specify the world

Create one synthetic station configuration with inverter power, PV profile, battery capacity, initial charge, charge/discharge power and efficiency, reserve floor, and fixed essential load. Mark parameters as synthetic unless supported by pilot records or reviewed sources. Ensure at least some development scenarios are feasible by hand calculation.

Create fixed household profiles with different appliance powers and durations. Record preferred time, acceptable window, shift penalty, budget, and booking authority. No private free-text explanation is needed. All comparison conditions see the same current requests.

Acceptance: one 1 kW session for 30 minutes uses 0.5 kWh before system losses; a 2 kW session doubles the interval load. Every fixture states expected values.

### A2. Allocate and verify

For each tariff, households score starts using quoted cost plus configured inconvenience, within acceptable windows. Use a fixed tie-breaking rule. Allocate reservations deterministically under forecast constraints; accept only when authorized. Record every unserved request and reason. For a quote crossing tier boundaries, integrate energy at each interval's price. Lock the quote on acceptance.

Check interval inverter limits, battery power limits, reserve, essential-load service, budget, and household windows. Do not silently clip away an energy deficit. Verify from the trace, and return machine-readable feedback codes. Keep the allocator identical in every condition.

Acceptance fixtures: overlapping high-power requests; low battery/no PV; battery-full charging; duplicate booking ID; outside-window offer; budget rejection; cross-tier quote; different actual versus forecast weather; expected-safe plan; deterministic energy shortfall.

### A3. Credible non-LLM baselines

Implement last successful tariff, nearest-context successful tariff, and finite deterministic tariff search. Use the same candidate lattice, households, allocator, verifier and forecast scenarios. Nearest-context distance and search order are fixed using development data. Details are in the evaluation protocol.

The search baseline is important because fixed windows and three prices may be a small optimization problem. Measure search effort and quality honestly; do not disadvantage it to justify the LLM.

### A4. Held-out evidence

Generate development cases varying weather class, starting battery, total demand and flexibility. Reserve unseen combinations and seeds without showing actual future weather to agents. Reset independent days to declared initial state; no cross-day learning in the headline evaluation.

Own metric calculation, evidence manifests, summaries and limitations. Keep all failed days in denominators. Measure inconvenience as absolute time shift and report late finishes separately. A plan with fewer completed meals cannot win merely by using fewer calls.

## 7. Partner's agents and memory work

Full plan, diagram, timeline, demo lines and definition of done:
[docs/person-b-plan.md](docs/person-b-plan.md). Status at 14:10 PT: all of B1–B5 built and
tested in mock mode (branch `agents-memory`, Abel's C0 world merged); live evidence pending
the Crusoe key.

| | Goal | Gate | Proof |
| --- | --- | --- | --- |
| **B1. Model path** | One Crusoe adapter; Nemotron id and settings from the live catalog, chosen by measurement and pinned; mock and live; schema validation, one repair, budgets, usage log without keys | C1: one real structured response; model in catalog; bench table | `gsa smoke`, `gsa models`, `gsa bench` |
| **B2. Bounded planning** | Fresh Pricing Agent proposes lattice-valid prices with cited memory; at most 3 attempts; repeat stop; no relaxation; honest status; an incomplete-coverage pass is never PLAN_VERIFIED; operator decision record | C2: live proposal through Abel's allocator and verifier, new candidate or reason-coded stop | `gsa run`, `gsa decide` |
| **B3. Shared Brain** | Note-writer reads a relevant brief and proposes; Keeper rebuilds facts from evidence and rejects with the first failing code; ≤20 entries with expiry; conflicts kept scoped; audit never served; 600-token deterministic briefs | C3: supported note admitted, fabricated rejected, stale excluded, fresh agent reads a recorded brief | `gsa brain show\|audit` |
| **B4. Handoff and evaluation** | Fresh agent per day; frozen brain and history; A/B/C on held-out days with stale-entry counts, cost and cache columns; demo where scene 3's admitted note is scene 4's brief | C4/C5: hashes in the manifest; A/B/C records for every held-out day | `gsa freeze`, `gsa eval-agents`, `gsa demo` |
| **B5. Extensions** | You.com forecast to solar profile; Version N+1 playbook kept only if it helps; always-on event runner; NemoClaw/OpenShell kit; all outside the headline claim | Labeled separately; never change A/B/C | `gsa forecast`, `gsa evolve`, `gsa runner` |

Everything chains in `gsa e2e [--mode live]`. Remaining for Person B: live key, then
C1 → C2 → warm-up and freeze → live A/B/C → recorded demo, per the timeline in the full plan.

## 8. Frozen experiment and success criteria

Use [the evaluation protocol](docs/simulator-and-evaluation.md) as the scoring authority. A/B/C use the same current inputs, model/settings, prompts apart from memory treatment, feedback and attempt budget. B and C use the same development evidence and memory token allowance. D/E reuse and F search use no LLM. Physical constraints, allocation and verification are identical for all six conditions.

At C4 write evaluation-manifest.json with commit/config/prompt/memory/history hashes, seed ranges, model/settings, candidate lattice, retrieval distance, search ordering, attempt limits, service thresholds, cost prices and sample size. Suggested sample: eight held-out days, increased to twelve only if time/budget permit. Eight days times three LLM conditions times three attempts is at most 72 initial pricing calls, before repair, warm-up and Note-writer overhead. Measure actual prices and token lengths before committing budget.

The claim is supported only if measured results show a reduction in effort at comparable service and no weakened constraints. Report exactly which comparator it beats. Do not say 'better than reuse' if it only beats the last tariff but not nearest-context reuse. Do not call the LLM necessary if search is faster, cheaper and equally good.

## 9. Partner tools with concrete evidence

| Tool | Owner | Use | Required demonstration |
| --- | --- | --- | --- |
| Crusoe | Partner | Core inference, fresh-agent handoff, call/usage traces | Real requests connected to proposal, memory snapshot and verdict |
| Nemotron | Partner | Pricing and Note-writer roles, if available through Crusoe | Exact model ID and measured valid outputs; same model across A/B/C |
| You.com | Abel, stretch after C3 | Find/read one authoritative equipment source, propose reviewed configuration update | Source/date/units -> reviewed assumption -> new config hash -> incompatible memories excluded |
| NemoClaw/OpenShell | Partner, stretch after C3 | Restrict agent tooling from directly editing approved memory, config and evaluator assets | Scripted prohibited write denied; valid proposal accepted via Keeper |

Time-box optional setup to 20 minutes initially. Do not call source retrieval calibration or assume a sandbox implements household authorization. Confirm OpenShell's chosen inference route works with Crusoe before claiming it. Never send private household information to web search.

Optional Note-writer extension: propose one counterexample within allowed development ranges. Code validates and simulates it, adding an observation under the tested conditions. It cannot alter held-out scenarios. This extension is first to cut if the core experiment is unfinished.

Provider capabilities and sources are listed in [the learning note](docs/llm-learnings-and-partner-tools.md). No integration is complete merely because it appears in the plan.

## 10. Failure handling and cuts

| Trigger | Joint decision |
| --- | --- |
| C1 late | Pair on the fixed-tariff path; cancel optional tools and visual polish |
| C2 late | Reduce to six households and a smaller price lattice; preserve household diversity and fixed checks |
| C3 late | Keep supported structured observations only; drop advanced conflict merging and counterexamples |
| C4 late | Reduce held-out sample equally across modes; preserve baselines and disclose sample size |
| Memory shows no benefit | Report it; inspect development behavior only; do not retune held-out cases |
| Search dominates | State that result; emphasize observed memory/governance findings without claiming planning superiority |
| Live inference fails | Label recorded real calls and mock plumbing distinctly; do not present mock quality as measured model performance |

Statuses: PLAN_VERIFIED, EXECUTION_COMPLETE with explicit outcome, NO_VERIFIED_PLAN, and INFEASIBLE only when a deterministic bound establishes it. A plan verified under forecast conditions may still fail actual simulated execution; report both phases.

## 11. Submission and demo

Use [submission.md](docs/submission.md). The main feature is the agent-memory experiment, not a dashboard. Preserve the event-start commit and identify exactly what was built during the event. The original Word document is background and requires the scope changes listed in README; do not use its broad claims verbatim.

Abel explains physical checks, baselines, results and limitations. Partner runs the demonstration and explains proposals, shared evidence and handoff. Rehearse both speaking parts together. Check the actual submission form for video requirements and timing; do not assume every earlier planning note is an organizer requirement.
