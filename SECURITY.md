# Security Policy

## Supported Versions

| Component | Version | Status |
|---|---|---|
| Intent Bus Server | v7.61+ | ✅ Supported |
| Python SDK (`intent-bus`) | v2.1.0+ | ✅ Supported |

---

# Security Model Overview

Intent Bus uses a dual-authentication model designed to balance operational simplicity with stronger cryptographic protections when needed.

## 1. Standard Authentication

Requires:

```http
X-API-KEY: <key>
```

Used over HTTPS, this protects against passive interception.

### Limitation

Standard authentication does **not** provide replay protection.
Captured requests MAY be replayed by an attacker capable of intercepting traffic.

---

## 2. Strict Authentication (HMAC)

Strict Auth adds:

- Timestamp validation
- Nonce replay prevention
- HMAC-SHA256 request signing

This provides:

- Replay protection
- Payload integrity
- Request authenticity

Enable globally with:

```bash
BUS_REQUIRE_SIGNATURES=true
```

or allow clients to opt-in by including signature headers.

### Required Headers

```http
X-API-KEY: <key>
X-Timestamp: <unix timestamp>
X-Nonce: <unique nonce>
X-Signature: <hex digest>
```

---

# Cryptographic Guarantees

## Constant-Time Verification

HMAC signatures are verified using constant-time comparison (`hmac.compare_digest`) to mitigate timing attacks.

---

## Replay Window

Strict Auth enforces a bounded timestamp validity window:

```text
±300 seconds
```

Requests outside this skew window are rejected.

---

## Nonce Storage

Nonces are stored in the SQLite `request_nonces` table and scoped per API key.

Expired nonces are purged during scheduled cleanup passes.

---

## Canonical Signature Format

The HMAC-SHA256 signature is computed over the following newline-delimited string:

```text
HTTP_METHOD
CANONICAL_REQUEST_PATH
TIMESTAMP
NONCE
REQUEST_BODY
```

### Canonicalization Rules

- `HTTP_METHOD` MUST be uppercase
- `REQUEST_BODY` MUST use the exact transmitted bytes
- Empty bodies serialize as an empty string
- Query parameters MUST:
  - preserve repeated parameters
  - preserve blank values
  - be sorted lexicographically by key
  - use strict RFC 3986 percent-encoding
  - encode `/` as `%2F`
- The final digest MUST be lowercase hexadecimal

### Important

Clients MUST NOT deserialize and re-serialize JSON before signing.
Whitespace and key-order differences will invalidate signatures.

---

# Claim Ownership Isolation (Protocol v2.1)

Intent Bus v2.1 introduces cryptographically isolated claim locks using ephemeral `claim_token` values.

Any state mutation on a claimed intent now requires the currently valid token.

Protected endpoints:

- `/fulfill/<id>`
- `/fail/<id>`
- `/extend_claim/<id>`

This prevents workers sharing the same API key from interfering with each other’s active claims.

### Lease-Loss Semantics

If a worker receives:

```http
404 Not Found
```

during `/fulfill`, `/fail`, or `/extend_claim`, the worker MUST treat the lease as permanently lost and MUST NOT retry the mutation.

The claim may have:

- expired
- been reclaimed
- been overwritten by another claim
- been deleted

---

# Server Operations

## Admin & Dashboard Access

Admin endpoints (`/admin/*`) use a separate privileged authentication layer.

Supported methods:

### Header Authentication

```http
X-Admin-Token: <BUS_ADMIN_SECRET>
```

### HTTP Basic Authentication

```text
Username: admin
Password: <DASHBOARD_PASSWORD>
```

There is no fallback from admin auth to standard API-key authentication.

---

# Reverse Proxy & HTTPS

HTTPS enforcement:

```bash
BUS_ENFORCE_HTTPS=true
```

When deploying behind Nginx, Apache, Traefik, or another reverse proxy:

- forward `X-Forwarded-Proto`
- enable:

```bash
BUS_TRUST_PROXY=true
```

to activate Werkzeug `ProxyFix`.

---

# Security Headers

The server automatically includes:

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Cache-Control: no-store
X-Intent-Version: <version>
```

Production deployments SHOULD additionally configure:

```text
Strict-Transport-Security
```

at the reverse proxy layer.

---

# Threat Model

## Security Assumptions

Intent Bus assumes:

- trusted server host environment
- correct TLS termination
- secure client credential storage
- reasonably synchronized clocks for Strict Auth

---

## Mitigated Threats

- Replay attacks (Strict Auth only)
- Concurrent claim race conditions
- Lock hijacking between workers
- Infinite retry loops
- Cross-tenant access
- Basic denial-of-service abuse
- Log injection / SIEM spoofing
- Signature timing attacks

---

## Not Mitigated

Intent Bus does NOT attempt to mitigate:

- Arbitrary code execution by workers
- Compromised API keys
- Host/VPS compromise
- Malicious worker logic
- Side-channel attacks

Workers are intentionally treated as unsandboxed execution agents.

---

# Reporting a Vulnerability

## Do NOT open public GitHub issues for vulnerabilities.

Report privately via:

- **Email:** dsecurity49@gmail.com
- **Discord:** DM `dsecurity`

---

# Include in Your Report

Please include:

- clear issue description
- reproduction steps
- proof of concept (if available)
- impact assessment

---

# Response Policy

| Stage | Target |
|---|---|
| Acknowledgement | within 48 hours |
| Initial triage | within 3–5 days |
| Fix timeline | severity dependent |

Valid reports MAY receive release-note credit.

---

# Security Best Practices

When operating Intent Bus:

- ALWAYS use HTTPS
- USE Strict Auth in production
- ROTATE API keys periodically
- STORE API keys securely
- AVOID sensitive payload contents
- GENERATE keys using cryptographically secure randomness
- ENSURE workers are idempotent

API keys SHOULD contain at least 128 bits of entropy.

---

# Deployment Security

## Recommended Deployment
**Use Docker on managed platforms** (Render, Railway, Fly.io) because:
- Multi-threaded request handling prevents queue backlog
- Containers isolate Intent Bus from the host system
- Easier secret management via environment variables

## Not Recommended: PythonAnywhere Free Tier
Single-threaded Gunicorn workers cannot handle concurrent requests reliably:
- Requests queue up and timeout
- Clients experience false "server stalled" errors
- Security validation still runs but latency becomes problematic

**If using PythonAnywhere:** Upgrade to Eco tier ($5/mo minimum) for multi-threaded support.

## Metrics Endpoint Security
The `/metrics` endpoint requires authentication via a bearer token matching `BUS_METRICS_TOKEN` or admin credentials.

```bash
curl -H "Authorization: Bearer <BUS_METRICS_TOKEN>" \
  https://your-bus.render.com/metrics
```
This endpoint exposes operational metrics and should **never be public**.

# Known Limitations

## 1. Payload Exposure

Payloads are NOT encrypted at rest inside SQLite.

### Never include:

- API keys
- passwords
- access tokens
- PII
- secrets

### Public Intent Warning

```text
visibility="public"
```

allows any authenticated worker in the namespace to claim the job.

Public intents should be treated as non-confidential broadcast work items.

---

## 2. Data Retention

Default retention behavior:

| Data | Retention |
|---|---|
| Open intents | TTL-based expiry (default 24h) |
| Fulfilled intents | eligible for deletion after 7 days |
| Dead letters | eligible for deletion after 7 days |
| KV entries | expire at configured TTL |

Cleanup is traffic-triggered and lazy.

---

## 3. Anti-DoS Limits

| Limit | Value |
|---|---|
| Payload size | 8KB |
| Rate limit | 60 req/min per API key |
| Open intent cap | 2000 open/claimed intents per key |

Admin credentials bypass these limits.

---

## 4. Concurrency & Scaling

SQLite operates in WAL mode but remains a single-writer architecture.

Under load:

- latency MAY increase
- temporary `503 Service Unavailable` responses MAY occur
- `SQLITE_BUSY` contention MAY appear

Clients SHOULD implement exponential backoff with jitter.

For workloads beyond low-thousands of jobs/minute:

- federate buses, or
- migrate `get_db()` to PostgreSQL

---

## 5. Replay Protection Scope

Replay protection exists ONLY under Strict Auth.

Standard authentication remains replayable by design.

---

# Out of Scope

The following are NOT considered vulnerabilities:

- Denial-of-service using valid requests
- Worker-side execution bugs
- Unsafe user code
- API-key misuse by authorized users
- Expected retry behavior
- SQLite contention under heavy concurrency

---

# Responsible Disclosure History

## 2026-05-21 — Claim Isolation & HMAC Determinism

### Lock Hijacking Mitigation

Previously, workers sharing the same API key could potentially interfere with active claims if they knew the intent ID.

Protocol v2.1 fixed this by introducing ephemeral `claim_token` validation for all state mutations.

### HMAC Canonicalization

Fixed deterministic-signature mismatches by enforcing strict RFC 3986 percent-encoding during canonical request generation.

---

## 2026-05-17 — Admin Escalation & Default Credential Hardening

### Default Secret Rejection

The default `dev_secret` was previously accepted under debug conditions.

This behavior was removed entirely.

### Admin Token Fallback

Older behavior allowed fallback from admin auth to `BUS_SECRET`.

This was replaced with fail-closed admin authentication.

---

# Disclosure Policy

- Security fixes are released before public disclosure
- Critical patches MAY ship without advance notice
- Relevant fixes are documented in changelogs

---

# Contact

For security concerns:

- **Email:** dsecurity49@gmail.com
- **Discord:** https://discord.gg/bzAneAQzGX

For non-sensitive communication, DM `dsecurity`.

---

# License

MIT
