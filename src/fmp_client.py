"""Thin client for the Financial Modeling Prep *stable* API.

The key in use is provisioned for the newer `/stable/` base path only —
legacy `/api/v3/` endpoints return 403 ("Legacy Endpoint") — so every path
lives here and nowhere else. Verified live against the key on 2026-07-10:

    historical-price-eod/full?symbol=X&from=Y   daily OHLCV, newest first
    earnings?symbol=X&limit=N                   EPS + revenue, actual vs estimate
    earnings-calendar?from=Y&to=Z               market-wide calendar
    news/stock?symbols=X&limit=N                per-ticker news with URLs
    earning-call-transcript-dates?symbol=X      transcript index
    earning-call-transcript?symbol=X&year=&quarter=   transcript content

Comma-separated symbols on the price endpoint return an empty list on this
plan, so price history is fetched one symbol per request with a delay.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class FMPError(Exception):
    """Base error for FMP requests. `endpoint` names the blocked/failed path."""

    def __init__(self, message: str, endpoint: str = "", status: int | None = None):
        super().__init__(message)
        self.endpoint = endpoint
        self.status = status


class FMPPlanError(FMPError):
    """402/403 — the key's plan tier does not permit this endpoint."""


class FMPClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://financialmodelingprep.com/stable",
        request_delay_seconds: float = 0.35,
        cache_dir: Path | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")
        if not self.api_key:
            raise FMPError("FMP_API_KEY is not set (env or .env)")
        self.base_url = base_url.rstrip("/")
        self.delay = request_delay_seconds
        self.cache_dir = cache_dir
        self.timeout = timeout
        self._session = requests.Session()
        self._last_request_ts = 0.0

    # ------------------------------------------------------------------ core

    def _cache_path(self, endpoint: str, params: dict) -> Path | None:
        if not self.cache_dir:
            return None
        items = sorted((k, str(v)) for k, v in params.items())
        slug = endpoint.replace("/", "_") + "__" + "_".join(f"{k}-{v}" for k, v in items)
        slug = "".join(c if c.isalnum() or c in "._-" else "-" for c in slug)[:200]
        return self.cache_dir / f"{slug}.json"

    def get(self, endpoint: str, params: dict | None = None, use_cache: bool = True):
        """GET an endpoint, returning parsed JSON. Caches to cache_dir."""
        params = dict(params or {})
        cache_path = self._cache_path(endpoint, params)
        if use_cache and cache_path and cache_path.exists():
            log.debug("cache hit: %s", cache_path.name)
            return json.loads(cache_path.read_text())

        # rate limiting
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        url = f"{self.base_url}/{endpoint}"
        params["apikey"] = self.api_key
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FMPError(f"request failed: {exc}", endpoint=endpoint) from exc
        finally:
            self._last_request_ts = time.monotonic()

        if resp.status_code in (402, 403):
            raise FMPPlanError(
                f"{resp.status_code} plan restriction on /{endpoint}: {resp.text[:200]}",
                endpoint=endpoint,
                status=resp.status_code,
            )
        if resp.status_code == 429:
            raise FMPError(f"429 rate limited on /{endpoint}", endpoint=endpoint, status=429)
        if resp.status_code != 200:
            raise FMPError(
                f"{resp.status_code} on /{endpoint}: {resp.text[:200]}",
                endpoint=endpoint,
                status=resp.status_code,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise FMPError(f"non-JSON response on /{endpoint}", endpoint=endpoint) from exc

        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data))
        return data

    # ------------------------------------------------------- typed wrappers

    def historical_prices(self, symbol: str, history_days: int = 400) -> list[dict]:
        """Daily bars, newest first: {date, open, high, low, close, volume, ...}."""
        frm = (date.today() - timedelta(days=history_days)).isoformat()
        data = self.get("historical-price-eod/full", {"symbol": symbol, "from": frm})
        if not isinstance(data, list):
            raise FMPError(
                f"unexpected shape from historical-price-eod/full for {symbol}",
                endpoint="historical-price-eod/full",
            )
        return data

    def earnings(self, symbol: str, limit: int = 8) -> list[dict]:
        """Recent + upcoming earnings: {date, epsActual, epsEstimated, revenueActual, revenueEstimated}."""
        data = self.get("earnings", {"symbol": symbol, "limit": limit})
        return data if isinstance(data, list) else []

    def earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        data = self.get("earnings-calendar", {"from": from_date, "to": to_date})
        return data if isinstance(data, list) else []

    def stock_news(self, symbol: str, limit: int = 15) -> list[dict]:
        data = self.get("news/stock", {"symbols": symbol, "limit": limit})
        return data if isinstance(data, list) else []

    def transcript_dates(self, symbol: str) -> list[dict]:
        data = self.get("earning-call-transcript-dates", {"symbol": symbol})
        return data if isinstance(data, list) else []

    def transcript(self, symbol: str, year: int, quarter: int) -> dict | None:
        data = self.get(
            "earning-call-transcript", {"symbol": symbol, "year": year, "quarter": quarter}
        )
        return data[0] if isinstance(data, list) and data else None

    def latest_transcript(self, symbol: str) -> dict | None:
        """Most recent transcript with content, or None."""
        dates = self.transcript_dates(symbol)
        for entry in dates:  # newest first
            year, quarter = entry.get("fiscalYear"), entry.get("quarter")
            if year and quarter:
                t = self.transcript(symbol, int(year), int(quarter))
                if t and t.get("content"):
                    return t
        return None
