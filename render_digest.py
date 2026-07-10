#!/usr/bin/env python3
"""Render the nightly digest (HTML + Markdown twin) from Layer 1/2 outputs.

Every number shown comes from triggers.json (Layer 1); investigations.json
contributes only prose, classification, and sources.

Usage: python render_digest.py [--date YYYY-MM-DD] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger("digest")

TYPE_LABELS = {
    "price_move": "Price move",
    "high_52w": "New 52-week high",
    "low_52w": "New 52-week low",
    "earnings_surprise": "Earnings surprise",
    "guidance_candidate": "New filing (guidance candidate)",
}


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _fmt_pct(x: float) -> str:
    return f"{x:+.1f}%"


def trigger_facts(t: dict) -> str:
    """Human-readable fact line built ONLY from Layer 1 numbers."""
    d = t.get("detail", {})
    ttype = t["type"]
    if ttype == "price_move":
        parts = []
        for w in t.get("windows", []):
            parts.append(
                f"{w['window']}: {_fmt_pct(w['stock_return_pct'])} vs {w['etf']} "
                f"{_fmt_pct(w['etf_return_pct'])} ({w['classification'].lower()}, "
                f"{w['pct_explained']:.0f}% explained)"
            )
        return "; ".join(parts)
    if ttype in ("high_52w", "low_52w"):
        kind = "high" if ttype == "high_52w" else "low"
        streak = d.get("streak_count_month")
        streak_txt = f" — {_ordinal(streak)} new {kind} this month" if streak and streak > 1 else ""
        return f"Close {d.get('close')} vs prior 52-week {kind} {d.get('prior_52w_extreme')}{streak_txt}"
    if ttype == "earnings_surprise":
        bits = []
        eps = d.get("eps")
        if eps:
            if eps.get("near_zero_estimate"):
                bits.append(
                    f"EPS {eps['actual']} vs est {eps['estimate']} "
                    f"({eps['direction']} by ${abs(eps['delta_usd']):.2f}; near-zero consensus)"
                )
            else:
                bits.append(
                    f"EPS {eps['actual']} vs est {eps['estimate']} "
                    f"({eps['direction']} {eps.get('surprise_pct', 0):.1f}%)"
                )
        rev = d.get("revenue")
        if rev:
            bits.append(
                f"Revenue {rev['actual']:,} vs est {rev['estimate']:,.0f} "
                f"({rev['direction']} {rev['surprise_pct']:.1f}%)"
            )
        return f"Reported {d.get('report_date')}: " + "; ".join(bits)
    if ttype == "guidance_candidate":
        kws = ", ".join(d.get("keyword_hits", []))
        flagged = f" — keywords: {kws}" if kws else ""
        return f"{d.get('form')} filed {d.get('filing_date')} (accession {d.get('accession')}){flagged}"
    return ""


def trigger_eyebrow(t: dict) -> str:
    if t["type"] == "price_move":
        cls = "IDIOSYNCRATIC" if any(
            w["classification"] == "IDIOSYNCRATIC" for w in t.get("windows", [])
        ) else "MARKET/SECTOR-DRIVEN"
        return f"PRICE MOVE · {cls}"
    if t["type"] == "guidance_candidate":
        return "GUIDANCE CANDIDATE · " + ("KEYWORD FLAGGED" if t["detail"].get("keyword_flagged") else "NEW FILING")
    return TYPE_LABELS[t["type"]].upper()


def build_context(run_date: str, triggers_data: dict | None, investigations: dict,
                  watchlist: list[dict]) -> dict:
    names = {w["ticker"]: w.get("name", w["ticker"]) for w in watchlist}
    ctx: dict = {
        "run_date": run_date,
        "tickers_scanned": len(watchlist),
        "scan_failed": triggers_data is None,
        "as_of": (triggers_data or {}).get("as_of_trading_day", "—"),
        "triggers": (triggers_data or {}).get("triggers", []),
        "data_issues": (triggers_data or {}).get("data_issues", []),
        "summary_rows": [], "cards": [], "uninvestigated": [], "reaffirmed": [],
    }

    reaffirmed_tickers = set()
    dropped_tickers = set()
    for ticker, inv in investigations.items():
        gc = inv.get("guidance_classification") or {}
        if gc.get("status") == "REAFFIRMED":
            reaffirmed_tickers.add(ticker)
        elif gc.get("status") == "NO_GUIDANCE_CONTENT":
            dropped_tickers.add(ticker)
            log.info("%s: guidance candidate classified NO_GUIDANCE_CONTENT (dropped)", ticker)

    for t in ctx["triggers"]:
        ticker = t["ticker"]
        facts = trigger_facts(t)
        label = TYPE_LABELS.get(t["type"], t["type"])
        inv = investigations.get(ticker)

        if ticker in reaffirmed_tickers and t["type"] == "guidance_candidate":
            status = "Reaffirmed guidance"
        elif ticker in dropped_tickers and t["type"] == "guidance_candidate":
            status = "No guidance content"
        elif inv:
            status = "Investigated"
        else:
            status = "Not investigated"
        ctx["summary_rows"].append(
            {"ticker": ticker, "label": label, "facts": facts, "status": status}
        )

        if t["type"] == "guidance_candidate" and ticker in (reaffirmed_tickers | dropped_tickers):
            continue  # reaffirmed gets a one-liner; no-content is dropped
        if inv:
            gc = inv.get("guidance_classification") or {}
            guidance_txt = ""
            if gc and gc.get("status"):
                guidance_txt = gc["status"]
                if gc.get("before") or gc.get("after"):
                    guidance_txt += f" — before: {gc.get('before') or 'n/a'}; after: {gc.get('after') or 'n/a'}"
            ctx["cards"].append(
                {
                    "ticker": ticker,
                    "name": names.get(ticker, ticker),
                    "eyebrow": trigger_eyebrow(t),
                    "facts": facts,
                    "hypothesis": inv.get("cause_hypothesis", ""),
                    "confidence": inv.get("confidence", "low"),
                    "cause_unknown": bool(inv.get("cause_unknown")),
                    "guidance": guidance_txt,
                    "sources": inv.get("sources", []),
                }
            )
        else:
            ctx["uninvestigated"].append({"ticker": ticker, "label": label, "facts": facts})

    for ticker in sorted(reaffirmed_tickers):
        gc = investigations[ticker].get("guidance_classification") or {}
        detail = gc.get("after") or ""
        ctx["reaffirmed"].append({"ticker": ticker, "detail": detail})

    return ctx


def send_email(cfg: dict, html: str, subject: str) -> None:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_TO")
    if not host or not to:
        log.warning("email enabled but SMTP_HOST/SMTP_TO not set — skipping")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)
    log.info("digest emailed to %s", to)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    load_dotenv(project_root / ".env")
    with (project_root / args.config).open() as f:
        cfg = yaml.safe_load(f)

    run_dir = project_root / cfg["run"]["state_dir"] / "runs" / args.date
    out_dir = project_root / cfg["run"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    triggers_data = None
    triggers_path = run_dir / "triggers.json"
    if triggers_path.exists():
        triggers_data = json.loads(triggers_path.read_text())
    else:
        log.error("no triggers.json for %s — rendering failure digest", args.date)

    investigations = {}
    inv_path = run_dir / "investigations.json"
    if inv_path.exists():
        try:
            investigations = json.loads(inv_path.read_text())
        except json.JSONDecodeError as exc:
            log.error("investigations.json is invalid JSON (%s) — rendering without it", exc)

    ctx = build_context(args.date, triggers_data, investigations, cfg["watchlist"])

    env = Environment(
        loader=FileSystemLoader(project_root / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("digest.html.j2").render(**ctx)
    md = env.get_template("digest.md.j2").render(**ctx)

    html_path = out_dir / f"{args.date}-digest.html"
    md_path = out_dir / f"{args.date}-digest.md"
    html_path.write_text(html)
    md_path.write_text(md)
    log.info("wrote %s and %s", html_path, md_path)

    if cfg.get("email", {}).get("enabled"):
        n = len(ctx["triggers"])
        send_email(cfg, html, f"Surveillance digest {args.date} — {n} trigger(s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
