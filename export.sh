#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CHECKPOINT="coursera_transcripts_checkpoint.json"
DEFAULT_OUTPUT="exports"

CHECKPOINT="${1:-$DEFAULT_CHECKPOINT}"
OUTPUT="${2:-$DEFAULT_OUTPUT}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Color output (terminal only)
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    _BOLD='\033[1m'
    _GREEN='\033[0;32m'
    _RED='\033[0;31m'
    _RESET='\033[0m'
else
    _BOLD=''
    _GREEN=''
    _RED=''
    _RESET=''
fi

log() { printf "${_BOLD}%s${_RESET}\n" "$1"; }
ok()  { printf "${_GREEN}✓ %s${_RESET}\n" "$1"; }
die() { printf "${_RED}error: %s${_RESET}\n" "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
log "checking environment..."

command -v python3 &>/dev/null \
    || die "python3 not found — install Python 3.10+ and try again"
ok "python3 found ($(python3 --version 2>&1))"

[ -f "$CHECKPOINT" ] \
    || die "checkpoint not found: $CHECKPOINT
  run the scraper first, or pass a path as the first argument:
  ./export.sh path/to/checkpoint.json"
ok "checkpoint found: $CHECKPOINT"

python3 -c "import json, sys; json.load(open(sys.argv[1]))" "$CHECKPOINT" 2>/dev/null \
    || die "checkpoint is not valid JSON: $CHECKPOINT"
ok "checkpoint is valid JSON"

log "creating output directory..."
mkdir -p "$OUTPUT"
[ -w "$OUTPUT" ] || die "output directory is not writable: $OUTPUT"
ok "output directory ready: $OUTPUT"

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
log "exporting transcripts..."
python3 "${SCRIPT_DIR}/export.py" --input "$CHECKPOINT" --output "$OUTPUT"

log "done."
