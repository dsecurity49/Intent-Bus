# RFC: Intent Protocol

## Version 2.1

### Status

Draft

### Author

Dsecurity

### Date

2026-05-20

---

## Abstract

The Intent Protocol defines a lightweight, HTTP-based job coordination system for unreliable networks and distributed workers. It provides:

- At-least-once delivery
- Atomic job claiming with exponential backoff
- Priority-based scheduling
- Namespace isolation
- Worker capability routing
- Dead-letter queue
- Result storage and polling
- Optional cryptographic request authentication
- **Cryptographic lock isolation via ephemeral claim tokens**

The protocol is designed to operate without external infrastructure and is suitable for environments ranging from mobile devices to cloud servers.

---

## 1. Terminology

The key words **MUST**, **SHOULD**, and **MAY** are to be interpreted as described in RFC 2119.

| Term | Meaning |
|---|---|
| Intent | A unit of work submitted to the system |
| Worker | A client that claims and executes intents |
| Publisher | A client that creates intents |
| Bus | The server implementing this protocol |
| Dead Letter | An intent that has exhausted all retry attempts |
| Namespace | A logical partition of intents within the bus |
| Claim Token | An ephemeral cryptographic token required to mutate a claimed intent |

---

## 2. Overview

The protocol operates over HTTP and defines a shared state machine for job execution. A publisher submits an intent. A worker claims it, receives an ephemeral claim token, executes it, and marks it complete or failed using that token.

On failure, the bus applies exponential backoff and retries the intent up to a configurable maximum. Intents that exhaust all attempts are moved to a dead-letter queue for inspection and manual retry.

The system is designed so that jobs are not silently lost during normal queue operation and execution is at-least-once. Because delivery is at-least-once, workers MUST be idempotent. The same intent MAY be executed more than once due to retries, lease expiry, network failures, or lost responses.

---

## 3. Intent Lifecycle

### 3.1 States

An Intent MUST exist in one of the following states:

| State | Description |
|---|---|
| `open` | Available for claiming |
| `claimed` | Locked by a worker; has an active lease and claim token |
| `fulfilled` | Successfully completed (terminal) |
| `dead` | Permanently failed; moved to dead-letter queue (terminal) |

Expired open intents are removed during cleanup and do not transition to `dead`.

### 3.2 State Transitions

| From | To | Trigger |
|---|---|---|
| `open` | `claimed` | Worker claims the intent and receives a `claim_token` |
| `claimed` | `fulfilled` | Worker calls `/fulfill/<id>` providing the valid `claim_token` |
| `claimed` | `open` | Worker calls `/fail/<id>` with `claim_token` and `claim_attempts < max_attempts` |
| `claimed` | `dead` | Worker calls `/fail/<id>` with `claim_token` and `claim_attempts >= max_attempts` |
| `claimed` | `open` | Lease expires and `claim_attempts < max_attempts` |
| `claimed` | `dead` | Lease expires and `claim_attempts >= max_attempts` |
| `dead` | `open` | Admin calls `/admin/intents/<id>/retry` |
| any | `dead` | Admin calls `/admin/intents/<id>/cancel` |

### 3.3 Retry and Backoff Semantics

- A claim lease MUST expire after `claim_timeout` seconds (default: 60).
- `claim_attempts` MUST increment atomically when the intent is successfully claimed.
- On lease expiry or explicit `/fail`, the bus MUST requeue the intent with a backoff delay:

```text
next_run = now + (backoff_base * (2 ^ claim_attempts)) + jitter
```

Where `jitter` is a random value in `[0, 2)` seconds to prevent thundering herd.
- A job MUST transition to `dead` when `claim_attempts >= max_attempts`.
- **If a lease expires and the current `claim_attempts >= max_attempts`, the server MUST transition the intent directly to the `dead` state instead of requeuing it.**
- Default `max_attempts`: 3
- Default `backoff_base`: 5.0 seconds

If a worker attempts to `/fail`, `/extend_claim`, or `/fulfill` an intent using a `claim_token` that has expired or been overwritten by a subsequent claim, the server MUST reject the operation with a `404 Not Found`.

---

## 4. Intent Fields

### 4.1 Publisher-Controlled Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `goal` | string | required | The task type. 1–256 characters. |
| `payload` | any | required | Arbitrary JSON value passed to the worker. |
| `namespace` | string | `"default"` | Logical partition. Alphanumeric, `.`, `-`, `_`. Max 64 chars. |
| `visibility` | string | `"private"` | `"private"` or `"public"`. |
| `priority` | integer | 100 | 0–1000. Higher values are claimed first. |
| `delay` | float | 0.0 | Seconds before the intent becomes claimable. |
| `max_attempts` | integer | 3 | Maximum claim attempts before the intent is dead. 1–20. |
| `backoff_base` | float | 5.0 | Base for exponential backoff in seconds. 1.0–3600.0. |
| `target_worker` | string | null | If set, only the worker with this ID can claim the intent. |
| `required_capability` | string | null | If set, only workers advertising this capability can claim. |

### 4.2 Server-Controlled Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Hex token, 32 characters. |
| `status` | string | Current state. |
| `claim_attempts` | integer | Number of times the intent has been claimed. |
| `claimed_by` | string | API key of the current claimer. |
| `claim_token` | string | Ephemeral cryptographic lock required to mutate a claimed intent. |
| `claimed_at` | float | Unix timestamp of the last claim. |
| `claim_expires_at` | float | Unix timestamp when the current lease expires. |
| `run_at` | float | Unix timestamp when the intent becomes claimable. |
| `created_at` | float | Unix timestamp of creation. |
| `expires_at` | float | Unix timestamp of TTL expiry. Default: 24 hours. |
| `last_error` | string | Error message from the last failure, if any. |
| `result` | any | Stored result from `/fulfill`, if provided. |
| `result_type` | string | `"json"` or `"text"`. |
| `completed_at` | float | Unix timestamp of fulfillment. |

---

## 5. Authentication

The protocol defines two authentication modes for regular clients, plus a separate admin authentication scheme.

### 5.1 Standard Authentication

Clients MUST include:

```text
X-API-KEY: <key>
```

Requirements:

- MUST be used over HTTPS in production
- Provides authentication only
- Does not provide replay protection

### 5.2 Strict Authentication (HMAC)

Clients MAY use request signing for enhanced security. If signature headers are present, the server validates them; if `BUS_REQUIRE_SIGNATURES=true`, all client requests MUST be signed.

Required headers:

```text
X-API-KEY: <key>
X-Timestamp: <unix timestamp>
X-Nonce: <unique value>
X-Signature: <lowercase hex digest>
```

Servers MUST:

- Reject timestamps outside ±300 seconds of server time
- Reject reused nonces within the same 300-second window per API key
- Validate the HMAC signature

#### Signature Construction

The HMAC-SHA256 signature is computed over the following canonical string using the API key as the secret:

```text
METHOD
CANONICAL_PATH
TIMESTAMP
NONCE
BODY
```

Rules:

- `METHOD` MUST be uppercase
- **`BODY` MUST be the exact, unmodified bytes transmitted over the network. Clients MUST NOT parse and re-serialize the payload before signing, as JSON formatting differences (e.g., whitespace, key ordering) will cause signature mismatches.**
- Empty bodies MUST serialize as an empty string
- Query parameters in `CANONICAL_PATH` MUST:
  - be sorted lexicographically by key
  - preserve repeated parameters
  - preserve blank values
  - **be strictly percent-encoded according to RFC 3986 (forward slashes `/` MUST be encoded as `%2F`)**
- The resulting digest MUST be lowercase hexadecimal

### 5.3 Admin Authentication

Admin endpoints are authenticated separately from regular API key auth. Servers MUST support the following methods in order of precedence:

1. `X-Admin-Token: <secret>` if `BUS_ADMIN_SECRET` is configured
2. HTTP Basic authentication with username `admin` and password `DASHBOARD_PASSWORD`

There is no fallback from `X-Admin-Token` to `BUS_SECRET`. Regular client endpoints use API key auth.

---

## 6. Routing

### 6.1 Namespace Isolation

All intents belong to a namespace. Workers MUST specify a namespace when claiming, defaulting to `default`. Intents MUST NOT cross namespace boundaries.

### 6.2 Visibility

- `private` — only workers authenticated using the same API key as the publisher can claim the intent
- `public` — any authenticated worker in the same namespace can claim the intent

### 6.3 Priority

Intents are claimed in descending priority order (higher value = higher priority). Within the same priority, intents are ordered by `run_at` ascending, then `claim_attempts` ascending, then `created_at` ascending, then `id` ascending. Priority scheduling does not guarantee fairness.

### 6.4 Worker Targeting

If `target_worker` is set on an intent, only a worker that presents a matching `X-Worker-ID` header (or `worker_id` query param) can claim it.

### 6.5 Capability Matching

If `required_capability` is set on an intent, only a worker that advertises the matching capability via `X-Worker-Capabilities` header (or `capabilities` query param, comma-separated) can claim it. Capability matching is case-sensitive and exact token matching.

---

## 7. API Definition

All regular endpoints require `X-API-KEY` authentication unless otherwise noted. Admin endpoints require admin authentication (Section 5.3). All responses use `Content-Type: application/json` unless otherwise noted.

Clients sending JSON request bodies SHOULD use `Content-Type: application/json`.

### 7.1 Health Check

**GET /health**

No authentication required. Response `200 OK`:

```json
{ "ok": true, "ts": 1234567890.0, "version": "7.61" }
```

### 7.2 Create Intent

**POST /intent**

#### Request

```json
{
  "goal": "send_notification",
  "payload": { "message": "Hello" },
  "namespace": "default",
  "visibility": "private",
  "priority": 100,
  "delay": 0.0,
  "max_attempts": 3,
  "backoff_base": 5.0,
  "target_worker": null,
  "required_capability": null
}
```

All fields except `goal` and `payload` are optional.

#### Optional Header

```text
Idempotency-Key: <string>
```

If provided, the server MUST return the same response for the same API key and semantically identical JSON request body. Reuse of the same key with a different canonicalized JSON body MUST return `422 Unprocessable Entity`.

#### Responses

| Code | Meaning |
|---|---|
| `201 Created` | Intent published |
| `400 Bad Request` | Missing or invalid fields |
| `413 Payload Too Large` | Total request body exceeds 8KB limit |
| `422 Unprocessable Entity` | Idempotency key reused with different body |
| `429 Too Many Requests` | Open intent limit reached or rate limited |

Response body:

```json
{ "id": "<hex>", "status": "published", "namespace": "default" }
```

### 7.3 Claim Intent

**POST /claim**

#### Query Parameters

| Param | Description |
|---|---|
| `goal` | Filter by goal string (optional) |
| `namespace` | Target namespace (default: `"default"`) |
| `publisher` | Filter by publisher key (admin only, or own key) |
| `worker_id` | Fallback for X-Worker-ID header |
| `capabilities` | Fallback for X-Worker-Capabilities header |

#### Request Headers

| Header | Description |
|---|---|
| `X-Worker-ID` | Worker identifier for `target_worker` matching |
| `X-Worker-Capabilities` | Comma-separated capability list |

#### Behavior

The server MUST atomically select and lock the highest-priority eligible intent. Eligible intents are those where:

- `status = 'open'` OR (`status = 'claimed'` AND lease expired)
- `run_at <= now`
- `expires_at > now`
- `claim_attempts < max_attempts`
- Namespace matches
- Visibility or publisher matches
- `target_worker` matches (if set)
- `required_capability` is satisfied (if set)

Selection order: `priority DESC`, `run_at ASC`, `claim_attempts ASC`, `created_at ASC`, `id ASC`. Lease expiry does not require a background cleanup pass before an intent can be reclaimed; expired intents are eligible for immediate atomic selection.

#### Responses

| Code | Meaning |
|---|---|
| `200 OK` | Intent claimed |
| `204 No Content` | No eligible intent (worker should back off and retry) |

Response body on `200`:

```json
{
  "id": "<hex>",
  "namespace": "default",
  "goal": "send_notification",
  "payload": { "message": "Hello" },
  "claim_attempts": 1,
  "priority": 100,
  "target_worker": null,
  "required_capability": null,
  "claim_token": "<16-byte-hex-string>",
  "claim_timeout": 60
}
```

The `204` response MUST include `Retry-After: 1`.

### 7.4 Extend Claim

**POST /extend_claim/<id>**

Extends the lease on a currently-held intent. Workers performing long-running tasks SHOULD call this periodically to avoid the job being requeued.

#### Request

```json
{ 
  "seconds": 60,
  "claim_token": "<16-byte-hex-string>"
}
```

`seconds` MUST be between 10 and 3600. `claim_token` is strictly required.

#### Responses

| Code | Meaning |
|---|---|
| `200 OK` | Lease extended |
| `404 Not Found` | Intent not found, not owned by caller, or claim_token invalid/expired |

A lease extension MUST fail if the existing lease has already expired. Workers MUST call `/claim` again to re-acquire the intent. **Workers receiving a `404 Not Found` during this operation MUST treat it as a permanent lease loss (the job was reclaimed or deleted) and MUST NOT retry the mutation.**

### 7.5 Fulfill Intent

**POST /fulfill/<id>**

Marks an intent as fulfilled. Optionally stores a result.

#### Request

```json
{
  "claim_token": "<16-byte-hex-string>",
  "result": { "status": "sent" },
  "result_type": "json"
}
```

`claim_token` is strictly required. `result_type` MUST be `"json"` or `"text"`. If omitted, it defaults to `"json"` when a result is provided. Body is optional — calling with no body marks the intent fulfilled with no stored result.

#### Responses

| Code | Meaning |
|---|---|
| `200 OK` | Intent fulfilled |
| `404 Not Found` | Not found, not the current claimer, or claim_token invalid/expired |

**Workers receiving a `404 Not Found` during this operation MUST treat it as a permanent lease loss (the job was reclaimed or deleted) and MUST NOT retry the mutation.**

### 7.6 Fail Intent

**POST /fail/<id>**

Explicitly fails a claimed intent. The bus applies backoff and requeues if attempts remain, or moves to dead if exhausted.

#### Request

```json
{ 
  "claim_token": "<16-byte-hex-string>",
  "error": "Connection timed out" 
}
```

`claim_token` is strictly required.

#### Responses

| Code | Meaning |
|---|---|
| `200 OK` | Processed |
| `404 Not Found` | Not found, not the current claimer, or claim_token invalid/expired |

**Workers receiving a `404 Not Found` during this operation MUST treat it as a permanent lease loss (the job was reclaimed or deleted) and MUST NOT retry the mutation.**

### 7.7 Get Result

**GET /result/<id>**

Retrieves the stored result and full status of an intent. Accessible by the publisher, the current claimer, or an admin.

Response:

```json
{
  "id": "<hex>",
  "namespace": "default",
  "goal": "send_notification",
  "status": "fulfilled",
  "priority": 100,
  "visibility": "private",
  "claim_attempts": 1,
  "run_at": 1234567890.0,
  "claim_expires_at": null,
  "target_worker": null,
  "required_capability": null,
  "result_type": "json",
  "result": { "status": "sent" },
  "completed_at": 1234567890.0
}
```

The response MUST include `error` when `last_error` is present.

### 7.8 Get Status

**GET /status/<id>**

Lightweight status check without the result body. Same access control as `/result`.

### 7.9 Ephemeral KV Store

A scoped key-value store for lightweight coordination. Keys are isolated per API key.

**POST /set/<key>**

```json
{ "value": "hello", "ttl": 600 }
```

`ttl` defaults to 600 seconds. Maximum 86400.

**GET /get/<key>**

Returns `200 { "value": "..." }` or `404`.

---

## 8. Admin API

All admin endpoints require admin authentication (Section 5.3). Admin routes are exempt from tester rate limits and the open-intent cap.

### 8.1 Dashboard

**GET /admin/dashboard**

Returns an HTML dashboard with live queue stats, recent intents, tester keys, and dead letters. Triggers browser Basic auth prompt if credentials are not provided.

### 8.2 Key Management

**POST /admin/generate_key**

```json
{ "owner": "alice" }
```

Returns `201 { "api_key": "tk_...", "owner": "alice" }`.

**POST /admin/revoke_key**

```json
{ "api_key": "tk_..." }
```

Revokes the key and clears all associated rate limits, idempotency keys, and nonces.

### 8.3 Purge

**POST /admin/purge**

```json
{ "confirm": true, "namespace": "my-ns" }
```

`confirm: true` is required. `namespace` is optional — if omitted, purges all intents, dead letters, and housekeeping tables. If provided, purges intents and dead letters in that namespace only.

### 8.4 Manual Cleanup

**POST /admin/cleanup**

Runs the background cleanup pass immediately. Returns stats:

```json
{
  "expired_open_deleted": 0,
  "expired_claims_requeued": 0,
  "expired_claims_dead": 0,
  "fulfilled_deleted": 0,
  "dead_deleted": 0,
  "dead_letters_deleted": 0,
  "store_deleted": 0,
  "rate_limits_deleted": 0,
  "idempotency_deleted": 0,
  "nonces_deleted": 0
}
```

### 8.5 Intent Management

**GET /admin/intents/<id>** — Full intent detail including payload.

**POST /admin/intents/<id>/cancel** — Forces intent to `dead` and archives to dead-letter queue.

**POST /admin/intents/<id>/retry** — Resets the intent to `open` with `claim_attempts = 0`, clears the current lease, clears stored result and error state, and removes any matching dead-letter row.

### 8.6 Dead Letter Queue

**GET /admin/dead** — Lists the 100 most recent dead letters.

**GET /admin/dead/<intent_id>** — Full detail for a dead letter including original payload.

---

## 9. Metrics

**GET /metrics**

Requires either a `Bearer <METRICS_TOKEN>` Authorization header or valid admin credentials (`X-Admin-Token` or HTTP Basic auth).

Returns Prometheus-compatible text format:

```text
# HELP intent_bus_intents_total Total intents by status and namespace
# TYPE intent_bus_intents_total gauge
intent_bus_intents_total{status="open",namespace="default"} 3
intent_bus_intents_total{status="claimed",namespace="default"} 1
# HELP intent_bus_dead_letters_total Total dead-letter intents
# TYPE intent_bus_dead_letters_total gauge
intent_bus_dead_letters_total 0
# HELP intent_bus_tester_keys_total Total active tester keys
# TYPE intent_bus_tester_keys_total gauge
intent_bus_tester_keys_total 2
```

---

## 10. Response Headers

All responses MUST include:

| Header | Value |
|---|---|
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Cache-Control` | `no-store` |
| `X-Intent-Version` | Server protocol version |

---

## 11. Error Format

All error responses MUST use the following shape:

```json
{
  "error": {
    "code": "snake_case_code",
    "message": "Human-readable description."
  }
}
```

Common error codes:

| Code | HTTP | Meaning |
|---|---|---|
| `unauthorized` | 401 | Missing or invalid API key |
| `forbidden` | 403 | Valid key but insufficient permission |
| `not_found` | 404 | Intent or resource not found |
| `invalid_request` | 400 | Missing required fields |
| `invalid_payload` | 400 | Malformed payload |
| `payload_too_large` | 413 | Total request body exceeds 8KB limit |
| `idempotency_conflict` | 422 | Key reused with different body |
| `rate_limited` | 429 | Too many requests |
| `limit_exceeded` | 429 | Open intent cap reached |
| `database_busy` | 503 | SQLite contention |
| `maintenance` | 503 | Server in maintenance mode |

Additional specific `invalid_*` errors MAY be returned for malformed fields.

---

## 12. Guarantees

Implementations MUST provide:

- At-least-once delivery
- Atomic job claiming
- Cryptographically isolated claim locks
- Retry with exponential backoff
- Dead-letter archival after max attempts
- Per-namespace and per-key isolation

---

## 13. Non-Goals

The protocol does NOT guarantee:

- Exactly-once execution
- Message ordering
- Distributed consensus
- Infinite scalability

---

## 14. Server Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `BUS_SECRET` | — | Main API key. Required in production. |
| `BUS_ADMIN_SECRET` | — | Admin token secret used by `X-Admin-Token`. |
| `DASHBOARD_PASSWORD` | — | HTTP Basic auth password for dashboard. |
| `BUS_METRICS_TOKEN` | — | Bearer token for `/metrics`. |
| `BUS_DB_PATH` | `infrastructure.db` | Path to the SQLite database file. |
| `BUS_TRUST_PROXY` | `false` | Enable ProxyFix for `X-Forwarded-For`. |
| `BUS_ENFORCE_HTTPS` | `false` | Reject non-HTTPS requests. |
| `BUS_REQUIRE_SIGNATURES` | `false` | Require HMAC signing on all requests. |
| `BUS_MAINTENANCE_MODE` | `false` | Block all non-admin traffic. |
| `BUS_CLEANUP_INTERVAL_SECONDS` | `21600` | Minimum seconds between cleanup passes. 300–86400. |

---

## 15. Security Considerations

- `BUS_SECRET` MUST be kept secret and MUST NOT be the default `dev_secret` in production
- HTTPS MUST be enforced in production (`BUS_ENFORCE_HTTPS=true` or proxy-level TLS)
- Replay attacks are mitigated by Strict Auth (nonce + timestamp window)
- Concurrency attacks are mitigated by ephemeral `claim_token` validation
- Rate limiting: 60 requests/minute per tester key
- Request body size limit: 8KB total per request
- Open intent cap: 2000 open intents per tester key (the main `BUS_SECRET` key is exempt)
- Dead letters are retained for 7 days
- Fulfilled intents are retained for 7 days

---

## 16. Implementation Notes

- **SQLite version 3.35.0 or higher is STRICTLY REQUIRED to support the `UPDATE ... RETURNING` syntax necessary for atomic claiming.**
- SQLite WAL mode is required; `journal_mode` must be set to `WAL`
- Atomic claiming MUST use `BEGIN IMMEDIATE` with `UPDATE ... RETURNING`
- Cleanup is lazy: triggered by request traffic, not a background thread
- If cleanup fails (e.g., database lock), the server enforces a 60-second backoff before retrying to prevent thrashing
- Multiple gunicorn workers will cause SQLite write contention; use `--workers 1 --threads N`

---

## 17. Versioning

- Version: 2.1 (Server API v7.61)
- Breaking changes MUST increment the major version
- Additive changes SHOULD be backward-compatible
- The current protocol version is advertised in the `X-Intent-Version` response header

---

## 18. Compatibility

An implementation is compliant if it:

- Implements all required endpoints in Sections 7 and 8
- Enforces authentication rules in Section 5
- Maintains lifecycle and routing guarantees in Sections 3 and 6
- Respects and requires `claim_token` verification on state mutations

---

## License

MIT
