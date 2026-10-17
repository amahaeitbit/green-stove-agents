# Submission and demonstration plan

Scope and evidence authority: [PLAN.md](../PLAN.md) and [evaluation protocol](simulator-and-evaluation.md). This is draft wording, not an implementation report. Replace planned integrations and results with actual evidence before submission.

## Short description

Green-Stove Agents studies shared memory for cooking coordination at a simulated solar station. Household software agents express different power needs, budgets and acceptable times. A grid agent proposes bounded prices; code checks reservations against power, battery and service constraints. A Keeper admits scoped observations backed by recorded simulations. We compare fresh agents using curated memory with raw history, no memory, tariff reuse and deterministic search on held-out scenarios. Existing Green-Stove hardware is prior work; the coordination, memory and evaluation code are the hackathon contribution.

Do not add claimed improvements until measured. Name only model routes and partner integrations that actually ran. Do not describe simulated results as deployed autonomous operation.

## Three-minute demonstration

| Time | Show | Speaker |
| --- | --- | --- |
| 0:00-0:25 | Three different household requests, fixed station resources, and the coordination objective | Abel |
| 0:25-0:55 | One real proposal, rejected condition, accepted alternative and capacity-backed reservations | Partner |
| 0:55-1:25 | Evidence-backed observation admitted; unsupported observation rejected; entry scope visible | Partner |
| 1:25-1:55 | Fresh agent on a different development/demo scenario, supplied brief, proposal and resulting world trace | Partner |
| 1:55-2:30 | Full held-out table, including nearest reuse and search, service and planning effort | Abel |
| 2:30-2:45 | Deterministic infeasibility calculation and honest stop status | Abel |
| 2:45-3:00 | Actual partner tools, today's new work and simulator limits | Both |

Choose an illustrative trace from measured development/demo runs after behavior is known. A recorded trace is acceptable only when clearly labeled. Do not select evaluation cases and then use them to tune prompts. Never script fake improvement, fabricate usage, or label mock output live.

A small replay may animate household bookings, requested load and reserve. The replay explains agent behavior; it is not the main product or evidence of aggregate benefit.

## What the table must answer

- Does curated memory improve the LLM workflow compared with facts and history?
- Does it help beyond last-tariff and nearest-context reuse?
- Is it preferable to a small deterministic search, accounting for simulation checks, time and cost?
- Does it preserve household service and every fixed constraint?
- Are stale/unsupported observations actually excluded?

If results are mixed, say which methods win which metrics. A truthful negative finding is preferable to a broad unsupported claim.

## Optional short video

If the actual organizer form requires a one-minute video, use: 0-10 seconds problem and new-work scope; 10-25 proposal/reservations/verdict; 25-40 admitted observation and fresh-agent handoff; 40-55 results table; 55-60 tools and limits. Confirm organizer requirements instead of treating an old planning note as authoritative.

## Submission checklist

- Public repository and all teammates included as required by organizer.
- Actual submission time and venue timezone confirmed; submit before deadline with buffer.
- Event-start commit and list of new features; prior hardware identified.
- Runtime instructions work from a clean checkout; env example contains no secrets.
- Results link to raw records, frozen manifest and exact code/model/snapshot versions.
- Live, recorded-live and mock output are labeled.
- Word document remains background; broad SMS/planting/federation/local-inference claims are not copied into MVP claims.
- Demo rehearsed under three minutes, roles assigned, recorded fallback available.

## Likely judge questions and evidence

| Question | Show |
| --- | --- |
| Why not cache a tariff? | Last and nearest reuse rows with same development evidence |
| Why an LLM for three prices? | Deterministic-search budget-3/full rows; honest cost/quality comparison |
| What makes it shared? | Evidence written after one role's work, Keeper decision, a fresh role/agent reading the resulting snapshot |
| How do you know memory is true? | Reconstructed structured fields and evidence IDs; explain the limits of generalization |
| Did memory cause the gain? | Frozen A/B/C comparison with same current facts and model/settings; sampling limits |
| Does this run a real grid? | No; synthetic simulator, assumptions and prior hardware clearly separated |
