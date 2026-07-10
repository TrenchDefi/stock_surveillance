"""Shared bootstrap for the Layer 2 helper CLIs — same config, cache, and
rate-limit handling as the scanner."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.edgar_client import EdgarClient  # noqa: E402
from src.fmp_client import FMPClient  # noqa: E402


def load_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    with (PROJECT_ROOT / "config.yaml").open() as f:
        return yaml.safe_load(f)


def make_fmp(cfg: dict) -> FMPClient:
    state_dir = PROJECT_ROOT / cfg["run"]["state_dir"]
    return FMPClient(
        base_url=cfg["fmp"].get("base_url", "https://financialmodelingprep.com/stable"),
        request_delay_seconds=float(cfg["fmp"].get("request_delay_seconds", 0.35)),
        cache_dir=state_dir / "cache" / date.today().isoformat(),
    )


def make_edgar(cfg: dict) -> EdgarClient:
    return EdgarClient(
        user_agent=os.path.expandvars(cfg["edgar"]["user_agent"]),
        max_requests_per_second=int(cfg["edgar"].get("max_requests_per_second", 10)),
    )
