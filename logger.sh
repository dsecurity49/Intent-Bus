#!/bin/sh
set -eu

# Intent Bus | Simple Logging Worker v7.61
# Portable logging worker for Intent Bus

API_KEY_FILE="${HOME}/.apikey"
BASE_URL="${BASE_URL:-https://dsecurity.pythonanywhere.com}"

GOAL="${GOAL:-log_event}"
NAMESPACE="${NAMESPACE:-default}"

WORKER_ID="${WORKER_ID:-log-worker}"
CAPABILITIES="${CAPABILITIES:-logging}"

LOG_FILE="${LOG_FILE:-bus_logs.txt}"

SLEEP_TIME="${SLEEP_TIME:-5}"
ERROR_BACKOFF="${ERROR_BACKOFF:-5}"
MAX_BACKOFF="${MAX_BACKOFF:-60}"

# --- Dependencies ---
command -v curl >/dev/null 2>&1 || {
    echo "[!] curl is required"
    exit 1
}

command -v jq >/dev/null 2>&1 || {
    echo "[!] jq is required"
    exit 1
}

# --- API Key ---
if [ ! -f "$API_KEY_FILE" ]; then
    echo "[!] Missing API key file: $API_KEY_FILE"
    exit 1
fi

if [ -L "$API_KEY_FILE" ]; then
    echo "[!] Refusing to read symlinked API key file"
    exit 1
fi

chmod 600 "$API_KEY_FILE" 2>/dev/null || true

API_KEY=$(cat "$API_KEY_FILE")

if [ -z "$API_KEY" ]; then
    echo "[!] API key is empty"
    exit 1
fi

# --- Log File ---
touch "$LOG_FILE"

chmod 600 "$LOG_FILE" 2>/dev/null || true

echo "Intent Bus Logging Worker v7.61 started"
echo "Goal: $GOAL"
echo "Namespace: $NAMESPACE"
echo "Log file: $LOG_FILE"

# --- Helpers ---
sleep_with_backoff() {
    sleep "$ERROR_BACKOFF"

    ERROR_BACKOFF=$((ERROR_BACKOFF * 2))

    if [ "$ERROR_BACKOFF" -gt "$MAX_BACKOFF" ]; then
        ERROR_BACKOFF="$MAX_BACKOFF"
    fi
}

reset_backoff() {
    ERROR_BACKOFF=5
}

parse_retry_after() {
    case "${1:-}" in
        ''|*[!0-9]*)
            echo "$SLEEP_TIME"
            ;;
        *)
            echo "$1"
            ;;
    esac
}

report_status() {
    endpoint="$1"
    id="$2"
    payload="$3"
    attempt=0
    max_attempts=3
    backoff=2

    while [ "$attempt" -lt "$max_attempts" ]; do
        RESPONSE=$(curl -sS \
            --max-time 10 \
            -w "\n%{http_code}" \
            -X POST "$BASE_URL/$endpoint/$id" \
            -H "X-API-KEY: $API_KEY" \
            -H "Content-Type: application/json" \
            -d "$payload" || true)

        HTTP_CODE=$(printf "%s" "$RESPONSE" | tail -n1 | tr -d '\r')

        if [ "$HTTP_CODE" = "200" ]; then
            return 0
        elif [ "$HTTP_CODE" = "404" ]; then
            echo "[!] Lease lost for $id (404 Not Found) - aborting $endpoint"
            return 1
        fi

        attempt=$((attempt + 1))

        if [ "$attempt" -lt "$max_attempts" ]; then
            sleep "$backoff"
            backoff=$((backoff * 2))
        fi
    done

    echo "[!] Failed to report $endpoint for $id after $max_attempts attempts"
    return 1
}

trap 'echo "Shutdown"; exit 0' INT TERM

# --- Main Loop ---
while true; do
    set +e
    RESPONSE=$(
        curl -sS \
            --connect-timeout 5 \
            --max-time 15 \
            -D - \
            -w "\n__HTTP_CODE__:%{http_code}" \
            -X POST \
            "$BASE_URL/claim?goal=$GOAL&namespace=$NAMESPACE" \
            -H "X-API-KEY: $API_KEY" \
            -H "X-Worker-ID: $WORKER_ID" \
            -H "X-Worker-Capabilities: $CAPABILITIES"
    )
    CURL_EXIT=$?
    set -e

    if [ "$CURL_EXIT" -ne 0 ]; then
        echo "[!] Network error during claim"
        sleep_with_backoff
        continue
    fi

    HTTP_CODE=$(printf "%s" "$RESPONSE" \
        | grep "__HTTP_CODE__:" \
        | cut -d: -f2 \
        | tr -d '\r')

    RAW_RESPONSE=$(printf "%s" "$RESPONSE" \
        | sed '/__HTTP_CODE__/d')

    # No jobs available
    if [ "$HTTP_CODE" = "204" ]; then
        RETRY_AFTER=$(printf "%s" "$RAW_RESPONSE" \
            | awk -F': *' '
                tolower($1) ~ /retry-after/ {
                    gsub(/[^0-9]/, "", $2)
                    print $2
                }
            ' \
            | head -n1)

        RETRY_AFTER=$(parse_retry_after "$RETRY_AFTER")

        sleep "$RETRY_AFTER"
        continue
    fi

    # Error response
    if [ "$HTTP_CODE" != "200" ]; then
        echo "[!] Claim failed (HTTP $HTTP_CODE)"
        sleep_with_backoff
        continue
    fi

    BODY=$(printf "%s" "$RAW_RESPONSE" \
        | awk '
            BEGIN { found=0 }
            /^[[:space:]]*\{/ { found=1 }
            found
        ' \
        | tr -d '\r')

    if [ -z "$BODY" ]; then
        echo "[!] Empty response body"
        sleep_with_backoff
        continue
    fi

    set +e
    echo "$BODY" | jq -e . >/dev/null 2>&1
    JQ_EXIT=$?
    set -e

    if [ "$JQ_EXIT" -ne 0 ]; then
        echo "[!] Invalid JSON received"
        sleep_with_backoff
        continue
    fi

    ID=$(echo "$BODY" | jq -r '.id // empty')
    CLAIM_TOKEN=$(echo "$BODY" | jq -r '.claim_token // empty')
    PAYLOAD=$(echo "$BODY" | jq -c '.payload // {}')

    if [ -z "$ID" ] || [ -z "$CLAIM_TOKEN" ]; then
        echo "[!] Missing id or claim_token"
        sleep_with_backoff
        continue
    fi

    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    LOG_LINE="[$TIMESTAMP] id=$ID payload=$PAYLOAD"

    if printf "%s\n" "$LOG_LINE" >> "$LOG_FILE"; then
        report_status \
            "fulfill" \
            "$ID" \
            "{\"claim_token\":\"$CLAIM_TOKEN\",\"result\":\"logged\",\"result_type\":\"text\"}"

        echo "[+] Logged intent $ID"

        reset_backoff
    else
        report_status \
            "fail" \
            "$ID" \
            "{\"claim_token\":\"$CLAIM_TOKEN\",\"error\":\"log_write_failed\"}"

        echo "[!] Failed to write log entry"

        sleep_with_backoff
    fi

    sleep "$SLEEP_TIME"
done
