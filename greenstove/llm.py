"""Model adapter: mock or live Crusoe Managed Inference (OpenAI-compatible).

Crusoe features used: the /v1/models catalog (exact ids, supported_parameters, pricing),
per-role Nemotron routing, structured output (response_format) when the catalog lists it,
per-role thinking control (chat_template_kwargs.enable_thinking), prompt-cache accounting
(cached_tokens), reasoning-token accounting, rate-limit headers, and 429/503 backoff.
Every HTTP request is one CallRecord; credentials are never logged.
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
ROLES = ("pricing", "note_writer", "forecast_extractor", "playbook_reviewer", "spec_writer")


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
    role_models: dict[str, str] = field(default_factory=dict)      # role -> model id (same across A/B/C)
    thinking: dict[str, bool] = field(default_factory=dict)        # role -> enable_thinking
    structured: str = "auto"                                       # auto | json_schema | json_object | off
    price_in_per_mtok: float | None = None                         # USD overrides of catalog pricing
    price_out_per_mtok: float | None = None
    api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, mode: str | None = None) -> "LLMSettings":
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        mode = mode or env("GREENSTOVE_LLM", "mock")
        num = lambda k: float(env(k)) if env(k) else None  # noqa: E731
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
            role_models={r: env(f"GREENSTOVE_MODEL_{r.upper()}") for r in ROLES if env(f"GREENSTOVE_MODEL_{r.upper()}")},
            thinking={r: env(f"GREENSTOVE_THINKING_{r.upper()}") == "1" for r in ROLES},
            structured=env("GREENSTOVE_STRUCTURED", "auto"),
            price_in_per_mtok=num("GREENSTOVE_PRICE_IN_PER_MTOK"), price_out_per_mtok=num("GREENSTOVE_PRICE_OUT_PER_MTOK"),
            api_key=env("CRUSOE_API_KEY", "").strip(),
        )

    def model_for(self, role: str) -> str:
        return self.role_models.get(role, self.model)

    def public(self, role: str | None = None) -> dict[str, Any]:
        model = self.model_for(role) if role else self.model
        return {"model": model if self.mode != "mock" else "mock-rules", "temperature": self.temperature,
                "max_tokens": self.max_tokens, "thinking": bool(role and self.thinking.get(role)),
                "structured": self.structured}


def parse_pricing(entry: dict[str, Any]) -> tuple[float | None, float | None]:
    """USD per token (in, out) from a catalog entry; values above 0.01 are read as per 1M tokens."""
    p = entry.get("pricing") or {}
    for kin, kout in (("prompt", "completion"), ("input", "output"), ("input_cost_per_token", "output_cost_per_token")):
        if kin in p and kout in p:
            try:
                vin, vout = float(p[kin]), float(p[kout])
            except (TypeError, ValueError):
                return None, None
            scale = 1e-6 if max(vin, vout) > 0.01 else 1.0
            return vin * scale, vout * scale
    return None, None


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
                 usage_path: Path | None = None, transport: httpx.BaseTransport | None = None,
                 catalog: dict[str, dict[str, Any]] | None = None):
        self.s = settings
        self.mock_handlers = mock_handlers or {}
        self.calls: list[CallRecord] = []
        self.usage_path = usage_path                 # append-only usage.jsonl: no keys, prompt hash only
        self.transport = transport                   # tests inject httpx.MockTransport
        self.catalog = catalog                       # model id -> /v1/models entry (lazy in live mode)
        self._no_response_format: set[str] = set()   # models that rejected response_format
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
        """Returns (validated object or None, call records for this request, last error).

        The system prompt and the stable facts come first so repeated attempts share a
        byte-identical prefix (Crusoe prompt caching); feedback and repairs come last."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}]
        records: list[CallRecord] = []
        error: str | None = None
        for repair in range(self.s.max_repairs + 1):
            try:
                self._check_budget()
            except BudgetExceeded as exc:
                records.append(self._record(role, messages, memory_hash, attempt, "budget", error=str(exc)))
                raise
            text, rec = self._send(role, messages, payload, memory_hash, attempt, schema)
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
        with httpx.Client(timeout=self.s.timeout_s, transport=self.transport) as client:
            r = client.get(f"{self.s.base_url}/models", headers=self._headers())
        if r.status_code != 200:
            raise ModelError(f"/v1/models returned HTTP {r.status_code}")
        return r.json().get("data", [])

    def ensure_catalog(self) -> dict[str, dict[str, Any]]:
        if self.catalog is None:
            try:
                self.catalog = {m.get("id"): m for m in self.list_models()}
            except (ModelError, httpx.HTTPError, ValueError):
                self.catalog = {}                    # features fall back to safe defaults
        return self.catalog

    def price_per_token(self, model: str) -> tuple[float | None, float | None]:
        if self.s.price_in_per_mtok is not None and self.s.price_out_per_mtok is not None:
            return self.s.price_in_per_mtok / 1e6, self.s.price_out_per_mtok / 1e6
        return parse_pricing((self.catalog or {}).get(model, {}))

    # ---- transport -------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.s.api_key}", "Content-Type": "application/json"}

    def _log(self, rec: CallRecord) -> None:
        if self.usage_path is not None:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_path.open("a") as f:
                f.write(rec.model_dump_json() + "\n")

    def _record(self, role, messages, memory_hash, attempt, outcome, defer: bool = False, **kw) -> CallRecord:
        pub = self.s.public(role)
        rec = CallRecord(role=role, llm_mode=self.s.mode, model=pub["model"], prompt_hash=stable_hash(messages),
                         memory_hash=memory_hash, settings=pub, attempt=attempt, outcome=outcome, **kw)
        self.calls.append(rec)
        if not defer:
            self._log(rec)
        return rec

    def _response_format(self, model: str, role: str, schema: type[BaseModel]) -> dict[str, Any] | None:
        mode = self.s.structured
        if mode == "off" or model in self._no_response_format:
            return None
        supported = set((self.ensure_catalog().get(model) or {}).get("supported_parameters") or [])
        if mode == "json_schema" or (mode == "auto" and "structured_outputs" in supported):
            return {"type": "json_schema", "json_schema": {"name": role, "schema": schema.model_json_schema()}}
        if mode == "json_object" or (mode == "auto" and "response_format" in supported):
            return {"type": "json_object"}
        return None

    def _send(self, role, messages, payload, memory_hash, attempt, schema) -> tuple[str, CallRecord]:
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
        model = self.s.model_for(role)
        thinking = bool(self.s.thinking.get(role))
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": self.s.temperature,
                                "max_tokens": max(self.s.max_tokens, 2048) if thinking else self.s.max_tokens,
                                "chat_template_kwargs": {"enable_thinking": thinking}}
        rf = self._response_format(model, role, schema)
        if rf:
            body["response_format"] = rf
        delay = 1.0
        for transient_try in range(5):
            try:
                with httpx.Client(timeout=self.s.timeout_s, transport=self.transport) as client:
                    r = client.post(f"{self.s.base_url}/chat/completions", headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                self._record(role, messages, memory_hash, attempt, "error", error=f"network: {exc.__class__.__name__}",
                             latency_ms=int((time.monotonic() - t0) * 1000))
                raise ModelError(f"network error: {exc.__class__.__name__}") from None
            if r.status_code in (400, 403, 422) and "response_format" in body:
                self._record(role, messages, memory_hash, attempt, "error", response_format=body["response_format"]["type"],
                             error=f"http {r.status_code}: response_format rejected, retrying without it")
                self._no_response_format.add(model)
                body.pop("response_format")
                self._check_budget()
                continue
            if r.status_code in (429, 503) and transient_try < 4:
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
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        p_tok, c_tok = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or usage.get("cached_tokens", 0) or 0)
        reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
        pin, pout = self.price_per_token(model)
        hdr = lambda k: int(r.headers[k]) if r.headers.get(k, "").isdigit() else None  # noqa: E731
        rec = self._record(role, messages, memory_hash, attempt, "ok", defer=True, request_id=data.get("id"),
                           prompt_tokens=p_tok, completion_tokens=c_tok, cached_tokens=cached,
                           reasoning_tokens=reasoning, latency_ms=latency,
                           cost_usd=round(p_tok * pin + c_tok * pout, 8) if pin is not None else None,
                           response_format=body.get("response_format", {}).get("type"),
                           ratelimit_remaining_requests=hdr("x-ratelimit-remaining-requests"),
                           ratelimit_remaining_tokens=hdr("x-ratelimit-remaining-tokens"))
        if not text and choice.get("finish_reason") == "length":
            rec.error = "empty content: hit max_tokens (raise GREENSTOVE_MAX_TOKENS)"
        return text, rec
