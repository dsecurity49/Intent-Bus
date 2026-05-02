# Intent Bus

[![PyPI version](https://img.shields.io/pypi/v/intent-bus.svg)](https://pypi.org/project/intent-bus/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Run code on any device from anywhere — using just HTTP.**

A zero-infrastructure job coordination system with retries, locking, and cross-device workers. Built for developers who want something more reliable than cron, without the overhead of Redis, RabbitMQ, or Firebase.

📖 [Read the full story](https://dev.to/d_security/how-i-control-my-android-phone-from-a-cloud-server-using-100-lines-of-flask-2fl6)

----

## 🔥 What makes this different?

- Trigger your **Android phone from a cloud server**
- Run jobs across devices **without opening ports**
- Build distributed systems using **just HTTP + curl**
- **Hybrid Routing:** Keep jobs private, or open them to a global worker fleet
- No brokers, no queues, no infrastructure

No Firebase. No message queues. Just a minimal Flask + SQLite core.

---

## 🧠 How it works (30 seconds)

1. A client **POSTs a job** to `/intent`
2. Workers **poll `/claim`** for matching jobs
3. One worker **atomically claims** the job (SQLite transactional locking with `BEGIN IMMEDIATE` + `UPDATE ... RETURNING`)
4. Worker executes and calls `/fulfill`
5. If it crashes → job is **retried automatically**

```mermaid
graph LR
    A[Cloud Script<br/>PythonAnywhere] -->|POST /intent| B[Intent Bus<br/>Flask + SQLite]
    B -->|claim + fulfill| C[Worker<br/>Termux / Linux / VPS]
    C -->|execute task| D[📱 Phone / System Action]
```

---

## 🤔 Why not just use X?

| Tool | Problem |
|------|--------|
| **Cron** | No coordination, no retries, silent failures |
| **Redis / Celery** | Requires running and maintaining a server |
| **RabbitMQ** | Heavy infra, steep learning curve |
| **Firebase** | Vendor lock-in, SDK bloat, pricing |
| **Intent Bus** | ✅ Single file, deploy anywhere |

---

## 👥 Who is this for?

- Developers running scripts across multiple machines
- People using **Termux / Android automation**
- Indie hackers avoiding infrastructure complexity
- Anyone who wants job queues without Redis/RabbitMQ

---

## 🔐 Authentication (Dual-Auth Model)

Intent Bus supports two modes:

### 1. Standard Auth (Simple)

Send header:

```bash
X-API-KEY: your_key_here
```

Works with:
- curl
- bash scripts
- IoT devices

### 2. Strict Auth (Recommended for production)

- HMAC-SHA256 signed requests
- Nonce-based replay protection
- Canonical request serialization
- Handled automatically by the Python SDK

---

## 🚀 Quickstart (Python SDK - Strict Auth)

[Python SDK](https://github.com/dsecurity49/Intent-Bus-sdk)

```bash
pip install intent-bus
```

## 📦 Official Client SDKs

- **Python SDK:** [github.com/dsecurity49/Intent-Bus-sdk](https://github.com/dsecurity49/Intent-Bus-sdk)
- **Node.js / Go:** *(Coming soon)*

### Publish a job

```python
from intent_bus import IntentClient

client = IntentClient(api_key="your_key_here")

job = client.publish(
    goal="send_notification",
    payload={"message": "Hello from the cloud"},
    idempotency_key="task_123"  # Prevents double-execution
    # visibility="public"  # Allow global workers if needed
)

print(job["id"])
```

### Run a worker

```python
from intent_bus import IntentClient

def handler(payload):
    try:
        print("Received:", payload["message"])
        return True
    except Exception:
        return False

client = IntentClient(api_key="your_key_here")
client.listen(goal="send_notification", handler=handler)
```

> ⚠️ Workers must be idempotent. The same job may be delivered again during retries.

## ⚙️ Quickstart (CURL / Bash - Standard Auth)

### 1. Publish a job

```bash
curl -X POST https://dsecurity.pythonanywhere.com/intent \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: your_key_here" \
  -d '{"goal":"send_notification","payload":{"message":"Hello"}}'
```

### 2. Worker loop

```bash
# Claim
curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=send_notification" \
  -H "X-API-KEY: your_key_here"

# Fulfill
curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/<INTENT_ID>" \
  -H "X-API-KEY: your_key_here"
```

If a job isn’t fulfilled within 60 seconds, it is retried.

---

## 🧩 Example Use Cases

- Trigger a **phone notification** when a scraper finishes
- Run scripts across multiple machines without hardcoding dependencies
- Replace cron pipelines with loosely coupled workers
- Execute remote commands via Termux without exposing ports

---

## ⚡ Features

- **Reliable Delivery** — jobs are retried automatically
- **Atomic Locking** — SQLite prevents race conditions
- **Hybrid Routing (Open Fleet)** — private by default, optional public execution
- **Poison Pill Handling** — failed jobs stop after 3 attempts
- **Rate Limiting** — 60 req/min per API key
- **Zero-Ops Cleanup** — synchronous lazy-evaluation prevents DB bloat
- **Ephemeral KV Store** — `/set` and `/get` endpoints

---

## 🏗️ Architecture Guarantees

- Jobs are **never silently lost**
- Only one worker can claim a job at a time
- Workers can crash safely without breaking the system

---

## ⚠️ Limitations

- SQLite = **single-writer contention** under high load
- Best for **low to medium traffic workloads**
- Not a replacement for Kafka / RabbitMQ at scale

---

## 🛠️ Setup

### Server (PythonAnywhere / VPS)

⚠️ **Requirement:** SQLite 3.35.0+ is required (for atomic `RETURNING`).

```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

```bash
git clone https://github.com/dsecurity49/Intent-Bus.git
cd Intent-Bus
pip install -r requirements.txt
```

Set your API key:

```bash
export BUS_SECRET="your_key_here"
```

Run the server:

```bash
python flask_app.py
```

### Advanced Configuration (Production)

```bash
export BUS_DB_PATH=/path/to/persistent/infrastructure.db
export BUS_MAINTENANCE_MODE=false
```

### Worker (Termux / Linux)

```bash
pkg install jq curl   # Termux
sudo apt install jq curl
```

```bash
echo "your_key_here" > ~/.apikey
chmod +x worker.sh
./worker.sh
```

---

## 🌍 Try It Live

```text
https://dsecurity.pythonanywhere.com
```

To get a tester key:
- Dev.to: https://dev.to/d_security
- GitHub Issues: https://github.com/dsecurity49/Intent-Bus/issues
- Discord: https://discord.gg/bzAneAQzGX

---

## 💡 Why I built this

I wanted to trigger scripts on my Android phone from a cloud server
without Firebase, open ports, or complex infrastructure.

So I built a tiny job bus using Flask + SQLite.

It worked — and became this project.

---

## 📁 Files

| File | Purpose |
|------|--------|
| `flask_app.py` | Core server |
| `worker.sh` | Termux worker |
| `logger.sh` | Logging worker |
| `Examples/` | Sample workers |
| `SPEC.md` | Protocol spec |

----
## 📜 License

MIT
