#!/usr/bin/env bash
# One-command setup for Green-Stove Agents (macOS/Linux). Safe to rerun.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --quiet --python .venv/bin/python -e ".[dev]"
[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example (mock mode)"; }

echo "== tests";      .venv/bin/python -m pytest -q
echo "== mock smoke"; .venv/bin/python -m greenstove smoke --mode mock
echo "== preflight";  .venv/bin/python -m greenstove preflight || true

cat <<'NEXT'

Setup done. Next:
  live model : .venv/bin/python scripts/set_crusoe_key.py --enable-live
               .venv/bin/python -m greenstove preflight --mode crusoe
  end to end : .venv/bin/python -m greenstove e2e [--mode crusoe]
  demo       : .venv/bin/python -m greenstove demo --pause [--mode crusoe]
NEXT
