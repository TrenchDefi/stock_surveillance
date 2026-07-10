"""Persistent state: last-run watermarks, alert history, cooldowns, streaks."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7  # first run ever: how far back "since last run" reaches


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.last_run_path = self.state_dir / "last_successful_run.json"
        self.history_path = self.state_dir / "alert_history.jsonl"
        self.streaks_path = self.state_dir / "streaks.json"

    # ------------------------------------------------------------ last run

    def load_last_run(self) -> dict:
        if self.last_run_path.exists():
            return json.loads(self.last_run_path.read_text())
        return {}

    def _default_watermark(self) -> str:
        return (date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()

    def watermark_for(self, run_date: str) -> str:
        """The 'since last successful run' boundary for a run dated `run_date`.

        A same-night re-run must reproduce identical triggers, so if the last
        recorded run IS run_date we fall back to the watermark that run used,
        not the already-advanced last_run_date.
        """
        info = self.load_last_run()
        last = info.get("last_run_date")
        if last and last < run_date:
            return last
        if info.get("watermark"):
            return info["watermark"]
        return self._default_watermark()

    def edgar_watermark_for(self, ticker: str, run_date: str) -> str:
        """Per-ticker EDGAR check boundary, with the same re-run semantics."""
        rec = self.load_last_run().get("edgar_last_check", {}).get(ticker)
        if not rec:
            return self.watermark_for(run_date)
        if rec.get("date") and rec["date"] < run_date:
            return rec["date"]
        return rec.get("watermark") or self._default_watermark()

    def record_successful_run(self, run_date: str, edgar_ok_tickers: list[str]) -> None:
        info = self.load_last_run()
        if info.get("last_run_date") != run_date:
            info["watermark"] = info.get("last_run_date", self._default_watermark())
        info["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        info["last_run_date"] = run_date
        checks = info.setdefault("edgar_last_check", {})
        for t in edgar_ok_tickers:
            rec = checks.get(t, {})
            if rec.get("date") != run_date:
                rec["watermark"] = rec.get("date", self._default_watermark())
            rec["date"] = run_date
            checks[t] = rec
        self.last_run_path.write_text(json.dumps(info, indent=2))

    # --------------------------------------------------------- alert history

    def load_history(self) -> list[dict]:
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("skipping corrupt history line: %r", line[:80])
        return out

    def seen_dedupe_keys(self) -> set[str]:
        return {h["dedupe_key"] for h in self.load_history() if "dedupe_key" in h}

    def append_alerts(self, alerts: list[dict]) -> None:
        """Append fired triggers, skipping dedupe_keys already recorded (idempotent re-runs)."""
        seen = self.seen_dedupe_keys()
        new = [a for a in alerts if a["dedupe_key"] not in seen]
        if not new:
            return
        with self.history_path.open("a") as f:
            for a in new:
                f.write(json.dumps(a) + "\n")

    def last_fired_date(self, ticker: str, trigger_type: str, before: str | None = None) -> str | None:
        """Most recent history date for ticker+type, optionally excluding `before` (today)."""
        dates = [
            h["date"]
            for h in self.load_history()
            if h.get("ticker") == ticker and h.get("type") == trigger_type
            and (before is None or h.get("date") != before)
        ]
        return max(dates) if dates else None

    def in_cooldown(self, ticker: str, trigger_type: str, as_of: str, cooldown_days: int) -> bool:
        last = self.last_fired_date(ticker, trigger_type, before=as_of)
        if not last:
            return False
        delta = date.fromisoformat(as_of) - date.fromisoformat(last)
        return delta.days < cooldown_days

    # -------------------------------------------------------------- streaks

    def _load_streaks(self) -> dict:
        if self.streaks_path.exists():
            return json.loads(self.streaks_path.read_text())
        return {}

    def record_extreme(self, ticker: str, kind: str, as_of: str) -> int:
        """Count a new 52w high/low occurrence (fired OR suppressed by cooldown).

        Returns the running count of occurrences for the current calendar month,
        so the digest can say "3rd new high this month". Idempotent per day.
        """
        streaks = self._load_streaks()
        month = as_of[:7]
        rec = streaks.setdefault(ticker, {}).setdefault(kind, {"month": month, "count": 0, "dates": []})
        if rec.get("month") != month:
            rec.update({"month": month, "count": 0, "dates": []})
        if as_of not in rec["dates"]:
            rec["dates"].append(as_of)
            rec["count"] += 1
        self.streaks_path.write_text(json.dumps(streaks, indent=2))
        return rec["count"]
