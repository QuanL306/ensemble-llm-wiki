# Knowledge Base Cloud Platform - Developer Guide

## Overview

This guide helps developers integrate with the Knowledge Base Cloud Platform API to build applications powered by structured knowledge.

## Base URL

```
Production: https://api.knowledgebase.cloud
Sandbox:    https://sandbox-api.knowledgebase.cloud
```

All API requests should be made to the base URL with the appropriate endpoint path.

## Authentication

### API Key Authentication

All API requests require authentication using an API Key.

```bash
# Include in request header
curl -H "X-API-Key: kb_live_xxxxxxxxxxxxxxxx" \
  https://api.knowledgebase.cloud/mcp/v1/tools/call
```

### Getting an API Key

1. **Register an account**
   ```bash
   POST /api/v1/auth/register
   Content-Type: application/json
   
   {
     "email": "developer@example.com",
     "password": "your_secure_password",
     "name": "Developer Name"
   }
   ```

2. **Login to get JWT token**
   ```bash
   POST /api/v1/auth/login
   Content-Type: application/json
   
   {
     "email": "developer@example.com",
     "password": "your_secure_password"
   }
   
   # Response
   {
     "access_token": "eyJhbGciOiJIUzI1NiIs...",
     "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
     "token_type": "bearer",
     "expires_in": 3600
   }
   ```

3. **Create API Key**
   ```bash
   POST /api/v1/api-keys
   Authorization: Bearer <access_token>
   Content-Type: application/json
   
   {
     "name": "Production API Key",
     "permissions": ["read"]
   }
   
   # Response
   {
     "id": "key_abc123",
     "name": "Production API Key",
     "key": "kb_live_xxxxxxxxxxxxxxxx",
     "permissions": ["read"],
     "rate_limit": 100,
     "quota_limit": 10000,
     "created_at": "2026-04-12T10:00:00Z"
   }
   ```

   ⚠️ **Important**: The API Key is only shown once. Save it securely!

## Core Concepts

### Knowledge Base Structure

A knowledge base contains:
- **Documents**: PDFs, EPUBs, Markdown files
- **Concepts**: Extracted key terms with definitions
- **Index**: Searchable metadata

### MCP Protocol

This API implements the Model Context Protocol (MCP) for AI tool integration.

## API Endpoints

### 1. Initialize Connection

Initialize MCP connection to get server capabilities.

```bash
POST /mcp/v1/initialize
X-API-Key: kb_live_xxx
Content-Type: application/json

{
  "protocolVersion": "2024-11-05",
  "capabilities": {},
  "clientInfo": {
    "name": "my-app",
    "version": "1.0.0"
  }
}
```

**Response:**
```json
{
  "protocolVersion": "2024-11-05",
  "capabilities": {
    "tools": {
      "listChanged": true
    },
    "resources": {
      "subscribe": true,
      "listChanged": true
    }
  },
  "serverInfo": {
    "name": "knowledge-base-mcp",
    "version": "1.0.0"
  }
}
```

### 2. List Available Tools

Get list of available MCP tools.

```bash
GET /mcp/v1/tools
X-API-Key: kb_live_xxx
```

**Response:**
```json
{
  "tools": [
    {
      "name": "kb_search",
      "description": "Search documents in knowledge base",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Search query"
          },
          "limit": {
            "type": "integer",
            "description": "Maximum results",
            "default": 5
          }
        },
        "required": ["query"]
      }
    },
    {
      "name": "kb_get_document",
      "description": "Get full content of a document",
      "inputSchema": {
        "type": "object",
        "properties": {
          "doc_id": {
            "type": "string",
            "description": "Document ID or filename"
          }
        },
        "required": ["doc_id"]
      }
    }
  ]
}
```

### 3. Call Tool

Execute an MCP tool.

```bash
POST /mcp/v1/tools/call
X-API-Key: kb_live_xxx
Content-Type: application/json

{
  "name": "kb_search",
  "arguments": {
    "query": "machine learning",
    "limit": 5
  }
}
```

**Response:**
```json
{
  "content": [
    {
      "type": "text",
      "text": "Found 3 documents:\n\n📄 **ML-Handbook.pdf**\n   Words: 15000\n   Key: Machine learning is a subset of...\n   ID: `abc123`\n\n📄 **Deep-Learning-Intro.md**\n   Words: 8500\n   Key: Deep learning uses neural networks...\n   ID: `def456`"
    }
  ],
  "isError": false
}
```

## Tool Reference

### kb_search

Search documents in the knowledge base.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| query | string | Yes | Search query string |
| limit | integer | No | Max results (default: 5) |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_search",
    "arguments": {
      "query": "neural network architecture",
      "limit": 10
    }
  }'
```

### kb_get_document

Retrieve full content of a specific document.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| doc_id | string | Yes | Document ID or filename |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_get_document",
    "arguments": {
      "doc_id": "ML-Handbook.pdf"
    }
  }'
```

### kb_get_summary

Get summary of a document (core claims and key data).

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| doc_id | string | Yes | Document ID or filename |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_get_summary",
    "arguments": {
      "doc_id": "research-paper.pdf"
    }
  }'
```

### kb_list_concepts

List all concepts in the knowledge base.

**Parameters:** None

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_list_concepts",
    "arguments": {}
  }'
```

### kb_get_concept

Get definition and related documents for a concept.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| concept | string | Yes | Concept name |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_get_concept",
    "arguments": {
      "concept": "Neural Network"
    }
  }'
```

### kb_query

Query with natural language question.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| question | string | Yes | Natural language question |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_query",
    "arguments": {
      "question": "What are the main challenges in AI safety?"
    }
  }'
```

### kb_write_article

Create or overwrite a wiki article. IDs are sanitised to prevent path traversal; the target KB must already exist.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| title | string | Yes | Article title (max 200 chars) |
| content | string | Yes | Article content in Markdown (max 1 MB) |
| overwrite | boolean | No | Overwrite if article exists (default: false) |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_write_article",
    "arguments": {
      "title": "Sleep Synthesis",
      "content": "---\ntitle: Sleep Synthesis\n---\n\n# Sleep Synthesis\n...",
      "overwrite": false
    }
  }'
```

### kb_append_note

Append a timestamped note to an existing article, optionally under a specific section.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| doc_id | string | Yes | Document ID or filename |
| note | string | Yes | Note text |
| section | string | No | Section heading to append under (created if absent) |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_append_note",
    "arguments": {
      "doc_id": "why_we_sleep",
      "note": "Contradicts polyphasic sleep claims.",
      "section": "Research Notes"
    }
  }'
```

### kb_update_index

Overwrite the main knowledge base index (`_index.md`).

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| content | string | Yes | New index content in Markdown (max 1 MB) |

**Example:**
```bash
curl -X POST https://api.knowledgebase.cloud/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb_update_index",
    "arguments": {
      "content": "# My KB\n\n## Topic Map\n..."
    }
  }'
```

## Error Handling

### HTTP Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 400 | Bad Request | Invalid request format |
| 401 | Unauthorized | Invalid or missing API Key |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Server Error | Internal server error |
| 502 | Bad Gateway | MCP server returned an error |
| 503 | Service Unavailable | Redis or MCP server unreachable |
| 504 | Gateway Timeout | MCP server did not respond in time |

### Error Response Format

Tool errors return a generic message without internal details:

```json
{
  "content": [
    {
      "type": "text",
      "text": "Document not found"
    }
  ],
  "isError": true
}
```

For gateway-level errors (auth, rate limit), the response body includes a `detail` field:

```json
{"detail": "Invalid API key"}
{"detail": "Rate limit exceeded"}
{"detail": "Service temporarily unavailable"}
```

## Rate Limits & Quotas

### Rate Limits

- **Per API Key**: configurable (default 100 requests/minute), enforced via Lua atomic counter in Redis

### Input Validation Limits

| Field | Tool | Limit |
|-------|------|-------|
| `title` | `kb_write_article` | ≤ 200 characters |
| `content` | `kb_write_article`, `kb_update_index` | ≤ 1 MB |
| `limit` | `kb_search` | ≤ 50 |

Requests exceeding these limits receive a `400 Bad Request` response.

### Monthly Quotas

Quotas are set per API key via the Admin CLI (`admin.py create-key --quota <n>`). Usage resets automatically at UTC month boundary via Redis TTL.

### Checking Usage

```bash
GET /api/v1/usage/stats
X-API-Key: kb_live_xxx
```

**Response:**
```json
{
  "quota": {
    "limit": 10000,
    "used": 5234,
    "remaining": 4766,
    "resets_at": "2026-05-01T00:00:00Z"
  },
  "rate_limit": {
    "limit": 100,
    "current": 23,
    "window": 60
  }
}
```

## SDKs & Libraries

No official SDK yet. Use standard HTTP clients:

```python
import requests

client = requests.Session()
client.headers.update({
    "X-API-Key": "kb_live_xxx",
    "Content-Type": "application/json"
})

# Search
resp = client.post(
    "https://api.knowledgebase.cloud/mcp/v1/tools/call",
    json={"name": "kb_search", "arguments": {"query": "machine learning", "limit": 5}}
)
print(resp.json())
```

See the [Code Examples](#code-examples) section below for complete client implementations.

## Code Examples

### Python Example

```python
import requests

class KnowledgeBaseClient:
    def __init__(self, api_key, base_url="https://api.knowledgebase.cloud"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
    
    def search(self, query, limit=5):
        response = requests.post(
            f"{self.base_url}/mcp/v1/tools/call",
            headers=self.headers,
            json={
                "name": "kb_search",
                "arguments": {"query": query, "limit": limit}
            }
        )
        return response.json()
    
    def query(self, question):
        response = requests.post(
            f"{self.base_url}/mcp/v1/tools/call",
            headers=self.headers,
            json={
                "name": "kb_query",
                "arguments": {"question": question}
            }
        )
        return response.json()

# Usage
client = KnowledgeBaseClient("kb_live_xxx")
results = client.search("artificial intelligence")
print(results)
```

### JavaScript Example

```javascript
class KnowledgeBaseClient {
  constructor(apiKey, baseUrl = 'https://api.knowledgebase.cloud') {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl;
  }

  async search(query, limit = 5) {
    const response = await fetch(`${this.baseUrl}/mcp/v1/tools/call`, {
      method: 'POST',
      headers: {
        'X-API-Key': this.apiKey,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        name: 'kb_search',
        arguments: { query, limit }
      })
    });
    return response.json();
  }

  async query(question) {
    const response = await fetch(`${this.baseUrl}/mcp/v1/tools/call`, {
      method: 'POST',
      headers: {
        'X-API-Key': this.apiKey,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        name: 'kb_query',
        arguments: { question }
      })
    });
    return response.json();
  }
}

// Usage
const client = new KnowledgeBaseClient('kb_live_xxx');
const results = await client.search('artificial intelligence');
console.log(results);
```

### cURL Example

```bash
#!/bin/bash

API_KEY="kb_live_xxx"
BASE_URL="https://api.knowledgebase.cloud"

# Search
search() {
  curl -X POST "${BASE_URL}/mcp/v1/tools/call" \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "kb_search",
      "arguments": {
        "query": "'"$1"'",
        "limit": 5
      }
    }'
}

# Query
query() {
  curl -X POST "${BASE_URL}/mcp/v1/tools/call" \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "kb_query",
      "arguments": {
        "question": "'"$1"'"
      }
    }'
}

# Usage
search "machine learning"
query "What is deep learning?"
```

## Best Practices

### 1. Error Handling

Always handle errors gracefully:

```python
try:
    result = client.search("query")
    if result.get("isError"):
        print(f"Error: {result['content'][0]['text']}")
    else:
        print(result['content'][0]['text'])
except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")
```

### 2. Rate Limiting

Implement exponential backoff:

```python
import time

def call_with_retry(func, max_retries=3):
    for i in range(max_retries):
        try:
            return func()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                time.sleep(2 ** i)  # Exponential backoff
            else:
                raise
    raise Exception("Max retries exceeded")
```

### 3. Caching

Cache frequently accessed data:

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_concept(concept_name):
    return client.get_concept(concept_name)
```

## Support

- **Documentation**: https://docs.knowledgebase.cloud
- **Support Email**: support@knowledgebase.cloud
- **Status Page**: https://status.knowledgebase.cloud
- **Community Discord**: https://discord.gg/knowledgebase

## Changelog

### v1.0.0 (2026-04-12)
- Initial release
- MCP protocol support
- 10 tools: search, get_document, get_summary, list_concepts, get_concept, query, stats, write_article, append_note, update_index
- API Key authentication
- Rate limiting (Lua atomic counter) and quotas

---

**Last Updated**: 2026-04-17
**API Version**: 1.0.0
