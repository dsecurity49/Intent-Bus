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
- **Open Fleet:** Public intents (`visibility="public"`) originate from untrusted third parties  

Therefore:

> **Workers MUST treat all input as untrusted.**

---

## 2. Core Security Principles

### 2.1 Least Privilege
Workers SHOULD:
- Run with minimal OS permissions
- Avoid root access unless strictly required
- Restrict filesystem and network access

---

### 2.2 Explicit Trust Boundaries
Workers MUST:
- Validate all incoming payload fields
- Reject incomplete or malformed data
- Avoid assumptions about payload structure

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
- Fields MUST match expected types

Workers SHOULD:
- Enforce maximum payload size limits

---

## 4. Execution Safety

### 4.1 Command Execution (CRITICAL)

Workers MUST NOT:

- Execute raw payload input directly
- Use `shell=True`, `eval`, or equivalent with untrusted input

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
- Prevent memory exhaustion

---

## 5. External Network Calls

### 5.1 URL Validation (CRITICAL)

Workers MUST validate outbound URLs.

#### Allowed:
- Explicitly allowlisted domains

#### Forbidden:
- Arbitrary user-provided URLs
- Internal network targets (`localhost`, `127.0.0.1`, `169.254.0.0/16`, etc.)

---

### 5.2 SSRF Protection

Workers MUST:
- Restrict protocols (`https://` only unless explicitly required)
- Validate domain patterns
- Resolve and verify IPs before connecting (prevent DNS rebinding)
- Reject private/internal IP ranges

---

## 6. Authentication Handling

Workers MUST:

- Store API keys securely (e.g., `~/.apikey`)
- NEVER log API keys
- NEVER expose keys in errors or responses

Workers SHOULD:
- Use Strict Auth (HMAC) in production environments
- Rotate API keys periodically

---

## 7. Lifecycle & Error Handling

Workers MUST:

- Call `/fulfill/<id>` upon successful execution
- Call `/fail/<id>` on execution failure
- Provide a meaningful error message when failing
- Avoid silent failures

Workers SHOULD:
- Avoid leaking sensitive internal details in error messages

---

## 8. Rate Limiting & Backoff

Workers SHOULD:

- Implement delay between polling requests
- Use exponential backoff on repeated failures
- Avoid tight retry loops

---

## 9. Resource Limits

Workers SHOULD enforce:

- Execution timeout (e.g., 30 seconds)
- Output size limits
- Memory-safe operations

Workers MAY:
- Use OS-level limits (ulimit, cgroups, containers)

---

## 10. Logging Guidelines

Workers SHOULD log:

- Job ID
- Execution status
- Errors

Workers MUST NOT:

- Log API keys or secrets
- Log sensitive payloads without sanitization

---

## 11. Safe vs Unsafe Worker Modes

### 11.1 Safe Mode (Default)

- Whitelisted actions only
- Restricted external calls
- Suitable for public/shared environments and Open Fleet usage

---

### 11.2 Power Mode (Restricted)

- May execute arbitrary commands
- MUST be used only:
  - With trusted, private intents
  - In isolated environments (VM/container)

Workers operating in Power Mode MUST clearly document:

> ⚠️ **CRITICAL:** This worker executes arbitrary commands.  
> It MUST NEVER be used to claim public intents (`visibility="public"`).

---

## 12. Compliance Checklist

A worker is considered **compliant** if it:

- [ ] Validates payload structure
- [ ] Does NOT execute raw input
- [ ] Uses safe command execution
- [ ] Validates outbound URLs
- [ ] Implements SSRF protections
- [ ] Handles success via `/fulfill`
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

Following this standard ensures:
- Safer automation
- Predictable behavior
- Production readiness

---

## License

MIT
