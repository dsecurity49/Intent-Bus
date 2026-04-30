# Intent Bus Examples

This directory contains **reference worker implementations** for the Intent Bus.

Each example shows how to:
- Claim an intent
- Execute a task locally
- Fulfill or fail the job

---

## 🛠️ Prerequisites

Store your API key locally:

```bash
echo "your_api_key_here" > ~/.apikey
```

---

## 🐚 Bash Workers (`.sh`)

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

## 🐍 Python Workers (`.py`)

**Auth Mode:** Strict (HMAC via SDK)

Install the SDK:

```bash
pip install intent-bus
```

### Available

- `python_worker.py` → Executes controlled system commands

---

## 🚀 Running an Example

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

## ⚠️ Notes

- Workers MUST call `/fulfill/<id>` on success
- Workers SHOULD call `/fail/<id>` on errors
- Jobs may be retried (at-least-once delivery)

See:
- `SPEC.md` → Protocol definition  
- `WORKER_SECURITY.md` → Security rules  

---

## License

MIT
