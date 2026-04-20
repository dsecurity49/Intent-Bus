#!/bin/bash

# Load API Key from file
if [ -f .apikey ]; then
    API_KEY=$(cat .apikey | tr -d '\n\r ')
else
    echo "[ERROR] .apikey file not found! Create it first."
    exit 1
fi

echo "[LOGGER] Booting up. Listening for 'log_event' intents..."

while true; do
  # Request a claim with the Auth header
  RESPONSE=$(curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=log_event" \
    -H "X-API-KEY: $API_KEY")

  # Check for empty queue
  if echo "$RESPONSE" | grep -q '"status":"empty"'; then
    sleep 10
    continue
  fi

  # Check for unauthorized access
  if echo "$RESPONSE" | grep -q '"error":"Unauthorized"'; then
    echo "[ERROR] Auth Failed. Key in .apikey does not match the server."
    sleep 30
    continue
  fi

  ID=$(echo "$RESPONSE" | jq -r '.id')
  
  # Safety check for null IDs
  if [ "$ID" == "null" ] || [ -z "$ID" ]; then
    sleep 5
    continue
  fi

  TEXT=$(echo "$RESPONSE" | jq -r '.payload.text')
  
  echo "[LOGGER] Claimed Intent $ID. Writing to bus.log..."
  echo "$(date): $TEXT" >> bus.log

  # Fulfill the intent with the Auth header
  curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/$ID" \
    -H "X-API-KEY: $API_KEY" > /dev/null
    
  echo "[LOGGER] Intent $ID logged and fulfilled."
done
