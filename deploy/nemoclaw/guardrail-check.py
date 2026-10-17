#!/usr/bin/env python3
"""Guardrail proof for the demo: is the OpenShell boundary actually active here?

Checks, from inside the process that runs the agents:
  1. egress to a host that is NOT on the allowlist (default https://example.com) is blocked
  2. writes to protected paths (frozen brain, evaluation fixtures, code/config) are blocked
  3. controls: data/ is still writable, and the allowlisted inference host is reachable (informational)

PASS only if 1 and 2 hold. Outside the sandbox this prints "guardrails NOT active" and exits 1:
it never claims protection it did not observe. Probe files that were written by mistake are removed.

  python deploy/nemoclaw/guardrail-check.py [--json] [--skip-allowed] [--denied-url URL]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_URL = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1").rstrip("/") + "/models"


def protected_paths() -> list[Path]:
    frozen = os.environ.get("GREENSTOVE_FROZEN_DIR", "frozen")
    out = [ROOT / frozen, ROOT / "fixtures", ROOT / "greenstove", ROOT / "config"]
    return [p if p.is_absolute() else ROOT / p for p in out]


def try_egress(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    """(reached, detail). A proxy denial (403/407 from the sandbox proxy) counts as not reached."""
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed (run setup first)"
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=False)
    except Exception as exc:  # noqa: BLE001 - any transport failure means the host was not reached
        return False, f"{exc.__class__.__name__}: {str(exc)[:100]}"
    if r.status_code in (403, 407) and "example.com" in url:
        return False, f"HTTP {r.status_code} (denied by proxy)"
    return True, f"HTTP {r.status_code}"


def try_write(directory: Path) -> tuple[bool, str]:
    """(written, detail). Creates the directory's probe file; removes it again if the write succeeded."""
    probe = directory / f".guardrail-probe-{uuid.uuid4().hex[:6]}"
    created = not directory.exists()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("guardrail probe: this write should have been denied\n")
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc.strerror or exc}"
    try:
        os.remove(probe)
        if created:
            os.rmdir(directory)                 # leave no trace of a probe that should have failed
    except OSError:
        pass
    return True, "write succeeded (probe removed)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--denied-url", default="https://example.com")
    p.add_argument("--skip-allowed", action="store_true", help="skip the allowlisted-host reachability control")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    rows = []
    reached, detail = try_egress(a.denied_url)
    rows.append({"check": f"egress {a.denied_url}", "expect": "blocked", "ok": not reached, "detail": detail})
    for d in protected_paths():
        written, detail = try_write(d)
        rows.append({"check": f"write {d.relative_to(ROOT)}/", "expect": "blocked", "ok": not written, "detail": detail})
    data = ROOT / os.environ.get("GREENSTOVE_DATA_DIR", "data")
    written, detail = try_write(data)
    controls = [{"check": f"write {data.relative_to(ROOT) if data.is_relative_to(ROOT) else data}/",
                 "expect": "allowed", "ok": written, "detail": detail}]
    if not a.skip_allowed:
        reached, detail = try_egress(ALLOWED_URL)
        controls.append({"check": f"egress {ALLOWED_URL}", "expect": "reachable (401 without key is fine)",
                         "ok": reached, "detail": detail})

    passed = all(r["ok"] for r in rows)
    sandbox_hint = any(os.environ.get(k) for k in ("OPENSHELL_SANDBOX", "NEMOCLAW_SANDBOX", "OPENSHELL"))
    verdict = ("PASS: guardrails active (denied egress and protected writes both blocked)" if passed else
               "FAIL: guardrails NOT active here" + (" (sandbox env detected, policy not enforced?)" if sandbox_hint
                                                   else " (this looks like a normal host, not the sandbox)"))
    if a.json:
        print(json.dumps({"passed": passed, "verdict": verdict, "checks": rows, "controls": controls}, indent=1))
    else:
        for r in rows + controls:
            mark = "ok  " if r["ok"] else "MISS"
            print(f"[{mark}] {r['check']:55} expect {r['expect']:36} -> {r['detail']}")
        print(verdict)
        if passed and not all(c["ok"] for c in controls):
            print("note: a control failed (data/ not writable or inference host unreachable); the agents need both")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
