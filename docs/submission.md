# Submission

## Short version (under 100 words)

Green-Stove Agents operates solar cooking mini-grids in the Horn of Africa without a human
on site. From one goal (families cook electric, no firewood, clinic never loses power)
agents on Crusoe write checkable success conditions, set a bounded community price, text
households when cooking is cheapest, reward tree planting, and verify every plan in a grid
simulator before acting. A code supervisor catches loops and drift, relaxes constraints
only in the committee's order, and reports honest partial results. Built on Green-Stove's
pilot that cooks for six homes on 1.8 kW; the agent layer is new today.

## Demo outline (about 3 minutes)

1. The problem in one slide: 18:00 on a cloudy evening, first-come-first-served, the
   station trips and the clinic fridge goes dark (simulator replay).
2. The goal and the definition of done the Spec Agent wrote, with what code added.
3. The pricing loop: attempt, verdict, revision, relaxation, "done with 1 relaxation".
4. SMS in local languages; one message blocked by the checker.
5. Replies parsed: `book 15:00`, `stove broken`, `PLANT 3`.
6. The day runs: afternoon drift, evening replan, one genset ping.
7. Results on unseen days against both baselines, mode labeled (mock or live Nemotron).

Fill in the numbers only from `docs/results.md` once milestone 8 has produced it.
