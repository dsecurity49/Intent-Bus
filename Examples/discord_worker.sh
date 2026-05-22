#!/bin/sh

# Intent Bus | Discord Webhook Worker (v7.61)
# Example worker that relays messages to Discord webhooks.

set -uo pipefail

# =========================================================
# CONFIGURATION
# =========================================================

API_KEY_FILE="${HOME}/.apikey"
BASE_URL="${BASE_URL:-https://dsecurity.pythonanywhere.com}"

GOAL="${GOAL:-discord_alert}"
NAMESPACE="${NAMESPACE:-default}"

WORKER_ID="${WORKER_ID:-discord-worker}"
CAPABILITIES="${CAPABILITIES:-discord,webhook}"

SLEEP_IDLE="${SLEEP_IDLE:-5}"
SLEEP_ERROR="${SLEEP_ERROR:-10}"
SLEEP_SUCCESS="${SLEEP_SUCCESS:-2}"

MAX_CONTENT_LENGTH="${MAX_CONTENT_LENGTH:-1900}"
CURL_TIMEOUT="${CURL_TIMEOUT:-10}"

# =========================================================
# DEPENDENCIES
# =========================================================

command -v curl >/dev/null 2>&1 || {
    echo "[!] curl is required"
    exit 1
}

command -v jq >/dev/null 2>&1 || {
    echo "[!] jq is required"
    exit 1
}

# =========================================================
# AUTH
# =========================================================

if [ ! -f "$API_KEY_FILE" ]; then
    echo "[!] Missing API key file: $API_KEY_FILE"
    echo "    Run: echo 'your_key' > ~/.apikey && chmod 600 ~/.apikey"
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

# =========================================================
# HELPERS
# =========================================================

log() {
    echo "[$(date +%T)] $*"
}

parse_retry_after() {
    case "${1:-}" in
        ''|*[!0-9]*)
            echo "$SLEEP_IDLE"
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
        set +e
        RESPONSE=$(curl -sS \
            --max-time "$CURL_TIMEOUT" \
            -w "\n%{http_code}" \
            -X POST "$BASE_URL/$endpoint/$id" \
            -H "X-API-KEY: $API_KEY" \
            -H "Content-Type: application/json" \
            -d "$payload")
        CURL_EXIT=$?
        set -e

        if [ "$CURL_EXIT" -eq 0 ]; then
            HTTP_CODE=$(printf "%s" "$RESPONSE" | tail -n1 | tr -d '\r')

            if [ "$HTTP_CODE" = "200" ]; then
                return 0
            fi

            if [ "$HTTP_CODE" = "404" ]; then
                log "[!] Lease lost for $id (404 Not Found)"
                return 1
            fi
        fi

        attempt=$((attempt + 1))

        if [ "$attempt" -lt "$max_attempts" ]; then
            sleep "$backoff"
            backoff=$((backoff * 2))
        fi
    done

    log "[!] Failed to report $endpoint for $id"
    return 1
}

fail_intent() {
    id="$1"
    token="$2"
    reason="$3"

    report_status \
        "fail" \
        "$id" \
        "$(jq -n \
            --arg t "$token" \
            --arg r "$reason" \
            '{claim_token:$t,error:$r}')"
}

fulfill_intent() {
    id="$1"
    token="$2"

    report_status \
        "fulfill" \
        "$id" \
        "$(jq -n \
            --arg t "$token" \
            '{claim_token:$t,result:"delivered",result_type:"text"}')"
}

trap 'log "Shutdown"; exit 0' INT TERM

# =========================================================
# MAIN LOOP
# =========================================================

log "Discord worker started"
log "Listening on $NAMESPACE/$GOAL"

while true; do

    set +e
    RESPONSE=$(curl -sS \
        --connect-timeout 5 \
        --max-time "$CURL_TIMEOUT" \
        -D - \
        -w "\n__HTTP_CODE__:%{http_code}" \
        -X POST \
        "$BASE_URL/claim?goal=$GOAL&namespace=$NAMESPACE" \
        -H "X-API-KEY: $API_KEY" \
        -H "X-Worker-ID: $WORKER_ID" \
        -H "X-Worker-Capabilities: $CAPABILITIES")
    CURL_EXIT=$?
    set -e

    if [ "$CURL_EXIT" -ne 0 ]; then
        log "[!] Network error during claim"
        sleep "$SLEEP_ERROR"
        continue
    fi

    HTTP_CODE=$(printf "%s" "$RESPONSE" \
        | grep "__HTTP_CODE__:" \
        | cut -d: -f2 \
        | tr -d '\r')

    RAW_RESPONSE=$(printf "%s" "$RESPONSE" \
        | sed '/__HTTP_CODE__/d')

    # -----------------------------------------------------
    # No jobs available
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Claim failure
    # -----------------------------------------------------

    if [ "$HTTP_CODE" != "200" ]; then
        log "[!] Claim failed (HTTP $HTTP_CODE)"
        sleep "$SLEEP_ERROR"
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
        log "[!] Empty response body"
        sleep "$SLEEP_ERROR"
        continue
    fi

    set +e
    printf "%s" "$BODY" | jq -e . >/dev/null 2>&1
    JQ_EXIT=$?
    set -e

    if [ "$JQ_EXIT" -ne 0 ]; then
        log "[!] Invalid JSON received"
        sleep "$SLEEP_ERROR"
        continue
    fi

    ID=$(printf "%s" "$BODY" | jq -r '.id // empty')
    CLAIM_TOKEN=$(printf "%s" "$BODY" | jq -r '.claim_token // empty')

    WEBHOOK_URL=$(printf "%s" "$BODY" \
        | jq -r '.payload.webhook_url // empty')

    CONTENT=$(printf "%s" "$BODY" \
        | jq -r '.payload.content // empty')

    # -----------------------------------------------------
    # Validation
    # -----------------------------------------------------

    if [ -z "$ID" ] || [ -z "$CLAIM_TOKEN" ]; then
        log "[!] Missing id or claim_token"
        sleep "$SLEEP_ERROR"
        continue
    fi

    if [ -z "$WEBHOOK_URL" ] || [ -z "$CONTENT" ]; then
        log "[!] $ID missing webhook_url or content"

        fail_intent \
            "$ID" \
            "$CLAIM_TOKEN" \
            "Invalid payload"

        sleep "$SLEEP_ERROR"
        continue
    fi

    # Strict Discord-only webhook allowlist
    case "$WEBHOOK_URL" in
        https://discord.com/api/webhooks/*)
            ;;
        *)
            log "[!] $ID rejected invalid webhook URL"

            fail_intent \
                "$ID" \
                "$CLAIM_TOKEN" \
                "Forbidden webhook URL"

            sleep "$SLEEP_ERROR"
            continue
            ;;
    esac

    # Normalize whitespace
    CONTENT=$(printf "%s" "$CONTENT" \
        | tr '\r\n\t' '   ' \
        | cut -c1-"$MAX_CONTENT_LENGTH")

    if [ -z "$CONTENT" ]; then
        log "[!] $ID content empty after sanitization"

        fail_intent \
            "$ID" \
            "$CLAIM_TOKEN" \
            "Empty content"

        sleep "$SLEEP_ERROR"
        continue
    fi

    JSON_PAYLOAD=$(jq -n \
        --arg content "$CONTENT" \
        '{content:$content}')

    # -----------------------------------------------------
    # Send to Discord
    # -----------------------------------------------------

    log "Sending $ID to Discord"

    set +e
    DISCORD_STATUS=$(curl -sS \
        --max-time "$CURL_TIMEOUT" \
        -o /dev/null \
        -w "%{http_code}" \
        -X POST "$WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "$JSON_PAYLOAD")
    CURL_EXIT=$?
    set -e

    if [ "$CURL_EXIT" -ne 0 ]; then
        DISCORD_STATUS="000"
    fi

    case "$DISCORD_STATUS" in
        2*)
            fulfill_intent "$ID" "$CLAIM_TOKEN"
            log "[+] Delivered ($DISCORD_STATUS)"
            sleep "$SLEEP_SUCCESS"
            ;;

        429)
            log "[!] Discord rate limited (429). Failing intent."
            fail_intent "$ID" "$CLAIM_TOKEN" "Discord rate limited" || true
            sleep 15
            ;;

        404)
            log "[!] Webhook not found (404)"

            fail_intent \
                "$ID" \
                "$CLAIM_TOKEN" \
                "Discord webhook returned 404"

            sleep "$SLEEP_ERROR"
            ;;

        *)
            log "[!] Discord error ($DISCORD_STATUS)"

            fail_intent \
                "$ID" \
                "$CLAIM_TOKEN" \
                "Discord returned HTTP $DISCORD_STATUS"

            sleep "$SLEEP_ERROR"
            ;;
    esac

done
