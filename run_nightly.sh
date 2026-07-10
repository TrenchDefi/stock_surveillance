#!/usr/bin/env bash
# Nightly surveillance pipeline: scan -> investigate (if triggers) -> digest.
#
# Designed for cron, but safe to run manually at any time:
#   ./run_nightly.sh                      # full pipeline
#   ./run_nightly.sh --skip-investigation # Layer 1 + digest only (no LLM)
#   ./run_nightly.sh --force              # ignore high/low cooldowns
#   ./run_nightly.sh --dry-run            # scan only, print triggers, change nothing
#
# Exit codes: 0 ok (with or without triggers), 1 scan failed.
set -uo pipefail

cd "$(dirname "$0")"

SKIP_INVESTIGATION=0
SCAN_ARGS=()
DIGEST_ARGS=()
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-investigation) SKIP_INVESTIGATION=1 ;;
    --force)              SCAN_ARGS+=("--force") ;;
    --dry-run)            DRY_RUN=1 ;;
    --config)             shift; SCAN_ARGS+=("--config" "$1"); DIGEST_ARGS+=("--config" "$1") ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PYTHON="${PYTHON:-python3}"
TODAY="$(date +%F)"
mkdir -p logs

if [[ "$DRY_RUN" == "1" ]]; then
  exec "$PYTHON" scan.py --dry-run "${SCAN_ARGS[@]+"${SCAN_ARGS[@]}"}"
fi

echo "[$(date '+%F %T')] scan starting" >> "logs/nightly-$TODAY.log"
"$PYTHON" scan.py "${SCAN_ARGS[@]+"${SCAN_ARGS[@]}"}" >> "logs/nightly-$TODAY.log" 2>&1
SCAN_RC=$?

case "$SCAN_RC" in
  0) echo "[$(date '+%F %T')] scan ok: triggers found" >> "logs/nightly-$TODAY.log" ;;
  3) echo "[$(date '+%F %T')] scan ok: no triggers" >> "logs/nightly-$TODAY.log" ;;
  *) echo "[$(date '+%F %T')] scan FAILED (rc=$SCAN_RC)" >> "logs/nightly-$TODAY.log"
     # still try to render whatever exists so a partial run leaves evidence
     "$PYTHON" render_digest.py "${DIGEST_ARGS[@]+"${DIGEST_ARGS[@]}"}" >> "logs/nightly-$TODAY.log" 2>&1 || true
     exit 1 ;;
esac

# Layer 2 — headless Claude investigation, only when triggers exist
if [[ "$SCAN_RC" == "0" && "$SKIP_INVESTIGATION" == "0" ]]; then
  if command -v claude >/dev/null 2>&1; then
    echo "[$(date '+%F %T')] investigation starting" >> "logs/nightly-$TODAY.log"
    # If we're being run from inside a Claude Code session (e.g. a manual run),
    # scrub its session env so the nested CLI authenticates on its own.
    CLAUDE_CMD=(claude)
    if [[ -n "${CLAUDECODE:-}" ]]; then
      CLAUDE_CMD=(env -u ANTHROPIC_BASE_URL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
                      -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION claude)
    fi
    "${CLAUDE_CMD[@]}" -p "$(cat prompts/investigate.md)" \
      --allowedTools "Read" "Write" "Bash(python:*)" "Bash(python3:*)" "Bash(curl:*)" "Bash(date:*)" "WebFetch" "WebSearch" \
      --max-turns 40 \
      --output-format json >> "logs/agent-$TODAY.json" 2>> "logs/nightly-$TODAY.log"
    AGENT_RC=$?
    echo "[$(date '+%F %T')] investigation finished (rc=$AGENT_RC)" >> "logs/nightly-$TODAY.log"
  else
    echo "[$(date '+%F %T')] WARNING: claude CLI not found; skipping investigation" >> "logs/nightly-$TODAY.log"
  fi
fi

# Digest — always rendered from whatever exists
"$PYTHON" render_digest.py "${DIGEST_ARGS[@]+"${DIGEST_ARGS[@]}"}" >> "logs/nightly-$TODAY.log" 2>&1
DIGEST_RC=$?
echo "[$(date '+%F %T')] digest rendered (rc=$DIGEST_RC)" >> "logs/nightly-$TODAY.log"
exit 0
