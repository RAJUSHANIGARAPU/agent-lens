# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Email: rajub4u927@gmail.com

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You'll receive a response within 48 hours. We'll keep you updated as we work on a fix
and will credit you in the release notes (unless you prefer to remain anonymous).

## Security Design

agent-lens is designed with security as a first principle:

- **Local-only by default** — server binds to `127.0.0.1`, never `0.0.0.0`
- **No telemetry** — nothing is ever sent to external servers
- **Secret redaction** — API keys and tokens are stripped before storage
- **CSRF protection** — a per-session token is required for all mutating endpoints
- **XSS prevention** — all user-generated content is HTML-escaped in exports
- **No pickle** — all data is serialized as JSON

See `docs/SECURITY.md` for the full threat model and audit findings.
