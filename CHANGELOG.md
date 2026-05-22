# Changelog

## [7.61] - 2026-05-21

### Security
- **Protocol v2.1 (Claim Ownership Isolation):** Introduced ephemeral cryptographic `claim_token` requirements for job mutation. Endpoints (`/fulfill`, `/fail`, and `/extend_claim`) now strictly require this token to mutate state, eliminating lease hijacking vulnerabilities between workers sharing the same API key.

### Fixed
- **HMAC Canonicalization:** Enforced strict RFC 3986 percent-encoding (translating `/` to `%2F`) during signature generation to guarantee deterministic signatures across SDK implementations and prevent verification mismatches.
- **Dead Intent Visibility:** Removed `claimed_by=NULL` during the `dead` transition. Workers that exhaust a job's retry attempts now correctly retain read-access to the telemetry of the dead job via `/result/<id>`.

---

## [7.6] - 2026-05-17

### Security
- **Queue Exhaustion Armor:** Publisher quota limits now evaluate both `open` and `claimed` states to prevent malicious workers from hoarding jobs and bypassing limits.
- **Log Injection Prevention:** Strict regex sanitization (`^[a-zA-Z0-9_.:-]{1,128}$`) applied to `X-Request-ID` headers to prevent SIEM spoofing.
- **Default Secret Rejection:** Unconditional rejection of `dev_secret` in production.
- **Admin Auth Hardened:** Fail-closed architecture with no fallback to standard API credentials.
- **Worker Security:** Symlink detection and strict file permissions (mode 600) enforced in edge workers.

### Added
- **Structured Observability:** Context-aware JSON logging with strict telemetry whitelists and defensive serialization fallbacks.
- **Request Tracing:** End-to-end trace IDs (`X-Request-ID`), remote IP capture, and `duration_ms` performance tracking.
- **Cleanup Telemetry:** Cleanup passes now emit explicit `cleanup_complete` JSON events with row deletion statistics.
- **Prometheus Metrics:** Added `/metrics` endpoint for intent counts and system health.
- **CI/CD & Testing:** Added GitHub Actions CI and a `pytest` suite covering replay attacks, dead-letter handling, and capability routing.

### Changed
- **Queue Elasticity:** Increased `MAX_OPEN_INTENTS_PER_KEY` from 100 to 2,000 to better absorb publisher bursts during worker downtime.

### Fixed
- **SQLite Thrashing:** Added an explicit 60-second cleanup failure cooldown (`last_cleanup_error_time`) to prevent repeated lock-contention cleanup attempts from bottlenecking request handling.
- **Silent Debounce Bug:** Cleanup scheduling now advances only after a confirmed successful cleanup pass.
- **Privilege Escalation:** Closed access-control vulnerabilities in admin routes.
- **Syntax:** Fixed a trailing tuple formatting bug in the `429 limit_exceeded` response.

### Contributors
- **Zan (@Ghost-Frame)** — Security auditing and hardening patches
- **Dhanush (@dsecurity49)** — Core architecture, protocol updates, and observability
