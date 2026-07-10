# Stock Surveillance Agent — Build Specification

**Project:** Nightly automated surveillance of a predefined watchlist of securities.
**Audience for this doc:** Claude Code (implementation agent).
**Deliverable:** A self-contained Python project, runnable nightly via cron / Task Scheduler, that detects a defined set of triggers, investigates the cause of any price-driven trigger, and emits a human-readable digest for internal review.

---

## 1. Purpose & Operating Principles

This is an **internal monitoring tool**. Output is reviewed by a human before any content derived from it reaches clients. Design accordingly:

1. **Two-layer architecture, strictly separated.**
   - **Layer 1 (deterministic):** Pure Python. Fetches data, evaluates every trigger with explicit arithmetic, writes structured results to disk. No LLM involvement. Must be independently runnable and testable (`python scan.py`).
   - **Layer 2 (investigation):** LLM-driven. Runs *only* when Layer 1 fires triggers. Reads news, filings, and transcripts to explain *why* — especially for price-move triggers. Invoked via `claude -p` in headless mode (subscription auth), orchestrated by a wrapper script.
2. **Idempotent and stateful.** Re-running the same night must not duplicate alerts. Persistent state lives in a local `state/` directory (JSON).
3. **Fail loud, degrade gracefully.** A single ticker's API failure must not kill the run. Failures are logged and surfaced in the digest under a "Data Issues" section.
4. **No secrets in code.** FMP API key read from environment variable `FMP_API_KEY` (support a `.env` file via `python-dotenv`). `.env` is gitignored.

---

## 2. Data Sources

### 2.1 Financial Modeling Prep (FMP)
Primary source for prices, earnings, estimates, news, and transcripts. API key already provisioned.

> **Implementation note:** FMP has migrated endpoints between `/api/v3/` and the newer `/stable/` base paths over time. Before writing the client, **verify the current endpoint paths and response schemas against the live FMP docs** (https://site.financialmodelingprep.com/developer/docs) and confirm which endpoints the key's plan tier actually permits. Build a thin `fmp_client.py` module so paths can be swapped in one place. Handle 402/403 (plan restriction) explicitly and report which endpoint was blocked.

Data needed from FMP (map to whatever the current endpoints are):
- End-of-day and historical daily prices (≥ 13 months of history per ticker)
- 52-week high/low (derivable from history; don't trust a pre-computed field without checking staleness)
- Earnings surprises: actual vs. consensus EPS and revenue, with report date
- Earnings calendar (to know when a watchlist name just reported)
- Stock news (per-ticker, with published timestamps and source URLs)
- Earnings call transcripts (most recent, per ticker)
- ETF prices (for the benchmark comparison — same price endpoints)

Respect rate limits: batch requests where FMP supports comma-separated symbols, add a configurable inter-request delay, and cache each night's raw pulls to `state/cache/YYYY-MM-DD/` so a re-run doesn't re-hit the API.

### 2.2 SEC EDGAR
Used to detect new filings — the reliable signal for **guidance revisions** (almost always disclosed via 8-K with a press-release exhibit).

- Poll each watchlist company's submissions JSON: `https://data.sec.gov/submissions/CIK{10-digit-cik}.json`
- Maintain a `ticker → CIK` map in config (resolve once via SEC's `company_tickers.json`, then pin).
- Filter for filings **since the last successful run**: forms `8-K`, `10-Q`, `10-K`, `6-K` (for foreign issuers if any).
- For 8-Ks, fetch the filing index and pull the primary document + press-release exhibits (EX-99.*) as text/HTML.
- **Compliance with SEC access rules:** declared `User-Agent` header with contact info (configurable), max 10 requests/second, no bulk scraping.

---

## 3. Configuration

Single `config.yaml`:

```yaml
run:
  timezone: America/New_York        # market timezone; run after close
  state_dir: ./state
  output_dir: ./reports

fmp:
  # key comes from env FMP_API_KEY
  request_delay_seconds: 0.35

edgar:
  user_agent: "Linden Thomas Surveillance internal tool <contact email>"

thresholds:
  eps_surprise_pct: 15          # absolute value, either direction
  revenue_surprise_pct: 5       # absolute value, either direction
  move_1d_pct: 5
  move_1w_pct: 10               # 5 trading days
  move_1m_pct: 15               # 21 trading days
  relative_move_flag_pct: 60    # see §4.4 — % of move explained by benchmark

alerts:
  high_low_cooldown_days: 10    # see §4.1

watchlist:
  - ticker: AAPL
    name: Apple Inc.
    cik: "0000320193"
    benchmark_etf: XLK           # sector/theme ETF for relative context
  - ticker: JPM
    name: JPMorgan Chase
    cik: "0000019617"
    benchmark_etf: XLF
  # ... etc. Benchmark defaults to SPY if omitted.
```

Every trigger threshold must come from config — nothing hardcoded.

---

## 4. Triggers (Layer 1 — deterministic)

All triggers are evaluated per ticker, per run, against the latest completed trading day. Each fired trigger becomes a structured record (see §6 schema).

### 4.1 New 52-week high / low
- Fires when the latest close is the highest (or lowest) close of the trailing 252 trading days.
- **Cooldown:** during a sustained run-up, a stock makes a new high almost daily. After firing, suppress repeat high (or low) triggers for that ticker for `high_low_cooldown_days`, *but* record the ongoing streak in state so the digest can say "3rd new high this month."

### 4.2 Earnings surprise
- Only evaluated when the ticker reported earnings since the last successful run (check earnings calendar + surprises data).
- Fires when `|actual − estimate| / |estimate| ≥ threshold`: **±5% revenue**, **±15% EPS**.
- Guard rails: skip the EPS ratio when the consensus estimate is near zero (|estimate| < $0.05) to avoid meaningless percentages — in that case report the absolute miss/beat in dollars and fire on a configurable absolute delta instead ($0.05 default). Record direction (beat/miss) and both metrics regardless of which one fired.

### 4.3 Guidance revision (detection in Layer 1, classification in Layer 2)
- Layer 1 detects **candidate events**: any new 8-K (or 10-Q/10-K) since last run, flagged higher-priority if the press-release exhibit text contains guidance-related keywords (`guidance`, `outlook`, `raises`, `lowers`, `reaffirms`, `withdraws`, `full-year`, `fiscal 2026`, etc.). Keyword hit list configurable.
- Layer 1 saves the extracted document text to `state/filings/{ticker}/{accession}.txt` and emits a `guidance_candidate` trigger.
- Layer 2 (Claude) reads the text and classifies: **RAISED / LOWERED / REAFFIRMED / WITHDRAWN / INITIATED / NO_GUIDANCE_CONTENT**, with the specific before/after numbers when stated. Only RAISED / LOWERED / WITHDRAWN / INITIATED surface as full alerts; REAFFIRMED appears as a one-line note; NO_GUIDANCE_CONTENT is dropped (but logged).

### 4.4 Significant price move, with relative (benchmark) context
- Compute total return over 1 day, 5 trading days, and 21 trading days. Fire at **±5% / ±10% / ±15%** respectively.
- For every fired move, compute the mapped `benchmark_etf` return over the identical window and derive:
  - `relative_move = stock_return − etf_return`
  - `pct_explained = clamp(etf_return / stock_return, 0..1) × 100` (only when signs match; otherwise 0)
- Classification attached to the trigger:
  - **MARKET/SECTOR-DRIVEN** if `pct_explained ≥ relative_move_flag_pct` (default 60%) — the benchmark moved with it.
  - **IDIOSYNCRATIC** otherwise — the stock moved on its own. These get priority in Layer 2 investigation.
- If multiple windows fire for the same ticker, collapse into one alert reporting all windows (avoid three alerts for one event).

---

## 5. Layer 2 — Investigation Agent

### 5.1 Invocation model
- The nightly entrypoint is `run_nightly.sh`:
  1. `python scan.py` → writes `state/runs/YYYY-MM-DD/triggers.json` and exits 0 (triggers found) / 3 (no triggers) / 1 (fatal error).
  2. If triggers exist, invoke headless Claude Code:
     ```bash
     claude -p "$(cat prompts/investigate.md)" \
       --allowedTools "Read" "Write" "Bash(python:*)" "Bash(curl:*)" "WebFetch" "WebSearch" \
       --max-turns 40 \
       --output-format json >> logs/agent-$(date +%F).json
     ```
  3. Regardless of outcome, `python render_digest.py` builds the final report from whatever exists.
- `prompts/investigate.md` is a checked-in prompt file (see §5.2). Keep it self-contained: headless runs get no follow-up questions.
- Provide helper scripts the agent can call instead of raw API fiddling: `python tools/get_news.py TICKER --days 7`, `python tools/get_transcript.py TICKER`, `python tools/get_filing.py TICKER ACCESSION`. These wrap FMP/EDGAR with the same caching and rate-limit handling as the scanner.

### 5.2 Investigation prompt requirements
The prompt must instruct the agent to, for each trigger in `triggers.json` (idiosyncratic price moves first, then guidance candidates, then earnings surprises, then highs/lows):

1. Pull recent news (helper script) and, when the trigger window includes an earnings date, the latest transcript.
2. For guidance candidates: read the saved filing text and classify per §4.3.
3. Write, per ticker, a JSON block conforming to the §6 `investigation` schema: a 2–4 sentence **cause hypothesis**, a confidence level (`high/medium/low`), the specific sources used (URLs / filing accession numbers), and an explicit `"cause_unknown": true` when nothing in the sources explains the move — **never speculate beyond sources; say so instead.**
4. Neutral, factual tone. No recommendations, no "attractive entry point" language, no forward-looking opinion. This is surveillance, not advice.
5. Save results to `state/runs/YYYY-MM-DD/investigations.json`.

### 5.3 Budget guards
- Cap investigation to the N highest-priority triggers per night (config, default 10); remainder listed in the digest as "triggered, not investigated."
- `--max-turns` set; helper scripts truncate transcripts to the most recent call and news to 15 items.

---

## 6. Data Contracts

### `triggers.json` (Layer 1 output)
```json
{
  "run_date": "2026-07-09",
  "as_of_trading_day": "2026-07-09",
  "triggers": [
    {
      "ticker": "AAPL",
      "type": "price_move",             // price_move | high_52w | low_52w | earnings_surprise | guidance_candidate
      "windows": [
        {"window": "1d", "stock_return_pct": -6.2, "etf": "XLK", "etf_return_pct": -1.1,
         "relative_move_pct": -5.1, "pct_explained": 17.7, "classification": "IDIOSYNCRATIC"}
      ],
      "priority": 1,
      "detail": {},                      // type-specific fields (surprise metrics, accession numbers, streak counts…)
      "dedupe_key": "AAPL:price_move:2026-07-09"
    }
  ],
  "data_issues": [
    {"ticker": "XYZ", "source": "fmp", "error": "429 rate limited on /historical-price-full"}
  ]
}
```

### `investigations.json` (Layer 2 output)
```json
{
  "AAPL": {
    "cause_hypothesis": "…",
    "confidence": "medium",
    "cause_unknown": false,
    "guidance_classification": null,     // or {"status": "LOWERED", "before": "...", "after": "..."}
    "sources": [{"title": "...", "url": "...", "date": "2026-07-09"}]
  }
}
```

Layer 1 must validate its own output against this schema before exiting (use `pydantic`).

---

## 7. Output: Nightly Digest

`render_digest.py` produces `reports/YYYY-MM-DD-digest.html` (plus a plain `.md` twin) from `triggers.json` + `investigations.json`. Requirements:

- **If zero triggers:** still emit a short "All quiet — N tickers scanned, no triggers" digest so a missing report unambiguously means the job failed.
- Sections, in order: (1) Summary table of all triggers; (2) One card per investigated ticker — trigger facts, benchmark context ("−6.2% vs XLK −1.1%; move is idiosyncratic"), cause hypothesis with linked sources; (3) Uninvestigated triggers; (4) Reaffirmed-guidance one-liners; (5) Data issues.
- Every number in the digest must come from `triggers.json` (Layer 1), never restated from the LLM's prose — the LLM explains, the scanner quantifies.
- Include a fixed footer: *"Internal surveillance output — for internal review only. Not for client distribution without compliance review."*
- **Styling of the HTML digest:** EB Garamond for headings, Source Sans 3 for body (Google Fonts, with serif/sans fallbacks); palette navy `#1F2D4A`, gold `#B79B47`, cream `#F7F4EA`. Cards: cream background, 3px gold top border, small-caps letter-spaced eyebrow labels (e.g., `PRICE MOVE · IDIOSYNCRATIC`), vertical gold tick marks on list items. Single self-contained HTML file, no external CSS/JS beyond the font import.
- Optional (config flag): email the digest via SMTP settings in `.env`. Default off.

---

## 8. State, Scheduling, Logging

- `state/last_successful_run.json` — timestamp + last EDGAR check time per ticker.
- `state/alert_history.jsonl` — append-only log of every fired trigger (`dedupe_key`, date, type) used for cooldowns and streaks.
- Cron example (in README): `30 22 * * 1-5` America/New_York (≈1 hr after close, weekdays). Document the Task Scheduler equivalent for Windows.
- Rotating file logs in `logs/`; INFO for normal ops, full tracebacks on errors. Exit codes: 0 success, 3 success-no-triggers, 1 failure (so cron monitoring can alert on 1).

---

## 9. Project Layout

```
surveillance/
├── config.yaml
├── .env.example              # FMP_API_KEY=, SMTP_* (optional)
├── run_nightly.sh
├── scan.py                   # Layer 1 orchestrator
├── render_digest.py
├── src/
│   ├── fmp_client.py
│   ├── edgar_client.py
│   ├── triggers/             # one module per trigger type
│   ├── models.py             # pydantic schemas (§6)
│   └── state.py
├── tools/                    # helper CLIs for the Layer 2 agent (§5.1)
├── prompts/investigate.md
├── tests/
├── state/                    # gitignored
├── reports/                  # gitignored
└── logs/                     # gitignored
```

Python ≥3.11. Dependencies: `requests`, `pydantic`, `pyyaml`, `python-dotenv`, `jinja2` (digest template), `pytest`. No pandas unless it genuinely simplifies the return math.

---

## 10. Testing & Acceptance Criteria

**Unit tests (offline, fixture-based — record real API responses once into `tests/fixtures/`):**
1. 52-week high/low detection incl. cooldown and streak counting.
2. Earnings surprise math incl. the near-zero-estimate guard.
3. Price-move windows and benchmark decomposition (verify `pct_explained` edge cases: opposite signs, zero ETF move).
4. EDGAR new-filing detection against a fixture submissions JSON with a moved watermark.
5. Guidance keyword flagging on sample 8-K text.
6. Schema validation of `triggers.json`.
7. Digest renders correctly for: no triggers / triggers without investigations / full pipeline.

**Definition of done:**
- [ ] `python scan.py --dry-run` runs against live APIs for a 5-ticker sample config and prints a valid `triggers.json` to stdout.
- [ ] Deliberately loosened thresholds (e.g., 1-day move ≥ 0.5%) produce triggers, and the full `run_nightly.sh` path — including the headless Claude investigation — completes and renders a digest.
- [ ] Killing the FMP key mid-run produces a digest with a populated Data Issues section, not a crash.
- [ ] Re-running the same night produces no duplicate alerts and reuses the cache.
- [ ] README covers: setup, config, cron/Task Scheduler install, how to add a ticker, how to adjust thresholds, and the compliance footer note.

---

## 11. Explicit Non-Goals
- No trading, order routing, or brokerage connectivity of any kind.
- No client-facing output — the digest is internal-only.
- No intraday monitoring; end-of-day only.
- No portfolio accounting — this watches securities, not positions.
