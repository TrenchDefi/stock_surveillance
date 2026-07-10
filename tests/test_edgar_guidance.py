import json

from src.edgar_client import EdgarClient, html_to_text
from src.triggers import guidance
from tests.conftest import FIXTURES

FORMS = ["8-K", "10-Q", "10-K", "6-K"]
KEYWORDS = ["guidance", "outlook", "raises", "lowers", "reaffirms",
            "withdraws", "full-year", "fiscal 2026"]


def load_submissions():
    return json.loads((FIXTURES / "submissions_aapl.json").read_text())


def test_new_filing_detection_with_moved_watermark():
    subs = load_submissions()
    # watermark before the 2026-04-30 8-K and 2026-05-01 10-Q
    filings = EdgarClient.recent_filings(subs, FORMS, since_date="2026-04-25")
    forms = {f["form"] for f in filings}
    assert "8-K" in forms and "10-Q" in forms
    assert all(f["filing_date"] > "2026-04-25" for f in filings)
    # non-watchlist forms (4, 144, SD, SCHEDULE 13G) are excluded
    assert forms <= set(FORMS)

    # moved watermark: nothing before it reappears
    later = EdgarClient.recent_filings(subs, FORMS, since_date="2026-05-01")
    assert all(f["filing_date"] > "2026-05-01" for f in later)
    assert len(later) < len(filings)

    # watermark at the newest filing -> nothing new
    assert EdgarClient.recent_filings(subs, FORMS, since_date="2026-06-30") == []


def test_guidance_keyword_flagging_on_8k_text():
    text = (FIXTURES / "sample_8k.txt").read_text()
    hits = guidance.keyword_hits(text, KEYWORDS)
    assert "guidance" in hits
    assert "raises" in hits
    assert "full-year" in hits
    assert "withdraws" not in hits


def test_keyword_matching_is_whole_word():
    assert guidance.keyword_hits("misguidance issues", ["guidance"]) == []
    assert guidance.keyword_hits("Guidance was updated.", ["guidance"]) == ["guidance"]


def test_guidance_candidate_trigger_and_text_saved(tmp_path):
    filings = [{"form": "8-K", "accession": "0000320193-26-000011",
                "filing_date": "2026-04-30", "primary_document": "doc.htm"}]
    text = (FIXTURES / "sample_8k.txt").read_text()
    triggers = guidance.evaluate(
        "AAPL", filings, lambda f: text, KEYWORDS, tmp_path / "filings", "2026-07-09"
    )
    assert len(triggers) == 1
    t = triggers[0]
    assert t.type == "guidance_candidate"
    assert t.detail["keyword_flagged"] is True
    assert (tmp_path / "filings" / "AAPL" / "0000320193-26-000011.txt").exists()
    # re-run reads from disk instead of refetching
    calls = []
    triggers2 = guidance.evaluate(
        "AAPL", filings, lambda f: calls.append(1) or "", KEYWORDS,
        tmp_path / "filings", "2026-07-09"
    )
    assert calls == []
    assert triggers2[0].dedupe_key == t.dedupe_key


def test_html_to_text_strips_markup():
    html = "<html><head><style>p{}</style></head><body><p>Raises full-year guidance</p><script>x()</script></body></html>"
    text = html_to_text(html)
    assert "Raises full-year guidance" in text
    assert "x()" not in text and "p{}" not in text
