#!/bin/bash

#Intent Bus | Standard Auth Logging Worker

API_KEY_FILE="$HOME/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"

GOAL="log_event"
NAMESPACE="default"

WORKER_ID="termux-log-worker-1"
CAPABILITIES="termux,log"

LOG_FILE="bus_logs.txt"

SLEEP_TIME=5
ERROR_BACKOFF=5
MAX_BACKOFF=60

# --- Dependencies ---
command -v jq >/dev/null 2>&1 || { echo "[!] jq required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "[!] curl required"; exit 1; }

# --- Auth ---
if [ ! -f "$API_KEY_FILE" ]; then
  echo "[!] Missing API key file: $API_KEY_FILE"
  exit 1
fi

chmod 600 "$API_KEY_FILE"
API_KEY=$(cat "$API_KEY_FILE")
[ -z "$API_KEY" ] && { echo "[!] API key is empty"; exit 1; }

touch "$LOG_FILE" || { echo "[!] Cannot write log file"; exit 1; }

report_status() {
  local endpoint="$1" id="$2" payload="$3"
  local attempt=0 max=3 delay=2
  while [ $attempt -lt $max ]; do
    if curl -sS --max-time 10 -X POST "$BASE_URL/$endpoint/$id" \
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

echo "Intent Bus Logging Worker v7.5 started"
echo "Logging to: $LOG_FILE"

trap "echo 'Shutdown'; exit 0" INT TERM

while true; do
  RESPONSE=$(curl -sS --max-time 10 --connect-timeout 5 \
    -D - \
    -w "\n__HTTP_CODE__:%{http_code}" \
    -X POST \
    "$BASE_URL/claim?goal=$GOAL&namespace=$NAMESPACE" \
    -H "X-API-KEY: $API_KEY" \
    -H "X-Worker-ID: $WORKER_ID" \
    -H "X-Worker-Capabilities: $CAPABILITIES")

  STATUS=$(printf "%s" "$RESPONSE" | tr -d '\r' | grep "__HTTP_CODE__:" | cut -d: -f2)
  RAW=$(printf "%s" "$RESPONSE" | sed '/__HTTP_CODE__/d')

  # --- Retry-After (robust parsing) ---
  RETRY_AFTER=$(printf "%s" "$RAW" \
    | awk -F': *' 'tolower($1) ~ /retry-after/ {gsub(/[^0-9]/,"",$2); print $2}' \
    | head -n1)

  if [ "$STATUS" = "204" ]; then
    sleep "${RETRY_AFTER:-$SLEEP_TIME}"
    continue
  fi

  if [ "$STATUS" != "200" ]; then
    echo "[!] HTTP $STATUS"
    sleep "$ERROR_BACKOFF"
    ERROR_BACKOFF=$((ERROR_BACKOFF * 2))
    [ "$ERROR_BACKOFF" -gt "$MAX_BACKOFF" ] && ERROR_BACKOFF=$MAX_BACKOFF
    continue
  fi

  # --- Extract JSON safely (Production Hardened) ---
  BODY=$(printf "%s" "$RAW" | awk 'BEGIN{found=0} /^[[:space:]]*\{/{found=1} found' | tr -d '\r')

  [ -z "$BODY" ] && { 
    echo "[!] Empty response body"
    sleep "$ERROR_BACKOFF"
    continue 
  }

  echo "$BODY" | jq -e . >/dev/null 2>&1 || {
    echo "[!] Invalid JSON received"
    sleep "$ERROR_BACKOFF"
    continue
  }

  ID=$(echo "$BODY" | jq -r '.id // empty')
  PAYLOAD=$(echo "$BODY" | jq -c '.payload // {}')

  if [ -z "$ID" ]; then
    echo "[!] Missing job ID"
    sleep "$ERROR_BACKOFF"
    continue
  fi

  TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
  LOG_LINE="[$TIMESTAMP] ID: $ID | DATA: $PAYLOAD"

  if printf "%s\n" "$LOG_LINE" >> "$LOG_FILE"; then
    report_status "fulfill" "$ID" '{"result":"logged","result_type":"text"}'
    echo "[$(date +%T)] Logged job $ID"
    ERROR_BACKOFF=5
  else
    report_status "fail" "$ID" '{"error":"Failed to write log file"}'
    echo "[!] Failed log for $ID"
    sleep "$ERROR_BACKOFF"
  fi

  sleep "$SLEEP_TIME"
done
