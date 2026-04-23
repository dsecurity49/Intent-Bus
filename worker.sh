#!/bin/bash

if [ ! -f ~/.apikey ]; then
  echo "[ERROR] ~/.apikey not found. Create it with: echo 'your_key' > ~/.apikey"
  exit 1
fi

API_KEY=$(cat ~/.apikey)
BUS_URL="https://dsecurity.pythonanywhere.com"
RESPONSE_FILE="$TMPDIR/worker_response.json"

echo "[WORKER] Booting up. Listening for 'send_notification' intents..."

while true; do
  HTTP_STATUS=$(curl -s -o "$RESPONSE_FILE" -w "%{http_code}" -X POST \
    "$BUS_URL/claim?goal=send_notification" \
    -H "X-API-Key: $API_KEY")

  if [ "$HTTP_STATUS" -eq 204 ]; then
    sleep 10
    continue
  fi

  if [ "$HTTP_STATUS" -ne 200 ]; then
    echo "[ERROR] Server returned $HTTP_STATUS. Check your API key."
    exit 1
  fi

  RESPONSE=$(cat "$RESPONSE_FILE")
  ID=$(echo $RESPONSE | jq -r '.id')
  MESSAGE=$(echo $RESPONSE | jq -r '.payload.message')

  echo "[WORKER] Claimed Intent $ID. Buzzing phone..."
  termux-notification --title "System Update" --content "$MESSAGE"

  curl -s -X POST "$BUS_URL/fulfill/$ID" \
    -H "X-API-Key: $API_KEY" > /dev/null

  echo "[WORKER] Intent $ID fulfilled and closed."
done
