# Knowledge Base Cloud Platform — Documentation Index

## For Developers

- [**Cloud Platform Guide**](../README.md) — Full API reference, authentication, MCP tools, security, deployment

### Getting Started (60 seconds)

```bash
# 1. Register
curl -X POST https://your-server/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "secure123", "name": "You"}'

# 2. Login and get JWT
curl -X POST https://your-server/api/v1/auth/login \
  -d '{"email": "you@example.com", "password": "secure123"}'
# → {"access_token": "...", "refresh_token": "..."}

# 3. Create an API key
curl -X POST https://your-server/api/v1/api-keys \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "My App", "permissions": ["read"]}'
# → {"key": "kb_live_xxx"}   ← save this, shown only once

# 4. Query your knowledge base
curl -X POST https://your-server/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "X-KB-ID: my-research" \
  -H "Content-Type: application/json" \
  -d '{"name": "kb_query", "arguments": {"question": "How does sleep affect memory?"}}'
```

### Available MCP Tools

| Tool | What it does |
|------|-------------|
| `kb_query` | Natural language question → synthesised answer + ranked sources |
| `kb_search` | Keyword search → ranked document list with confidence tier |
| `kb_list_docs` | Browse all documents with confidence + lifecycle state |
| `kb_get_document` | Full wiki article by doc ID |
| `kb_list` | KB overview with document/verified/concept counts |
| `kb_write_article` | Create or overwrite a wiki article |
| `kb_append_note` | Append a timestamped note to an existing article |
| `kb_update_index` | Rewrite the wiki index |
| `kb_save_synthesis` | Save a query answer as a permanent wiki synthesis page |

### Headers

| Header | Required for | Notes |
|--------|-------------|-------|
| `X-API-Key` | All MCP endpoints | Issued via `/api/v1/api-keys` |
| `X-KB-ID` | All MCP endpoints | Knowledge base identifier |
| `Authorization: Bearer` | Management endpoints | JWT from `/auth/login` |

### Developer Guide

- [Full API Reference](DEVELOPER_GUIDE.md)
- [Architecture](../../docs/ARCHITECTURE.md) — System design, security model, retrieval design
