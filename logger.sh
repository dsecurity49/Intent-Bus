#!/bin/bash

if [ ! -f ~/.apikey ]; then
  echo "[ERROR] ~/.apikey not found. Create it with: echo 'your_key' > ~/.apikey"
  exit 1
fi

API_KEY=$(cat ~/.apikey)
BUS_URL="https://dsecurity.pythonanywhere.com"
RESPONSE_FILE="$TMPDIR/logger_response.json"

echo "[LOGGER] Booting up. Listening for 'log_event' intents..."

while true; do
  HTTP_STATUS=$(curl -s -o "$RESPONSE_FILE" -w "%{http_code}" -X POST \
    "$BUS_URL/claim?goal=log_event" \
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
  TEXT=$(echo $RESPONSE | jq -r '.payload.text')

  echo "[LOGGER] Claimed Intent $ID. Writing to bus.log..."
  echo "$(date): $TEXT" >> bus.log

  curl -s -X POST "$BUS_URL/fulfill/$ID" \
    -H "X-API-Key: $API_KEY" > /dev/null

  echo "[LOGGER] Intent $ID logged and fulfilled."
done
