#!/bin/bash
set -uo pipefail

# Intent Bus | Generic Worker v7.6
# Cross-platform polling worker for Intent Bus

API_KEY_FILE="$HOME/.apikey"
BASE_URL="https://dsecurity.pythonanywhere.com"

GOAL="${GOAL:-generic_task}"
NAMESPACE="${NAMESPACE:-default}"

WORKER_ID="${WORKER_ID:-worker-$(hostname)}"
CAPABILITIES="${CAPABILITIES:-generic}"

SLEEP_TIME=5
ERROR_BACKOFF=2
MAX_BACKOFF=60

# --- Dependency Checks ---
command -v curl >/dev/null 2>&1 || { echo "[!] curl required"; exit 1; }

# --- API Key Setup ---
if [ ! -f "$API_KEY_FILE" ]; then
    echo "[!] Missing API key file: $API_KEY_FILE"
    exit 1
fi

if [ -L "$API_KEY_FILE" ]; then
    echo "[!] API key file is a symlink -- refusing to read"
    exit 1
fi

# POSIX compliant ownership check
if [ "$(ls -nd "$API_KEY_FILE" | awk '{print $3}')" != "$(id -u)" ]; then
    echo "[!] API key file not owned by current user"
    exit 1
fi

API_KEY=$(cat "$API_KEY_FILE")
if [ -z "$API_KEY" ]; then
    echo "[!] API key is empty"
    exit 1
fi

echo "Intent Bus Worker v7.6 started (GOAL=$GOAL, NAMESPACE=$NAMESPACE)"

# --- Retry Parsing ---
parse_retry_after() {
    local retry="${1:-}"
    if [[ "$retry" =~ ^[0-9]+$ ]]; then
        echo "$retry"
    else
        echo "$SLEEP_TIME"
    fi
}

# --- Main Loop ---
while true; do
    # Claim an intent (capturing headers)
    CLAIM_RESPONSE=$(curl -sS --max-time 15 --connect-timeout 5 -D - \
        -w "\n__HTTP_CODE__:%{http_code}" \
        -X POST "$BASE_URL/claim?goal=$GOAL&namespace=$NAMESPACE" \
        -H "X-API-KEY: $API_KEY" \
        -H "X-Worker-ID: $WORKER_ID" \
        -H "X-Worker-Capabilities: $CAPABILITIES")

    HTTP_CODE=$(printf "%s" "$CLAIM_RESPONSE" | grep "__HTTP_CODE__:" | cut -d: -f2 | tr -d '\r')
    RAW_OUTPUT=$(printf "%s" "$CLAIM_RESPONSE" | sed '/__HTTP_CODE__/d')

    # 204 = no intents available
    if [ "$HTTP_CODE" = "204" ]; then
        # POSIX compliant awk extraction (no grep -P)
        RETRY_AFTER=$(printf "%s" "$RAW_OUTPUT" | awk -F': *' 'tolower($1) ~ /retry-after/ {gsub(/[^0-9]/,"",$2); print $2}' | head -n1)
        RETRY_AFTER=$(parse_retry_after "$RETRY_AFTER")
        sleep "$RETRY_AFTER"
        continue
    fi

    # Check for error responses
    if [ "$HTTP_CODE" != "200" ]; then
        echo "[!] Claim failed (HTTP $HTTP_CODE)"
        sleep "$ERROR_BACKOFF"
        continue
    fi

    BODY=$(printf "%s" "$RAW_OUTPUT" | awk 'BEGIN{found=0} /^[[:space:]]*\{/{found=1} found' | tr -d '\r')

    # Parse intent (fallback to awk if jq is missing to remain POSIX compliant)
    if command -v jq >/dev/null 2>&1; then
        INTENT_ID=$(echo "$BODY" | jq -r '.id // empty' 2>/dev/null || echo "")
        PAYLOAD=$(echo "$BODY" | jq -r '.payload // empty' 2>/dev/null || echo "")
    else
        # POSIX awk-based parsing fallback
        INTENT_ID=$(echo "$BODY" | awk -F'"id" *: *"' '{print $2}' | cut -d'"' -f1 | head -n1)
        PAYLOAD="$BODY"
    fi

    if [ -z "$INTENT_ID" ] || [ -z "$PAYLOAD" ]; then
        echo "[!] Failed to parse intent response"
        sleep "$ERROR_BACKOFF"
        continue
    fi

    echo "Claimed intent: $INTENT_ID"

    # --- Execute Task ---
    echo "Processing payload: $PAYLOAD"
    
    RESULT="Task executed successfully"
    RESULT_TYPE="text"
    SUCCESS=true

    # --- Report Result with Retry ---
    if [ "$SUCCESS" = true ]; then
        ATTEMPT=0
        MAX_ATTEMPTS=3
        BACKOFF=$ERROR_BACKOFF

        while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
            FULFILL_RESPONSE=$(curl -s --max-time 10 -X POST "$BASE_URL/fulfill/$INTENT_ID" \
                -H "X-API-KEY: $API_KEY" \
                -H "Content-Type: application/json" \
                -d "{\"result\": \"$RESULT\", \"result_type\": \"$RESULT_TYPE\"}" \
                -w "\n%{http_code}")

            HTTP_CODE=$(echo "$FULFILL_RESPONSE" | tail -n 1 | tr -d '\r')

            if [ "$HTTP_CODE" = "200" ]; then
                echo "✓ Intent $INTENT_ID fulfilled"
                break
            fi

            ATTEMPT=$((ATTEMPT + 1))
            if [ $ATTEMPT -lt $MAX_ATTEMPTS ]; then
                sleep "$BACKOFF"
                BACKOFF=$((BACKOFF * 2))
                if [ $BACKOFF -gt $MAX_BACKOFF ]; then
                    BACKOFF=$MAX_BACKOFF
                fi
            fi
        done
    else
        # Report failure
        ATTEMPT=0
        MAX_ATTEMPTS=3
        BACKOFF=$ERROR_BACKOFF

        while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
            FAIL_RESPONSE=$(curl -s --max-time 10 -X POST "$BASE_URL/fail/$INTENT_ID" \
                -H "X-API-KEY: $API_KEY" \
                -H "Content-Type: application/json" \
                -d '{"error": "task_failed"}' \
                -w "\n%{http_code}")

            HTTP_CODE=$(echo "$FAIL_RESPONSE" | tail -n 1 | tr -d '\r')

            if [ "$HTTP_CODE" = "200" ]; then
                echo "✓ Intent $INTENT_ID failed (retrying)"
                break
            fi

            ATTEMPT=$((ATTEMPT + 1))
            if [ $ATTEMPT -lt $MAX_ATTEMPTS ]; then
                sleep "$BACKOFF"
                BACKOFF=$((BACKOFF * 2))
                if [ $BACKOFF -gt $MAX_BACKOFF ]; then
                    BACKOFF=$MAX_BACKOFF
                fi
            fi
        done
    fi

    sleep "$SLEEP_TIME"
done
