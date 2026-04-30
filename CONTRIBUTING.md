# Contributing to Intent Bus

First off — thank you for considering contributing 🙌  
Intent Bus grows through community ideas, workers, and protocol improvements.

---

## ⚖️ Philosophy

Intent Bus is built on three core principles:

### 1. Protocol First
We are defining a **protocol standard** (`RFC: Intent Protocol`), not just a Flask app.

Any change to the server MUST consider:
- Bash / CURL clients
- Official SDKs (Python)
- Future ports (Go, Rust, Node, etc.)

---

### 2. Zero-Ops Core
The server is intentionally minimal. We avoid:
- External brokers (Redis, RabbitMQ)
- Heavy ORMs
- Complex configuration systems

We prefer:
- Standard Library
- SQLite
- Simple, inspectable logic

---

### 3. At-Least-Once is Law
We prioritize **reliability over performance**.

- Jobs MUST not be lost  
- Retries are expected  
- Workers MUST handle duplicate execution safely (**idempotency**)  

---

## 🛠️ Ways to Contribute

### 1. Build a Worker (Best First Contribution)

Workers are what make the bus useful.

- Add your script to the `Examples/` directory  
- Use a clear name: `telegram_worker.py`, `sms_worker.sh`, etc.  
- Use either:
  - **Standard Auth** (CURL / Bash)
  - **Strict Auth** (Python SDK)

**Requirement:**  
Workers MUST report failures back to the bus:

```bash
POST /fail/<id>
```

---

### 2. Improve the Python SDK

The SDK is critical for production adoption.

Areas to improve:
- Retry logic
- Canonical request serialization
- Signature generation
- Developer experience (DX)

---

### 3. Build SDKs in Other Languages

Want Intent Bus in **Go, Rust, or Node.js**?

Use the protocol spec (`SPEC.md` / RFC) as the source of truth and build a compliant client.

---

## 🧪 Development Setup

```bash
git clone https://github.com/<your-username>/Intent-Bus.git
cd Intent-Bus

# Install dependencies
pip install -r requirements.txt

# Set your local admin key
export BUS_SECRET="dev_key_here"

# Start the server
python flask_app.py
```

Server will run at:

```
http://localhost:5000
```

---

## 🔍 Testing Guidelines

Before submitting a PR, verify your changes against both authentication modes.

### Standard Auth Test

```bash
curl -X POST http://localhost:5000/intent \
  -H "X-API-KEY: dev_key_here" \
  -H "Content-Type: application/json" \
  -d '{"goal":"test","payload":{"msg":"hello"}}'
```

### Claim Flow Test

```bash
curl -X POST "http://localhost:5000/claim?goal=test" \
  -H "X-API-KEY: dev_key_here"
```

Ensure:
- Intents can be created
- Workers can claim jobs
- Jobs can be fulfilled or failed correctly
- Retry behavior still works

---

## 📌 Pull Request Rules

- **Focus:** One feature or fix per PR  
- **Style:** Follow **PEP-8**  
- **Dependencies:** Do NOT introduce external libraries without discussion  
- **Backward Compatibility:** Do NOT break existing API behavior  

---

### 🚫 Breaking Changes

If unavoidable:

- Must be backward-compatible  
- Must be optional  
- MUST update the protocol spec (`SPEC.md` / RFC)  

---

## 💬 Discussions & Help

For large ideas, open an **Issue** first to discuss impact.

You can also reach out:

- Dev.to: https://dev.to/d_security  
- Discord: https://discord.gg/bzAneAQzGX  

---

## 📜 License

By contributing, you agree that your contributions will be licensed under the **MIT License**.
