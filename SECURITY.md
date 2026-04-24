# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :--- |
| Phase 1 (current) | ✅ |

## Authentication

All endpoints require an `X-API-Key` header. If your key is compromised, rotate it immediately by updating the `BUS_SECRET` environment variable in your PythonAnywhere WSGI config and your local `~/.apikey` file.

## Reporting a Vulnerability

If you find a security issue, do not open a public GitHub Issue.

Contact via Dev.to: https://dev.to/d_security

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

I will respond within 48 hours and credit you in the fix commit if you choose.

## Known Limitations

- This is a Phase 1 project. Do not store sensitive secrets in intent payloads.
- The bus is as secure as your API key — keep it out of version control.
- SQLite is single-file — back up `infrastructure.db` if your data matters.
