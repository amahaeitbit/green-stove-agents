# LLM learnings: household coordination and partner tools

Date: October 17, 2026

Status: design proposals synthesized from Abel's ChatGPT discussion and the Claude refinement shared in that discussion. These are hypotheses and implementation recommendations, not measured results or completed integrations. This note supplements the existing plan; it does not silently replace it. The team should reconcile accepted decisions into PLAN.md before implementation.

> Current implementation authority: [PLAN.md](../PLAN.md), [contracts.md](contracts.md), and [evaluation protocol](simulator-and-evaluation.md). This note preserves earlier reasoning. Where it differs, the current plan wins: fixed windows and a finite price lattice, no generalized-rule promotion, six comparator conditions including nearest reuse and deterministic search, no required performance win, and frozen evaluation memory.

## The claim we want to test

> Household agents coordinate affordable cooking times with a village grid agent. A shared memory of simulation-backed observations helps fresh agents reach acceptable plans with fewer attempts, without weakening community constraints.

Describe the hackathon build as a **simulated operator**. Existing Green-Stove hardware and pilot work remain separate from the agent functionality built during the event.

Primary prompt: **Shared Brain**. Supporting mechanism: **Done Means Done**, through executable constraints, verified reservations, bounded negotiation, and honest stop states. Version N+1 is a later extension if strategy changes are evaluated on untouched tasks.

## What we learned about household agents

Different households need different power, durations, deadlines, and acceptable alternatives. Represent each household with:

- Appliance power in kW and session duration; derive energy in kWh.
- Preferred cooking time, earliest start, and latest finish.
- Maximum acceptable cost and willingness to shift.
- Explicit authority to auto-book within bounds, or a requirement to ask the household.
- Existing reservations and any configured community subsidy eligibility.

Private household preferences belong to the household agent. Share the operational constraints necessary to coordinate, rather than private explanations for them. For the MVP, use synthetic households only.

Start with one station and six to ten deterministic household agents. They communicate through structured messages and make autonomous choices within their configured preferences. They do not each require an LLM call. The grid Pricing Agent and Note-writer are the core LLM roles; a natural-language household representative is optional.

### Pricing and recommendations

The grid proposes a published time-of-use tariff within committee bounds, informed by solar availability, battery reserve, and competing requests. Equivalent service has the same published tariff; configured lifeline subsidies are explicit. Do not personalize prices based on inferred willingness to pay.

Illustrative numbers only:

| Window | Price per kWh | Cost for a 0.5 kWh meal |
| --- | ---: | ---: |
| 13:00-16:00 | 4 units | 2 units |
| 16:00-18:00 | 8 units | 4 units |
| 18:00-20:00 | 12 units | 6 units |

A household agent can recommend: "Your 18:30 session costs 6 units. A 15:30 session costs 2 units and fits your allowed window." It books automatically only when already authorized. Lock the quoted price when a reservation is accepted. Define whether pricing uses session-start time or energy consumed in each interval; use interval-based accounting consistently in the simulator and quotes.

Cheap-time recommendations alone can create a new peak. Use reservations:

1. Household agents send power, duration, acceptable windows, and budget.
2. The grid proposes tariff windows and prices.
3. Households request acceptable slots under that tariff.
4. A deterministic allocator checks interval capacity, battery energy, reserve, and fairness.
5. The grid confirms reservations or offers alternatives; households accept within their authority.
6. The verifier checks the accepted plan. Stop after a fixed number of rounds and report unserved requests.

Prices encourage shifting; reservations enforce capacity. Essential-load protections and physical limits remain code-enforced under every tariff.

## What the Shared Brain remembers

Keep three stores conceptually distinct:

| Store | Contents | Authority |
| --- | --- | --- |
| Household memory | Private preferences and booking authority | Household configuration |
| Coordination state | Requests, offers, accepted reservations | Deterministic transaction logic |
| Shared learned memory | Scoped observations from previous simulated runs | Evidence-checking Keeper |

The third store is the hackathon contribution. A booking database alone does not demonstrate learned shared memory.

For the MVP, use capped JSONL observations with a weather class, starting battery range, station/configuration hash, tariff parameters, outcomes, evidence run IDs, and expiry. Python reconstructs factual fields from the evidence. Agents propose entries but cannot directly write approved memory or committee limits.

A passing run supports an observation under its recorded conditions. It does not prove an arbitrary natural-language generalization. Defer generalized-rule promotion unless the rule has an executable interpretation and survives additional development scenarios. Keep conflicting observations scoped or unresolved; recency alone is not proof that one is better.

## How the hackathon partners improve this design

The capabilities below are documented by the providers. Their proposed uses and expected benefits in Green-Stove are design hypotheses until implemented and measured.

### Crusoe Managed Inference: the core agent loop

Run the grid Pricing Agent and Note-writer on Crusoe. Send structured household requests, current facts, and a bounded memory brief. Return tariff proposals, cited memory IDs, and proposed observations. Python owns allocation, validation, acceptance, and status.

Before choosing a model, inspect the organization's `/v1/models` catalog for exact identifiers, pricing, and supported parameters such as tools and structured output. Do not assume the model IDs in the original plan are available. Pin the selected model and settings throughout the memory experiment.

Record model ID, prompt hash, memory snapshot, calls, tokens, latency, retries, and verdict IDs. Set call and token limits in code; measure representative development calls before claiming the evaluation fits the $50 credit budget.

Demo evidence: a real Crusoe request produces a bounded tariff proposal, the verifier rejects or accepts it, and logs connect the result to its memory snapshot. Mock runs remain clearly labeled.

Source: [Crusoe model discovery and supported parameters](https://support.crusoecloud.com/hc/en-us/articles/55002524169499-How-To-List-Model-Features-via-curl-and-the-Inference-API).

### NVIDIA Nemotron: proposals and memory notes

Use an available Nemotron model through Crusoe for pricing reasoning and structured observation proposals. Household software agents handle routine preference scoring without additional inference. Start with one model for both LLM roles; only introduce model routing if measured latency or cost justifies it.

Nemotron is an open-model family designed for agentic workloads. Open weights do not establish that a chosen model fits an unspecified station controller. Local deployment remains future work until hardware and memory requirements are confirmed.

Demo evidence: show a tariff proposal, its cited memory entries, and a note rejected for unsupported claims. Measure valid structured outputs, calls, and latency. Do not change models between brain-off and brain-on runs.

Source: [NVIDIA Nemotron models](https://developer.nvidia.com/topics/ai/nemotron).

### NVIDIA NemoClaw and OpenShell: enforce agent boundaries

NemoClaw provides agent deployment blueprints incorporating OpenShell runtime controls. Proposed integration: run agent tooling inside a restricted environment while keeping committee configuration, evaluator fixtures, and approved-memory writes outside its write authority. Permit only the network destinations needed for chosen inference and research integrations, where supported by the configured runtime.

Household agents should submit requests through an interface, not edit other households' records or the approved brain. These ownership rules still require application-level checks; a sandbox alone does not implement them.

Time-box this stretch integration. Verify compatibility with the chosen Crusoe inference route before claiming it works. Do not replace the small harness with a new framework solely to add a sponsor logo.

Demo evidence: an attempted direct write to protected assets is denied, while an ordinary proposal passes through the authorized validator. Report the tested policy and its actual limits.

Sources: [NVIDIA NemoClaw overview](https://www.nvidia.com/en-us/ai/nemoclaw/), [NemoClaw repository](https://github.com/NVIDIA/NemoClaw).

### You.com: grounded assumptions and optional forecast research

Use the Search API to locate authoritative technical sources, and Contents extraction to read known source URLs. Feed the retrieved evidence into a Crusoe-hosted research step rather than outsourcing the core planning loop.

Useful research questions include appliance energy requirements, battery model assumptions, and public forecast sources for a specified pilot location. You.com is a search/retrieval service, not itself a guaranteed weather forecast provider. Record source URL, publication/access date, units, geography, and uncertainty. Require team review before changing simulator configuration from retrieved material.

Keep external-source evidence separate from simulation evidence. A source supports an assumption; a simulator tests consequences under that assumption. Web content cannot override committee limits or directly promote a memory entry. Household requests and private preferences should not be sent to web search.

For evaluation, freeze any retrieved inputs so every condition receives identical data. Live research can be a separate demo, but do not make benchmark outcomes depend on changing search results.

Demo evidence: a cited technical finding becomes a reviewed assumption, followed by a reproducible simulation. Basic RAG is not the project; retrieval supports the coordination and memory experiment.

Sources: [You.com API selection](https://you.com/docs/choose-the-right-api), [You.com agent skills](https://github.com/youdotcom-oss/agent-skills).

## One controlled experiment

Warm up memory on development seeds, then freeze it. Give the raw-history condition the same development runs used to construct curated observations. Evaluate fresh planner instances on unseen days with identical model settings, household preferences, simulator, random draws, tools, physical constraints, and attempt limits.

| Condition | Planner input |
| --- | --- |
| A. Brain off | Current facts and current household requests |
| B. History dump | Same inputs plus bounded raw development history |
| C. Curated brain | Same inputs plus Keeper-approved development observations |
| D. Reuse last verified tariff | Last verified development tariff, no LLM; same allocator and verifier |

Give B and C the same memory token allowance. Do not update memory from evaluation outcomes. Keep relaxations disabled for the headline comparison, or predefine identical relaxation rules and report every relaxation. If D fails verification, record failure rather than silently adding an unmatched fallback.

Report real measured values only: verified-plan success rate, first-candidate pass rate, mean calls/tokens across all days including failures, negotiation rounds, accepted bookings, electric meals completed, household spend, delay, relaxations, execution violations, and stale/unsupported entries served. Include mean and worst-day outcomes where meaningful. For D, calls/tokens are zero and the first candidate is its reused tariff.

Fewer calls is a benefit only if service quality and constraints hold. A simple deterministic scheduling heuristic is an additional baseline if time permits. Compare on development scenarios first, then freeze the test; do not manufacture difficulty until curated memory wins.

## Completion and honest stopping

- `PLAN_VERIFIED`: accepted reservations passed the specified forecast scenarios.
- `EXECUTION_COMPLETE`: simulated execution ended; attach the observed pass/fail outcome explicitly.
- `NO_VERIFIED_PLAN`: the attempt budget ended without an acceptable plan.
- `INFEASIBLE`: a deterministic bound proves requirements exceed available resources.

Three failed proposals do not prove infeasibility. Include an intentionally infeasible energy-shortfall example and show the calculation. A verified forecast plan is not a guarantee about every possible real-world condition.

## Build order and demo

1. One-station simulator, household request protocol, allocator, and invariant checks.
2. Crusoe/Nemotron pricing loop with bounded attempts and honest statuses.
3. Shared observations and deterministic Keeper; run the four-condition evaluation.
4. Add a small You.com evidence example if time permits.
5. Add OpenShell isolation only after the complete loop works.

Defer planting credits, payments, free-text SMS, multilingual generation, and federation. Split ownership between simulator/evaluation and agents/memory. This note does not authorize or implement real payments, messages, or hardware control.

Three-minute demo: introduce households with conflicting needs; show a cheaper acceptable offer and capacity-backed reservation; admit a supported observation and reject an unsupported one; show a fresh agent using the brain; display the measured comparison; finish with the infeasible case. Label recorded examples and live calls clearly. Do not promise that brain-on will win before measuring it.

## Continuing the LLM learning log

For each future suggestion, append: source (human or LLM), proposal, rationale, status (`proposed`, `accepted`, `implemented`, `measured`, or `rejected`), evidence links, and the team's decision. An LLM recommendation becomes a project fact only after implementation or evidence supports it. Never commit chat transcripts containing keys or private household data.

Open decisions: accept the reduced scope; select an available Crusoe/Nemotron model; define tariff accounting and acceptance authority; freeze scenario and evaluation rules; decide whether You.com and OpenShell fit the remaining time.
