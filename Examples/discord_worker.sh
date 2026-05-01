#!/usr/bin/env bash

# Intent Bus | Discord Webhook Worker (Hardened)

set -uo pipefail

API_KEY_FILE="${HOME}/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"
GOAL="discord_alert"

SLEEP_IDLE=5
SLEEP_ERROR=10
SLEEP_SUCCESS=2

MAX_CONTENT_LENGTH=1900
CURL_TIMEOUT=10

command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

if [[ ! -f "$API_KEY_FILE" ]]; then
  echo "[!] API key not found"
  exit 1
fi

API_KEY=$(cat "$API_KEY_FILE")

echo "DSECURITY // DISCORD WORKER STARTED"

trap "echo 'Shutting down worker...'; exit 0" SIGINT SIGTERM

while true; do

  HTTP_RESPONSE=$(curl -s --max-time $CURL_TIMEOUT -w "\n%{http_code}" \
    -X POST "$BASE_URL/claim?goal=$GOAL" \
    -H "X-API-KEY: $API_KEY") || true

  BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
  STATUS=$(echo "$HTTP_RESPONSE" | tail -n1)
  STATUS=${STATUS:-000}

  if [[ "$STATUS" == "204" ]]; then
    sleep $SLEEP_IDLE
    continue
  fi

  if [[ "$STATUS" != "200" ]]; then
    echo "[!] Server error: $STATUS"
    sleep $SLEEP_ERROR
    continue
  fi

  echo "$BODY" | jq . >/dev/null 2>&1 || {
    echo "[!] Invalid JSON"
    sleep $SLEEP_ERROR
    continue
  }

  ID=$(echo "$BODY" | jq -r '.id // empty')
  WEBHOOK_URL=$(echo "$BODY" | jq -r '.payload.webhook_url // empty')
  CONTENT=$(echo "$BODY" | jq -r '.payload.content // empty')

  if [[ -z "$ID" || -z "$WEBHOOK_URL" || -z "$CONTENT" ]]; then
    echo "[!] Invalid payload"

    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"error":"Invalid payload"}' > /dev/null || echo "[!] Failed to report fail"

    sleep $SLEEP_ERROR
    continue
  fi

  # --- Strict URL validation ---
  if [[ "$WEBHOOK_URL" != https://discord.com/api/webhooks/* ]]; then
    echo "[!] Rejected webhook"

    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"error":"Invalid webhook"}' > /dev/null || echo "[!] Failed to report fail"

    sleep $SLEEP_ERROR
    continue
  fi

  # Extra SSRF protection
  HOST=$(echo "$WEBHOOK_URL" | awk -F/ '{print $3}')
  if [[ "$HOST" != "discord.com" ]]; then
    echo "[!] Host mismatch"

    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"error":"Host validation failed"}' > /dev/null

    sleep $SLEEP_ERROR
    continue
  fi

  # --- Sanitize content ---
  CONTENT=$(echo "$CONTENT" | tr -d '\r\n\t')
  CONTENT=$(echo "$CONTENT" | cut -c1-$MAX_CONTENT_LENGTH)

  JSON_PAYLOAD=$(jq -n --arg content "$CONTENT" '{content: $content}')

  echo "[$(date +%T)] Job $ID"

  DISCORD_STATUS=$(curl -s --max-time $CURL_TIMEOUT \
    -o /dev/null -w "%{http_code}" \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD") || DISCORD_STATUS="000"

  if [[ "$DISCORD_STATUS" =~ ^2 ]]; then
    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fulfill/$ID" \
      -H "X-API-KEY: $API_KEY" > /dev/null || echo "[!] Failed to report fulfill"

    echo "   -> Success"
    sleep $SLEEP_SUCCESS

  elif [[ "$DISCORD_STATUS" == "429" ]]; then
    echo "[!] Discord rate limit hit"
    sleep 15

  else
    curl -s --max-time $CURL_TIMEOUT -X POST "$BASE_URL/fail/$ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"error\":\"Discord HTTP $DISCORD_STATUS\"}" > /dev/null || echo "[!] Failed to report fail"

    echo "   -> Failed ($DISCORD_STATUS)"
    sleep $SLEEP_ERROR
  fi

done
