#!/bin/bash

# Intent Bus | Termux Bash Worker (Hardened+)

API_KEY_FILE="$HOME/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"
GOAL="send_notification"

# --- Dependency Checks ---
command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }
command -v termux-notification >/dev/null 2>&1 || { echo "termux-notification not found"; exit 1; }

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

echo "DSECURITY // TERMUX WORKER STARTED"
echo "Listening for goal: $GOAL"

SLEEP_TIME=2
ERROR_BACKOFF=5
MAX_BACKOFF=60
MAX_MSG_LEN=200   # prevent UI abuse

trap "echo 'Shutting down worker...'; exit 0" SIGINT SIGTERM

while true; do
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

  echo "$BODY" | jq . >/dev/null 2>&1 || {
    echo "[!] Invalid JSON received"
    sleep "$ERROR_BACKOFF"
    continue
  }

  ID=$(echo "$BODY" | jq -r '.id // empty')
  MSG=$(echo "$BODY" | jq -r '.payload.message // empty')

  if [ -z "$ID" ] || [ -z "$MSG" ]; then
    echo "[!] Missing required fields"
    sleep "$ERROR_BACKOFF"
    continue
  fi

  # --- Sanitize message ---
  MSG=$(echo "$MSG" | tr -d '\r\n\t')             # remove control chars
  MSG=$(echo "$MSG" | cut -c1-$MAX_MSG_LEN)       # limit size

  echo "[$(date +%T)] Job $ID: $MSG"

  # --- Execute with timeout ---
  timeout 5 termux-notification --title "Intent Bus" --content "$MSG"
  RESULT=$?

  if [ $RESULT -eq 0 ]; then
    curl -s --max-time 10 -X POST "$BASE_URL/fulfill/$ID" \
      -H "X-API-KEY: $API_KEY" > /dev/null

    if [ $? -ne 0 ]; then
      echo "[!] Failed to report fulfill (network issue)"
    else
      echo "   -> Fulfilled"
    fi

    ERROR_BACKOFF=5

  else
    curl -s --max-time 10 -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"error\": \"Notification execution failed\"}" > /dev/null

    echo "   -> Reported Failure"
    sleep "$ERROR_BACKOFF"
  fi

  sleep "$SLEEP_TIME"
done
