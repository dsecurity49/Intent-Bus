#!/usr/bin/env bash

# Intent Bus | Discord Webhook Worker (v7.6)
# Relays intent payloads to a Discord channel via webhook.
#
# Payload schema:
#   { "webhook_url": "https://discord.com/api/webhooks/...", "content": "message" }
#
# Security:
#   - Webhook URLs validated against discord.com only (SSRF protection)
#   - Content sanitized and truncated before delivery
#   - All unknown or malformed payloads are explicitly failed

set -uo pipefail

# =========================================================
# CONFIGURATION
# =========================================================

API_KEY_FILE="${HOME}/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"

GOAL="discord_alert"
NAMESPACE="default"

WORKER_ID="discord-worker-1"
CAPABILITIES="discord,webhook"

SLEEP_IDLE=5
SLEEP_ERROR=10
SLEEP_SUCCESS=2
MAX_BACKOFF=60

MAX_CONTENT_LENGTH=1900
CURL_TIMEOUT=10

# =========================================================
# DEPENDENCY CHECKS
# =========================================================

command -v jq   >/dev/null 2>&1 || { echo "[!] jq is required. Install: pkg install jq / apt install jq"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "[!] curl is required."; exit 1; }

# =========================================================
# AUTH
# =========================================================

if [[ ! -f "$API_KEY_FILE" ]]; then
  echo "[!] API key not found at $API_KEY_FILE"
  echo "    Run: echo 'your_key' > ~/.apikey && chmod 600 ~/.apikey"
  exit 1
fi

if [[ -L "$API_KEY_FILE" ]]; then
  echo "[!] API key file is a symlink -- refusing to read"
  exit 1
fi

if [[ "$(ls -nd "$API_KEY_FILE" | awk "{print \$3}")" != "$(id -u)" ]]; then
  echo "[!] API key file not owned by current user"
  exit 1
fi

chmod 600 "$API_KEY_FILE"
API_KEY=$(cat "$API_KEY_FILE")

if [[ -z "$API_KEY" ]]; then
  echo "[!] API key file is empty"
  exit 1
fi

# =========================================================
# HELPERS
# =========================================================

log() {
  echo "[$(date +%T)] $*"
}

report_status() {
  local endpoint="$1" id="$2" payload="$3"
  local attempt=0 max=3 delay=2
  while [ $attempt -lt $max ]; do
    if curl -s --max-time "$CURL_TIMEOUT" -X POST "$BASE_URL/$endpoint/$id" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "$payload" >/dev/null; then
      return 0
    fi
    attempt=$((attempt + 1))
    [ $attempt -lt $max ] && sleep $delay
    delay=$((delay * 2))
  done
  log "[!] Failed to report $endpoint for $id after $max attempts"
  return 1
}

fail_intent() {
  local id="$1"
  local reason="$2"
  report_status "fail" "$id" "$(jq -n --arg r "$reason" '{error: $r}')"
}

fulfill_intent() {
  local id="$1"
  report_status "fulfill" "$id" '{"result":"delivered","result_type":"text"}'
}

# =========================================================
# MAIN LOOP
# =========================================================

log "Discord worker started"
log "Listening: $NAMESPACE/$GOAL | Worker: $WORKER_ID | Caps: $CAPABILITIES"

trap "log 'Shutdown signal received. Exiting.'; exit 0" SIGINT SIGTERM

while true; do

  # --- Claim ---
  HTTP_RESPONSE=$(curl -s --max-time "$CURL_TIMEOUT" \
    -w "\n%{http_code}" \
    -X POST "$BASE_URL/claim?goal=$GOAL&namespace=$NAMESPACE" \
    -H "X-API-KEY: $API_KEY" \
    -H "X-Worker-ID: $WORKER_ID" \
    -H "X-Worker-Capabilities: $CAPABILITIES") || true

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1)
  STATUS="${STATUS:-000}"

  # --- Idle ---
  if [[ "$STATUS" == "204" ]]; then
    sleep "$SLEEP_IDLE"
    continue
  fi

  # --- Server error ---
  if [[ "$STATUS" != "200" ]]; then
    log "[!] Claim returned HTTP $STATUS"
    sleep "$SLEEP_ERROR"
    SLEEP_ERROR=$(( SLEEP_ERROR * 2 ))
    [[ "$SLEEP_ERROR" -gt "$MAX_BACKOFF" ]] && SLEEP_ERROR="$MAX_BACKOFF"
    continue
  fi

  # Reset backoff on successful claim
  SLEEP_ERROR=10

  # --- JSON validation ---
  echo "$BODY" | jq -e . >/dev/null 2>&1 || {
    log "[!] Non-JSON response from /claim"
    sleep "$SLEEP_ERROR"
    continue
  }

  ID=$(echo "$BODY" | jq -r '.id // empty')
  WEBHOOK_URL=$(echo "$BODY" | jq -r '.payload.webhook_url // empty')
  CONTENT=$(echo "$BODY" | jq -r '.payload.content // empty')

  # --- Field validation ---
  if [[ -z "$ID" ]]; then
    log "[!] Missing intent ID in response"
    sleep "$SLEEP_ERROR"
    continue
  fi

  if [[ -z "$WEBHOOK_URL" || -z "$CONTENT" ]]; then
    log "[!] $ID: missing webhook_url or content"
    fail_intent "$ID" "Invalid payload: webhook_url and content are required" || true
    sleep "$SLEEP_ERROR"
    continue
  fi

  # --- Strict URL validation (SSRF protection) ---
  # Only discord.com webhooks are permitted.
  if [[ "$WEBHOOK_URL" != https://discord.com/api/webhooks/* ]]; then
    log "[!] $ID: rejected non-Discord webhook URL"
    fail_intent "$ID" "Forbidden webhook URL: only discord.com webhooks are allowed" || true
    sleep "$SLEEP_ERROR"
    continue
  fi

  # Extra host check to guard against DNS rebinding
  HOST=$(echo "$WEBHOOK_URL" | awk -F/ '{print $3}')
  if [[ "$HOST" != "discord.com" ]]; then
    log "[!] $ID: host mismatch (got: $HOST)"
    fail_intent "$ID" "Host validation failed" || true
    sleep "$SLEEP_ERROR"
    continue
  fi

  # --- Content sanitization ---
  CONTENT=$(printf "%s" "$CONTENT" | tr -d '\r\n\t')
  CONTENT=$(printf "%s" "$CONTENT" | cut -c1-"$MAX_CONTENT_LENGTH")

  if [[ -z "$CONTENT" ]]; then
    log "[!] $ID: content empty after sanitization"
    fail_intent "$ID" "Content was empty after sanitization" || true
    sleep "$SLEEP_ERROR"
    continue
  fi

  JSON_PAYLOAD=$(jq -n --arg content "$CONTENT" '{content: $content}')

  log "Sending $ID → Discord"

  # --- Deliver to Discord ---
  DISCORD_STATUS=$(curl -s --max-time "$CURL_TIMEOUT" \
    -o /dev/null -w "%{http_code}" \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD") || DISCORD_STATUS="000"

  if [[ "$DISCORD_STATUS" =~ ^2 ]]; then
    fulfill_intent "$ID" || true
    log "   → fulfilled ($DISCORD_STATUS)"
    sleep "$SLEEP_SUCCESS"

  elif [[ "$DISCORD_STATUS" == "429" ]]; then
    # Discord rate limit — let the lease expire and requeue naturally
    log "   → Discord rate limited (429). Backing off 15s."
    sleep 15

  elif [[ "$DISCORD_STATUS" == "404" ]]; then
    # Webhook deleted or invalid — permanent failure, no point retrying
    log "   → Webhook not found (404). Failing intent."
    fail_intent "$ID" "Discord webhook returned 404: webhook may have been deleted" || true
    sleep "$SLEEP_ERROR"

  else
    log "   → Discord error ($DISCORD_STATUS). Failing intent."
    fail_intent "$ID" "Discord returned HTTP $DISCORD_STATUS" || true
    sleep "$SLEEP_ERROR"
  fi

done
