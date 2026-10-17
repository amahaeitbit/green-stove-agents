# Simulator and evaluation protocol

Status: protocol to implement. [PLAN.md](../PLAN.md) fixes ownership and scope; [contracts.md](contracts.md) fixes interfaces. Abel owns scoring. Do not edit thresholds or test cases after observing held-out results.

## The question

Does a curated shared brain help a fresh agent reach an acceptable tariff with less total planning effort than raw history, tariff reuse, or deterministic search, at comparable service and unchanged constraints?

A positive result is not assumed. Distinguish memory benefit within the LLM system from evidence that LLM planning is preferable to deterministic methods.

## Simulator

Use one synthetic station and 96 fifteen-minute intervals. Include inverter limit, PV supply, fixed essential load, battery energy capacity, initial charge, charge/discharge power limits, separate efficiencies and reserve floor. Use per-household power and duration. Track deficits explicitly; clipping battery state is not a way to erase unserved energy. Invertibility, PV curtailment, and other approximations must be documented.

For each tariff, deterministic households rank acceptable cooking starts and the same deterministic allocator forms reservations. Quotes integrate consumption through tariff intervals. The verifier recomputes trace totals and fixed conditions. All conditions use identical physics and allocation.

Planning uses expected and downside forecasts. The planner never sees actual future PV. A chosen plan is then replayed on actual simulated weather, with essential-load protection and unserved cooking counted. Never call a forecast pass an actual execution pass. Independent scenarios reset initial battery/request state; multi-day state carryover is outside the headline experiment.

Test energy balance, efficiency, battery power and reserve, overloaded intervals, essential deficits, heterogeneous loads, duplicate bookings, acceptance, budgets, windows and quote arithmetic against hand-calculated fixtures.

## Development, memory and frozen evaluation

Development seeds: reserve a named range such as 0-99. Held-out seeds: a disjoint named range such as 1000 onward. At C0 define factors and their plausible synthetic ranges: weather class, initial battery, total demand and household flexibility. Hold out some combinations, not merely alternate seeds of the same exact day. Avoid adding tasks only to force the brain to win.

Development produces one common evidence library, including successful and failed proposals. Curation may select and summarize supported observations. The raw-history and curated modes have the same underlying source evidence and the same memory token allowance, initially 600 tokens. Current facts are identical. Fix raw-history truncation order on development data; do not deliberately fill it with irrelevant padding.

At C4 freeze evidence/history/brain snapshots, model/settings, prompts, seed manifest, configuration, factor ranges, candidate prices, baseline distance and order, all thresholds and scoring. Hash them. Evaluation outcomes cannot update the brain. Post-run Note-writer outputs, if collected, are quarantined and not served. Disable them uniformly or report their overhead separately.

## Six conditions

| ID | Method | Historical input | Candidate budget |
| --- | --- | --- | --- |
| A | Facts only LLM | None beyond current facts | At most 3 candidate attempts |
| B | Raw-history LLM | Bounded development history | Same as A |
| C | Curated-brain LLM | Scoped approved observations | Same as A |
| D | Last verified tariff | Most recent verified development tariff | 1 candidate; no LLM |
| E | Nearest-context reuse | Same successful development tariffs available to C | 1 candidate; no LLM |
| F | Deterministic tariff search | Same current inputs; fixed finite price lattice | Record first 3 candidates and full configured search |

For E, define fixed normalized distance using forecast energy/profile summaries, starting battery fraction, demand energy, peak requested power, and flexibility. Fix weights/scales before evaluation. Reject configuration-incompatible entries. Choose the nearest verified candidate with deterministic tie-breaking. If no compatible candidate exists or verification fails, record that failure without an unmatched fallback.

For F, use the same lattice available to LLM proposals, never a weaker action space. A small lattice, for example three allowed levels per tier before validity filtering, permits at most 27 triples. Validate committee bounds and tier ordering. Freeze the order using development data. Stop at the first acceptable candidate for the effort metric; a separate full-search outcome can report best service under a fixed selection rule. Report budget-3 and full-search results so differences in candidate budget remain visible. No gold actual weather is available to the search.

Prefer a feasibility-first, explicitly ordered candidate-selection rule shared with the agent loop. Freeze any tie-breaking between acceptable plans. Do not use an evaluator objective unavailable to another method.

## Resource accounting

Define one candidate evaluation as allocating a tariff and checking both forecast scenarios. Also count underlying simulator executions, actual-day replay, invalid proposals, model repairs, Note-writer calls, memory construction, tokens, latency and billed cost separately. Stop-on-first-success rules must be consistent and documented.

D/E/F use zero inference calls, so LLM call reduction is not a useful claim against them. Compare candidate/simulator evaluations, end-to-end time, inference plus compute cost where measured, and service. Report curation/warm-up overhead separately; no claim of net savings without accounting for it or explaining amortization.

A/B/C use identical model/settings, current facts, attempt budgets, feedback format and shared prompts except memory input. Any repair counts toward the call budget; invalid proposals consume an attempt. A fresh agent request starts each scenario. Fix a low supported temperature but do not claim determinism. Interleave modes or rotate their order to reduce timing bias.

Cache keys include full inputs, role, mode, model/settings, prompt hash, configuration and memory snapshot. Cached replay is not a new independent measurement. Never share one mode's generated answer with another.

## Metrics

Report counts and denominators alongside percentages. Suggested sample: 8-12 held-out days, sized from actual development latency and cost. Small single-run results are descriptive, not proof of statistical significance.

| Metric | Definition |
| --- | --- |
| Verified-plan success | Days where fixed requirements pass under both forecasts, divided by all days |
| First-candidate pass | First candidate passes; D/E have only one candidate |
| Calls/tokens/cost per day | All attempted days, including failures; separate one-time memory construction |
| Candidate/simulator evaluations | Planning effort, with search budget-3 and full-search distinguished |
| Meals completed | Actual execution-completed sessions over total requested sessions; also show worst day |
| Household service | Per-household completed/requested sessions; expose households with no service |
| Inconvenience | Absolute accepted-start minus preferred-start; report max and mean |
| Late finishes | Count and duration beyond specified limits; hard-window violations fail |
| Spend | Mean/max household spend and budget violations; no averaging away violations |
| Execution violations | Essential unserved energy/time, reserve breaches, inverter/power violations |
| Memory hygiene | Unsupported, stale, superseded or incompatible entries actually served; admission rejections separately |
| Repeated failed proposals | Repeated exact tariff hashes within a run; partial similarity is optional |

Record reason-coded unserved requests. Do not include only successful days in effort metrics. Disable relaxations in all headline conditions. Operational constraints include essential load, capacity, reserve, windows and budgets; service floor and inconvenience tolerance are fixed in the manifest using development requirements.

## Results table and interpretation

One row per A-F, with separate F budget-3/full rows. Columns: verified n/N; first-candidate n/N; calls; tokens; candidate checks; wall time; completed meals; worst-day service; maximum time shift; household spend; actual violations; memory hygiene. Attach per-day evidence so means cannot conceal a bad day.

A positive memory claim requires comparable predefined service and no weakened hard constraints, plus measured improvement in the named effort metric. State exactly which baselines C beats. If C beats A/B but not E/F, report benefit to the LLM workflow and the stronger deterministic baseline. If only costs improve, do not call it output-quality improvement. If no advantage appears, publish the negative result rather than reshaping held-out cases.

One chosen demo trace illustrates a mechanism; it does not establish average improvement. Model-cited memory IDs are not causal proof. The A/B/C treatment comparison supplies the evidence, with remaining sampling/nondeterminism limitations stated.

## Evidence package

Save manifest, implementation commit, synthetic configs, call records without secrets, memory/history snapshots, proposals, reservations, traces, verdicts, Keeper admission/rejection records, per-scenario metrics and aggregate table. Record mock, recorded-live, and live runs distinctly. Mock results validate software mechanics only.
