#!/usr/bin/env bash

# Intent Bus | Discord Webhook Worker (Production Grade)
# Relays 'discord_alert' intents safely to Discord.

set -euo pipefail

API_KEY_FILE="${HOME}/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"
GOAL="discord_alert"

SLEEP_IDLE=5
SLEEP_ERROR=10
SLEEP_SUCCESS=2

MAX_CONTENT_LENGTH=1900
CURL_TIMEOUT=10

# --- Dependency Checks ---
command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

# --- Load API Key ---
if [[ ! -f "$API_KEY_FILE" ]]; then
  echo "[!] API key not found at $API_KEY_FILE"
  exit 1
fi

API_KEY=$(cat "$API_KEY_FILE")

echo "===================================="
echo "DSECURITY // DISCORD RELAY WORKER"
echo "Goal        : $GOAL"
echo "Server      : $BASE_URL"
echo "Mode        : SAFE (validated webhook)"
echo "===================================="

# Graceful shutdown
trap "echo 'Shutting down worker...'; exit 0" SIGINT SIGTERM

while true; do

  # -------------------------------
  # 1. Claim Intent
  # -------------------------------
  HTTP_RESPONSE=$(curl -s --max-time $CURL_TIMEOUT -w "\n%{http_code}" \
    -X POST "$BASE_URL/claim?goal=$GOAL" \
    -H "X-API-KEY: $API_KEY")

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1)

  if [[ "$STATUS" == "204" ]]; then
    sleep $SLEEP_IDLE
    continue
  fi

  if [[ "$STATUS" != "200" ]]; then
    echo "[!] Server error: HTTP $STATUS"
    sleep $SLEEP_ERROR
    continue
  fi

  # -------------------------------
  # 2. Validate JSON
  # -------------------------------
  if ! echo "$BODY" | jq . >/dev/null 2>&1; then
    echo "[!] Invalid JSON received"
    sleep $SLEEP_ERROR
    continue
  fi

  ID=$(echo "$BODY" | jq -r '.id // empty')
  WEBHOOK_URL=$(echo "$BODY" | jq -r '.payload.webhook_url // empty')
  CONTENT=$(echo "$BODY" | jq -r '.payload.content // empty')

  if [[ -z "$ID" || -z "$WEBHOOK_URL" || -z "$CONTENT" ]]; then
    echo "[!] Invalid payload for job $ID"

    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"error":"Missing webhook_url or content"}' > /dev/null

    sleep $SLEEP_ERROR
    continue
  fi

  # -------------------------------
  # 3. Validate Webhook URL
  # -------------------------------
  if [[ "$WEBHOOK_URL" != https://discord.com/api/webhooks/* ]]; then
    echo "[!] Rejected invalid webhook for job $ID"

    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"error":"Invalid webhook URL"}' > /dev/null

    sleep $SLEEP_ERROR
    continue
  fi

  # -------------------------------
  # 4. Prepare Payload
  # -------------------------------
  CONTENT=$(echo "$CONTENT" | cut -c1-$MAX_CONTENT_LENGTH)

  JSON_PAYLOAD=$(jq -n --arg content "$CONTENT" '{content: $content}')

  echo "[$(date +%T)] Relaying job $ID..."

  # -------------------------------
  # 5. Send to Discord
  # -------------------------------
  DISCORD_STATUS=$(curl -s --max-time $CURL_TIMEOUT \
    -o /dev/null -w "%{http_code}" \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD")

  # -------------------------------
  # 6. Fulfill / Fail
  # -------------------------------
  if [[ "$DISCORD_STATUS" =~ ^2 ]]; then
    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fulfill/$ID" \
      -H "X-API-KEY: $API_KEY" > /dev/null

    echo "   -> Success"
    sleep $SLEEP_SUCCESS
  else
    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"error\":\"Discord HTTP $DISCORD_STATUS\"}" > /dev/null

    echo "   -> Failed (HTTP $DISCORD_STATUS)"
    sleep $SLEEP_ERROR
  fi

done
