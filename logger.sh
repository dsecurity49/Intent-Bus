#!/bin/bash

echo "[LOGGER] Booting up. Listening for 'log_event' intents..."

while true; do
  RESPONSE=$(curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=log_event")

  if echo "$RESPONSE" | grep -q '"status":"empty"'; then
    sleep 10
    continue
  fi

  ID=$(echo $RESPONSE | jq -r '.id')
  TEXT=$(echo $RESPONSE | jq -r '.payload.text')
  
  echo "[LOGGER] Claimed Intent $ID. Writing to bus.log..."
  echo "$(date): $TEXT" >> bus.log

  curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/$ID" > /dev/null
  echo "[LOGGER] Intent $ID logged and fulfilled."
done
