"""Pydantic schemas for the data contracts in spec §6."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TriggerType = Literal[
    "price_move", "high_52w", "low_52w", "earnings_surprise", "guidance_candidate"
]

MoveClassification = Literal["MARKET/SECTOR-DRIVEN", "IDIOSYNCRATIC"]


class MoveWindow(BaseModel):
    window: Literal["1d", "1w", "1m"]
    stock_return_pct: float
    etf: str
    etf_return_pct: float
    relative_move_pct: float
    pct_explained: float = Field(ge=0.0, le=100.0)
    classification: MoveClassification


class Trigger(BaseModel):
    ticker: str
    type: TriggerType
    windows: list[MoveWindow] = Field(default_factory=list)
    priority: int = Field(ge=1)
    detail: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str


class DataIssue(BaseModel):
    ticker: str
    source: str
    error: str


class TriggersFile(BaseModel):
    run_date: str
    as_of_trading_day: str
    triggers: list[Trigger]
    data_issues: list[DataIssue] = Field(default_factory=list)


class GuidanceClassification(BaseModel):
    status: Literal[
        "RAISED", "LOWERED", "REAFFIRMED", "WITHDRAWN", "INITIATED", "NO_GUIDANCE_CONTENT"
    ]
    before: Optional[str] = None
    after: Optional[str] = None


class Source(BaseModel):
    title: str
    url: Optional[str] = None
    date: Optional[str] = None


class Investigation(BaseModel):
    cause_hypothesis: str
    confidence: Literal["high", "medium", "low"]
    cause_unknown: bool = False
    guidance_classification: Optional[GuidanceClassification] = None
    sources: list[Source] = Field(default_factory=list)
