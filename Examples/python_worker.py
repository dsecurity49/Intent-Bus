import argparse
import logging
import subprocess
import sys
from typing import List

try:
    from intent_bus import IntentClient, WorkerRuntime
except ImportError:
    print("Error: intent-bus SDK not found. Install it with 'pip install intent-bus'")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30

# Explicit allowlist of permitted executables
ALLOWED_COMMANDS = {
    "ls",
    "pwd",
    "whoami",
    "date",
    "uptime",
    "echo",
}


def validate_command(command: List[str]):
    """Validate incoming command payload."""

    if not isinstance(command, list) or not command:
        raise ValueError("'command' must be a non-empty list")

    if not all(isinstance(arg, str) for arg in command):
        raise ValueError("all command arguments must be strings")

    executable = command[0]

    if executable not in ALLOWED_COMMANDS:
        raise ValueError(f"command '{executable}' is not allowed")


def safe_execute(command_args: List[str], timeout: int = DEFAULT_TIMEOUT):
    """Execute a system command safely without shell=True."""

    try:
        result = subprocess.run(
            command_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
            shell=False,
        )

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        raise ValueError(f"command timed out after {timeout} seconds")

    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()

        logger.warning(
            "Command failed | command=%r | exit_code=%s | stderr=%r",
            command_args,
            e.returncode,
            stderr,
        )

        raise ValueError(f"command failed with exit code {e.returncode}")

    except Exception:
        logger.exception("Unexpected execution error")
        raise ValueError("execution failed")


def handle_sys_command(payload, timeout_sec):
    """
    Handler for the intent bus.

    Expected payload:
    {
        "command": ["ls", "-la"]
    }
    """

    command = payload.get("command")

    validate_command(command)

    logger.info("Executing command: %r", command)

    # Pass the timeout down to the execution wrapper
    output = safe_execute(command, timeout=timeout_sec)

    logger.info("SUCCESS | command=%r", command)

    # Structured fulfillment response for Intent Protocol v2.0
    return {
        "result": output,
        "result_type": "text"
    }


def main():
    parser = argparse.ArgumentParser(
        description="Intent Bus Secure Python Worker"
    )

    parser.add_argument(
        "--goal",
        default="sys_exec",
        help="The goal to listen for"
    )

    parser.add_argument(
        "--namespace",
        default="default",
        help="The namespace to listen on"
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds"
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Subprocess timeout in seconds"
    )

    args = parser.parse_args()

    # Automatically loads INTENT_API_KEY from environment or ~/.apikey
    client = IntentClient()

    # Initialize resilient v2.0 orchestrator
    runtime = WorkerRuntime(
        client=client,
        poll_interval=args.interval,
        capabilities=["sys_exec"],
    )

    logger.info(
        "Starting worker | goal=%s | namespace=%s",
        args.goal,
        args.namespace,
    )

    try:
        runtime.listen(
            goal=args.goal,
            namespace=args.namespace,
            handler=lambda p: handle_sys_command(p, args.timeout),
        )

    except KeyboardInterrupt:
        logger.info("Worker shutting down cleanly")


if __name__ == "__main__":
    main()
