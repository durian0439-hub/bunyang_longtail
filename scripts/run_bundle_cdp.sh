#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${GPT_WEB_PROFILE:-$ROOT/data/gpt_profiles/gpt_terminal_profile_dev}"
PORT="${GPT_WEB_CDP_PORT:-9333}"
DISPLAY_ID="${GPT_WEB_DISPLAY:-:100}"
DEFAULT_WAIT_FOR_READY="${GPT_WEB_WAIT_FOR_READY_SECONDS:-60}"
DEFAULT_RESPONSE_TIMEOUT="${GPT_WEB_RESPONSE_TIMEOUT_SECONDS:-600}"

args=("$@")

has_arg() {
  local needle="$1"
  for arg in "${args[@]}"; do
    if [[ "$arg" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

python3 "$ROOT/scripts/gpt_web_session.py" start \
  --display "$DISPLAY_ID" \
  --port "$PORT" \
  --profile "$PROFILE"

if has_arg "run-bundle"; then
  if ! has_arg "--image-fallback"; then
    args+=("--image-fallback" "local_canvas")
  fi
  if ! has_arg "--wait-for-ready-seconds"; then
    args+=("--wait-for-ready-seconds" "$DEFAULT_WAIT_FOR_READY")
  fi
  if ! has_arg "--response-timeout-seconds"; then
    args+=("--response-timeout-seconds" "$DEFAULT_RESPONSE_TIMEOUT")
  fi
fi

exec python3 "$ROOT/run.py" "${args[@]}" \
  --executor playwright \
  --cdp-url "http://127.0.0.1:${PORT}" \
  --text-profile "$PROFILE" \
  --image-profile "$PROFILE"
