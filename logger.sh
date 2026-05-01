#!/bin/bash

# Intent Bus | Standard Auth Logging Worker (Hardened)

API_KEY_FILE="$HOME/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"
GOAL="log_event"
LOG_FILE="bus_logs.txt"

# --- Dependency Checks ---
command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

# --- API Key Check ---
if [ ! -f "$API_KEY_FILE" ]; then
  echo "[!] Missing API key file: $API_KEY_FILE"
  exit 1
fi

API_KEY=$(cat "$API_KEY_FILE")
if [ -z "$API_KEY" ]; then
  echo "[!] API key is empty"
  exit 1
fi

# --- Setup ---
touch "$LOG_FILE" || { echo "[!] Cannot write to log file"; exit 1; }

echo "DSECURITY // LOGGING WORKER STARTED"
echo "Logging to: $LOG_FILE"

SLEEP_TIME=5
ERROR_BACKOFF=5
MAX_BACKOFF=60

# Graceful shutdown
trap "echo 'Shutting down worker...'; exit 0" SIGINT SIGTERM

while true; do
  # 1. Claim intent (with timeout)
  HTTP_RESPONSE=$(curl -s --max-time 10 --connect-timeout 5 -w "\n%{http_code}" -X POST \
    "$BASE_URL/claim?goal=$GOAL" \
    -H "X-API-KEY: $API_KEY")

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1)

  if [ "$STATUS" = "204" ]; then
    ERROR_BACKOFF=5
    sleep "$SLEEP_TIME"
    continue
  fi

  if [ "$STATUS" != "200" ]; then
    echo "[!] Server error: HTTP $STATUS"
    sleep "$ERROR_BACKOFF"
    ERROR_BACKOFF=$((ERROR_BACKOFF * 2))
    [ "$ERROR_BACKOFF" -gt "$MAX_BACKOFF" ] && ERROR_BACKOFF=$MAX_BACKOFF
    continue
  fi

  # Validate JSON
  echo "$BODY" | jq . >/dev/null 2>&1
  if [ $? -ne 0 ]; then
    echo "[!] Invalid JSON received"
    sleep "$ERROR_BACKOFF"
    continue
  fi

  # 2. Extract data
  ID=$(echo "$BODY" | jq -r '.id // empty')
  PAYLOAD=$(echo "$BODY" | jq -c '.payload // {}')

  if [ -z "$ID" ]; then
    echo "[!] Missing job ID"
    sleep "$ERROR_BACKOFF"
    continue
  fi

  TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
  LOG_LINE="[$TIMESTAMP] ID: $ID | DATA: $PAYLOAD"

  # 3. Safe write
  printf "%s\n" "$LOG_LINE" >> "$LOG_FILE"
  WRITE_STATUS=$?

  if [ $WRITE_STATUS -eq 0 ]; then
    # 4. Fulfill (with timeout)
    curl -s --max-time 10 -X POST "$BASE_URL/fulfill/$ID" \
      -H "X-API-KEY: $API_KEY" > /dev/null

    echo "[$(date +%T)] Logged job $ID"
    ERROR_BACKOFF=5
  else
    # 5. Fail
    curl -s --max-time 10 -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"error\": \"Failed to write log file\"}" > /dev/null

    echo "[!] Failed to write log for $ID"
    sleep "$ERROR_BACKOFF"
  fi

  sleep "$SLEEP_TIME"
done
