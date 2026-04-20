#!/bin/bash

# Load API Key from file
if [ -f .apikey ]; then
    API_KEY=$(cat .apikey | tr -d '\n\r ')
else
    echo "[ERROR] .apikey file not found! Create it first."
    exit 1
fi

echo "[WORKER] Booting up. Listening for 'send_notification' intents..."

while true; do
  # Request a claim with the Auth header
  RESPONSE=$(curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=send_notification" \
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

  MESSAGE=$(echo "$RESPONSE" | jq -r '.payload.message')
  
  echo "[WORKER] Claimed Intent $ID. Buzzing phone..."
  termux-notification --title "System Update" --content "$MESSAGE"
  
  # Fulfill the intent with the Auth header
  curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/$ID" \
    -H "X-API-KEY: $API_KEY" > /dev/null
    
  echo "[WORKER] Intent $ID fulfilled and closed."
done
