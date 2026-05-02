# Security Policy

## Threat Model

agent-lens is a local-first debugging tool. The threat model assumes:

1. **The user is the attacker** — not a threat. The tool runs entirely on the developer's machine.
2. **Malicious LLM output** — agent responses may contain XSS payloads, injection attempts, or social engineering. These must not escape into the dashboard HTML or the export HTML.
3. **Compromised dependencies** — transitive dependencies could supply malicious code.
4. **API key leakage** — LLM API keys present in request headers must never be stored.

## Security Measures

### Network Isolation
- The dashboard server **only binds to `127.0.0.1`** (loopback). There is no mechanism to bind to `0.0.0.0` in the default configuration.
- CORS headers only permit origins from `http://localhost:*` and `http://127.0.0.1:*`.
- No outbound network connections are ever made by agent-lens itself (no telemetry, no callbacks).

### CSRF Protection
- On startup, the server generates a cryptographically random 32-byte token (`secrets.token_hex(32)`) and prints it to stdout.
- All mutating API endpoints (pause, resume, fork, inject) require this token in the `X-Agent-Lens-Token` request header.
- Without the token, the endpoints return HTTP 403.
- This prevents drive-by requests from other browser tabs or CSRF attacks.

### Secret Redaction
All event data is passed through a redaction pipeline before being written to SQLite. The regex patterns cover:
- `Bearer <token>` — OAuth2 Bearer tokens
- `sk-[A-Za-z0-9_-]{20,}` — OpenAI-style keys (including `sk-proj-` variants)
- `sk-ant-[A-Za-z0-9_-]{20,}` — Anthropic keys
- `AIza[A-Za-z0-9_-]{30,}` — Google/Firebase keys
- `Authorization: <value>` and `x-api-key: <value>` header patterns

Redaction applies recursively to nested dicts and lists.

### Input Validation
- All API request bodies are validated by Pydantic models.
- No `eval()`, `exec()`, or `pickle.loads()` are used anywhere in the codebase.
- SQLite data is always serialized as JSON (`json.dumps`) and deserialized with `json.loads`. No Python serialization formats are used.

### HTML Export Safety
- Tool outputs, LLM responses, and run names are HTML-escaped (`html.escape()`) before embedding in exported HTML files.
- This prevents XSS in the exported file when opened in a browser.
- Data is embedded as a JSON blob in a `<script type="application/json">` tag (not inline JavaScript), parsed by `JSON.parse()` — not `eval()`.

### Path Traversal Prevention
- The `replay` CLI command resolves the provided path with `Path.resolve()` and checks it against a list of forbidden system prefixes (`/etc`, `/proc`, `/sys`, `/dev`, `/root`).
- Only `.agentlens` and `.json` file extensions are accepted.

### SQLite File Permissions
- The database is created at `~/.agent-lens/runs.db` with default OS permissions (user-readable only on most Unix systems).
- No other user on a multi-user system can read the database without explicit permission grants.

## Audit Findings

### Known Limitations

1. **No authentication for the dashboard** — The dashboard at `http://127.0.0.1:7878` is accessible to any process on the local machine. On shared machines (HPC clusters, multi-user workstations), other users could access the dashboard if they can bind to that port. Mitigation: do not run agent-lens on shared machines without additional OS-level access controls.

2. **CSRF token is in-memory only** — The token is regenerated on every server restart. Bookmarked API calls will fail after restart. This is a deliberate security trade-off.

3. **SQLite WAL files** — SQLite WAL mode creates additional files (`runs.db-wal`, `runs.db-shm`). These are not separately protected. Users should treat the entire `~/.agent-lens/` directory as sensitive.

4. **Log injection** — The server logs run names and span names. If these contain ANSI escape sequences or newlines, terminal output could be manipulated. Mitigation: strip control characters before logging (not currently implemented).

## Responsible Disclosure

If you discover a security vulnerability in agent-lens, please report it **privately** before public disclosure:

**Email**: rajub4u927@gmail.com
**Subject**: `[agent-lens SECURITY] <brief description>`

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

We will acknowledge receipt within 48 hours and aim to release a fix within 14 days for critical issues.

**Please do not file a public GitHub issue for security vulnerabilities.**

## Security Updates

Security fixes are released as patch versions (`0.1.x`). We recommend always running the latest version.

Subscribe to GitHub releases for security notifications.
