# Intent Bus Examples

This directory contains **reference worker implementations** for the Intent Bus.

Each example shows how to:
- Claim an intent
- Execute a task locally
- Fulfill or fail the job

> Workers are part of the protocol — not just clients. Incorrect worker behavior can break system guarantees.

---

##  Prerequisites

Store your API key locally:

```bash
echo "your_api_key_here" > ~/.apikey
```

---

##  Bash Workers (`.sh`)

**Auth Mode:** Standard (API Key Header)  
**Dependencies:** `curl`, `jq`

### Install Dependencies

**Termux**
```bash
pkg install curl jq
```

**Linux**
```bash
sudo apt install curl jq
```

### Available

- `worker.sh` → Sends notifications (Termux)
- `logger.sh` → Logs events to file
- `discord_worker.sh` → Sends messages to Discord

---

##  Python Workers (`.py`)

**Auth Mode:** Strict (HMAC via SDK)

Install the SDK:

```bash
pip install intent-bus
```

### Available

- `python_worker.py` → Demonstrates controlled execution with strict validation (safe command patterns)

---

##  Running an Example

### Bash

```bash
chmod +x worker.sh
./worker.sh
```

### Python

```bash
python python_worker.py --goal sys
```

---

## ⚠️ Notes & Rules

- **Completion is mandatory:** Workers MUST call `/fulfill/<id>` on success.
- **Failure reporting is mandatory:** Workers MUST call `/fail/<id>` on any execution error.
- Silent drops are considered protocol violations.

- **Idempotency:** Jobs may be retried (at-least-once delivery). Execution logic MUST be safe to run multiple times.

- **Hybrid Routing:** By default, workers claim private intents. You may modify these examples to claim `visibility="public"` intents.

⚠️ **Security Warning (Open Fleet):**  
If a worker claims public intents, payloads MUST be treated as untrusted input.  
Refer to `WORKER_SECURITY.md` before enabling public execution.

---

##  Required Reading

- `SPEC.md` → Protocol definition and state machine  
- `WORKER_SECURITY.md` → Critical security rules for executing payloads  

---

## License

MIT
