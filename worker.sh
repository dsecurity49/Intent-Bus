#!/bin/bash

API_KEY=$(cat ~/.apikey)
BUS_URL="https://dsecurity.pythonanywhere.com"

echo "[WORKER] Booting up. Listening for 'send_notification' intents..."

while true; do
  HTTP_STATUS=$(curl -s -o /tmp/worker_response.json -w "%{http_code}" -X POST \
    "$BUS_URL/claim?goal=send_notification" \
    -H "X-API-Key: $API_KEY")

  if [ "$HTTP_STATUS" -eq 204 ]; then
    sleep 10
    continue
  fi

  RESPONSE=$(cat /tmp/worker_response.json)
  ID=$(echo $RESPONSE | jq -r '.id')
  MESSAGE=$(echo $RESPONSE | jq -r '.payload.message')

  echo "[WORKER] Claimed Intent $ID. Buzzing phone..."
  termux-notification --title "System Update" --content "$MESSAGE"

  curl -s -X POST "$BUS_URL/fulfill/$ID" \
    -H "X-API-Key: $API_KEY" > /dev/null

  echo "[WORKER] Intent $ID fulfilled and closed."
done
