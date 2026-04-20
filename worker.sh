#!/bin/bash

echo "[WORKER] Booting up. Listening for 'send_notification' intents..."

while true; do
  RESPONSE=$(curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=send_notification")
  
  if echo "$RESPONSE" | grep -q '"status":"empty"'; then
    sleep 10
    continue
  fi
  
  ID=$(echo $RESPONSE | jq -r '.id')
  MESSAGE=$(echo $RESPONSE | jq -r '.payload.message')
  
  echo "[WORKER] Claimed Intent $ID. Buzzing phone..."
  termux-notification --title "System Update" --content "$MESSAGE"
  
  curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/$ID" > /dev/null
  echo "[WORKER] Intent $ID fulfilled and closed."
done
