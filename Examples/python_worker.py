import os
import sys
import argparse
import subprocess
import logging
import time
import json
from typing import Dict, List

try:
    from intent_bus import IntentClient
except ImportError:
    print("Error: intent-bus SDK not found. Install it with 'pip install intent-bus'")
    sys.exit(1)

# -------------------------------
# Configuration
# -------------------------------

DEFAULT_INTERVAL = 5
MAX_OUTPUT_LENGTH = 1000
MAX_PAYLOAD_SIZE = 2048  # bytes
EXECUTION_COOLDOWN = 1   # seconds between executions

ALLOWED_COMMANDS: Dict[str, List[str]] = {
    "uptime": ["uptime"],
    "date": ["date"],
    "disk": ["df", "-h"],
    "memory": ["free", "-m"],
    "whoami": ["whoami"]
}

# -------------------------------
# Logging
# -------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("IntentWorker")

last_execution_time = 0

# -------------------------------
# Helpers
# -------------------------------

def load_api_key(path: str) -> str:
    path = os.path.expanduser(path)

    if not os.path.exists(path):
        logger.error(f"API key not found at {path}")
        sys.exit(1)

    with open(path, "r") as f:
        key = f.read().strip()

    if not key:
        logger.error("API key file is empty")
        sys.exit(1)

    return key


def sanitize_text(text: str) -> str:
    """Remove non-printable control characters."""
    return "".join(c for c in text if c.isprintable())


def safe_execute(command: List[str]) -> str:
    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            err = result.stderr.strip() or f"Exit code {result.returncode}"
            raise RuntimeError(err)

        output = sanitize_text(result.stdout.strip())
        return output[:MAX_OUTPUT_LENGTH]

    except subprocess.TimeoutExpired:
        raise RuntimeError("Command execution timed out (30s)")
    except Exception as e:
        raise RuntimeError(f"Internal execution error: {str(e)}")


# -------------------------------
# Handler
# -------------------------------

def handle_sys_command(payload: dict) -> bool:
    global last_execution_time

    # --- Payload validation ---
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")

    if len(json.dumps(payload)) > MAX_PAYLOAD_SIZE:
        raise ValueError("Payload exceeds maximum safe size")

    cmd_key = payload.get("cmd")

    if not isinstance(cmd_key, str):
        raise ValueError("Payload field 'cmd' must be a string")

    cmd_key = sanitize_text(cmd_key).strip()

    if not cmd_key:
        raise ValueError("Payload field 'cmd' is empty")

    if cmd_key not in ALLOWED_COMMANDS:
        raise ValueError(f"Command '{cmd_key}' is not allowed")

    # --- Smooth rate limiting (prevents retry storms) ---
    now = time.time()
    elapsed = now - last_execution_time

    if elapsed < EXECUTION_COOLDOWN:
        time.sleep(EXECUTION_COOLDOWN - elapsed)

    command = ALLOWED_COMMANDS[cmd_key]
    logger.info(f"EXECUTE | CMD: {cmd_key}")

    try:
        output = safe_execute(command)
        logger.info(f"SUCCESS | OUT: {output}")

        last_execution_time = time.time()
        return True

    except Exception as e:
        logger.error(f"FAILURE | ERR: {str(e)}")
        raise  # SDK handles /fail


# -------------------------------
# Main
# -------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Intent Bus Production Worker (Strict Auth)"
    )
    parser.add_argument("--goal", default="sys", help="Goal to poll for")
    parser.add_argument("--url", default="https://dsecurity.pythonanywhere.com")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--key-path", default="~/.apikey")

    args = parser.parse_args()

    api_key = load_api_key(args.key_path)

    client = IntentClient(
        base_url=args.url,
        api_key=api_key
    )

    logger.info("===================================")
    logger.info("DSECURITY // INTENT BUS WORKER READY")
    logger.info("Mode         : SAFE (Hardened)")
    logger.info(f"Goal         : {args.goal}")
    logger.info(f"Server       : {args.url}")
    logger.info(f"Poll Interval: {args.interval}s")
    logger.info("===================================")

    try:
        client.listen(
            goal=args.goal,
            handler=handle_sys_command,
            poll_interval=args.interval
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested via SIGINT")
    except Exception as e:
        logger.error(f"FATAL SDK ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
