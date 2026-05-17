#!/bin/bash

#Intent bus | Termux Worker

API_KEY_FILE="$HOME/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"

GOAL="send_notification"
NAMESPACE="default"

WORKER_ID="termux-phone-1"
CAPABILITIES="termux,notify"

SLEEP_TIME=2
ERROR_BACKOFF=5
MAX_BACKOFF=60
MAX_MSG_LEN=200

# --- Dependency Checks ---
command -v jq >/dev/null 2>&1 || { echo "[!] jq required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "[!] curl required"; exit 1; }
command -v termux-notification >/dev/null 2>&1 || { echo "[!] termux-api required"; exit 1; }

# --- API Key Setup ---
if [ ! -f "$API_KEY_FILE" ]; then
  echo "[!] Missing API key file: $API_KEY_FILE"
  exit 1
fi

chmod 600 "$API_KEY_FILE"
API_KEY=$(cat "$API_KEY_FILE")

if [ -z "$API_KEY" ]; then
  echo "[!] API key is empty"
  exit 1
fi

report_status() {
  local endpoint="$1" id="$2" payload="$3"
  local attempt=0 max=3 delay=2
  while [ $attempt -lt $max ]; do
    if curl -s --max-time 10 -X POST "$BASE_URL/$endpoint/$id" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "$payload" >/dev/null; then
      return 0
    fi
    attempt=$((attempt + 1))
    [ $attempt -lt $max ] && sleep $delay
    delay=$((delay * 2))
  done
  echo "[!] Failed to report $endpoint for $id after $max attempts"
  return 1
}

echo "Intent Bus Worker started"
echo "Listening: $NAMESPACE/$GOAL"

trap "echo 'Shutdown'; exit 0" INT TERM

while true; do
  # --- Claim Job ---
  HTTP_RESPONSE=$(curl -s --max-time 10 --connect-timeout 5 \
    -w "\n%{http_code}" \
    -X POST \
    "$BASE_URL/claim?goal=$GOAL&namespace=$NAMESPACE" \
    -H "X-API-KEY: $API_KEY" \
    -H "X-Worker-ID: $WORKER_ID" \
    -H "X-Worker-Capabilities: $CAPABILITIES")

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1 | tr -d '\r')

  # --- Idle (No Jobs) ---
  if [ "$STATUS" = "204" ]; then
    sleep "$SLEEP_TIME"
    continue
  fi

  # --- Error Handling ---
  if [ "$STATUS" != "200" ]; then
    echo "[!] HTTP $STATUS"
    [ -n "$BODY" ] && echo "$BODY"
    sleep "$ERROR_BACKOFF"
    ERROR_BACKOFF=$((ERROR_BACKOFF * 2))
    [ "$ERROR_BACKOFF" -gt "$MAX_BACKOFF" ] && ERROR_BACKOFF=$MAX_BACKOFF
    continue
  fi

  # --- JSON Validation ---
  echo "$BODY" | jq -e . >/dev/null 2>&1 || {
    echo "[!] Invalid JSON"
    sleep "$ERROR_BACKOFF"
    continue
  }

  ID=$(echo "$BODY" | jq -r '.id // empty')
  MSG=$(echo "$BODY" | jq -r '.payload.message // empty')

  if [ -z "$ID" ] || [ -z "$MSG" ] || [ "$MSG" = "null" ]; then
    echo "[!] Missing fields"
    sleep "$ERROR_BACKOFF"
    continue
  fi

  # --- Sanitize Output ---
  MSG=$(printf "%s" "$MSG" | tr -d '\r\n\t' | cut -c1-$MAX_MSG_LEN)

  echo "[$(date +%T)] $ID -> $MSG"

  # --- Execute Action ---
  if timeout 5 termux-notification --title "Intent Bus" --content "$MSG"; then
    report_status "fulfill" "$ID" '{"result":"delivered","result_type":"text"}'
    echo "   -> fulfilled"
    ERROR_BACKOFF=5
  else
    report_status "fail" "$ID" '{"error":"Notification failed"}'
    echo "   -> failed"
    sleep "$ERROR_BACKOFF"
  fi

  sleep "$SLEEP_TIME"
done
