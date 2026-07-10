"""SEC EDGAR client: new-filing detection and document text extraction.

Complies with SEC fair-access rules: declared User-Agent with contact info,
hard cap of 10 requests/second, no bulk scraping — only the watchlist CIKs.
"""
from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser

import requests

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


class EdgarError(Exception):
    pass


class _TextExtractor(HTMLParser):
    """Minimal HTML→text: drops script/style, keeps block structure."""

    _SKIP = {"script", "style", "head"}
    _BLOCK = {"p", "div", "tr", "br", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t\xa0]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # malformed HTML — fall back to tag stripping
        return re.sub(r"<[^>]+>", " ", html)
    return parser.text()


class EdgarClient:
    def __init__(self, user_agent: str, max_requests_per_second: int = 10, timeout: int = 30):
        if "<" not in user_agent and "@" not in user_agent:
            log.warning("EDGAR user_agent should include contact info: %r", user_agent)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self.min_interval = 1.0 / max(1, max_requests_per_second)
        self.timeout = timeout
        self._last_request_ts = 0.0

    def _get(self, url: str) -> requests.Response:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        try:
            resp = self._session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EdgarError(f"request failed: {url}: {exc}") from exc
        finally:
            self._last_request_ts = time.monotonic()
        if resp.status_code != 200:
            raise EdgarError(f"{resp.status_code} on {url}")
        return resp

    def submissions(self, cik: str) -> dict:
        cik10 = str(cik).lstrip("0").zfill(10)
        return self._get(SUBMISSIONS_URL.format(cik=cik10)).json()

    @staticmethod
    def recent_filings(submissions: dict, forms: list[str], since_date: str) -> list[dict]:
        """Filings of the given forms accepted strictly after `since_date` (YYYY-MM-DD).

        Returns [{form, accession, filing_date, primary_document, primary_doc_description}]
        newest first, from the `recent` block of a submissions JSON.
        """
        recent = submissions.get("filings", {}).get("recent", {})
        out = []
        for i, form in enumerate(recent.get("form", [])):
            if form not in forms:
                continue
            filing_date = recent["filingDate"][i]
            if filing_date <= since_date:
                continue
            out.append(
                {
                    "form": form,
                    "accession": recent["accessionNumber"][i],
                    "filing_date": filing_date,
                    "primary_document": recent["primaryDocument"][i],
                    "primary_doc_description": recent.get("primaryDocDescription", [""] * len(recent["form"]))[i],
                }
            )
        return out

    def filing_index(self, cik: str, accession: str) -> dict:
        """The filing's index.json listing every document in the accession."""
        cik_nz = str(cik).lstrip("0")
        acc_nodash = accession.replace("-", "")
        return self._get(f"{ARCHIVES_BASE}/{cik_nz}/{acc_nodash}/index.json").json()

    def document_text(self, cik: str, accession: str, filename: str) -> str:
        cik_nz = str(cik).lstrip("0")
        acc_nodash = accession.replace("-", "")
        resp = self._get(f"{ARCHIVES_BASE}/{cik_nz}/{acc_nodash}/{filename}")
        if filename.lower().endswith((".htm", ".html")):
            return html_to_text(resp.text)
        return resp.text

    def filing_text(self, cik: str, accession: str, primary_document: str) -> str:
        """Primary document plus any EX-99.* press-release exhibits, as plain text."""
        parts = []
        names = [primary_document]
        try:
            index = self.filing_index(cik, accession)
            for item in index.get("directory", {}).get("item", []):
                name = item.get("name", "")
                low = name.lower()
                # press-release exhibits are conventionally named ex99*/ex-99*
                if low.endswith((".htm", ".html", ".txt")) and re.search(r"ex[-_]?99", low):
                    if name not in names:
                        names.append(name)
        except EdgarError as exc:
            log.warning("could not list index for %s: %s", accession, exc)
        for name in names:
            try:
                parts.append(f"===== DOCUMENT: {name} =====\n" + self.document_text(cik, accession, name))
            except EdgarError as exc:
                log.warning("could not fetch %s in %s: %s", name, accession, exc)
        return "\n\n".join(parts)
