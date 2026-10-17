"""You.com Web Search client (POST https://ydc-index.io/v1/search, header X-API-Key).

Live calls need YDC_API_KEY and GREENSTOVE_LIVE_SPEND_OK=1. Mock mode returns a fixture
that is labeled as such and never presented as a real forecast.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

from greenstove.llm import ROOT, ModelError

SEARCH_URL = "https://ydc-index.io/v1/search"


@dataclass
class SearchHit:
    url: str
    title: str
    text: str                                   # description + snippets/highlights, as retrieved
    page_age: str | None = None


@dataclass
class SearchResult:
    query: str
    mode: str
    hits: list[SearchHit] = field(default_factory=list)
    search_uuid: str | None = None
    latency_ms: int = 0


def _mock(query: str, place: str, day_label: str) -> SearchResult:
    text = (f"MOCK SEARCH FIXTURE (not a real forecast). {place} forecast for {day_label}: "
            "Partly cloudy in the morning, showers likely in the afternoon. Cloud cover 55%. "
            "Chance of rain 60%.")
    return SearchResult(query=query, mode="mock", hits=[SearchHit(
        url="https://example.invalid/mock-forecast", title=f"Mock forecast for {place}", text=text)])


def search(query: str, *, place: str, day_label: str, mode: str = "mock", count: int = 8) -> SearchResult:
    if mode == "mock":
        return _mock(query, place, day_label)
    load_dotenv(ROOT / ".env")
    key = os.environ.get("YDC_API_KEY", "").strip()
    if not key:
        raise ModelError("YDC_API_KEY is not set (scripts/set_crusoe_key.py --var YDC_API_KEY)")
    if os.environ.get("GREENSTOVE_LIVE_SPEND_OK", "0") != "1":
        raise ModelError("live You.com calls need GREENSTOVE_LIVE_SPEND_OK=1")
    body = {"query": query, "count": count, "freshness": "week",
            "extraction": {"extraction_mode": "highlights", "highlights": {"total_max_tokens": 2048}}}
    t0 = time.monotonic()
    try:
        r = httpx.post(SEARCH_URL, headers={"X-API-Key": key, "Content-Type": "application/json"},
                       json=body, timeout=30)
    except httpx.HTTPError as exc:
        raise ModelError(f"You.com network error: {exc.__class__.__name__}") from None
    if r.status_code != 200:
        raise ModelError(f"You.com returned HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    hits = []
    for w in (data.get("results") or {}).get("web", []):
        parts = [w.get("description") or ""] + list(w.get("snippets") or [])
        parts += list((w.get("contents") or {}).get("highlights") or [])
        hits.append(SearchHit(url=w.get("url", ""), title=w.get("title", ""),
                              text="\n".join(p for p in parts if p), page_age=w.get("page_age")))
    return SearchResult(query=query, mode="live", hits=hits,
                        search_uuid=(data.get("metadata") or {}).get("search_uuid"),
                        latency_ms=int((time.monotonic() - t0) * 1000))
