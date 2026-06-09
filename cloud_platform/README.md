# Knowledge Base Cloud Platform

Expose your knowledge base to teams or external users via a secure HTTP API — the same MCP tool surface as the local server, now over the internet.

## Features

- HTTP API with full MCP protocol support
- Multi-tenant architecture — each user's KB isolated at the filesystem level
- API Key + JWT authentication with bcrypt password storage
- Monthly usage quotas (auto-reset at month boundary via Redis TTL)
- Per-minute rate limiting (Lua atomic script)
- Input validation: title ≤ 200 chars, content ≤ 1 MB, search limit ≤ 50
- Inline synthesis: `kb_query` returns a synthesised answer (when any LLM API key is set)
- Chapter-level chunk scoring — books surface relevant sections, not just titles
- Write-back tools: AI clients can create articles, append notes, and update the index
- Security: path traversal prevention + KB ownership guard on all write operations
- Docker deployment included

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                          Clients                            │
│   AI clients · Web apps · cURL · Python SDK · JS SDK       │
└────────────────────────────┬────────────────────────────────┘
                             │  HTTPS  X-API-Key
                             ▼
              ┌──────────────────────────────┐
              │       API Gateway :8000       │
              │  • API key auth (Redis)        │
              │  • Rate limiting (Lua atomic, per-minute)   │
              │  • Monthly quota (Lua atomic, auto-reset)   │
              │  • Injects X-User-ID/X-KB-ID  │
              │  • _proxy_to_mcp() for all    │
              │    MCP routes (GET + POST)     │
              └──────────────┬───────────────┘
                             │ internal
                             ▼
              ┌──────────────────────────────┐
              │    MCP HTTP Server :8001      │
              │  • Reads headers only (secure) │
              │  • _guard_kb_write() on writes │
              │  • KnowledgeBaseManager        │
              │  • Inline LLM synthesis        │
              └──────────────┬───────────────┘
                             │
              ┌──────────────┴───────────────┐
              │   /data/knowledge-bases/      │
              │   <user_id>/<kb_id>/wiki/     │
              └──────────────────────────────┘
                  PostgreSQL · Redis
```

## Observability

### Structured JSON Logging

Both the gateway and MCP server emit single-line JSON logs to stdout. Each event includes:

```json
{"ts": "2026-04-17T14:23:01.456Z", "level": "INFO", "component": "gateway",
 "event": "request_complete", "method": "POST", "path": "/mcp/v1/tools/call",
 "status": 200, "duration_ms": 143, "req_id": "a1b2c3d4", "user_id": "alice"}
```

**Key fields:**

| Field | Description |
|-------|-------------|
| `component` | `gateway` or `mcp_server` |
| `req_id` | Correlation ID — same value across gateway and MCP server for one request |
| `user_id` / `kb_id` | Set on all authenticated requests |
| `duration_ms` | Wall-clock time for the full request |
| `event` | `request_complete`, `auth_fail`, `rate_limit_exceeded`, `tool_call_start`, `synthesis_complete`, etc. |

The `req_id` is generated at the gateway (`X-Request-ID` header) and forwarded to the MCP server, so one request produces correlated log lines in both services.

Set `LOG_LEVEL=DEBUG` to include verbose diagnostic output.

### `/health` Endpoint

```bash
curl http://localhost:8000/health
```

Returns `200 ok` when all services are healthy, or `200 degraded` when Redis is unreachable (the platform continues serving cached/in-flight requests):

```json
{"status": "ok",      "version": "1.0.0", "redis": "ok"}
{"status": "degraded","version": "1.0.0", "redis": "unavailable"}
```

### Redis Fail-Closed Behaviour

If Redis becomes unavailable mid-request:

| Operation | Behaviour |
|-----------|-----------|
| API key auth | Returns **503** (safe — never grants unauthenticated access) |
| Rate limiting | **Denies** the request (fail closed) |
| Monthly quota | **Denies** the request (fail closed) |
| `/health` | Reports `"redis": "unavailable"` but stays `200` |

This prevents the platform from silently allowing unlimited requests when Redis is down.

---

## Quick Start

### Docker (recommended)

```bash
# 1. Enter the deploy directory
cd cloud_platform/deploy

# 2. Configure environment
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD, JWT_SECRET, API_KEY_SALT

# 3. Start all services
docker-compose up -d

# 4. Verify
curl http://localhost:8000/health
# {"status": "ok", "version": "1.0.0"}
```

### Manual Setup

```bash
pip install -r requirements.txt
# Start Redis and PostgreSQL (Docker or local)
python src/gateway/main.py        # port 8000
python src/server/mcp_http_server.py   # port 8001
```

### Enable Inline Synthesis

```bash
# Set any supported LLM API key in the MCP server's environment
export DEEPSEEK_API_KEY=sk-...         # DeepSeek (recommended — fast + cheap)
# or: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, KIMI_API_KEY, etc.
```

When set, `kb_query` synthesises a direct 2–4 sentence answer from the top-3 results before returning the source list. See [LLM API Key Setup in QUICKSTART.md](../QUICKSTART.md) for all supported providers.

---

## Admin CLI

Manage users and API keys directly — no server login required. Redis must be running.

```bash
cd cloud_platform/src

# Create an API key for a user
python admin.py create-key --user alice --name "Production"
python admin.py create-key --user alice --name "Dev" --rate-limit 20 --quota 500

# List keys
python admin.py list-keys
python admin.py list-keys --user alice

# Revoke a key
python admin.py delete-key <key_id>

# List users
python admin.py list-users
```

Set `REDIS_URL` if Redis is not on localhost:
```bash
export REDIS_URL=redis://your-server:6379
python admin.py list-users
```

---

## API Reference

### Authentication

#### API Key (programmatic / MCP access)

```bash
curl -H "X-API-Key: kb_live_xxx" \
     -H "X-KB-ID: my-research" \
     http://api.example.com/mcp/v1/tools/call
```

#### JWT Token (user management)

```bash
# Register
curl -X POST http://api.example.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "secure", "name": "Alice"}'

# Login
curl -X POST http://api.example.com/api/v1/auth/login \
  -d '{"email": "alice@example.com", "password": "secure"}'
# → {"access_token": "...", "refresh_token": "..."}

# Use token
curl -H "Authorization: Bearer <token>" \
     http://api.example.com/api/v1/user/profile
```

---

### MCP Tool Reference

All MCP endpoints require `X-API-Key` and `X-KB-ID` headers.

#### `kb_query` — Natural Language Question

```bash
curl -X POST http://api.example.com/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "X-KB-ID: my-research" \
  -H "Content-Type: application/json" \
  -d '{"name": "kb_query", "arguments": {"question": "How does sleep affect memory?"}}'
```

Response includes a synthesised answer (if any LLM API key is set) followed by ranked sources with chapter-level hints.

#### `kb_search` — Keyword Browse

```bash
-d '{"name": "kb_search", "arguments": {"query": "attention mechanism", "limit": 5}}'
```

#### `kb_get_document` — Full Wiki Article

```bash
-d '{"name": "kb_get_document", "arguments": {"doc_id": "why_we_sleep"}}'
```

#### `kb_list_docs` — Browse Documents

```bash
-d '{"name": "kb_list_docs", "arguments": {}}'
```

Returns all documents with confidence tier, lifecycle state, and contradiction flags.

#### `kb_list` — KB Overview

```bash
-d '{"name": "kb_list", "arguments": {}}'
```

#### `kb_write_article` — Create or Overwrite an Article

```bash
-d '{
  "name": "kb_write_article",
  "arguments": {
    "title": "Sleep Synthesis",
    "content": "---\ntitle: Sleep Synthesis\n...",
    "overwrite": false
  }
}'
```

**Security**: validated against `_guard_kb_write()` before writing. Requires the KB to already exist under the authenticated user's path. IDs are sanitised to prevent path traversal.

#### `kb_append_note` — Timestamped Note

```bash
-d '{
  "name": "kb_append_note",
  "arguments": {
    "doc_id": "why_we_sleep",
    "note": "Contradicts polyphasic sleep claims.",
    "section": "Research Notes"
  }
}'
```

#### `kb_update_index` — Rewrite `_index.md`

```bash
-d '{"name": "kb_update_index", "arguments": {"content": "# My KB\n\n..."}}'
```

#### `kb_save_synthesis` — Save a Query Answer

```bash
-d '{
  "name": "kb_save_synthesis",
  "arguments": {
    "question": "How does sleep affect memory?",
    "answer": "Sleep consolidates memories during slow-wave sleep...",
    "sources": ["why_we_sleep", "huberman_lab_notes"]
  }
}'
```

Saves the answer as `wiki/syntheses/<slug>.md`. Syntheses are searchable via `kb_query` and `kb_search`.

---

### Management Endpoints

#### Create API Key

```bash
POST /api/v1/api-keys
Authorization: Bearer <jwt_token>

{"name": "Production Key", "permissions": ["read"]}
```

Response:
```json
{
  "id": "key-123",
  "name": "Production Key",
  "key": "kb_live_xxxxxxxxxxx"
}
```

⚠️ **The key is shown only once. Store it securely.**

#### Usage Statistics

```bash
GET /api/v1/usage/stats
X-API-Key: kb_live_xxx
```

```json
{
  "quota": {"limit": 10000, "used": 5234, "remaining": 4766},
  "rate_limit": {"limit": 100, "current": 23, "window": 60}
}
```

---

## Deploying a Knowledge Base

Build locally, then push to the server:

```bash
# Preview first
kb deploy --host myserver.com --remote-user alice --dry-run

# Deploy
kb deploy --host myserver.com --remote-user alice --kb-id my-research

# Then users access it with:
# X-API-Key: kb_live_xxx
# X-KB-ID: my-research
```

---

## Security

| Layer | Control |
|-------|---------|
| Transport | HTTPS (terminate at load balancer or Nginx) |
| Authentication | API key (Redis lookup) + JWT (short-lived, refresh token) |
| Passwords | bcrypt (cost factor 12) stored in Redis |
| Quotas | Monthly counter, `SETEX` with TTL = seconds until UTC month-end |
| Write validation | `_sanitize_id()` strips non-`[\w\-]` chars; `kb_exists()` blocks phantom KB writes |
| Port isolation | MCP server (8001) not exposed externally; all traffic via gateway (8000) |
| User isolation | `/data/knowledge-bases/<user_id>/<kb_id>/` — no cross-user path access |

---

## Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/kb_mcp
REDIS_URL=redis://localhost:6379

# Security
JWT_SECRET=your-secret-key-min-32-chars
API_KEY_SALT=another-secret

# Rate limiting
RATE_LIMIT_DEFAULT=100    # requests per minute
QUOTA_DEFAULT=10000       # requests per month

# Optional: inline synthesis in MCP server (any one of these)
DEEPSEEK_API_KEY=sk-...
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# GEMINI_API_KEY=AIza...

# Logging
LOG_LEVEL=INFO       # DEBUG | INFO | WARNING | ERROR

# Ports
GATEWAY_PORT=8000
MCP_SERVER_PORT=8001
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `docker-compose up` fails | Check `.env` has all required variables |
| 401 Unauthorized | API key not in Redis; run `admin.py create-key` |
| 403 on write tools | KB does not exist under this user_id; deploy with `cli.py deploy` first |
| No synthesis in query response | Set any LLM API key in MCP server environment — see [QUICKSTART.md](../QUICKSTART.md) |
| Rate limit exceeded | Check usage stats; adjust `--rate-limit` when creating key |
| 503 on all authenticated requests | Redis is down — restore Redis, then retry |
| `/health` shows `"redis": "unavailable"` | Redis unreachable; auth and rate-limiting are blocked until Redis recovers |
| Logs not appearing | Check `LOG_LEVEL`; default is `INFO` (set `DEBUG` for verbose output) |

## License

MIT License
