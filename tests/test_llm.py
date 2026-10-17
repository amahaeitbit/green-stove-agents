import pytest
from pydantic import BaseModel

from greenstove.agent_models import PricingOutput
from greenstove.llm import BudgetExceeded, LLMClient, LLMSettings, ModelError, extract_json


def test_extract_json_strips_think_and_fences():
    text = '<think>hmm {"no": 1}</think>\n```json\n{"prices": {"a": 1}, "note": "x}y"}\n```'
    assert extract_json(text) == {"prices": {"a": 1}, "note": "x}y"}


def test_extract_json_rejects_missing_object():
    with pytest.raises(ValueError):
        extract_json("no json here")


def _client(handler, **kw):
    return LLMClient(LLMSettings(mode="mock", **kw), {"pricing": handler})


def test_mock_call_is_recorded_without_credentials():
    llm = _client(lambda p: {"prices": {"sun": 4}, "rationale": "r"})
    out, recs, err = llm.complete_json("pricing", "sys", {"x": 1}, PricingOutput, memory_hash="m1")
    assert out.prices == {"sun": 4} and err is None
    assert recs[0].outcome == "ok" and recs[0].memory_hash == "m1" and recs[0].prompt_tokens > 0
    assert "api_key" not in recs[0].model_dump_json()


def test_invalid_output_repairs_and_counts_every_call():
    replies = iter([{"prices": "bad"}, {"prices": {"sun": 4}}])
    llm = _client(lambda p: next(replies))
    out, recs, _ = llm.complete_json("pricing", "sys", {}, PricingOutput)
    assert out is not None and [r.outcome for r in recs] == ["invalid", "repaired"]
    assert len(llm.calls) == 2


def test_still_invalid_after_repair_returns_none():
    llm = _client(lambda p: {"prices": "bad"})
    out, recs, err = llm.complete_json("pricing", "sys", {}, PricingOutput)
    assert out is None and err and len(recs) == 2


def test_call_budget_enforced():
    llm = _client(lambda p: {"prices": {"sun": 4}}, max_calls=1)
    llm.complete_json("pricing", "sys", {}, PricingOutput)
    with pytest.raises(BudgetExceeded):
        llm.complete_json("pricing", "sys", {}, PricingOutput)


def test_live_mode_needs_key_and_explicit_spend_flag():
    with pytest.raises(ModelError):
        LLMClient(LLMSettings(mode="crusoe", api_key=""))
    with pytest.raises(ModelError):
        LLMClient(LLMSettings(mode="crusoe", api_key="k" * 30, live_spend_ok=False))


def test_usage_log_has_final_outcomes_and_no_key(tmp_path):
    replies = iter([{"prices": "bad"}, {"prices": {"sun": 4}}])
    llm = LLMClient(LLMSettings(mode="mock", api_key="secret-should-not-appear"), {"pricing": lambda p: next(replies)},
                    usage_path=tmp_path / "usage.jsonl")
    llm.complete_json("pricing", "sys", {}, PricingOutput)
    lines = (tmp_path / "usage.jsonl").read_text().splitlines()
    assert ['"invalid"' in lines[0], '"repaired"' in lines[1]] == [True, True]
    assert "secret-should-not-appear" not in "".join(lines)
