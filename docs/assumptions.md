# Assumptions and evidence limits

The current MVP is defined by [PLAN.md](../PLAN.md). Parameters are synthetic until supported by reviewed sources or pilot records. This document does not certify physical deployment suitability.

## Decisions to freeze at C0 and C4

| Item | MVP decision | Evidence or validation needed |
| --- | --- | --- |
| Station scale | One station; six to ten synthetic households | Choose internally consistent power/energy limits; hand-calculate feasible fixtures |
| Time | 96 fifteen-minute intervals | Explicit interval units and billing rounding |
| PV | Seeded forecast, downside and actual profiles | Synthetic unless calibrated with records; actual excluded from planning |
| Battery | Capacity, initial energy, power limits, efficiencies, reserve in config | Hand-calculated conservation/power-limit checks; equipment evidence if available |
| Essential load | One fixed protected load | Synthetic specification, no clinical-outcome claim |
| Households | Different powers, durations, windows, budgets, shift penalties, authority | Synthetic behavior; no claim about actual willingness to shift |
| Tariffs | Fixed windows, three lattice prices, fixed bounds | One small shared action space for agents and search; no personalized willingness-to-pay pricing |
| Service floor | Fixed required completed meals/service and inconvenience tolerance | Agree on development requirements before evaluation; no relaxation |
| Initial state | Reset each evaluation day to manifest state | No hidden carryover or evaluation learning |
| Memory | Up to 20 scoped observations, initial 600-token brief | Freeze simulated clock/expiry semantics, config hash, caps and selection |
| Model | Available Crusoe catalog ID, preferably Nemotron | Confirm route, supported parameters, latency and usage cost |
| Controller inference | Outside MVP | Unknown hardware requirements; open weights alone do not establish fit |

Record actual numeric values in configs and the evaluation manifest, rather than copying large-village placeholders from older plans. Expiry must use one explicit clock so replay does not make memories accidentally stale or immortal.

## Background claims needing independent evidence

The original Word document and earlier README mention roughly 140 million people without electricity, percentages of biomass dependence, stove efficiencies, and a 1.8 kW pilot serving six households. Do not use those as established findings without source/year/geography or pilot records. No such claims are validated by this planning update.

A public equipment source supports an input assumption; it does not validate household behavior or calibrate the entire simulator. Search output and webpages are untrusted inputs until reviewed. Existing hardware/pilot work must be separated from new hackathon code.

## Interpretation limits

- A simulated meal completed is not measured firewood displacement, collection time avoided, carbon reduction, or vaccine preservation.
- A forecast-verified plan may fail actual simulated execution; report both.
- Three unsuccessful candidates do not prove infeasibility.
- Matching evidence values validates a structured observation, not an arbitrary causal rule.
- A small single-run test is descriptive. Low temperature and caching do not establish independent repeated evidence.
- Memory construction and research costs must not disappear from net-efficiency claims.
- If nearest reuse or deterministic search dominates, state that result without claiming the LLM is necessary.

## Deferred deployment questions

Real equipment calibration, real user consent, communications delivery, payment, multilingual messaging, offline control, plant verification, federation and real-world operational approval are outside this MVP. They are not demo claims.
