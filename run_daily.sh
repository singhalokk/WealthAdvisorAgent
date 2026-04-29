#!/usr/bin/env zsh
# Portfolio analysis runner.
# Runs if: today is Monday, Wednesday, or Friday
#       OR portfolio.csv was modified since the last analysis.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
PORTFOLIO="$SCRIPT_DIR/portfolio.csv"
DATE=$(date +%Y-%m-%d)
LOG_FILE="$OUTPUT_DIR/${DATE}.log"
JSON_FILE="$OUTPUT_DIR/${DATE}.json"
SKIP_LOG="$OUTPUT_DIR/${DATE}.skipped"

mkdir -p "$OUTPUT_DIR"

# ── Decision: should we run today? ───────────────────────────────────────────

# Check if today is Mon (1), Wed (3), or Fri (5)
DAY_OF_WEEK=$(date +%u)   # 1=Mon … 7=Sun
IS_SCHEDULED_DAY=false
[[ "$DAY_OF_WEEK" == "1" || "$DAY_OF_WEEK" == "3" || "$DAY_OF_WEEK" == "5" ]] && IS_SCHEDULED_DAY=true

# Check if portfolio.csv was modified more recently than the last JSON output
PORTFOLIO_UPDATED=false
LAST_JSON=$(ls -t "$OUTPUT_DIR"/*.json 2>/dev/null | head -1)
if [[ -f "$PORTFOLIO" && -n "$LAST_JSON" ]]; then
  # portfolio.csv is newer than the most recent JSON → user updated their holdings
  [[ "$PORTFOLIO" -nt "$LAST_JSON" ]] && PORTFOLIO_UPDATED=true
elif [[ -f "$PORTFOLIO" && -z "$LAST_JSON" ]]; then
  # No previous run exists at all
  PORTFOLIO_UPDATED=true
fi

if [[ "$IS_SCHEDULED_DAY" == "false" && "$PORTFOLIO_UPDATED" == "false" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping — not Mon/Wed/Fri and portfolio.csv unchanged." \
    | tee "$SKIP_LOG"
  exit 0
fi

if [[ "$PORTFOLIO_UPDATED" == "true" ]]; then
  REASON="portfolio.csv updated since last run"
else
  REASON="scheduled day ($(date +%A))"
fi

# ── Run analysis ──────────────────────────────────────────────────────────────

# Load API key from shell profile if not already in environment
if [[ -z "$ANTHROPIC_API_KEY" ]]; then
  source ~/.zshrc 2>/dev/null || true
fi

cd "$SCRIPT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting analysis — reason: $REASON" | tee "$LOG_FILE"

python3 main.py portfolio.csv --output "$JSON_FILE" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done. Results saved to $JSON_FILE" | tee -a "$LOG_FILE"

python3 "$SCRIPT_DIR/generate_report.py" "$JSON_FILE" >> "$LOG_FILE" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] HTML report generated." | tee -a "$LOG_FILE"
