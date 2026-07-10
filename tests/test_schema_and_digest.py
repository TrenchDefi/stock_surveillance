import json

import pytest
from pydantic import ValidationError

from render_digest import build_context, trigger_facts
from src.models import TriggersFile

WATCHLIST = [
    {"ticker": "AAPL", "name": "Apple Inc.", "cik": "0000320193", "benchmark_etf": "XLK"},
    {"ticker": "JPM", "name": "JPMorgan Chase", "cik": "0000019617", "benchmark_etf": "XLF"},
]

VALID_TRIGGERS = {
    "run_date": "2026-07-09",
    "as_of_trading_day": "2026-07-09",
    "triggers": [
        {
            "ticker": "AAPL",
            "type": "price_move",
            "windows": [
                {"window": "1d", "stock_return_pct": -6.2, "etf": "XLK",
                 "etf_return_pct": -1.1, "relative_move_pct": -5.1,
                 "pct_explained": 17.7, "classification": "IDIOSYNCRATIC"}
            ],
            "priority": 1,
            "detail": {},
            "dedupe_key": "AAPL:price_move:2026-07-09",
        },
        {
            "ticker": "JPM",
            "type": "guidance_candidate",
            "windows": [],
            "priority": 2,
            "detail": {"form": "8-K", "accession": "0000019617-26-000123",
                       "filing_date": "2026-07-09", "keyword_hits": ["guidance"],
                       "keyword_flagged": True},
            "dedupe_key": "JPM:guidance_candidate:0000019617-26-000123",
        },
    ],
    "data_issues": [{"ticker": "XYZ", "source": "fmp", "error": "429 rate limited"}],
}


def test_valid_triggers_schema_roundtrip():
    tf = TriggersFile.model_validate(VALID_TRIGGERS)
    TriggersFile.model_validate_json(tf.model_dump_json())


def test_schema_rejects_bad_type():
    bad = json.loads(json.dumps(VALID_TRIGGERS))
    bad["triggers"][0]["type"] = "mystery_event"
    with pytest.raises(ValidationError):
        TriggersFile.model_validate(bad)


def test_schema_rejects_out_of_range_pct_explained():
    bad = json.loads(json.dumps(VALID_TRIGGERS))
    bad["triggers"][0]["windows"][0]["pct_explained"] = 140.0
    with pytest.raises(ValidationError):
        TriggersFile.model_validate(bad)


def test_digest_no_triggers():
    ctx = build_context("2026-07-09", {"as_of_trading_day": "2026-07-09",
                                       "triggers": [], "data_issues": []},
                        {}, WATCHLIST)
    assert not ctx["triggers"]
    assert ctx["tickers_scanned"] == 2
    assert not ctx["scan_failed"]


def test_digest_scan_failed():
    ctx = build_context("2026-07-09", None, {}, WATCHLIST)
    assert ctx["scan_failed"]


def test_digest_triggers_without_investigations():
    ctx = build_context("2026-07-09", VALID_TRIGGERS, {}, WATCHLIST)
    assert len(ctx["summary_rows"]) == 2
    assert len(ctx["uninvestigated"]) == 2
    assert not ctx["cards"]
    assert all(r["status"] == "Not investigated" for r in ctx["summary_rows"])


def test_digest_full_pipeline():
    investigations = {
        "AAPL": {
            "cause_hypothesis": "Shares declined after a supplier warning.",
            "confidence": "medium",
            "cause_unknown": False,
            "guidance_classification": None,
            "sources": [{"title": "News", "url": "https://x", "date": "2026-07-09"}],
        },
        "JPM": {
            "cause_hypothesis": "8-K reaffirms existing guidance.",
            "confidence": "high",
            "cause_unknown": False,
            "guidance_classification": {"status": "REAFFIRMED", "before": None,
                                        "after": "FY26 NII ~$94B"},
            "sources": [],
        },
    }
    ctx = build_context("2026-07-09", VALID_TRIGGERS, investigations, WATCHLIST)
    assert len(ctx["cards"]) == 1  # reaffirmed guidance is a one-liner, not a card
    assert ctx["cards"][0]["ticker"] == "AAPL"
    assert ctx["cards"][0]["eyebrow"] == "PRICE MOVE · IDIOSYNCRATIC"
    assert ctx["reaffirmed"] == [{"ticker": "JPM", "detail": "FY26 NII ~$94B"}]
    assert not ctx["uninvestigated"]


def test_facts_line_uses_layer1_numbers_only():
    facts = trigger_facts(VALID_TRIGGERS["triggers"][0])
    assert "-6.2%" in facts and "XLK" in facts and "-1.1%" in facts
    assert "idiosyncratic" in facts.lower()


def test_templates_render(tmp_path):
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from pathlib import Path

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    for scenario_inv in ({}, {"AAPL": {"cause_hypothesis": "x", "confidence": "low",
                                       "cause_unknown": True, "sources": []}}):
        ctx = build_context("2026-07-09", VALID_TRIGGERS, scenario_inv, WATCHLIST)
        html = env.get_template("digest.html.j2").render(**ctx)
        md = env.get_template("digest.md.j2").render(**ctx)
        assert "Not for client distribution" in html
        assert "Not for client distribution" in md
        assert "AAPL" in html
    # all-quiet scenario
    ctx = build_context("2026-07-09", {"as_of_trading_day": "2026-07-09",
                                       "triggers": [], "data_issues": []}, {}, WATCHLIST)
    html = env.get_template("digest.html.j2").render(**ctx)
    assert "All quiet" in html
