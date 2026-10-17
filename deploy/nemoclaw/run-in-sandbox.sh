#!/usr/bin/env bash
# Run Green-Stove's always-on runner inside a NemoClaw / OpenShell sandbox.
#   bash deploy/nemoclaw/run-in-sandbox.sh [ENV_FILE] [--check-only]
# Steps: load env -> check env -> setup -> preflight -> guardrail check -> gsa runner --watch.
# Works with the repo mounted read-only (onboard --host-mount; recommended) or uploaded (writable).
# Prints key lengths only, never key values. Safe to rerun.
set -euo pipefail

ENV_FILE=/sandbox/gsa.env
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=1 ;;
    *) ENV_FILE="$arg" ;;
  esac
done

say()  { printf '== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# ---- 1. environment --------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
  say "loaded $ENV_FILE"
else
  say "no $ENV_FILE (copy deploy/nemoclaw/env.example there); using defaults, mock mode"
fi
GSA_REPO="${GSA_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
GSA_VENV="${GSA_VENV:-/sandbox/gsa-venv}"
export GREENSTOVE_DATA_DIR="${GREENSTOVE_DATA_DIR:-/sandbox/gsa-data}"
export GREENSTOVE_LLM="${GREENSTOVE_LLM:-mock}"
export PYTHONDONTWRITEBYTECODE=1
INTERVAL="${GSA_RUNNER_INTERVAL:-30}"

say "checks"
[ -f "$GSA_REPO/pyproject.toml" ] || fail "repo not found at GSA_REPO=$GSA_REPO"
PY_BASE="$(command -v python3 || true)"
[ -n "$PY_BASE" ] || fail "python3 not found"
"$PY_BASE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || fail "python >= 3.12 required, found $("$PY_BASE" -V 2>&1)"
mkdir -p "$GREENSTOVE_DATA_DIR" 2>/dev/null || true
[ -w "$GREENSTOVE_DATA_DIR" ] || fail "data dir $GREENSTOVE_DATA_DIR is not writable"
if [ "$GREENSTOVE_LLM" = "crusoe" ] || [ "$GREENSTOVE_LLM" = "live" ]; then
  [ -n "${CRUSOE_API_KEY:-}" ] || fail "CRUSOE_API_KEY is empty (enter it in $ENV_FILE yourself)"
  [ "${GREENSTOVE_LIVE_SPEND_OK:-0}" = "1" ] || fail "GREENSTOVE_LIVE_SPEND_OK must be 1 for live calls"
  say "model: live ${GREENSTOVE_MODEL:-default} via ${CRUSOE_BASE_URL:-https://api.inference.crusoecloud.com/v1} (key ${#CRUSOE_API_KEY} chars)"
else
  say "model: MOCK (rule-based stand-in, no key, no network)"
fi
YDC="${YDC_API_KEY:-}"; say "YDC_API_KEY: ${#YDC} chars (empty = mock forecast search)"

# ---- 2. setup --------------------------------------------------------------------------
if touch "$GSA_REPO/.gsa-write-probe" 2>/dev/null; then
  "$PY_BASE" -c 'import os, sys; os.remove(sys.argv[1])' "$GSA_REPO/.gsa-write-probe"
  REPO_RO=0
else
  REPO_RO=1
fi

if [ "$REPO_RO" = "0" ]; then
  say "setup: repo is writable (uploaded copy) -> scripts/setup.sh"
  if command -v uv >/dev/null 2>&1; then
    (cd "$GSA_REPO" && bash scripts/setup.sh)
  else
    say "uv not in sandbox: venv + pip install -e instead of scripts/setup.sh"
    [ -x "$GSA_REPO/.venv/bin/python" ] || "$PY_BASE" -m venv "$GSA_REPO/.venv"
    "$GSA_REPO/.venv/bin/python" -m pip install --quiet -e "$GSA_REPO[dev]"
  fi
  PY="$GSA_REPO/.venv/bin/python"
else
  say "setup: repo is read-only (host mount) -> dependencies into $GSA_VENV, code from the mount"
  [ -x "$GSA_VENV/bin/python" ] || "$PY_BASE" -m venv "$GSA_VENV"
  PY="$GSA_VENV/bin/python"
  # dependencies straight from pyproject.toml (an editable install would write into the read-only repo)
  DEPS=()
  while IFS= read -r dep; do [ -n "$dep" ] && DEPS+=("$dep"); done < <("$PY" -c 'import sys, tomllib
p = tomllib.load(open(sys.argv[1], "rb"))["project"]
print("\n".join(p["dependencies"] + p.get("optional-dependencies", {}).get("dev", [])))' "$GSA_REPO/pyproject.toml")
  if ! "$PY" -c 'import pydantic, httpx, dotenv, rich, pytest' 2>/dev/null; then
    "$PY" -m pip install --quiet "${DEPS[@]}"
  fi
  export PYTHONPATH="$GSA_REPO${PYTHONPATH:+:$PYTHONPATH}"
fi
cd "$GSA_REPO"

# ---- 3. preflight (runs the test suite; prints no secrets) -------------------------------
say "preflight"
"$PY" -m greenstove preflight ${GREENSTOVE_LLM:+--mode "$GREENSTOVE_LLM"} || say "preflight reported FAIL rows (see table); continuing"

# ---- 4. guardrail proof ------------------------------------------------------------------
say "guardrail check (PASS only inside an enforcing sandbox)"
"$PY" deploy/nemoclaw/guardrail-check.py || say "guardrails NOT active: do not claim the sandbox boundary for this run"

[ "$CHECK_ONLY" = "1" ] && { say "check only: not starting the runner"; exit 0; }

# ---- 5. always-on runner -----------------------------------------------------------------
if "$PY" -m greenstove runner --help >/dev/null 2>&1; then
  RUNNER=("$PY" -m greenstove runner)
else
  RUNNER=("$PY" -m greenstove.runner runner)          # until cli.py wires runner.register()
fi
say "runner: ${RUNNER[*]} --watch --interval $INTERVAL (stop: gsa event shutdown, or Ctrl-C)"
exec "${RUNNER[@]}" --watch --interval "$INTERVAL"
