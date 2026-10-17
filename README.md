# Green-Stove Agents

A shared memory experiment for household energy coordination at a simulated solar cooking station.

**Status: plans only until code and results are added.** No performance improvement or partner integration is established by this repository's design documents.

## What we are building

Six to ten synthetic household agents have different appliance powers, cooking durations, acceptable windows and budgets. A Pricing Agent proposes community-bounded prices. Deterministic household acceptance, reservations and simulation check whether a plan meets fixed requirements.

A shared brain stores scoped observations of successful and failed attempts. Fresh agents receive relevant observations rather than an ever-growing transcript. A code Keeper reconstructs approved facts from evidence and excludes unsupported or incompatible entries.

Primary hackathon prompt: **Shared Brain**. Fixed verification and honest stopping support Done Means Done. The project does not claim deployed autonomy or Version N+1 strategy evolution.

## The claim we will test

Does curated shared experience reduce planning effort at comparable service, relative to facts only, raw history, last-tariff reuse, nearest-context tariff reuse, and deterministic price search?

The baselines get the same current facts, household behavior, resource checks and action lattice. Evaluation memory is frozen. Count simulator evaluations, latency, cost, completed meals and violations, not just LLM calls. Report a negative result if reuse/search performs better.

## Two-person build

| Owner | Responsibility |
| --- | --- |
| Abel | Environment, household acceptance, allocation, simulator, verifier, baselines, evaluation and evidence |
| Partner | Crusoe/Nemotron adapter, agent loop, Note-writer, Keeper, briefs, audit and demo |
| Both | Shared contract, integration gates, experiment freeze, cross-review and submission |

Start with [PLAN.md](PLAN.md). It contains the ordered tasks, six-hour reference schedule, gates C0-C6 and cuts. Commands in the contracts are intended interfaces until implemented.

## Partner tools

- Crusoe runs the core agent inference and records fresh-agent handoffs.
- Nemotron is the preferred model family if available through the team's Crusoe account; confirm exact IDs and supported parameters.
- You.com is an optional evidence-to-configuration demonstration using one reviewed public equipment source.
- NemoClaw/OpenShell is an optional runtime boundary protecting approved memory and evaluator assets from direct agent writes.

Optional integrations do not precede the memory experiment. See [the learning note](docs/llm-learnings-and-partner-tools.md) for provider references and ideas, subject to the current PLAN.

## Existing work and scope

Green-Stove hardware and pilot concepts predate the agent build and must be identified as prior work. New hackathon contributions must be listed with commits when implemented. This prototype controls no live hardware and establishes no clinical, environmental or deployment outcomes.

The root Word document, Green-Stove Agents — Problem & Solution.docx, remains historical background. Its unattended-operation, local-inference, SMS, planting and federation claims are not the current MVP. Do not copy those claims into the submission. Relevant impact statistics and pilot performance require sources or records; see [assumptions.md](docs/assumptions.md).

## Documents

| Document | Purpose |
| --- | --- |
| [PLAN.md](PLAN.md) | Authoritative scope, ownership, ordered tasks and joint gates |
| [contracts.md](docs/contracts.md) | Units, typed interfaces, authority and fixtures |
| [checkpoints.md](docs/checkpoints.md) | Shared acceptance checklist, evidence and sign-offs |
| [architecture.md](docs/architecture.md) | Agent/code boundaries and memory flow |
| [simulator-and-evaluation.md](docs/simulator-and-evaluation.md) | Credible baselines, frozen protocol and claim rules |
| [assumptions.md](docs/assumptions.md) | Synthetic parameters and unvalidated claims |
| [submission.md](docs/submission.md) | Demo and submission wording |
| [llm-learnings-and-partner-tools.md](docs/llm-learnings-and-partner-tools.md) | Historical reasoning and proposed integrations |

## Built during the hackathon

To be filled only with implemented features, commits, reproducible commands and measured results. Mark live, recorded-live and mock output separately. No final results are available yet.
