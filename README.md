# Green-Stove Agents

Autonomous operator for solar cooking mini-grids — built on Crusoe for the Recursive Agents Hackathon.

Oct 17, 2026 · @isaac

> **Status: plans only.** This repository holds the design and build plan. No code has
> been written yet. Start with [PLAN.md](PLAN.md).
>
> **Hackathon entry:** Shared Brain problem statement, built on a Done Means Done loop.
> Recursive Agents Hackathon (Cerebral Valley × Crusoe), October 17, 2026.

## Problem

Across the Horn of Africa, roughly 140 million people have no electricity. In Eritrea,
Ethiopia, Sudan, and Uganda, about 90% of household energy comes from burning wood and
charcoal, rising to 97% in Southern Ethiopia. Traditional stoves convert only 30–40% of
that fuel into usable heat, so families burn more than they need, forests are cut faster
than they regrow, and the smoke stays indoors. The time cost lands on women and girls, who
spend hours each day collecting fuel instead of in school or at work.

Solar mini-grids with electric cooking can replace the firewood. Green-Stove's 1.8 kW pilot
already cooks for six households a day on one small station, but only because a person
manages who cooks when. At 100–200 households per village and dozens of villages, that
person does not exist. If everyone cooks at 18:00 on a cloudy evening, the station
overloads, the battery drains, and the clinic's vaccine fridge goes down. A dashboard does
not solve this; someone has to decide, every day, without being there.

## Solution

Green-Stove Agents is the "someone." An agent team running on open-weight models (Nemotron
via Crusoe Managed Inference; the same weights can run on the station controller) operates
the grid unattended.

1. **Writes its own definition of done.** From one standing goal (every family cooks
   electric, no firewood, clinic never loses power) a Spec Agent produces machine-checkable
   conditions: station limits, battery floor, clinic uptime, max meal delay, fairness,
   affordability, revenue target.
2. **Sets the day's community price.** A Pricing Agent proposes a three-tier tariff
   (Sun / Standard / Peak) inside bounds the village committee set: floor, ceiling, and a
   lifeline rate for priority households. Prices shape demand instead of a schedule
   dictating it.
3. **Proves the plan before acting.** An independent Verifier runs the plan through a grid
   simulator (demand response, per-station limits, battery, clinic load tiers) and grades
   it against the definition of done. The model never grades itself.
4. **Texts families when cooking is cheapest.** Feature-phone SMS in the local language;
   every message passes a deterministic checker so no promise goes out that the simulator
   has not confirmed. Replies ("book 15:00", "stove broken", "PLANT 3") are parsed back
   into the plan.
5. **Rewards planting.** Households earn cooking credit for trees planted and kept alive,
   verified by two community members, capped and audited in code. The grid replaces the
   wood; the bonus regrows it.
6. **Catches itself.** A code-level Supervisor detects loops and drift, relaxes constraints
   only in the committee's order (revenue gives before meals), and reports "done with N
   relaxations" or "not done", never a fake success. It pings a human only for money and
   safety: genset start, clinic load shedding, deferring a priority household.

## Shared Brain

Every agent on the team (Spec, Pricing, SMS, reply parser, Supervisor) reads a short brief
from one shared memory before it works: the village's facts, yesterday's state, the
pricing playbook for today's weather, verified lessons, SMS wording that passed the
checker, recent human decisions. Afterward, code writes what the run proved, and a Keeper
admits agent notes only when they match simulator or meter evidence. It resolves conflicts
by evidence and keeps each section capped and fresh. Stations and villages sync their
brains, so a new village starts from what others proved. We measure the brain on, off,
and as a naive history dump, on unseen days. See [PLAN.md](PLAN.md) sections 9 and 10.

## How it fits Green-Stove

The hardware and deployment concept are pre-existing Green-Stove work; the agent layer is
new this hackathon.

| Green-Stove hardware (pre-existing) | Agent layer (built this hackathon) |
| --- | --- |
| 15 kW solar stations, inverter, IoT controller | Station = feeder limit in the simulator; controller = where the Supervisor runs |
| Smart meters | Telemetry the Supervisor compares to the plan hourly |
| Prepaid / mobile payment | Credit ledger: tiers debit, planting credits post |
| App notifications | SMS first, app later |
| HQ + QR per station | Federation memory: lessons learned sync across stations and villages |

## What we measure

On unseen weather days, versus first-come-first-served and versus a one-shot LLM without
verification:

- Wood meals per day and collection hours avoided
- Max meal delay and household spend
- Revenue vs. target
- Station overload minutes and clinic outage hours
- Trees verified
- Token cost per day

Metric definitions and the evaluation protocol are in
[docs/simulator-and-evaluation.md](docs/simulator-and-evaluation.md).

## Why this matters for AI

Open-weight models making unattended decisions about critical infrastructure, within
human-set bounds, in places with no grid and intermittent internet, and proving their work
before anyone trusts it.

## Documents

| File | What it covers |
| --- | --- |
| [PLAN.md](PLAN.md) | Build plan for the coding agent: constraints, stack, repo layout, data model, component behavior, milestones with acceptance checks |
| [docs/architecture.md](docs/architecture.md) | Agent team, daily loop, what the model decides versus what code decides, where each part runs |
| [docs/simulator-and-evaluation.md](docs/simulator-and-evaluation.md) | Grid simulator model, weather and demand, baselines, metric definitions, evaluation protocol |
| [docs/assumptions.md](docs/assumptions.md) | Every placeholder number and every pitch claim that needs a source or pilot data before submission |
| [docs/submission.md](docs/submission.md) | Submission-form text and demo outline |

## Planned quick start

Not runnable yet. After milestone 0 (see [PLAN.md](PLAN.md)) it is intended to look like:

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env            # GREENSTOVE_LLM=mock needs no key
.venv/bin/python -m greenstove demo           # one cloudy day, narrated
.venv/bin/python -m greenstove eval --days 20 # agents vs baselines on unseen days
```
