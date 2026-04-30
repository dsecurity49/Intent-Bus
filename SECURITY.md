# Security Policy

## Supported Versions

| Component | Version | Status |
| :--- | :--- | :--- |
| Intent Bus Server | v7.0.1+ | ✅ Supported |
| Python SDK (`intent-bus`) | v1.1.0+ | ✅ Supported |

---

## Security Model Overview

Intent Bus follows a **Dual-Auth Model** designed to balance usability and security:

### 1. Standard Authentication

- Requires `X-API-KEY` over HTTPS
- Protects against passive network interception
- **Limitation:** Does NOT protect against replay attacks if requests are captured

### 2. Strict Authentication (HMAC)

- Uses:
  - Timestamp
  - Nonce
  - HMAC-SHA256 signature
- Provides:
  - Replay protection
  - Payload integrity
  - Request authenticity

**Recommendation:**  
Strict Auth SHOULD be used in all production environments.

---

## Threat Model (High-Level)

Intent Bus is designed to mitigate:

- Replay attacks (Strict Auth)
- Concurrent claim race conditions (transactional locking)
- Poison-pill job loops (retry limits)
- Cross-tenant data access (API key scoping)

Intent Bus does NOT attempt to mitigate:

- Malicious workers executing arbitrary payload logic
- Compromised API keys
- Side-channel or host-level attacks

---

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Instead, report privately via:

- Dev.to: https://dev.to/d_security

---

### Include in Your Report

- Clear description of the issue
- Step-by-step reproduction
- Proof of concept (if possible)
- Potential impact

---

### Response Policy

- Acknowledgement: within 48 hours
- Initial triage: within 3–5 days
- Fix timeline: depends on severity

Valid reports may result in:
- Public credit (optional)
- Patch and disclosure notes

---

## Security Best Practices

When using Intent Bus:

- NEVER expose API keys in client-side code
- ALWAYS use HTTPS
- USE Strict Auth for production systems
- AVOID placing sensitive secrets in payloads
- ROTATE API keys periodically

---

## Known Limitations

### 1. Payload Sensitivity

Intent payloads are not encrypted at rest.

**Do not store:**
- API keys
- Passwords
- Secrets

---

### 2. Data Retention

- Intents are ephemeral
- Expired and fulfilled jobs are pruned
- KV store values expire via TTL

---

### 3. Concurrency Constraints

- SQLite uses a single-writer model
- High write contention may lead to:
  - Increased latency
  - Temporary request failures

---

### 4. Replay Protection Scope

- Only enforced in Strict Auth mode
- Standard Auth remains replayable by design

---

## Out of Scope

The following are NOT considered vulnerabilities:

- Denial of Service via excessive valid requests (rate limits apply)
- Worker-side execution bugs
- Misuse of API keys by authorized users
- Expected retry behavior of jobs

---

## Disclosure Policy

- Fixes will be released before public disclosure
- Security patches may be shipped silently for critical issues
- Changelogs will include relevant security notes when appropriate

---

## Contact

For all security-related concerns:

👉 https://dev.to/d_security
👉 https://discord.gg/bzAneAQzGX

---

## License

MIT
