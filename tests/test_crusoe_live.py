"""Live Crusoe path against a simulated endpoint (httpx.MockTransport): no key, no network."""
import json

import httpx
import pytest

from greenstove.agent_models import PricingOutput
from greenstove.llm import LLMClient, LLMSettings, parse_pricing

CATALOG = {"data": [
    {"id": "nvidia/Nemotron-3-Super-120B-A12B", "supported_parameters": ["response_format", "structured_outputs"],
     "pricing": {"prompt": "0.0000003", "completion": "0.0000012"}},
    {"id": "nvidia/Nemotron-3-Nano-30B-A3B", "supported_parameters": ["response_format"],
     "pricing": {"input": 0.06, "output": 0.24}},
]}
ANSWER = {"prices": {"sun": 3, "standard": 8, "peak": 13}, "rationale": "r"}


def settings(**kw):
    base = dict(mode="crusoe", api_key="k" * 32, live_spend_ok=True)
    return LLMSettings(**{**base, **kw})


def endpoint(log, *, reject_rf=False, fail_first=0):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=CATALOG)
        body = json.loads(request.content)
        log.append(body)
        assert request.headers["Authorization"].startswith("Bearer ")
        state["n"] += 1
        if state["n"] <= fail_first:
            return httpx.Response(429, json={"error": "rate"})
        if reject_rf and "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, headers={"x-ratelimit-remaining-requests": "599",
                                            "x-ratelimit-remaining-tokens": "1999000"},
                              json={"id": f"req-{state['n']}", "choices": [{"message": {"content": json.dumps(ANSWER)},
                                                                         "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                                              "prompt_tokens_details": {"cached_tokens": 800},
                                              "completion_tokens_details": {"reasoning_tokens": 0}}})
    return httpx.MockTransport(handler)


def test_live_call_records_cache_cost_and_rate_limits(tmp_path):
    log = []
    llm = LLMClient(settings(), transport=endpoint(log), usage_path=tmp_path / "usage.jsonl")
    out, recs, err = llm.complete_json("pricing", "sys", {"x": 1}, PricingOutput)
    assert out.prices["peak"] == 13 and err is None
    r = recs[0]
    assert (r.request_id, r.cached_tokens, r.ratelimit_remaining_requests) == ("req-1", 800, 599)
    assert r.cost_usd == pytest.approx(1000 * 3e-7 + 50 * 1.2e-6)
    assert r.response_format == "json_schema" and log[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "k" * 32 not in (tmp_path / "usage.jsonl").read_text()


def test_rejected_response_format_falls_back_and_is_remembered():
    log = []
    llm = LLMClient(settings(), transport=endpoint(log, reject_rf=True))
    out, _, _ = llm.complete_json("pricing", "sys", {}, PricingOutput)
    assert out is not None and "response_format" in log[0] and "response_format" not in log[1]
    llm.complete_json("pricing", "sys", {}, PricingOutput)
    assert "response_format" not in log[2]                       # not retried for this model
    assert [c.outcome for c in llm.calls][:2] == ["error", "ok"]  # the rejected request still counts


def test_role_routing_and_thinking_per_role():
    log = []
    s = settings(role_models={"note_writer": "nvidia/Nemotron-3-Nano-30B-A3B"}, thinking={"pricing": True})
    llm = LLMClient(s, transport=endpoint(log))
    llm.complete_json("pricing", "sys", {}, PricingOutput)
    llm.complete_json("note_writer", "sys", {}, PricingOutput)
    assert log[0]["model"].endswith("Super-120B-A12B") and log[0]["chat_template_kwargs"]["enable_thinking"] is True
    assert log[0]["max_tokens"] >= 2048
    assert log[1]["model"].endswith("Nano-30B-A3B") and log[1]["response_format"] == {"type": "json_object"}
    assert llm.calls[1].cost_usd == pytest.approx(1000 * 0.06e-6 + 50 * 0.24e-6)   # per-1M pricing read correctly


def test_rate_limit_backoff(monkeypatch):
    monkeypatch.setattr("greenstove.llm.time.sleep", lambda s: None)
    log = []
    llm = LLMClient(settings(), transport=endpoint(log, fail_first=2))
    out, _, _ = llm.complete_json("pricing", "sys", {}, PricingOutput)
    assert out is not None and [c.outcome for c in llm.calls] == ["error", "error", "ok"]


def test_parse_pricing_units():
    assert parse_pricing({"pricing": {"prompt": "0.000001", "completion": "0.000002"}}) == (1e-6, 2e-6)
    assert parse_pricing({"pricing": {"input": 0.5, "output": 1.5}}) == pytest.approx((0.5e-6, 1.5e-6))
    assert parse_pricing({}) == (None, None)


def test_bench_and_usage_report(tmp_path):
    from greenstove import crusoe
    from greenstove.scenarios import scenario
    llm = LLMClient(settings(), transport=endpoint([]), usage_path=tmp_path / "usage.jsonl")
    rows = crusoe.bench(llm, ["nvidia/Nemotron-3-Super-120B-A12B", "nvidia/Nemotron-3-Nano-30B-A3B"],
                        scenario("dev-overcast-low-battery"), n=2)
    assert [r["valid"] for r in rows] == ["2/2", "2/2"] and rows[0]["response_format"] == "json_schema"
    assert rows[1]["cost_usd"] < rows[0]["cost_usd"]                      # Nano priced lower in the catalog
    usage = crusoe.usage_rows(tmp_path / "usage.jsonl")
    assert {u["model"] for u in usage} == {"nvidia/Nemotron-3-Super-120B-A12B", "nvidia/Nemotron-3-Nano-30B-A3B"}
    assert all(u["cache_hit"] == 0.8 for u in usage)
    cat = crusoe.catalog_rows(CATALOG["data"])
    by_id = {r["id"]: r for r in cat}
    assert all(r["nemotron"] for r in cat) and by_id["nvidia/Nemotron-3-Nano-30B-A3B"]["usd_per_mtok_in"] == 0.06
    assert by_id["nvidia/Nemotron-3-Super-120B-A12B"]["usd_per_mtok_in"] == 0.3
