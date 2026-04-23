#!/bin/bash
# A ready-to-use worker that relays Intents to a Discord channel.
# Usage: ./discord_worker.sh

if [ ! -f ../.apikey ]; then
  echo "[ERROR] ../.apikey not found. See README."
  exit 1
fi

if [ ! -f .discord_webhook ]; then
  echo "[ERROR] .discord_webhook not found."
  echo "Create it: echo 'https://discord.com/api/webhooks/...' > .discord_webhook"
  exit 1
fi

API_KEY=$(cat ../.apikey)
DISCORD_URL=$(cat .discord_webhook)
BUS_URL="https://dsecurity.pythonanywhere.com"
RESPONSE_FILE="${TMPDIR:-/tmp}/discord_response.json"

echo "[DISCORD WORKER] Listening for 'discord_alert' intents..."

while true; do
  HTTP_STATUS=$(curl -s -o "$RESPONSE_FILE" -w "%{http_code}" -X POST \
    "$BUS_URL/claim?goal=discord_alert" \
    -H "X-API-Key: $API_KEY")

  if [ "$HTTP_STATUS" -eq 204 ]; then
    sleep 5
    continue
  fi

  if [ "$HTTP_STATUS" -ne 200 ]; then
    echo "[ERROR] Server returned $HTTP_STATUS."
    exit 1
  fi

  RESPONSE=$(cat "$RESPONSE_FILE")
  ID=$(echo $RESPONSE | jq -r '.id')
  MESSAGE=$(echo $RESPONSE | jq -r '.payload.message')

  echo "[DISCORD WORKER] Claimed Intent $ID. Pushing to Discord..."
  
  # Safely construct the JSON payload to prevent formatting/injection errors
  BODY=$(jq -nc --arg msg "$MESSAGE" '{"content": $msg}')
  
  # 1. Execute the actual work (Send to Discord)
  curl -s -H "Content-Type: application/json" \
       -d "$BODY" \
       "$DISCORD_URL" > /dev/null
  
  # 2. Fulfill the intent on the Bus
  curl -s -X POST "$BUS_URL/fulfill/$ID" \
    -H "X-API-Key: $API_KEY" > /dev/null

  echo "[DISCORD WORKER] Intent $ID fulfilled."
done
