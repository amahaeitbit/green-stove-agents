# Submission

## Short version (under 100 words)

Green-Stove Agents runs solar cooking mini-grids in the Horn of Africa with no one on
site, and every agent shares one brain. Before each call, Nemotron agents on Crusoe read
a brief of verified facts, playbooks and lessons; afterward a code Keeper admits only
notes backed by simulator evidence, resolves conflicts and keeps memory lean. From one
goal (families cook electric, no firewood, clinic never loses power) the team writes
checkable conditions, sets a bounded price, texts households and verifies every plan
before acting. Built on Green-Stove's 1.8 kW pilot; the agent layer and brain are new
today.

## One-minute video (built today only)

| Seconds | Shot |
| --- | --- |
| 0–8 | Goal line; "agent layer and Shared Brain built today; Green-Stove hardware is earlier work" |
| 8–20 | FCFS on a cloudy day: station trips, clinic fridge dark |
| 20–38 | Same day, brain off versus on: attempts, calls, tokens, status |
| 38–48 | Keeper admits one note with its verdict id, rejects one, supersedes a stale lesson |
| 48–60 | Results on unseen days and village B cold start; "live Nemotron on Crusoe" |

## Live demo outline (about 3 minutes, round 1 and final)

1. The problem in one slide: 18:00 on a cloudy evening, first-come-first-served, the
   station trips and the clinic fridge goes dark (simulator replay).
2. The goal and the definition of done the Spec Agent wrote, with what code added.
3. The pricing loop: attempt, verdict, revision, relaxation, "done with 1 relaxation".
4. SMS in local languages; one message blocked by the checker.
5. Replies parsed: `book 15:00`, `stove broken`, `PLANT 3`.
6. The day runs: afternoon drift, evening replan, one genset ping.
7. Results on unseen days against both baselines, mode labeled (mock or live Nemotron).

Fill in the numbers only from `docs/results.md` once milestones 7 and 8 have produced them.
