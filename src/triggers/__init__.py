"""Layer 1 trigger evaluation — deterministic, no LLM involvement.

Priority ranking used across modules (lower = investigated first, per §5.2):
  1  idiosyncratic price move
  2  guidance candidate
  3  earnings surprise
  4  market/sector-driven price move
  5  52-week high / low
"""

PRIORITY_IDIOSYNCRATIC_MOVE = 1
PRIORITY_GUIDANCE = 2
PRIORITY_EARNINGS = 3
PRIORITY_MARKET_MOVE = 4
PRIORITY_HIGH_LOW = 5
