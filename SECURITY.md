# Security Policy

## Supported Versions

| Component | Version | Status |
| :--- | :--- | :--- |
| Intent Bus Server | v7.0.1+ | ✅ Supported |
| Python SDK (`intent-bus`) | v1.1.0+ | ✅ Supported |

---

## Security Model Overview

Intent Bus uses a **Dual-Auth Model** to balance simplicity and security.

### 1. Standard Authentication

- Requires `X-API-KEY` over HTTPS
- Protects against passive network interception

**Limitation:**
- Requests can be replayed if captured by an active attacker

---

### 2. Strict Authentication (HMAC)

Each request includes:

- Timestamp
- Nonce
- HMAC-SHA256 signature

Provides:

- Replay protection
- Payload integrity
- Request authenticity

**Recommendation:** Use Strict Auth in all production environments.

---

## Server Operations

### Admin Dashboard Access

If the optional dashboard is enabled in `flask_app.py`, it is protected via HTTP Basic Auth:

- **Username:** `admin` (fixed)
- **Password:** Set via `DASHBOARD_PASSWORD` environment variable

---

### Reverse Proxy & HTTPS

The server enforces HTTPS in production using the `X-Forwarded-Proto` header.

If deploying behind Nginx / Apache:

- Ensure this header is forwarded correctly
- Otherwise, requests may be rejected with `403 Forbidden`

---

## Threat Model (High-Level)

### Mitigated

- Replay attacks (Strict Auth only)
- Concurrent claim race conditions (SQLite transactional locking)
- Infinite retry loops (3-attempt limit)
- Cross-tenant access (API key isolation)

### Not Mitigated

- Malicious or unsafe worker execution
- Compromised API keys
- Host-level or VPS compromise
- Side-channel attacks

---

## Reporting a Vulnerability

**Do not open public GitHub issues for security vulnerabilities.**

Report privately via:

- **Email:** dsecurity49@gmail.com

---

### Include in Your Report

- Clear description of the issue
- Step-by-step reproduction
- Proof of concept (if available)
- Impact assessment

---

### Response Policy

- **Acknowledgement:** within 48 hours  
- **Initial triage:** within 3–5 days  
- **Fix timeline:** depends on severity  

Valid reports may receive:

- Credit in release notes (optional)
- Fast-tracked fixes

---

## Security Best Practices

When using Intent Bus:

- NEVER expose API keys in client-side code
- ALWAYS use HTTPS
- USE Strict Auth in production
- AVOID storing sensitive data in payloads
- ROTATE API keys periodically

---

## Known Limitations

### 1. Payload Exposure

- Payloads are **not encrypted at rest**
- Public intents (`visibility="public"`) can be claimed by any authenticated worker

**Do NOT include:**

- API keys
- Passwords
- PII
- Secrets of any kind

---

### 2. Data Retention

- Intents are ephemeral
- Completed and expired jobs are pruned automatically
- KV store values expire via TTL

---

### 3. Concurrency Constraints

- SQLite uses a **single-writer model**

Under high load:

- Increased latency may occur
- Requests may fail temporarily (`503 Database Busy`)

---

### 4. Replay Protection Scope

- Enforced **only in Strict Auth**
- Standard Auth is replayable by design

---

## Out of Scope

The following are NOT considered vulnerabilities:

- Denial of Service via valid requests (rate limits apply)
- Worker-side execution bugs
- Unsafe user code (e.g., `eval`)
- API key misuse by authorized users
- Expected retry behavior

---

## Disclosure Policy

- Fixes are released before public disclosure
- Critical patches may be shipped without prior notice
- Changelogs include relevant security notes when applicable

---

## Contact

For security concerns:

- **Email:** dsecurity49@gmail.com  
- **Discord:** https://discord.gg/bzAneAQzGX  
  *(DM `dsecurity` for non-sensitive communication)*

---

## License

MIT
