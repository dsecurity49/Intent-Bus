# Changelog

## [7.61] - 2026-05-21

### Security
- **Protocol v2.1 (Claim Ownership Isolation):** Introduced ephemeral cryptographic `claim_token` requirements for job mutation. Endpoints (`/fulfill`, `/fail`, and `/extend_claim`) now strictly require this token to mutate state, eliminating lease hijacking vulnerabilities between workers sharing the same API key.

### Fixed
- **HMAC Canonicalization:** Enforced strict RFC 3986 percent-encoding (translating `/` to `%2F`) during signature generation to guarantee deterministic signatures across SDK implementations and prevent verification mismatches.
- **Dead Intent Visibility:** Dead intents now retain result visibility after retry exhaustion, so telemetry remains readable via `/result/<id>`.

### Edge Performance Validated - 2026-05-26
- ✅ **Validated on Android 12 (Termux):** Successfully hosted entirely on an Android phone using Termux, with the server running on mobile ARM CPU and flash storage.
- ✅ **Heavy Load (Burst):** 28.04 j/s throughput with a 99.07% success rate and P99 latency of 2.556s (outperforming cloud container free tiers).
- ✅ **Extreme Load (Sustained):** 5,000 jobs over 4.5 minutes. Maintained 18.52 j/s sustained throughput (bursting up to 36.7 j/s) with a 98.89% success rate before hitting hardware I/O limits.
- ✅ **Zero architecture failures:** 0 network drops, 0 lease losses, 0 publish rejects, and graceful SQLite WAL degradation under massive hardware contention.

### Performance & Reliability (v7.61) - 2026-05-24
- ✅ **Validated on Docker/Render:** 2000 jobs, 99.01% success rate, 13.6 j/s throughput.
- ✅ **P99 latency:** 2.586s under heavy load (40 concurrent workers).
- ✅ **Zero infrastructure failures:** 0 network errors, 0 lease losses, 0 rate limit errors.
- ✅ **Deployment Validated:** Validated for deployment on Docker-based platforms (Render, Railway, Fly.io).

### Known Limitations
- **PythonAnywhere Free Tier:** Not recommended due to single-threaded Gunicorn worker limitations causing queue backlogs. Use Docker deployments instead.
- **Max concurrent workers:** Regular testing validated stability up to 40 concurrent workers; Extreme Load test at 100+w achieved 98.89% success.

---

## [7.60] - 2026-05-17

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
