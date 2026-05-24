# Contributing to Intent Bus

First off — thank you for considering contributing. 🙌

Intent Bus grows through community ideas, diverse workers, and protocol improvements.

---

# Philosophy

Intent Bus is built on several core principles.

## 1. Protocol First

We are defining a protocol standard (the Intent Protocol), not just a Flask application.

Any change to the server MUST consider:
* Bash / curl clients
* Official SDKs (Python)
* Future language ports (Go, Rust, Node.js, etc.)
* Backward compatibility for existing v2.x integrations

The protocol specification (`SPEC.md`) is the authoritative source of truth.

---

## 2. Zero-Ops Core

The server is intentionally minimal and hermetically sealed.

We explicitly avoid:
* External brokers (Redis, RabbitMQ, Kafka, etc.)
* Heavy ORMs
* Distributed coordination systems
* Complex configuration frameworks
* Background worker daemons

We strongly prefer:
* The Python standard library
* Native SQLite features (`WAL`, `RETURNING`)
* Simple, inspectable, single-file logic

The core server SHOULD remain dependency-light and operationally simple.

---

## 3. Fail-Closed Security

Security hardening takes priority over feature expansion.

Core concerns include:
* Authentication
* Replay protection
* Request validation
* Safe worker execution patterns
* SQLite durability and locking behavior
* Protocol correctness under unreliable networks

---

## 4. At-Least-Once Delivery is Fundamental

We prioritize reliability over performance.

* Jobs MUST NOT be silently lost.
* Retries are expected and native to the bus.
* Workers MUST safely handle duplicate execution (idempotency).
* Lease expiry and reclaim behavior are intentional protocol semantics.

---

# Security Disclosure

If you discover a security vulnerability, **DO NOT open a public issue or pull request.**

Instead:
* **Email:** dsecurity49@gmail.com
* **Discord:** DM `dsecurity` directly via the community server

We will:
* Acknowledge the report within 48 hours
* Patch the issue as quickly as possible
* Credit you in release notes if desired

See `SECURITY.md` for full responsible disclosure guidance.

---

# Ways to Contribute

## 1. Build a Worker (Best First Contribution)

Workers are what make the bus useful.

* Add your worker to the `Examples/` directory
* Use a clear descriptive name (`telegram_worker.py`, `sms_worker.sh`, etc.)
* Explore both private workloads and public intents ("Open Fleet")

### Worker Requirements

Workers:
* MUST safely handle duplicate execution (idempotency)
* MUST tolerate retries and exponential backoff
* MUST respect the `claim_timeout` lease window
* SHOULD utilize capability routing (`X-Worker-Capabilities`) for specialized tasks
* MUST report successful execution via `POST /fulfill/<id>` using the ephemeral `claim_token`
* MUST report failures via `POST /fail/<id>` using the ephemeral `claim_token`

### Worker Security Rules

Workers MUST explicitly avoid dangerous execution patterns such as:
* `shell=True`
* `eval()`
* Unsafe deserialization
* Unsandboxed command execution

See `WORKER_SECURITY.md` for additional guidance.

---

## 2. Improve the Python SDK

The SDK is critical for production adoption.

High-value contribution areas include:
* Resilient retry and backoff logic
* Canonical request serialization
* Strict HMAC-SHA256 signature generation
* `asyncio` support (`AsyncIntentClient`)
* OpenTelemetry instrumentation
* Terminal dashboards and operational tooling
* Typed models and stricter validation
* Overall developer experience (DX)

---

## 3. Build SDKs in Other Languages

Want Intent Bus in Go, Rust, or TypeScript?

Use the protocol specification (`SPEC.md`) as the authoritative source of truth and build a compliant implementation.

---

## 4. Server Hardening & Performance

Intent Bus intentionally relies on SQLite and a single-process architecture.

High-value improvements include:
* WAL-mode concurrency optimization
* Reduced `SQLITE_BUSY` contention
* Query/index tuning
* HTTP validation hardening
* Replay-protection robustness
* Cleanup efficiency improvements
* Better operational observability

---

## 5. Run Benchmarks & Stress Tests

Help validate performance and scalability:

```bash
# Install test dependencies
pip install pytest requests

# Run stress test suite (requires live server)
python3 tests/stest_ui.py --all

# Expected results for Docker deployment (Render/Railway):
# - LOW (5 workers, 50 jobs): 100% success, ~3.7 j/s
# - MEDIUM (15 workers, 500 jobs): 98%+ success, ~13.3 j/s
# - HIGH (40 workers, 2000 jobs): 99% success, ~13.6 j/s
```

Report results in your PR with:
- Platform (Render, Railway, local Docker, etc.)
- Worker count
- Success rate
- P99 latency

# Development Setup

## Requirements

SQLite `3.35.0+` is required (for atomic `RETURNING` support).

Check your SQLite version:

```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

Clone the repository:

```bash
git clone https://github.com/dsecurity49/Intent-Bus.git
cd Intent-Bus

pip install -r server-requirements.txt
```

---

## Set Environment Variables

### Linux / macOS

```bash
export BUS_SECRET="dev_key_here"
```

### Windows (PowerShell)

```powershell
setx BUS_SECRET "dev_key_here"
```

---

## Run the Server

```bash
python flask_app.py
```

The server will start at:

```text
http://localhost:5000
```

**Important:** WAL mode MUST be enabled during testing.

---

# Testing Guidelines

Before submitting a Pull Request, verify changes against the core protocol flows.

## 1. Standard Auth Test (Publishing)

```bash
curl -X POST http://localhost:5000/intent \
  -H "X-API-KEY: dev_key_here" \
  -H "Content-Type: application/json" \
  -d '{"goal":"test","visibility":"public","payload":{"msg":"hello"}}'
```

---

## 2. Claim Flow Test (Consuming)

```bash
curl -X POST "http://localhost:5000/claim?goal=test" \
  -H "X-API-KEY: dev_key_here"
```

---

## 3. Concurrency Test (CRITICAL)

Run multiple workers simultaneously and verify:
* No duplicate claims for the same intent
* Lock contention (`SQLITE_BUSY`) degrades gracefully
* Lease expiry behaves correctly
* Synchronous cleanup does not introduce prolonged database locks

---

## 4. Security Checks

Verify:
* Invalid HMAC signatures are rejected
* Replay attacks fail correctly
* Rate limits trigger properly

> **Note:** The primary `BUS_SECRET` bypasses tester rate limits.

To test rate limiting behavior, generate a tester key via:

```text
POST /admin/generate_key
```

---

# Pull Request Rules

* **Focus:** One feature or fix per PR. Keep diffs small and reviewable.
* **Style:** Follow PEP 8. Keep code clean and production-grade.
* **Dependencies:** Do NOT introduce external infrastructure or library dependencies without discussion first.
* **Backward Compatibility:** Do NOT break existing API behavior.
* **Protocol Changes:** Protocol-affecting changes MUST update `SPEC.md`.
* **Documentation:** Update examples and relevant documentation alongside behavior changes.
* **Architecture Changes:** Open an issue before proposing major architectural modifications.

---

# Versioning & Compatibility

API and protocol changes MUST remain backward compatible whenever possible.

If a breaking change is unavoidable:
* It MUST be optional or opt-in where feasible
* It MUST increment the major protocol version
* It MUST update `SPEC.md`
* It MUST include a clear migration path

---

# Discussions & Help

For large architectural ideas or protocol changes, please open an issue first before implementation work begins.

* **Dev.to Blog:** https://dev.to/d_security
* **Discord:** https://discord.gg/bzAneAQzGX

---

# Security & Secrets

Never commit:
* Real API keys
* Production secrets
* SQLite database files
* `.env` files containing credentials

Add sensitive files to `.gitignore` before development.

---

# License

By contributing, you agree that your contributions will be licensed under the MIT License.
