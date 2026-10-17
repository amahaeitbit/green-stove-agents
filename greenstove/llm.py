"""Model adapter: mock or live Crusoe Managed Inference (OpenAI-compatible).

Every HTTP request (including schema repairs and transient retries) is one CallRecord.
Credentials are never logged. Mock output validates plumbing, not model quality.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from greenstove.agent_models import CallRecord, stable_hash

ROOT = Path(__file__).resolve().parents[1]
MockHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ModelError(RuntimeError):
    """Network, HTTP, budget or configuration failure (never physical infeasibility)."""


class BudgetExceeded(ModelError):
    pass


@dataclass
class LLMSettings:
    mode: str = "mock"
    model: str = "nvidia/Nemotron-3-Super-120B-A12B"
    base_url: str = "https://api.inference.crusoecloud.com/v1"
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_s: float = 60.0
    max_calls: int = 8
    max_tokens_budget: int = 30000
    max_repairs: int = 1
    live_spend_ok: bool = False
    api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, mode: str | None = None) -> "LLMSettings":
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        mode = mode or env("GREENSTOVE_LLM", "mock")
        return cls(
            mode="crusoe" if mode == "live" else mode,
            model=env("GREENSTOVE_MODEL", cls.model),
            base_url=env("CRUSOE_BASE_URL", cls.base_url).rstrip("/"),
            temperature=float(env("GREENSTOVE_TEMPERATURE", "0.2")),
            max_tokens=int(env("GREENSTOVE_MAX_TOKENS", "1024")),
            timeout_s=float(env("GREENSTOVE_TIMEOUT_S", "60")),
            max_calls=int(env("GREENSTOVE_MAX_CALLS_PER_RUN", "8")),
            max_tokens_budget=int(env("GREENSTOVE_MAX_TOKENS_PER_RUN", "30000")),
            live_spend_ok=env("GREENSTOVE_LIVE_SPEND_OK", "0") == "1",
            api_key=env("CRUSOE_API_KEY", "").strip(),
        )

    def public(self) -> dict[str, Any]:
        return {"model": self.model if self.mode != "mock" else "mock-rules",
                "temperature": self.temperature, "max_tokens": self.max_tokens}


def extract_json(text: str) -> dict[str, Any]:
    """Strip <think> blocks and code fences, then parse the first balanced JSON object."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in model output")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON object in model output")


class LLMClient:
    def __init__(self, settings: LLMSettings, mock_handlers: dict[str, MockHandler] | None = None,
                 usage_path: Path | None = None):
        self.s = settings
        self.mock_handlers = mock_handlers or {}
        self.calls: list[CallRecord] = []
        self.usage_path = usage_path                 # append-only usage.jsonl: no keys, prompt hash only
        if settings.mode == "crusoe":
            if not settings.api_key:
                raise ModelError("CRUSOE_API_KEY is not set (run scripts/set_crusoe_key.py)")
            if not settings.live_spend_ok:
                raise ModelError("live calls need GREENSTOVE_LIVE_SPEND_OK=1 (a key alone never spends)")
        elif settings.mode != "mock":
            raise ModelError(f"unknown GREENSTOVE_LLM mode {settings.mode!r}")

    # ---- budgets ---------------------------------------------------------------------
    def reset_budget(self) -> None:
        self.calls = []

    def _tokens_used(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls)

    def _check_budget(self) -> None:
        if sum(c.outcome != "budget" for c in self.calls) >= self.s.max_calls:
            raise BudgetExceeded(f"call budget {self.s.max_calls} reached")
        if self._tokens_used() >= self.s.max_tokens_budget:
            raise BudgetExceeded(f"token budget {self.s.max_tokens_budget} reached")

    # ---- public API ------------------------------------------------------------------
    def complete_json(self, role: str, system: str, payload: dict[str, Any], schema: type[BaseModel],
                      memory_hash: str = "", attempt: int = 1) -> tuple[BaseModel | None, list[CallRecord], str | None]:
        """Returns (validated object or None, call records for this request, last error)."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}]
        records: list[CallRecord] = []
        error: str | None = None
        for repair in range(self.s.max_repairs + 1):
            try:
                self._check_budget()
            except BudgetExceeded as exc:
                rec = self._record(role, messages, memory_hash, attempt, "budget", error=str(exc))
                records.append(rec)
                raise
            text, rec = self._send(role, messages, payload, memory_hash, attempt)
            records.append(rec)
            try:
                obj = schema.model_validate(extract_json(text))
                rec.outcome = "repaired" if repair else "ok"
                self._log(rec)
                return obj, records, None
            except (ValueError, ValidationError) as exc:
                error = f"invalid output: {str(exc)[:300]}"
                rec.outcome, rec.error = "invalid", error
                self._log(rec)
                messages = messages + [
                    {"role": "assistant", "content": (text or "")[:2000]},
                    {"role": "user", "content": "Your reply was not valid for the schema. Error: "
                     f"{error}. Reply with ONLY the corrected JSON object."}]
        return None, records, error

    def list_models(self) -> list[dict[str, Any]]:
        if self.s.mode != "crusoe":
            return [{"id": "mock-rules"}]
        with httpx.Client(timeout=self.s.timeout_s) as client:
            r = client.get(f"{self.s.base_url}/models", headers=self._headers())
            r.raise_for_status()
            return r.json().get("data", [])

    # ---- transport -------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.s.api_key}", "Content-Type": "application/json"}

    def _log(self, rec: CallRecord) -> None:
        if self.usage_path is not None:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_path.open("a") as f:
                f.write(rec.model_dump_json() + "\n")

    def _record(self, role, messages, memory_hash, attempt, outcome, defer: bool = False, **kw) -> CallRecord:
        rec = CallRecord(role=role, llm_mode=self.s.mode, model=self.s.public()["model"],
                         prompt_hash=stable_hash(messages), memory_hash=memory_hash,
                         settings=self.s.public(), attempt=attempt, outcome=outcome, **kw)
        self.calls.append(rec)
        if not defer:
            self._log(rec)
        return rec

    def _send(self, role, messages, payload, memory_hash, attempt) -> tuple[str, CallRecord]:
        t0 = time.monotonic()
        if self.s.mode == "mock":
            handler = self.mock_handlers.get(role)
            if handler is None:
                raise ModelError(f"no mock handler for role {role!r}")
            text = json.dumps(handler(payload))
            prompt_chars = sum(len(m["content"]) for m in messages)
            return text, self._record(role, messages, memory_hash, attempt, "ok", defer=True,
                                      prompt_tokens=prompt_chars // 4, completion_tokens=len(text) // 4,
                                      latency_ms=int((time.monotonic() - t0) * 1000))
        body = {"model": self.s.model, "messages": messages, "temperature": self.s.temperature,
                "max_tokens": self.s.max_tokens, "chat_template_kwargs": {"enable_thinking": False}}
        delay = 1.0
        for transient_try in range(4):
            try:
                with httpx.Client(timeout=self.s.timeout_s) as client:
                    r = client.post(f"{self.s.base_url}/chat/completions", headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                self._record(role, messages, memory_hash, attempt, "error", error=f"network: {exc.__class__.__name__}",
                             latency_ms=int((time.monotonic() - t0) * 1000))
                raise ModelError(f"network error: {exc.__class__.__name__}") from None
            if r.status_code in (429, 503) and transient_try < 3:
                self._record(role, messages, memory_hash, attempt, "error", error=f"http {r.status_code} (retrying)",
                             latency_ms=int((time.monotonic() - t0) * 1000))
                self._check_budget()
                time.sleep(min(30.0, delay) + random.uniform(0, 0.5))
                delay *= 2
                continue
            break
        latency = int((time.monotonic() - t0) * 1000)
        if r.status_code != 200:
            self._record(role, messages, memory_hash, attempt, "error", error=f"http {r.status_code}", latency_ms=latency)
            raise ModelError(f"Crusoe returned HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        usage = data.get("usage") or {}
        rec = self._record(role, messages, memory_hash, attempt, "ok", defer=True, request_id=data.get("id"),
                           prompt_tokens=int(usage.get("prompt_tokens", 0)),
                           completion_tokens=int(usage.get("completion_tokens", 0)), latency_ms=latency)
        if not text and choice.get("finish_reason") == "length":
            rec.error = "empty content: hit max_tokens (raise GREENSTOVE_MAX_TOKENS)"
        return text, rec
