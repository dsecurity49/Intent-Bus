#  Worker Security Standard (v1.0)

## Status

Stable

---

## Scope

This document defines the **minimum security requirements** for all Intent Bus workers, including:

- Bash workers
- Python SDK workers
- Third-party integrations

Any worker that interacts with the Intent Bus **SHOULD comply** with this standard.

---

## 1. Threat Model

Workers operate in an **untrusted-by-default environment**:

- Payloads MAY be malformed or malicious
- API keys MAY be leaked or misused
- External endpoints MAY be attacker-controlled
- Workers MAY run on sensitive systems (phones, servers)
- Public intents (`visibility="public"`) originate from untrusted third parties

Therefore:

> **Workers MUST treat all input as untrusted.**

---

## 2. Core Security Principles

### 2.1 Least Privilege

Workers SHOULD:

- Run with minimal OS permissions
- Avoid root access unless strictly required
- Restrict filesystem and network access
- Explicitly advertise only the capabilities they safely support via `X-Worker-Capabilities`

---

### 2.2 Explicit Trust Boundaries

Workers MUST:

- Validate all incoming payload fields
- Reject incomplete or malformed data
- Avoid assumptions about payload structure
- Reject unknown action types by default

---

### 2.3 Fail Closed

On any unexpected condition:

- Workers MUST fail safely
- Workers MUST NOT execute partial or unsafe actions
- Workers SHOULD reject unknown commands or unsupported capabilities

---

## 3. Input Validation Requirements

Workers MUST validate:

### Required Fields

- `id` MUST be present
- Required payload fields MUST NOT be empty

### JSON Integrity

- Payload MUST be valid JSON before parsing

### Type Safety

- Fields MUST match expected types

Workers SHOULD:

- Enforce maximum payload size limits

---

## 4. Execution Safety

### 4.1 Command Execution (CRITICAL)

Workers MUST NOT:

- Execute raw payload input directly
- Use `shell=True`, `eval`, or equivalent with untrusted input
- Dynamically import modules from payload data
- Use unsafe deserialization mechanisms such as:
  - Python `pickle`
  - `yaml.load()` with unsafe loaders

Workers SHOULD:

- Use strict allowlists for permitted actions
- Use argument arrays instead of shell strings

#### ✅ Safe Example

```bash
cmd=("uptime")
"${cmd[@]}"
```

#### ❌ Unsafe Example

```bash
eval "$USER_INPUT"
```

---

### 4.2 Output Handling

Workers SHOULD:

- Limit output size
- Sanitize logs where necessary
- Truncate excessively large output
- Prevent memory exhaustion

---

## 5. External Network Calls

### 5.1 URL Validation (CRITICAL)

Workers MUST validate outbound URLs.

#### Allowed

- Explicitly allowlisted domains

#### Forbidden

- Arbitrary user-provided URLs
- Internal network targets:
  - `localhost`
  - `127.0.0.1`
  - `::1`
  - `169.254.0.0/16`
  - RFC1918 private IP ranges

---

### 5.2 SSRF Protection

Workers MUST:

- Restrict protocols (`https://` only unless explicitly required)
- Validate domain patterns
- Resolve and verify IPs before connecting
- Reject private/internal IP ranges
- Protect against DNS rebinding attacks

---

## 6. Authentication Handling

Workers MUST:

- Store API keys securely (e.g. `~/.apikey`)
- NEVER log API keys
- NEVER expose keys in errors or responses

Workers SHOULD:

- Use Strict Auth (HMAC) in production
- Rotate API keys periodically

---

## 7. Lifecycle & Error Handling

Workers MUST:

- Call `/fulfill/<id>` upon successful execution, providing the ephemeral `claim_token`
- Call `/fail/<id>` on execution failure, providing the ephemeral `claim_token`
- Provide meaningful error messages
- Avoid silent failures

Workers SHOULD:

- Avoid leaking sensitive internal details in errors

---

## 8. Rate Limiting & Backoff

Workers SHOULD:

- Implement delay between polling requests
- Use exponential backoff on repeated failures
- Avoid tight retry loops

---

## 9. Resource Limits

Workers SHOULD enforce:

- Execution timeouts
- Output size limits
- Memory-safe operations

Workers MAY:

- Use OS-level limits (`ulimit`, cgroups, containers)

---

## 10. Logging Guidelines

Workers SHOULD log:

- Job ID
- Execution status
- Errors

Workers SHOULD:

- Prefer structured logs where possible
- Truncate oversized payloads or outputs before logging

Workers MUST NOT:

- Log API keys or secrets
- Log sensitive payloads without sanitization

---

## 11. Safe vs Unsafe Worker Modes

### 11.1 Safe Mode (Default)

- Whitelisted actions only
- Restricted external calls
- Suitable for public/shared environments

---

### 11.2 Power Mode (Restricted)

- May execute arbitrary commands
- MUST only be used:
  - With trusted private intents
  - In isolated environments
  - Inside containers, VMs, or sandboxed systems where possible

Workers operating in Power Mode MUST clearly document:

> ⚠️ **CRITICAL:** This worker executes arbitrary commands.  
> It MUST NEVER claim public intents (`visibility="public"`).

---

## 12. Compliance Checklist

A worker is considered **compliant** if it:

- [ ] Validates payload structure
- [ ] Rejects unknown actions by default
- [ ] Does NOT execute raw input
- [ ] Uses safe command execution
- [ ] Avoids unsafe deserialization
- [ ] Validates outbound URLs
- [ ] Implements SSRF protections
- [ ] Handles success via `/fulfill` (using `claim_token`)
- [ ] Handles errors via `/fail` (using `claim_token`)
- [ ] Implements retry/backoff
- [ ] Avoids logging secrets
- [ ] Enforces timeouts

---

## 13. Non-Goals

This standard does NOT guarantee:

- Complete system security
- Protection from compromised API keys
- Isolation from OS-level attacks

---

## 14. Future Improvements

Planned areas:

- Worker sandboxing guidelines
- Signed worker packages
- Capability-based permission models

---

## 15. Summary

Intent Bus workers are execution engines in a distributed system.

Security is not optional.

> A single unsafe worker can compromise an entire environment.

Following this standard improves:

- Safer automation
- Predictable behavior
- Production readiness

---

## License

MIT
