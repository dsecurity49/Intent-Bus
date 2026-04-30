import os
import sys
import argparse
import subprocess
import logging
from typing import Dict, List
from intent_bus import IntentClient

# -------------------------------
# Configuration
# -------------------------------

DEFAULT_INTERVAL = 5
MAX_OUTPUT_LENGTH = 1000

# Whitelisted commands (SAFE MODE)
ALLOWED_COMMANDS: Dict[str, List[str]] = {
    "uptime": ["uptime"],
    "date": ["date"],
    "disk": ["df", "-h"],
    "memory": ["free", "-m"],
    "whoami": ["whoami"]
}

# -------------------------------
# Logging Setup
# -------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("IntentWorker")


# -------------------------------
# Helpers
# -------------------------------

def load_api_key(path: str) -> str:
    if not os.path.exists(path):
        logger.error(f"API key not found at {path}")
        sys.exit(1)

    with open(path, "r") as f:
        return f.read().strip()


def safe_execute(command: List[str]) -> str:
    """
    Executes a whitelisted command safely.
    """
    result = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()[:MAX_OUTPUT_LENGTH]


# -------------------------------
# Handler
# -------------------------------

def handle_sys_command(payload: dict) -> bool:
    """
    Safe handler using command whitelist.

    Expected payload:
    {
        "cmd": "uptime"
    }
    """

    cmd_key = payload.get("cmd")

    if not cmd_key:
        raise ValueError("Missing 'cmd' in payload")

    if cmd_key not in ALLOWED_COMMANDS:
        raise ValueError(f"Command '{cmd_key}' is not allowed")

    command = ALLOWED_COMMANDS[cmd_key]

    logger.info({
        "event": "execute",
        "command": command
    })

    output = safe_execute(command)

    logger.info({
        "event": "success",
        "output": output
    })

    return True


# -------------------------------
# Main
# -------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Intent Bus Production Worker (Strict Auth)"
    )
    parser.add_argument("--goal", default="sys", help="Goal to listen for")
    parser.add_argument("--url", default="https://dsecurity.pythonanywhere.com")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--key-path", default="~/.apikey")
    args = parser.parse_args()

    api_key_path = os.path.expanduser(args.key_path)
    api_key = load_api_key(api_key_path)

    client = IntentClient(
        api_key=api_key,
        base_url=args.url
    )

    logger.info("===================================")
    logger.info("INTENT BUS WORKER STARTED")
    logger.info(f"Mode        : SAFE (whitelisted commands)")
    logger.info(f"Goal        : {args.goal}")
    logger.info(f"Server      : {args.url}")
    logger.info(f"Poll Interval: {args.interval}s")
    logger.info("===================================")

    try:
        client.listen(
            goal=args.goal,
            handler=handle_sys_command,
            interval=args.interval
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
