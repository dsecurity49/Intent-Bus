#!/bin/bash

# Intent Bus | Standard Auth Logging Worker

API_KEY=$(cat ~/.apikey)
BASE_URL="https://dsecurity.pythonanywhere.com"
GOAL="log_event"
LOG_FILE="bus_logs.txt"

# --- Dependency Checks ---
command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

# --- Setup ---
touch "$LOG_FILE" || { echo "Cannot write to log file"; exit 1; }

echo "DSECURITY // LOGGING WORKER STARTED"
echo "Logging to: $LOG_FILE"

SLEEP_TIME=5

# Graceful shutdown
trap "echo 'Shutting down worker...'; exit 0" SIGINT SIGTERM

while true; do
  # 1. Claim intent
  HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "$BASE_URL/claim?goal=$GOAL" \
    -H "X-API-KEY: $API_KEY")

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1)

  if [ "$STATUS" = "204" ]; then
    sleep $SLEEP_TIME
    continue
  fi

  if [ "$STATUS" != "200" ]; then
    echo "[!] Server error: HTTP $STATUS"
    sleep 10
    continue
  fi

  # Validate JSON
  echo "$BODY" | jq . >/dev/null 2>&1
  if [ $? -ne 0 ]; then
    echo "[!] Invalid JSON received"
    sleep 5
    continue
  fi

  # 2. Extract data
  ID=$(echo "$BODY" | jq -r '.id // empty')
  PAYLOAD=$(echo "$BODY" | jq -c '.payload // {}')

  if [ -z "$ID" ]; then
    echo "[!] Missing job ID"
    sleep 3
    continue
  fi

  TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
  LOG_LINE="[$TIMESTAMP] ID: $ID | DATA: $PAYLOAD"

  # 3. Write safely
  echo "$LOG_LINE" >> "$LOG_FILE"
  WRITE_STATUS=$?

  if [ $WRITE_STATUS -eq 0 ]; then
    # 4. Fulfill
    curl -s -X POST "$BASE_URL/fulfill/$ID" \
      -H "X-API-KEY: $API_KEY" > /dev/null

    echo "[$(date +%T)] Logged job $ID"
  else
    # 5. Fail if logging failed
    curl -s -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"error\": \"Failed to write log file\"}"

    echo "[!] Failed to write log for $ID"
  fi

  sleep $SLEEP_TIME
done
