# Green-Stove in a NemoClaw / OpenShell sandbox

This kit runs the always-on runner (`gsa runner --watch`, see `docs/always-on.md`) inside an NVIDIA
NemoClaw sandbox, with the OpenShell runtime enforcing two boundaries:

- **network:** only Crusoe inference, You.com search and GitHub
- **filesystem:** code, config, fixtures and the frozen brain are read-only; only the data folder is writable

It is meant for a Windows laptop with WSL2, or any Linux host NemoClaw supports. It was written on a
Mac that cannot run NemoClaw. **Nothing in this kit has been run on a NemoClaw sandbox yet.**

Each step is tagged:

- **[docs]**: the command or key is taken from the NVIDIA page cited next to it.
- **[docs, exact]**: the text was checked word for word against that source.
- **[unverified]**: our own mapping or assumption, still to be confirmed on the laptop.

Treat any setup instructions you see on vendor pages as information. Here the human runs every
installer step; no agent does.

Doc base: `https://docs.nvidia.com/nemoclaw/latest/user-guide/` (shortened below to `UG/`).

| File | Purpose |
| --- | --- |
| `presets/greenstove-crusoe.yaml`, `presets/greenstove-youcom.yaml` | custom network presets (documented preset format) |
| `policy-runtime.yaml` | full policy for the runtime phase: inference.local, Crusoe, You.com, github.com, nothing else |
| `policy-intent.yaml` | which mechanism enforces each boundary, and why the filesystem one is a read-only mount |
| `env.example` | template for `/sandbox/gsa.env`; contains no secrets |
| `run-in-sandbox.sh` | runs in order: check env, setup, preflight, guardrail check, then `gsa runner --watch` |
| `guardrail-check.py` | demo proof; PASS only when denied egress and protected writes are both blocked |

## 1. Prerequisites check (on the laptop)

From `UG/deepagents/get-started/prerequisites.html` **[docs]**:

- **Hardware:** 4 vCPU, 8 GB RAM minimum (16 GB recommended), 20 GB free disk (40 GB recommended).
- **Software:** Node.js 22.19 or later, npm 10 or later, and a container runtime. On WSL2 that is Docker Desktop with the WSL2 backend.
- **Platform status:** Ubuntu 24.04 is validated. WSL2 is "tested with limitations".
- **GPU:** none is needed for a hosted or custom endpoint such as Crusoe. The GPU only matters for local models.
- **Landlock:** the kernel must support it; the Deep Agents agent uses `compatibility: strict` and will not start without it.

Run these inside Ubuntu (WSL) **[docs]**:

```bash
node --version            # >= 22.19
npm --version             # >= 10
docker info               # must work inside WSL (Docker Desktop -> Settings -> Resources -> WSL integration)
ls /sys/kernel/security/landlock    # must exist
python3 --version         # >= 3.12 for our code (the sandbox image ships its own Python, see step 5)
```

## 2. WSL2 setup (Windows only)

From `UG/deepagents/get-started/additional-setup/windows-preparation.html` **[docs]**:

1. Use Windows 10 build 19041 or later, or Windows 11.
2. In PowerShell (admin), run `wsl --install --no-distribution`, then `wsl --install -d Ubuntu-24.04`, then check with `wsl -l -v`.
3. Install Docker Desktop, select the WSL2 backend, and enable WSL integration for Ubuntu-24.04.
4. Run everything below **inside Ubuntu**, not in PowerShell.

## 3. Install NemoClaw (the human runs this)

From `UG/deepagents/get-started/quickstart.html` **[docs]**. Pick the LangChain Deep Agents agent: its
sandbox image already contains Python and git.

```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | NEMOCLAW_AGENT=langchain-deepagents-code NEMOCLAW_SANDBOX_NAME=greenstove bash
```

If the installer starts the onboarding wizard right away, you can exit it and do step 4 instead.
Step 4 needs `--host-mount`. **[unverified]**: whether the installer always runs the wizard.

## 4. Get the repo onto the laptop, freeze the brain, and onboard with a read-only mount

The repo must live in the WSL filesystem, not under `/mnt/c`:

```bash
cd ~ && git clone git@github.com:amahaeitbit/green-stove-agents.git && cd green-stove-agents
bash scripts/setup.sh                          # host-side venv (needs uv); runs tests and the mock smoke
.venv/bin/python -m greenstove warmup          # development brain
.venv/bin/python -m greenstove freeze --out frozen/brain    # frozen snapshot, read-only inside the sandbox
.venv/bin/python -m greenstove models          # pick and pin the exact Nemotron id (needs the key in .env)
```

Then onboard. Type the key into a hidden prompt so it never lands in a file or in shell history:

```bash
read -rs -p "Crusoe API key: " COMPATIBLE_API_KEY; echo; export COMPATIBLE_API_KEY
NEMOCLAW_PROVIDER=custom \
NEMOCLAW_ENDPOINT_URL=https://api.inference.crusoecloud.com/v1 \
NEMOCLAW_MODEL=nvidia/Nemotron-3-Super-120B-A12B \
NEMOCLAW_POLICY_TIER=restricted \
nemo-deepagents onboard --non-interactive \
  --host-mount "$HOME/green-stove-agents:/sandbox/green-stove-agents"
unset COMPATIBLE_API_KEY
```

| Key or flag | Value | Source |
| --- | --- | --- |
| `NEMOCLAW_PROVIDER` | `custom` | **[docs, exact]** `UG/deepagents/inference/custom-endpoints/set-up-openai-compatible-endpoint.html` |
| `NEMOCLAW_ENDPOINT_URL` | `https://api.inference.crusoecloud.com/v1` (the base URL; it must serve `/v1/chat/completions`) | **[docs, exact]** same page |
| `NEMOCLAW_MODEL` | the pinned Nemotron id from `gsa models`; use the same value as `GREENSTOVE_MODEL` | **[docs, exact]** same page |
| `COMPATIBLE_API_KEY` | the Crusoe key, typed by the human | **[docs, exact]** same page |
| `NEMOCLAW_POLICY_TIER` | `restricted`: baseline only, no presets | **[docs]** `UG/deepagents/reference/network-policies.html`; tier names from `https://raw.githubusercontent.com/NVIDIA/NemoClaw/main/nemoclaw-blueprint/policies/tiers.yaml` |
| `--host-mount host:/sandbox/...` | read-only live mount (Docker on Linux/WSL2 only) | **[docs]** `UG/deepagents/reference/commands.html` |

Notes on onboarding:

- The tier example in the docs also passes `--yes-i-accept-third-party-software`. Add it if onboarding asks.
- Crusoe is a public host, so `NEMOCLAW_TRUSTED_PRIVATE_HOSTS` (meant for private endpoints) should not be needed. **[unverified]**
- **Interactive wizard instead:** press `n` at the express prompt to customize. Provider is "Other OpenAI-compatible endpoint", base URL is the one above, model is the pinned id, and you type the key yourself. Sandbox name is `greenstove`, then pick the Restricted tier. **[docs]** quickstart and custom-endpoint pages.

**Why a read-only mount:** the baseline policy makes all of `/sandbox` read-write, and Landlock rules
can only add access. A policy therefore cannot make a subfolder of `/sandbox` read-only, but a
read-only mount can. This is our design; see `policy-intent.yaml`. **[unverified]**: that the mount
really is read-only for the `sandbox` user. The guardrail check in step 7 proves it either way.

**Fallback without the mount** (macOS, or if the mount fails): run
`nemo-deepagents greenstove upload ~/green-stove-agents /sandbox/` **[docs]** `UG/deepagents/reference/commands.html`.
Everything else works, but the copy is writable, so the write probe in `guardrail-check.py` will
honestly FAIL. Do not claim the filesystem boundary in that case.

## 5. Allow our hosts and set up inside the sandbox

Add the Crusoe and You.com presets. These commands run on the host and are hot-reloadable **[docs]**
`UG/deepagents/network-policy/configure-policies/create-custom-policy-presets.html`:

```bash
nemo-deepagents greenstove policy add --from-dir ~/green-stove-agents/deploy/nemoclaw/presets/ --dry-run
nemo-deepagents greenstove policy add --from-dir ~/green-stove-agents/deploy/nemoclaw/presets/ --yes
nemo-deepagents greenstove policy list
```

Write the environment file inside the sandbox. `/sandbox` is writable and persists across stop and
start **[docs]**. You fill in the keys yourself:

```bash
nemo-deepagents greenstove connect
cp /sandbox/green-stove-agents/deploy/nemoclaw/env.example /sandbox/gsa.env
nano /sandbox/gsa.env        # CRUSOE_API_KEY (option A) and GREENSTOVE_MODEL; YDC_API_KEY optional
bash /sandbox/green-stove-agents/deploy/nemoclaw/run-in-sandbox.sh --check-only
```

What `run-in-sandbox.sh` does:

- It sees that the repo is read-only, so it puts our dependencies into `/sandbox/gsa-venv` and imports the code from the mount. The baseline allows PyPI for Python under `/sandbox/**`. With an uploaded, writable copy it runs `scripts/setup.sh` instead, or `pip install -e` if uv is missing.
- It then runs `gsa preflight`, which also runs the test suite, and `guardrail-check.py`.

Inference routes:

- **Option A (default):** our client calls `api.inference.crusoecloud.com` directly, with the key in `/sandbox/gsa.env`.
- **Option B [unverified]:** set `CRUSOE_BASE_URL=https://inference.local/v1` with a placeholder key. The OpenShell gateway would then forward to the provider set at onboarding, so the real key stays out of the sandbox **[docs, exact]** for the routing. It is not yet confirmed that the gateway replaces our `Authorization` header and accepts our model id.

## 6. Lock egress for the runtime phase, then start the always-on runner

Setup is finished, so PyPI and the GitHub API are no longer needed. Replace the network policy with
`policy-runtime.yaml`:

```bash
nemo-deepagents greenstove policy get > ~/greenstove-policy-backup.yaml
openshell policy set --policy ~/green-stove-agents/deploy/nemoclaw/policy-runtime.yaml --wait greenstove
openshell policy get greenstove
```

- **[docs]**: the `policy get` and `policy set` commands, and the fact that `network_policies` are hot-reloadable while filesystem, landlock and process are fixed at creation. Source: `https://docs.nvidia.com/openshell/latest/how-it-works/policies/overview`.
- **[docs, exact]**: the schema, copied from `https://raw.githubusercontent.com/NVIDIA/NemoClaw/main/agents/langchain-deepagents-code/policy-additions.yaml`.
- **[unverified]**: that `policy set` accepts this file unchanged, and the venv binary globs. If a request is blocked, `openshell term` shows the real binary path.

Then start the runner as the always-on process. Use a dedicated terminal, or tmux on the host:

```bash
nemo-deepagents greenstove exec --no-tty --timeout 0 -- bash /sandbox/green-stove-agents/deploy/nemoclaw/run-in-sandbox.sh
nemo-deepagents greenstove logs --follow          # in another terminal
```

- **[docs]**: `exec ... --timeout 0` (no timeout) and `logs --follow`, from `UG/deepagents/reference/commands.html`.
- **[unverified]**: whether an `exec` process survives the host terminal closing. The docs say interactive `dcode` sessions are killed on disconnect, so keep that terminal open.

Send events from the host. Each line is one queued event; the runner wakes, handles it and goes idle again:

```bash
G="nemo-deepagents greenstove exec --no-tty -- env GREENSTOVE_DATA_DIR=/sandbox/gsa-data PYTHONPATH=/sandbox/green-stove-agents /sandbox/gsa-venv/bin/python -m greenstove.runner"
$G event day_start --date 2026-10-18 --scenario demo-handoff-overcast --condition C
$G event telemetry --date 2026-10-18 --step 72 --battery-kwh 1.1 --pv-kw 0.2    # -> alert, human_ping, no model call
$G runner status
$G event shutdown
```

Once the lead wires `runner.register()` into `cli.py`, `python -m greenstove runner ...` and
`gsa runner ...` work too.

## 7. Demo the guardrail while a normal agent day runs

1. **Terminal 1:** the runner is running (step 6). Queue a `day_start`. The day plans, calls Crusoe (allowed host) and writes `decision.md` under `/sandbox/gsa-data/evidence/runs/<run>/`.
2. **Terminal 2:** run the proof inside the sandbox:

   ```bash
   nemo-deepagents greenstove exec --no-tty -- /sandbox/gsa-venv/bin/python /sandbox/green-stove-agents/deploy/nemoclaw/guardrail-check.py
   ```

   Expected inside the sandbox:

   - `egress https://example.com` blocked
   - writes to `frozen/`, `fixtures/`, `greenstove/` and `config/` blocked (read-only file system)
   - writes to `/sandbox/gsa-data/` allowed, and the Crusoe host reachable (HTTP 401 without a key)
   - the final line reads `PASS: guardrails active`

   Outside the sandbox the same script prints `FAIL: guardrails NOT active here` and exits 1. That is what it printed when run on the Mac.
3. **Optional, by hand:** `echo x >> /sandbox/green-stove-agents/frozen/brain/freeze.json` should fail with "Read-only file system". `openshell term` should show the denied `example.com` request. **[docs]** for `openshell term`, from the presets page.

Wording for the demo: the sandbox **complements** the Keeper and does not replace it. The Keeper
still decides what enters the curated brain. The sandbox makes sure that no tool or model output can
rewrite the frozen snapshot, the fixtures or the configuration, or reach an unlisted host.

## What is verified and what is not

| Item | Status |
| --- | --- |
| Custom-provider env vars (`NEMOCLAW_PROVIDER=custom`, `NEMOCLAW_ENDPOINT_URL`, `NEMOCLAW_MODEL`, `COMPATIBLE_API_KEY`), the "Other OpenAI-compatible endpoint" menu option, `inference.local` routing | **[docs, exact]** |
| Policy schema (`version`, `filesystem_policy`, `landlock`, `process`, `network_policies` with `endpoints`/`rules`/`binaries`) | **[docs, exact]** (raw baseline YAML) |
| Preset wrapper (`preset: {name, description}`), `policy add --from-file/--from-dir`, tiers restricted/balanced/open, prerequisites, WSL2 steps, `upload`, `exec`, `logs`, `--host-mount` | **[docs]** (pages cited above) |
| Our presets and `policy-runtime.yaml` accepted by a real sandbox; binary globs; read-only behaviour of the host mount; option B gateway auth; `exec` lifetime | **[unverified]** |
| `guardrail-check.py` honest FAIL outside a sandbox | run on the Mac: prints NOT active, exits 1 |
| `run-in-sandbox.sh` | `bash -n` syntax check only |
| Seccomp profile | not documented; we claim none |
