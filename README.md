# Intent Bus

[![PyPI version](https://img.shields.io/pypi/v/intent-bus.svg)](https://pypi.org/project/intent-bus/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Run code on any device from anywhere — using just HTTP.**

A zero-infrastructure job coordination system with retries, locking, and cross-device workers.  
Built for developers who want something more reliable than cron, without the overhead of Redis, RabbitMQ, or Firebase.

📖 [Read the full story](https://dev.to/d_security/how-i-control-my-android-phone-from-a-cloud-server-using-100-lines-of-flask-2fl6)

---

## 🔥 What makes this different?

- Trigger your **Android phone from a cloud server**
- Run jobs across devices **without opening ports**
- Build distributed systems using **just HTTP + curl**
- No brokers, no queues, no infrastructure

> No Firebase. No message queues. Just a single Flask file.

---

## 🧠 How it works (30 seconds)

1. A client **POSTs a job** to `/intent`
2. Workers **poll `/claim`** for matching jobs
3. One worker **atomically claims** the job (SQLite lock)
4. Worker executes and calls `/fulfill`
5. If it crashes → job is **automatically retried**

---

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
- Just pass:

```bash
X-API-KEY: your_key
```

- Works with:
  - curl
  - bash scripts
  - IoT devices

---

### 2. Strict Auth (Recommended for production)
- HMAC-SHA256 signed requests
- Nonce-based replay protection
- Handled automatically by the Python SDK

---

## 🚀 Quickstart (Python SDK - Strict Auth)

```bash
pip install intent-bus
```

### Publish a job

```python
from intent_bus import IntentClient

client = IntentClient(api_key="your_key_here")

job = client.publish(
    goal="send_notification",
    payload={"message": "Hello from the cloud"}
)

print(job["id"])
```

### Run a worker

```python
from intent_bus import IntentClient

def handler(payload):
    print("Received:", payload["message"])

client = IntentClient(api_key="your_key_here")

# Blocking loop
client.listen(goal="send_notification", handler=handler)
```

---

## ⚙️ Quickstart (CURL / Bash - Standard Auth)

### 1. Publish a job

```bash
curl -X POST https://dsecurity.pythonanywhere.com/intent \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_key_here" \
  -d '{"goal":"send_notification","payload":{"message":"Hello"}}'
```

### 2. Worker loop

```bash
# Claim
curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=send_notification" \
  -H "X-API-Key: your_key_here"

# Fulfill
curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/<INTENT_ID>" \
  -H "X-API-Key: your_key_here"
```

> If a job isn’t fulfilled within 60 seconds, it is automatically retried.

---

## 🧩 Example Use Cases

- Trigger a **phone notification** when a scraper finishes
- Run scripts across multiple machines without hardcoding dependencies
- Replace cron pipelines with loosely coupled workers
- Execute remote commands via Termux without exposing ports

---

## ⚡ Features

- **At-Least-Once Delivery** — jobs are retried automatically
- **Atomic Locking** — SQLite transactions prevent race conditions
- **Poison Pill Handling** — failed jobs stop after 3 attempts
- **Goal Routing** — workers filter jobs via `?goal=`
- **Rate Limiting** — 60 req/min per key + IP
- **Tester Keys** — isolate external users
- **Ephemeral KV Store** — `/set` and `/get` endpoints

---

## 🏗️ Architecture Guarantees

- Jobs are **never silently lost**
- Only one worker can claim a job at a time
- Workers can crash safely without breaking the system

---

## ⚠️ Limitations

- SQLite = **not designed for high concurrency**
- Best for **low to medium traffic workloads**
- Not a replacement for Kafka / RabbitMQ at scale

---

## 🛠️ Setup

### Server (PythonAnywhere)

1. Clone the repo  
2. Set your API key in WSGI:

```python
import os
os.environ['BUS_SECRET'] = 'your_key_here'
```

3. Deploy `flask_app.py`

---

### Worker (Termux / Linux)

```bash
pkg install jq curl     # Termux
sudo apt install jq curl
```

```bash
echo "your_key_here" > ~/.apikey
chmod +x worker.sh
./worker.sh
```

---

## 🌍 Try It Live

Public instance:

```
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

---

## 📜 License

MIT
