#!/bin/bash

# Intent Bus | Termux Bash Worker (Standard Auth)

API_KEY=$(cat ~/.apikey)
BASE_URL="https://dsecurity.pythonanywhere.com"
GOAL="send_notification"

# --- Dependency Checks ---
command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }
command -v termux-notification >/dev/null 2>&1 || { echo "termux-notification not found"; exit 1; }

echo "DSECURITY // INTENT-BUS WORKER STARTED"
echo "Listening for goal: $GOAL"

SLEEP_TIME=2

while true; do
  # 1. Claim an intent (capture body + status)
  HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "$BASE_URL/claim?goal=$GOAL" \
    -H "X-API-KEY: $API_KEY")

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1)

  # --- Handle response ---
  if [ "$STATUS" = "204" ]; then
    sleep 5
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

  # 2. Extract data safely
  ID=$(echo "$BODY" | jq -r '.id // empty')
  MSG=$(echo "$BODY" | jq -r '.payload.message // empty')

  if [ -z "$ID" ] || [ -z "$MSG" ]; then
    echo "[!] Missing required fields"
    sleep 3
    continue
  fi

  echo "[$(date +%T)] Claimed job $ID: $MSG"

  # 3. Execute task
  termux-notification --title "Intent Bus" --content "$MSG"
  RESULT=$?

  # 4. Report result
  if [ $RESULT -eq 0 ]; then
    curl -s -X POST "$BASE_URL/fulfill/$ID" \
      -H "X-API-KEY: $API_KEY" > /dev/null
    echo "   -> Fulfilled"
  else
    curl -s -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"error\": \"Local execution failed in bash worker\"}"
    echo "   -> Reported Failure"
  fi

  sleep $SLEEP_TIME
done
