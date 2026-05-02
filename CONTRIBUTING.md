# Contributing to Intent Bus

First off — thank you for considering contributing. 🙌 
Intent Bus grows through community ideas, diverse workers, and protocol improvements.

---

## ⚖️ Philosophy

Intent Bus is built on three core principles:

### 1. Protocol First
We are defining a protocol standard (the Intent Protocol), not just a Flask app. Any change to the server MUST consider:
* Bash / cURL clients
* Official SDKs (Python)
* Future language ports (Go, Rust, Node, etc.)

### 2. Zero-Ops Core
The server is intentionally minimal and hermetically sealed. We explicitly avoid:
* External brokers (Redis, RabbitMQ)
* Heavy ORMs
* Complex configuration systems

We strongly prefer:
* The Python Standard Library
* Native SQLite features (WAL, RETURNING)
* Simple, inspectable, single-file logic

### 3. At-Least-Once is Law
We prioritize reliability over performance.
* Jobs MUST NOT be lost.
* Retries are expected and native to the bus.
* Workers MUST handle duplicate execution safely (idempotency).

---

## 🔐 Security Disclosure

If you discover a security vulnerability, **DO NOT open a public issue.**

Instead:
* **Email:** dsecurity49@gmail.com
* **Discord:** DM `dsecurity` directly via our server.

We will:
* Acknowledge within 48 hours.
* Patch quickly.
* Credit you in the release notes (if desired).

---

## 🛠️ Ways to Contribute

### 1. Build a Worker (Best First Contribution)
Workers are what make the bus useful. 
* Add your script to the `examples/` directory.
* Use a clear, descriptive name (e.g., `telegram_worker.py`, `sms_worker.sh`).
* Explore both private tasks and **public intents (Open Fleet)**.

**Worker Requirements:**
* MUST handle duplicate execution safely (idempotency).
* MUST implement retry logic with backoff.
* MUST respect the `CLAIM_TIMEOUT` window.
* MUST mark successful execution via `POST /fulfill/<id>`.
* MUST report failures via `POST /fail/<id>`.

### 2. Improve the Python SDK
The SDK is critical for production adoption. Areas to improve include:
* Resilient retry and backoff logic.
* Canonical request serialization.
* Strict HMAC-SHA256 signature generation.
* Overall Developer Experience (DX).

### 3. Build SDKs in Other Languages
Want Intent Bus in Go, Rust, or TypeScript? Use the protocol spec (`SPEC.md`) as your absolute source of truth and build a compliant client.

---

## 🧪 Development Setup

**Requirement:** SQLite 3.35.0+ is required (for the atomic `RETURNING` clause). 
Check your version with:
```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

```bash
git clone https://github.com/dsecurity/Intent-Bus.git
cd Intent-Bus
pip install -r requirements.txt
```

### Set Environment Variables

**Linux / macOS:**
```bash
export BUS_SECRET="dev_key_here"
```

**Windows (PowerShell):**
```powershell
setx BUS_SECRET "dev_key_here"
```

### Run Server
```bash
python flask_app.py
```
The server will boot at: `http://localhost:5000`

---

## 🔍 Testing Guidelines

Before submitting a Pull Request, verify your changes against the core protocol loops:

**1. Standard Auth Test (Publishing)**
```bash
curl -X POST http://localhost:5000/intent \
  -H "X-API-KEY: dev_key_here" \
  -H "Content-Type: application/json" \
  -d '{"goal":"test", "visibility":"public", "payload":{"msg":"hello"}}'
```

**2. Claim Flow Test (Consuming)**
```bash
curl -X POST "http://localhost:5000/claim?goal=test" \
  -H "X-API-KEY: dev_key_here"
```

**3. Concurrency Test (CRITICAL)**
Run multiple workers simultaneously and verify:
* No duplicate claims for the same intent.
* Lock contention (`SQLITE_BUSY`) degrades gracefully.
* Synchronous garbage collection does not trigger SQLITE_BUSY timeouts.

**4. Security Checks**
Verify:
* Invalid HMAC signatures are rejected.
* Replay attacks fail (timestamps are within the 300s window).
* Rate limits trigger correctly (60 requests/min).

---

## 📌 Pull Request Rules

* **Focus:** One feature or fix per PR. Keep diffs small.
* **Style:** Follow PEP-8. Keep it clean and release-grade.
* **Dependencies:** Do NOT introduce external libraries without opening an issue for discussion first.
* **Backward Compatibility:** Do NOT break existing API behavior.

---

## 🔄 Versioning & Compatibility

API changes MUST be backward compatible. If a breaking change is completely unavoidable:
* It MUST be optional/opt-in where possible.
* It MUST bump the major version.
* It MUST strictly update the protocol spec (`SPEC.md`).
* It MUST include a clear migration path.

---

## 💬 Discussions & Help

For large architectural ideas or protocol changes, please open an Issue first to hash out the design.

* **Dev.to Blog:** [dev.to/d_security](https://dev.to/d_security)
* **Discord:** [Join the Community](https://discord.gg/bzAneAQzGX)

---

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.
