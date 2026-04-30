# RFC: Intent Protocol
## Version 1.0

### Status
Draft

### Author
Dsecurity

### Date
2026-04-30

---

## Abstract

The Intent Protocol defines a lightweight, HTTP-based job coordination system for unreliable networks and distributed workers.

It provides:
- At-least-once delivery
- Atomic job claiming
- Retry and failure handling
- Optional cryptographic request authentication

The protocol is designed to operate without external infrastructure and is suitable for environments ranging from mobile devices to cloud servers.

---

## 1. Terminology

The key words **MUST**, **SHOULD**, and **MAY** are to be interpreted as described in RFC 2119.

- **Intent**: A unit of work submitted to the system.
- **Worker**: A client that claims and executes intents.
- **Publisher**: A client that creates intents.
- **Bus**: The server implementing this protocol.

---

## 2. Overview

The protocol operates over HTTP and defines a shared state machine for job execution.

A publisher submits an Intent.  
A worker claims it, executes it, and marks it complete.

The system guarantees:
- Jobs are not silently lost
- Jobs may be retried if execution fails

---

## 3. Intent Lifecycle

### 3.1 States

An Intent MUST exist in one of the following states:

- **open** — Available for claiming  
- **claimed** — Locked by a worker  
- **fulfilled** — Successfully completed (terminal)  
- **failed** — Permanently failed (terminal)  

---

### 3.2 State Transitions

| From     | To         | Condition |
|----------|-----------|----------|
| open     | claimed   | Worker claims job |
| claimed  | fulfilled | Worker completes job |
| claimed  | failed    | Worker explicitly fails job |
| claimed  | open      | Claim timeout expires |
| claimed  | failed    | Retry limit exceeded |

---

### 3.3 Retry Semantics

- A claim lock MUST expire after a fixed timeout (default: 60 seconds)
- A job MUST be retried if it is not fulfilled before timeout
- A job MUST transition to `failed` if:
  - `claim_attempts >= MAX_ATTEMPTS` (default: 3)

---

## 4. Authentication

The protocol defines two authentication modes.

Servers MUST support both.

---

### 4.1 Standard Authentication

Clients MUST include:

```
X-API-KEY: <key>
```

Requirements:
- MUST be used over HTTPS
- Provides authentication only (no replay protection)

---

### 4.2 Strict Authentication (HMAC)

Clients MAY use request signing for enhanced security.

---

### 4.3 Required Headers

```
X-API-KEY: <key>
X-Timestamp: <unix timestamp>
X-Nonce: <unique value>
X-Signature: <lowercase hex digest>
```

---

### 4.4 Validation Rules

Servers MUST:

- Reject timestamps outside ±300 seconds
- Reject reused nonces per API key
- Validate HMAC signature

Servers SHOULD:

- Limit nonce storage to a bounded window
- Enforce per-key nonce quotas

---

### 4.5 Signature Construction

The signature MUST be computed as the SHA-256 HMAC of the following payload, using the API key as the secret.

The result MUST be encoded as a lowercase hexadecimal string.

```
METHOD \n
CANONICAL_PATH \n
TIMESTAMP \n
NONCE \n
BODY
```

- `METHOD` MUST be uppercase
- `BODY` MUST be the raw request body (or empty if none)

---

### 4.6 Canonical Path

- MUST include path and query string
- Query parameters MUST be:
  - Sorted lexicographically by key
  - Percent-encoded per RFC 3986

Example:

```
/claim?goal=notify
```

---

## 5. Intent Object

Returned when a job is successfully claimed:

```json
{
  "id": "string",
  "goal": "string",
  "payload": {},
  "claim_attempts": 1
}
```

---

## 6. API Definition

---

### 6.1 Create Intent

**POST /intent**

Creates a new intent.

#### Request

```json
{
  "goal": "string",
  "payload": {}
}
```

#### Constraints

- `goal` MUST be a non-empty string
- `payload` MUST be a JSON object

#### Optional Header

```
Idempotency-Key: <UUID>
```

#### Behavior

- Server MUST ensure idempotency if header is present
- Reuse of the same key with a different body MUST return `409 Conflict`

#### Responses

- `201 Created`
- `400 Bad Request`
- `409 Conflict`

---

### 6.2 Claim Intent

**POST /claim**

Optional query:

```
?goal=<string>
```

#### Behavior

- MUST atomically select and lock a job
- MUST increment `claim_attempts`
- MUST only return jobs that are:
  - `open`, OR
  - `claimed` but expired

#### Responses

- `200 OK` (returns intent)
- `204 No Content`

---

### 6.3 Fulfill Intent

**POST /fulfill/<id>**

#### Behavior

- MUST transition intent to `fulfilled`
- MUST only allow the claiming worker to fulfill

#### Responses

- `200 OK`
- `404 Not Found`

---

### 6.4 Fail Intent

**POST /fail/<id>**

#### Request

```json
{
  "error": "string"
}
```

#### Behavior

- MUST transition intent to `failed`
- SHOULD store error message (truncated if necessary)

#### Responses

- `200 OK`
- `404 Not Found`

---

## 7. Ephemeral Key-Value Store

A scoped key-value store for coordination between clients.

Keys MUST be isolated per API key.

---

### 7.1 Set Value

**POST /set/<key>**

```json
{
  "value": "string",
  "ttl": 600
}
```

- `ttl` MUST be bounded by server limits

---

### 7.2 Get Value

**GET /get/<key>**

Responses:

- `200 OK`
```json
{ "value": "..." }
```

- `404 Not Found`

---

## 8. Guarantees

Implementations MUST provide:

- At-least-once delivery
- Atomic job claiming
- Retry on failure
- Poison pill protection
- Per-key isolation

---

## 9. Non-Goals

The protocol does NOT guarantee:

- Exactly-once execution
- Message ordering
- Distributed consensus
- Infinite scalability

---

## 10. Security Considerations

- API keys MUST be kept secret
- HTTPS MUST be enforced
- Replay attacks MUST be mitigated (Strict Auth)
- Servers SHOULD implement rate limiting
- Servers SHOULD enforce payload size limits
- Servers SHOULD validate input types

---

## 11. Implementation Notes

- SQLite is sufficient for single-node deployments
- Locking SHOULD be transactional (e.g., `BEGIN IMMEDIATE`)
- Cleanup of expired jobs SHOULD be periodic
- Systems SHOULD handle database contention gracefully

---

## 12. Versioning

- Version: 1.0
- Breaking changes MUST increment major version
- Additive changes SHOULD be backward-compatible

---

## 13. Compatibility

An implementation is compliant if it:

- Implements required endpoints
- Enforces authentication rules
- Maintains lifecycle guarantees

---

## License

MIT
