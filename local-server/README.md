# Knowledge Base Local MCP Server

Access your knowledge base via the MCP protocol in AI clients like Claude Desktop, Cursor, and Cline — entirely local, entirely private.

## Features

- MCP protocol over stdio transport
- Compatible with Claude Desktop, Cursor, Cline, Continue.dev
- **Inline synthesis**: `kb_query` returns a direct synthesised answer (when any LLM API key is set) plus source list — one tool call instead of three
- **Chapter-level retrieval**: chunk scoring surfaces the most relevant section of a book, not just the book title
- **Semantic-style scoring**: matches queries against LLM-generated retrieval sentences, not just raw keywords
- **Save syntheses**: AI can save generated answers back into the wiki as permanent pages
- Local-only — knowledge never leaves your machine

## Setup

### 1. Build a knowledge base first

```bash
kb init ~/my-research --name "My Research"
cd ~/my-research
# Add documents to raw/, then:
kb ingest
kb compile-llm   # recommended; requires any LLM API key (see LLM API Key Setup in QUICKSTART.md)
```

### 2. Start the server

```bash
cd /path/to/ensemble-llm-wiki/local-server/src
python3 server.py --kb-path /Users/yourname/my-research
```

> **Note:** Use the full absolute path for `--kb-path`. The `~/` shorthand is not
> expanded when passed from JSON config files (Claude Desktop, Cursor, etc.).

### 3. Enable inline synthesis (optional)

Set any supported LLM API key in the same environment where the server runs:

```bash
export DEEPSEEK_API_KEY=sk-...   # or OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
python3 server.py --kb-path /Users/yourname/my-research
```

When a key is present, `kb_query` automatically synthesises a 2–4 sentence answer from the top results using the auto-detected provider before returning the source list.

### 4. Configure your AI client

#### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-research": {
      "command": "python3",
      "args": [
        "/path/to/ensemble-llm-wiki/local-server/src/server.py",
        "--kb-path",
        "/Users/yourname/my-research"
      ],
      "env": {
        "DEEPSEEK_API_KEY": "sk-..."
      }
    }
  }
}
```

> Replace `DEEPSEEK_API_KEY` with whichever provider key you use.
> All paths must be absolute — `~/` is not expanded in JSON configs.

#### Cursor

Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "my-kb": {
      "command": "python3",
      "args": [
        "/path/to/ensemble-llm-wiki/local-server/src/server.py",
        "--kb-path",
        "/Users/yourname/my-research"
      ]
    }
  }
}
```

#### Cline (VSCode)

```json
{
  "cline.mcpServers": {
    "my-kb": {
      "command": "python3",
      "args": [
        "/path/to/ensemble-llm-wiki/local-server/src/server.py",
        "--kb-path",
        "/Users/yourname/my-research"
      ]
    }
  }
}
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `kb_query` | Natural-language question → synthesised answer + ranked sources |
| `kb_search` | Keyword search with confidence tier + lifecycle display |
| `kb_list_docs` | Browse all documents with ★★★/★★☆/★☆☆ confidence + verified count |
| `kb_get_document` | Full wiki article by doc ID |
| `kb_list` | KB overview with document/verified/concept counts |
| `kb_save_synthesis` | Save a generated answer as a permanent `wiki/syntheses/` page |

---

#### `kb_query` — Natural Language Question ⭐

The primary tool. Ask a research question; get a synthesised answer plus source list.

```json
{ "name": "kb_query", "arguments": { "question": "How does sleep affect memory consolidation?" } }
```

**Response (with any LLM API key set):**
```
## Answer

Sleep consolidates declarative memories during slow-wave sleep through hippocampal
replay, while procedural skills are reinforced during REM sleep (Why We Sleep).
The process requires 7–9 hours for full consolidation (Huberman Lab Notes).

*Sources: Why We Sleep, Huberman Lab Notes, Memory Research Review*

---

# Query Results

## Why We Sleep
*Most relevant section: Chapter 6: Your Mother and Shakespeare*
Relevance: 47
...
```

**Without an LLM API key:** returns only the ranked source list with summaries.

**Scoring logic:**
- Title: 5×
- `core_claims` / `key_data` / `quotes`: 1×
- Retrieval queries (word-level): 2×
- Retrieval queries (sentence-level, ≥2 keywords): +5 per hit
- Chunk title / preview: up to 0.8× bonus

---

#### `kb_search` — Keyword Browse

```json
{ "name": "kb_search", "arguments": { "query": "machine learning", "limit": 5 } }
```

Returns ranked documents with confidence tier and lifecycle state per result.

---

#### `kb_list_docs` — Browse Documents

```json
{ "name": "kb_list_docs", "arguments": {} }
```

Returns all documents with ★★★/★★☆/★☆☆ confidence tier, lifecycle state, and ⚠️ contradiction flags.

---

#### `kb_get_document` — Full Article

```json
{ "name": "kb_get_document", "arguments": { "doc_id": "why_we_sleep" } }
```

Returns the full compiled wiki article with optional section extraction.

---

#### `kb_list` — KB Overview

```json
{ "name": "kb_list", "arguments": {} }
```

Returns document count, verified count, and concept count for the KB.

---

#### `kb_save_synthesis` — Save a Query Answer

```json
{
  "name": "kb_save_synthesis",
  "arguments": {
    "question": "What is the relationship between stress and learning?",
    "answer": "Chronic stress impairs hippocampal neurogenesis...",
    "sources": ["why_we_sleep", "stress_response_review"]
  }
}
```

Saves the answer as `wiki/syntheses/<slug>.md` with YAML frontmatter. Syntheses are searchable alongside compiled articles via `kb_query` and `kb_search`, creating a compounding knowledge growth loop.

---

## Typical AI Session

```
You: What do my sources say about the relationship between stress and learning?

AI:
  → calls kb_query("relationship between stress and learning")

  ## Answer
  Chronic stress impairs hippocampal neurogenesis and disrupts long-term
  potentiation (Why We Sleep, Ch. 12). However, acute moderate stress
  enhances encoding by raising norepinephrine (Stress Response Review).
  The key variable is cortisol duration, not peak level (Huberman Lab Notes).

  *Sources: Why We Sleep, Stress Response Review, Huberman Lab Notes*

You: Save that as a synthesis.

AI:
  → calls kb_save_synthesis(question="...", answer="...", sources=[...])
  Saved: wiki/syntheses/stress_and_learning.md
```

---

## Architecture

```
AI Client (Claude Desktop / Cursor / Cline)
    │
    │  MCP stdio transport
    ▼
KnowledgeBaseMCPServer
    │
    ├── kb_query ──────── file_index.json + embeddings.json (hybrid scoring)
    │                 └── wiki/_articles/*.md (snippets)
    │                 └── LLM synthesis via any configured provider (optional)
    │
    ├── kb_search ──────── file_index.json
    ├── kb_list_docs ───── file_index.json (confidence + lifecycle)
    ├── kb_get_document ── wiki/_articles/*.md
    ├── kb_list ────────── file_index.json (counts)
    └── kb_save_synthesis ─ wiki/syntheses/ (write)
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Server not starting | Check Python 3.9+; `pip install -r requirements.txt` |
| AI not recognising tools | Verify JSON config syntax; restart client completely |
| KB not found | `--kb-path` must point to directory containing `.kbaconfig` |
| No synthesis in responses | Set any LLM API key in the server's environment (see QUICKSTART.md) |
| Search returns nothing | Run `compile-llm --docs` to generate retrieval queries |
| Book results too generic | Run `compile-llm --docs`; chunking runs automatically |

## Multiple Knowledge Bases

```json
{
  "mcpServers": {
    "research": {
      "command": "python3",
      "args": ["/path/to/local-server/src/server.py", "--kb-path", "/Users/yourname/research"]
    },
    "work": {
      "command": "python3",
      "args": ["/path/to/local-server/src/server.py", "--kb-path", "/Users/yourname/work-kb"]
    }
  }
}
```

## License

MIT License
