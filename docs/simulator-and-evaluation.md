# Simulator and evaluation

All numbers below are placeholders listed in [assumptions.md](assumptions.md) and live in
config files.

## Grid simulator

- **Time:** 96 steps of 15 minutes, 00:00–24:00, village time zone. Battery state carries
  over between consecutive days in multi-day runs.
- **Per station, per step:**
  - `pv_kw = pv_kwp × derate × clear_sky(t) × cloud(t)`
  - `load_kw = Σ active stoves × stove_kw × jitter + clinic loads not shed`
  - If `load_kw > inverter_kw`, the inverter trips for the step: every running cooking
    session is interrupted (finished on wood) and the clinic is dark for that step.
  - Battery: charge or discharge the net with round-trip efficiency; SoC bounded
    `[0, 1]`. At `hw_lvd_soc` the hardware disconnects every load, clinic included, until
    SoC recovers by 5 points.
  - Approved genset hours add up to `genset_kw`; litres burned are counted.
  - Clinic tiers: tier 3 is shed automatically below the cooking reserve; tier 2 only with
    an approved `clinic_shed`; tier 1 is never shed by software.
- **Cooking session:** `session_minutes` at `stove_kw` (electric pressure cooker). A
  session needs all its steps powered; an interruption means the meal is finished on wood.

## Weather

Day classes: `clear`, `partly_cloudy`, `overcast`, `rainy_afternoon` (clear morning,
clouds after about 13:00, the rainy-season pattern). Each day is generated from a seed:
hourly cloud factors from an AR(1) process around the class mean. The **forecast** is the
actual day smoothed plus a biased error; the **downside scenario** is the forecast times
`downside_factor`. Seeds 0–99 are development days (used for building, debugging and
federation warm-up). Seeds 1000–1019 are unseen evaluation days and are not run during
development.

## Households and demand response

Synthetic households (seeded) with a language, a priority flag, two electric meals a day
(lunch, dinner), a preferred start, a window (dinner can move earlier because the pressure
cooker keeps food warm), a delay cost per hour, a price sensitivity, a patience in minutes
and a compliance probability. Under a tariff each household requests the slot that
minimizes `price × meal_kwh × sensitivity + delay_cost × |shift|`. Compliant households
cook in their granted slot; the rest arrive at their own requested slot as walk-ins.

## Policies compared

| Policy | Tariff | Safety limits | Verification | Replanning |
| --- | --- | --- | --- | --- |
| `fcfs` | Flat standard price | Nameplate cap `floor(inverter_kw / stove_kw)`, hardware LVD only | none | none |
| `llm_oneshot` | Model proposes tariff **and** cap and reserve, once | as proposed | none | none |
| `agents_no_verifier` | Model tariff, once | code-derived limits | none | none |
| `agents` | Model tariff, loop | code-derived limits | Verifier + Supervisor | hourly drift replans |

`agents_no_verifier` separates what code-derived safety limits buy from what the
verify-and-relax loop buys. A `--no-federation` flag runs `agents` without lessons.

## Metrics (per day; report mean and worst day, overall and per weather class)

| Metric | Definition |
| --- | --- |
| Wood meals per day | Meals not cooked electric: deferred, abandoned after patience, or interrupted |
| Collection hours avoided | `(all-wood meals − wood meals) × collection_hours_per_wood_meal` |
| Max meal delay | Largest `granted − requested` start among electric meals, minutes |
| Household spend | Mean and max per household per day, after lifeline pricing |
| Revenue vs. target | Day revenue ÷ `revenue_target_per_day` |
| Station overload minutes | Sum over stations of minutes with a trip |
| Clinic outage hours | Tier-1 unpowered time; tier-2 reported separately |
| Trees verified | Trees with two verifications posted in the ledger |
| Token cost per day | Tokens in and out per village-day, and USD when prices are set |
| Also reported | Model calls per day, attempts to a verified plan, relaxations, human pings, genset litres, SMS blocked by the checker |

## Protocol

1. Same households, same unseen days and same random draws for every policy.
2. Mock mode for all 20 days; live Nemotron for 3 of them (milestone 9). Results say which.
3. Success for the project: on unseen days, `agents` has zero overload minutes and zero
   tier-1 outage on every day, fewer wood meals than `fcfs`, and never reports `DONE` on a
   day where a condition failed in execution. Anything short of that is reported as it is.

## Threats to validity

Synthetic households and weather; no calibration against pilot data unless it is supplied;
mock mode replaces the model with rules; SMS compliance is a parameter, not a measurement;
the pilot config (6 households, 1.8 kW) is the only link to real operation.
