#!/bin/bash
# Ensures that the script exits immediately if a command exits with a non-zero status.
# -u: Treats unset variables as an error.
# -o pipefail: The return value of a pipeline is the status of the last command to exit with a non-zero status, or zero if all commands exit successfully.
set -uo pipefail

# Intent Bus | Discord Webhook Worker (v7.61)
# This worker claims intents and relays their content to a specified Discord webhook URL.

# =========================================================
# CONFIGURATION
# =========================================================

# Path to the file containing the API key.
API_KEY_FILE="${HOME}/.apikey"
# Base URL of the Intent Bus server.
BASE_URL="${BASE_URL:-https://your-bus.render.com}"

# The 'goal' this worker listens for (e.g., "discord_alert").
GOAL="${GOAL:-discord_alert}"
# The 'namespace' this worker operates within (e.g., "default", "alerts").
NAMESPACE="${NAMESPACE:-default}"

# Unique identifier for this worker instance.
WORKER_ID="${WORKER_ID:-discord-worker}"
# Capabilities advertised by this worker.
CAPABILITIES="${CAPABILITIES:-discord,webhook}"

# Time to sleep when no jobs are available.
SLEEP_IDLE="${SLEEP_IDLE:-5}"
# Time to sleep on network or processing errors.
SLEEP_ERROR="${SLEEP_ERROR:-10}"
# Time to sleep after successfully fulfilling an intent.
SLEEP_SUCCESS="${SLEEP_SUCCESS:-2}"

# Maximum length of the content sent to Discord (Discord's limit is 2000 chars, leave some buffer).
MAX_CONTENT_LENGTH="${MAX_CONTENT_LENGTH:-1900}"
# Curl timeout for network operations.
CURL_TIMEOUT="${CURL_TIMEOUT:-10}"

# =========================================================
# DEPENDENCIES
# =========================================================

# Check if 'curl' command is available.
command -v curl >/dev/null 2>&1 || {
    echo "[!] curl is required"
    exit 1
}

# Check if 'jq' command is available (for JSON parsing).
command -v jq >/dev/null 2>&1 || {
    echo "[!] jq is required"
    exit 1
}

# =========================================================
# AUTHENTICATION SETUP
# =========================================================

# Check if the API key file exists.
if [ ! -f "$API_KEY_FILE" ]; then
    echo "[!] Missing API key file: $API_KEY_FILE"
    echo "    Run: echo 'your_key' > ~/.apikey && chmod 600 ~/.apikey"
    exit 1
fi

# SECURITY: Prevent reading API key from a symlink to avoid path traversal vulnerabilities.
if [ -L "$API_KEY_FILE" ]; then
    echo "[!] Refusing to read symlinked API key file"
    exit 1
fi

# Set restrictive file permissions (read/write only for owner).
chmod 600 "$API_KEY_FILE" 2>/dev/null || true

# Read the API key from the file.
API_KEY=$(cat "$API_KEY_FILE")

# Check if the API key is empty.
if [ -z "$API_KEY" ]; then
    echo "[!] API key is empty"
    exit 1
fi

# =========================================================
# HELPER FUNCTIONS
# =========================================================

# Simple logging function with timestamp.
log() {
    echo "[$(date +%T)] $*"
}

# Parses the Retry-After header from server responses.
parse_retry_after() {
    case "${1:-}" in
        ''|*[!0-9]*)
            echo "$SLEEP_IDLE" # If not a number, use default idle sleep time.
            ;;
        *)
            echo "$1" # Otherwise, use the provided numeric value.
            ;;
    esac
}

# Reports the status of an intent (fulfill or fail) back to the Intent Bus.
report_status() {
    endpoint="$1" # e.g., "fulfill" or "fail"
    id="$2"       # Intent ID
    payload="$3"  # JSON payload for the status report (e.g., {"claim_token": "...", "result": "..."})

    attempt=0
    max_attempts=3 # Max attempts to report status.
    backoff=2      # Initial backoff for reporting status.

    while [ "$attempt" -lt "$max_attempts" ]; do
        set +e # Temporarily disable exit on error for curl command.
        RESPONSE=$(curl -sS \
            --max-time "$CURL_TIMEOUT" \
            -w "\n%{http_code}" \
            -X POST "$BASE_URL/$endpoint/$id" \
            -H "X-API-KEY: $API_KEY" \
            -H "Content-Type: application/json" \
            -d "$payload")
        CURL_EXIT=$?
        set -e # Re-enable exit on error.

        if [ "$CURL_EXIT" -eq 0 ]; then
            HTTP_CODE=$(printf "%s" "$RESPONSE" | tail -n1 | tr -d '\r')

            if [ "$HTTP_CODE" = "200" ]; then
                return 0 # Success.
            fi

            if [ "$HTTP_CODE" = "404" ]; then
                log "[!] Lease lost for $id (404 Not Found)"
                return 1 # Lease lost, no need to retry.
            fi
        fi

        attempt=$((attempt + 1))

        if [ "$attempt" -lt "$max_attempts" ]; then
            sleep "$backoff" # Backoff before retrying status report.
            backoff=$((backoff * 2))
        fi
    done

    log "[!] Failed to report $endpoint for $id"
    return 1 # Failed to report status after max attempts.
}

# Helper function to send a 'fail' status to the Intent Bus.
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

# Helper function to send a 'fulfill' status to the Intent Bus.
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

# Trap signals for graceful shutdown (Ctrl+C, kill command).
trap 'log "Shutdown"; exit 0' INT TERM

# =========================================================
# MAIN LOOP
# =========================================================

log "Discord worker started"
log "Listening on $NAMESPACE/$GOAL"

while true; do

    set +e # Temporarily disable exit on error for curl.
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
    set -e # Re-enable exit on error.

    # Handle network errors during claim.
    if [ "$CURL_EXIT" -ne 0 ]; then
        log "[!] Network error during claim"
        sleep "$SLEEP_ERROR"
        continue
    fi

    # Extract HTTP code from curl output.
    HTTP_CODE=$(printf "%s" "$RESPONSE" \
        | grep "__HTTP_CODE__:" \
        | cut -d: -f2 \
        | tr -d '\r')

    # Extract raw response (excluding HTTP code line).
    RAW_RESPONSE=$(printf "%s" "$RESPONSE" \
        | sed '/__HTTP_CODE__/d')

    # -----------------------------------------------------
    # Handle 'No jobs available' (HTTP 204).
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
    # Handle Claim failure (Non-200, Non-204 HTTP codes).
    # -----------------------------------------------------

    if [ "$HTTP_CODE" != "200" ]; then
        log "[!] Claim failed (HTTP $HTTP_CODE)"
        sleep "$SLEEP_ERROR"
        continue
    fi

    # Extract JSON body from raw response.
    BODY=$(printf "%s" "$RAW_RESPONSE" \
        | awk '
            BEGIN { found=0 }
            /^[[:space:]]*\{/ { found=1 }
            found
        ' \
        | tr -d '\r')

    # Handle empty response body.
    if [ -z "$BODY" ]; then
        log "[!] Empty response body from claim"
        sleep "$SLEEP_ERROR"
        continue
    fi

    set +e # Temporarily disable exit on error for jq.
    printf "%s" "$BODY" | jq -e . >/dev/null 2>&1 # Validate JSON using jq.
    JQ_EXIT=$?
    set -e # Re-enable exit on error.

    # Handle invalid JSON.
    if [ "$JQ_EXIT" -ne 0 ]; then
        log "[!] Invalid JSON received from claim"
        sleep "$SLEEP_ERROR"
        continue
    fi

    # Extract intent ID, claim token, webhook URL, and content using jq.
    ID=$(printf "%s" "$BODY" | jq -r '.id // empty')
    CLAIM_TOKEN=$(printf "%s" "$BODY" | jq -r '.claim_token // empty')

    WEBHOOK_URL=$(printf "%s" "$BODY" \
        | jq -r '.payload.webhook_url // empty')

    CONTENT=$(printf "%s" "$BODY" \
        | jq -r '.payload.content // empty')

    # -----------------------------------------------------
    # Payload Validation.
    # -----------------------------------------------------

    # Check for missing essential fields.
    if [ -z "$ID" ] || [ -z "$CLAIM_TOKEN" ]; then
        log "[!] Missing id or claim_token in claim response"
        sleep "$SLEEP_ERROR"
        continue
    fi

    # Check for missing webhook URL or content in payload.
    if [ -z "$WEBHOOK_URL" ] || [ -z "$CONTENT" ]; then
        log "[!] $ID missing webhook_url or content in payload"

        # Fail the intent if payload is invalid.
        fail_intent \
            "$ID" \
            "$CLAIM_TOKEN" \
            "Invalid payload: missing webhook_url or content"

        sleep "$SLEEP_ERROR"
        continue
    fi

    # SECURITY: Strict allowlist for Discord webhook URLs. Prevents SSRF and arbitrary requests.
    case "$WEBHOOK_URL" in
        https://discord.com/api/webhooks/*)
            ;; # Valid Discord webhook URL.
        *)
            log "[!] $ID rejected invalid webhook URL: $WEBHOOK_URL"

            # Fail the intent if URL is forbidden.
            fail_intent \
                "$ID" \
                "$CLAIM_TOKEN" \
                "Forbidden webhook URL"

            sleep "$SLEEP_ERROR"
            continue
            ;;
    esac

    # Normalize whitespace and truncate content to prevent oversized Discord messages.
    CONTENT=$(printf "%s" "$CONTENT" \
        | tr '\r\n\t' '   ' \
        | cut -c1-"$MAX_CONTENT_LENGTH")

    # Check if content is empty after sanitization.
    if [ -z "$CONTENT" ]; then
        log "[!] $ID content empty after sanitization"

        # Fail the intent if content is empty.
        fail_intent \
            "$ID" \
            "$CLAIM_TOKEN" \
            "Empty content after sanitization"

        sleep "$SLEEP_ERROR"
        continue
    fi

    # Create JSON payload for Discord webhook.
    JSON_PAYLOAD=$(jq -n \
        --arg content "$CONTENT" \
        '{content:$content}')

    # -----------------------------------------------------
    # Send message to Discord Webhook.
    # -----------------------------------------------------

    log "Sending intent $ID content to Discord webhook"

    set +e # Temporarily disable exit on error for curl.
    DISCORD_STATUS=$(curl -sS \
        --max-time "$CURL_TIMEOUT" \
        -o /dev/null \
        -w "%{http_code}" \
        -X POST "$WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "$JSON_PAYLOAD")
    CURL_EXIT=$?
    set -e # Re-enable exit on error.

    # If curl command itself failed, set a generic error status.
    if [ "$CURL_EXIT" -ne 0 ]; then
        DISCORD_STATUS="000"
    fi

    # Handle Discord API responses.
    case "$DISCORD_STATUS" in
        2*) # HTTP 2xx status codes indicate success.
            fulfill_intent "$ID" "$CLAIM_TOKEN" # Fulfill the intent.
            log "[+] Delivered intent $ID successfully ($DISCORD_STATUS)"
            sleep "$SLEEP_SUCCESS" # Short sleep on success.
            ;;

        429) # Discord rate limit.
            log "[!] Discord rate limited (429). Failing intent."
            fail_intent "$ID" "$CLAIM_TOKEN" "Discord rate limited" || true # Fail the intent, '|| true' prevents exit on failure to report status.
            sleep 15 # Longer sleep to respect Discord's rate limits.
            ;;

        404) # Webhook not found.
            log "[!] Discord webhook not found (404) for intent $ID"

            fail_intent \
                "$ID" \
                "$CLAIM_TOKEN" \
                "Discord webhook returned 404"

            sleep "$SLEEP_ERROR"
            ;;

        *) # Other Discord errors.
            log "[!] Discord returned HTTP $DISCORD_STATUS for intent $ID"

            fail_intent \
                "$ID" \
                "$CLAIM_TOKEN" \
                "Discord returned HTTP $DISCORD_STATUS"

            sleep "$SLEEP_ERROR"
            ;;
    esac

done
