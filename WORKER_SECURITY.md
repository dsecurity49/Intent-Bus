# 🛡️ Worker Security Standard (v1.0)

## Status
Stable

## Scope
This document defines the **minimum security requirements** for all Intent Bus workers, including:
- Bash workers
- Python SDK workers
- Third-party integrations

Any worker that interacts with the Intent Bus **SHOULD comply** with this standard.

---

## 1. Threat Model

Workers operate in a **hostile-by-default environment**:

- Payloads MAY be malformed or malicious  
- API keys MAY be leaked or misused  
- External endpoints MAY be attacker-controlled  
- Workers MAY run on sensitive systems (phones, servers)

Therefore:

> **Workers MUST treat all input as untrusted.**

---

## 2. Core Security Principles

### 2.1 Least Privilege
Workers SHOULD:
- Run with minimal OS permissions
- Avoid root access unless strictly required
- Limit filesystem and network access

---

### 2.2 Explicit Trust Boundaries
Workers MUST:
- Validate all incoming payload fields
- Reject incomplete or malformed data
- Avoid implicit assumptions about payload structure

---

### 2.3 Fail Closed
On any unexpected condition:
- Workers MUST fail safely
- Workers MUST NOT execute partial or unsafe actions

---

## 3. Input Validation Requirements

Workers MUST validate:

### Required Fields
- `id` MUST be present
- Required payload fields MUST NOT be empty

### JSON Integrity
- Payload MUST be valid JSON before parsing

### Type Safety
- Fields MUST match expected types (string, object, etc.)

---

## 4. Execution Safety

### 4.1 Command Execution (Critical)

Workers MUST NOT:

- Execute raw payload input directly
- Use `shell=True` or equivalent with untrusted input

Workers SHOULD:

- Use **whitelists** for allowed commands
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
- Sanitize logs if needed
- Prevent memory exhaustion

---

## 5. External Network Calls

### 5.1 URL Validation (Critical)

Workers MUST validate outbound URLs.

#### Allowed:
- Explicitly whitelisted domains

#### Forbidden:
- Arbitrary user-provided URLs
- Internal network targets (e.g., `localhost`, `127.0.0.1`)

---

### 5.2 SSRF Protection

Workers MUST:
- Restrict protocols (`https://` only)
- Validate domain patterns
- Reject IP-based URLs unless explicitly allowed

---

## 6. Authentication Handling

Workers MUST:

- Store API keys securely (e.g., `~/.apikey`)
- NEVER log API keys
- NEVER expose keys in error messages

Workers SHOULD:
- Use Strict Auth (HMAC) in production

---

## 7. Error Handling

Workers MUST:

- Call `/fail/<id>` on execution failure
- Provide a meaningful error message
- Avoid silent failures

Workers SHOULD:
- Avoid leaking sensitive internal details in errors

---

## 8. Rate Limiting & Backoff

Workers SHOULD:

- Implement sleep intervals between requests
- Increase delay on repeated failures
- Avoid tight retry loops

---

## 9. Resource Limits

Workers SHOULD enforce:

- Execution timeout (e.g., 30 seconds)
- Output size limits
- Memory-safe operations

---

## 10. Logging Guidelines

Workers SHOULD:

- Log:
  - Job ID
  - Execution status
  - Errors

Workers MUST NOT:
- Log secrets (API keys, tokens)
- Log sensitive payloads without filtering

---

## 11. Safe vs Unsafe Worker Modes

### 11.1 Safe Mode (Default)

- Whitelisted actions only
- Restricted external calls
- Suitable for public/shared environments

---

### 11.2 Power Mode (Restricted)

- May execute arbitrary commands
- MUST be used only:
  - With trusted API keys
  - In isolated environments (VM/container)

Workers operating in Power Mode MUST clearly document:

> ⚠️ This worker executes arbitrary commands and is not safe for untrusted environments.

---

## 12. Compliance Checklist

A worker is considered **compliant** if it:

- [ ] Validates payload structure
- [ ] Does NOT execute raw input
- [ ] Uses safe command execution
- [ ] Validates outbound URLs
- [ ] Handles errors via `/fail`
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
- Capability-based permission model

---

## 15. Summary

Intent Bus workers are **execution engines** in a distributed system.

Security is not optional.

> A single unsafe worker can compromise an entire environment.

Follow this standard to ensure:
- Safe automation
- Predictable behavior
- Production readiness

---

## License

MIT
