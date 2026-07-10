# Stock Surveillance Agent

Nightly automated surveillance of a predefined watchlist. A deterministic
Python scanner (Layer 1) detects triggers — 52-week highs/lows, earnings
surprises, guidance-revision candidates from SEC filings, and significant
price moves with benchmark decomposition — then a headless Claude Code agent
(Layer 2) investigates *why*, and a styled HTML/Markdown digest is rendered
for internal review.

> Internal surveillance output — for internal review only. Not for client
> distribution without compliance review. (This footer is baked into every
> digest; keep it there.)

## Setup

```bash
cd stock_surveillance
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set FMP_API_KEY= and EDGAR_CONTACT_EMAIL=
chmod +x run_nightly.sh
```

Requirements: Python ≥ 3.11, an FMP API key provisioned for the `/stable/`
API, and the `claude` CLI on PATH for Layer 2 (optional — the scanner and
digest work without it).

The SEC requires a declared contact email in the EDGAR User-Agent header —
that's `EDGAR_CONTACT_EMAIL` in `.env`, substituted into `edgar.user_agent`
at startup.

## Running

### Manual (any time)

```bash
./run_nightly.sh                        # full pipeline: scan → investigate → digest
./run_nightly.sh --skip-investigation   # no LLM: scan + digest only
./run_nightly.sh --dry-run              # print triggers to stdout, change nothing
./run_nightly.sh --force                # ignore 52w high/low cooldowns
./run_nightly.sh --config other.yaml    # alternate config (e.g. loosened thresholds)

# individual stages
.venv/bin/python scan.py --dry-run      # Layer 1 only, prints triggers.json
.venv/bin/python render_digest.py --date 2026-07-10   # re-render a past digest
```

Re-running the same night is safe: raw API pulls are cached per day in
`state/cache/YYYY-MM-DD/`, alert history is deduplicated by `dedupe_key`, and
the "since last run" watermark is not advanced by a same-day re-run.

### Scheduled (cron, macOS/Linux)

Run ~1 hour after the close, weekdays. `crontab -e`:

```cron
CRON_TZ=America/New_York
30 22 * * 1-5 cd /path/to/stock_surveillance && PYTHON=.venv/bin/python ./run_nightly.sh >> logs/cron.log 2>&1
```

If your cron has no `CRON_TZ` support, schedule in the machine's local
equivalent of 22:30 ET. Monitor for exit code 1 (scan failure); a missing
digest for a weekday also unambiguously means the job failed — an "all
quiet" digest is emitted even with zero triggers.

### Scheduled (Windows Task Scheduler)

```
schtasks /Create /TN "StockSurveillance" /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
  /ST 22:30 /TR "cmd /c cd /d C:\path\to\stock_surveillance && bash run_nightly.sh"
```

(or point the action at `bash.exe`/WSL, or run `python scan.py` and
`python render_digest.py` as two actions if bash is unavailable).

## Output

- `reports/YYYY-MM-DD-digest.html` (+ `.md` twin) — trigger summary table,
  one card per investigated ticker with benchmark context and sourced cause
  hypothesis, uninvestigated triggers, reaffirmed-guidance one-liners, data
  issues.
- `state/runs/YYYY-MM-DD/triggers.json` — Layer 1 output (schema-validated).
- `state/runs/YYYY-MM-DD/investigations.json` — Layer 2 output.
- `logs/` — rotating scan logs, per-night pipeline logs, raw agent output.

Every number in the digest comes from Layer 1; the LLM only explains.

## Configuration (`config.yaml`)

### Adding a ticker

```yaml
watchlist:
  - ticker: NVDA
    name: NVIDIA Corporation
    cik: "0001045810"          # zero-padded 10-digit CIK
    benchmark_etf: SMH          # omit to default to SPY
```

Find the CIK in SEC's [company_tickers.json](https://www.sec.gov/files/company_tickers.json)
or the company's EDGAR page. Pin it in config — it is resolved once, not nightly.

### Adjusting thresholds

All triggers read `thresholds:` — nothing is hardcoded:

| Key | Default | Meaning |
|-----|---------|---------|
| `move_1d_pct` / `move_1w_pct` / `move_1m_pct` | 5 / 10 / 15 | abs % move over 1/5/21 trading days |
| `relative_move_flag_pct` | 60 | % of move explained by benchmark ⇒ MARKET/SECTOR-DRIVEN |
| `eps_surprise_pct` | 15 | abs EPS surprise vs consensus, either direction |
| `revenue_surprise_pct` | 5 | abs revenue surprise, either direction |
| `eps_estimate_floor_usd` | 0.05 | below this consensus, % is meaningless → absolute-delta guard |
| `eps_surprise_abs_usd` | 0.05 | absolute EPS delta trigger used under the guard |
| `alerts.high_low_cooldown_days` | 10 | suppress repeat 52w high/low alerts (streaks still counted) |

`guidance_keywords:` controls which phrases flag a new filing as a
higher-priority guidance candidate. `investigation.max_triggers_per_night`
caps Layer 2 spend; the remainder appear in the digest as "triggered, not
investigated".

### Email (optional, default off)

Set `email.enabled: true` and the `SMTP_*` variables in `.env` to have the
HTML digest emailed after rendering.

## Architecture

```
run_nightly.sh
├─ scan.py                 Layer 1 — deterministic. FMP + EDGAR → triggers.json
│    src/fmp_client.py       /stable/ API only (legacy /api/v3/ is plan-blocked)
│    src/edgar_client.py     submissions JSON, filing text, SEC fair-access rules
│    src/triggers/*          one module per trigger type
├─ claude -p prompts/investigate.md     Layer 2 — only when triggers exist
│    tools/get_news.py TICKER --days 7
│    tools/get_transcript.py TICKER
│    tools/get_filing.py TICKER ACCESSION
└─ render_digest.py        digest from triggers.json + investigations.json
```

Exit codes: `0` triggers found · `3` success, no triggers · `1` failure
(alert on 1 in cron monitoring).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Offline, fixture-based: trigger math (incl. cooldown/streaks, near-zero EPS
guard, `pct_explained` edge cases), EDGAR new-filing detection with a moved
watermark, guidance keyword flagging, schema validation, and digest rendering
for all three scenarios (no triggers / triggers only / full pipeline).
