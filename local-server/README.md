# Knowledge Base Local MCP Server

Access your knowledge base via the MCP protocol in AI clients like Claude Desktop, Cursor, and Cline — entirely local, entirely private.

## Features

- MCP protocol over stdio transport
- Compatible with Claude Desktop, Cursor, Cline, Continue.dev
- **Inline synthesis**: `kb_query` returns a direct synthesised answer (when any LLM API key is set) plus source list — one tool call instead of three
- **Chapter-level retrieval**: chunk scoring surfaces the most relevant section of a book, not just the book title
- **Semantic-style scoring**: matches queries against LLM-generated retrieval sentences, not just raw keywords
- **Write-back tools**: AI can file articles, notes, and index updates back into the wiki
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
cd /path/to/knowledge-base-suite-en/local-server/src
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
        "/path/to/knowledge-base-suite-en/local-server/src/server.py",
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
        "/path/to/knowledge-base-suite-en/local-server/src/server.py",
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
        "/path/to/knowledge-base-suite-en/local-server/src/server.py",
        "--kb-path",
        "/Users/yourname/my-research"
      ]
    }
  }
}
```

---

## Available Tools

### Retrieval Tools

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
Sleep is the single most effective thing we can do to reset our brain and body
health each day...
Relevance: 47

## Huberman Lab Notes
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

Returns ranked documents with word count and best matching chapter (for long documents).

---

#### `kb_get_document` — Full Article

```json
{ "name": "kb_get_document", "arguments": { "doc_id": "why_we_sleep" } }
```

Returns the full compiled wiki article.

---

#### `kb_get_summary` — Document Metadata

```json
{ "name": "kb_get_summary", "arguments": { "doc_id": "why_we_sleep" } }
```

Returns word count, core claims, key data, and notable quotes from the index (no file I/O).

---

#### `kb_list_concepts` — Browse Concepts

```json
{ "name": "kb_list_concepts", "arguments": {} }
```

---

#### `kb_get_concept` — Concept Detail

```json
{ "name": "kb_get_concept", "arguments": { "concept": "hippocampal replay" } }
```

---

#### `kb_stats` — Knowledge Base Statistics

```json
{ "name": "kb_stats", "arguments": {} }
```

Returns document counts, concept counts, and available resource URIs.

---

### Write-back Tools

These allow the AI to file research outputs directly into the wiki — no copy-paste needed.

#### `kb_write_article` — Create or Overwrite a Wiki Article

```json
{
  "name": "kb_write_article",
  "arguments": {
    "title": "Sleep and Memory — Synthesis",
    "content": "---\ntitle: Sleep and Memory — Synthesis\n...\n",
    "overwrite": false
  }
}
```

Creates `wiki/_articles/sleep_and_memory_synthesis.md`. If the file already exists, returns an error unless `overwrite: true`.

---

#### `kb_append_note` — Add a Timestamped Note to an Article

```json
{
  "name": "kb_append_note",
  "arguments": {
    "doc_id": "why_we_sleep",
    "note": "Contradicts Polyphasic Sleep Handbook's claim about 4-hour cycles.",
    "section": "Research Notes"
  }
}
```

Appends under `## Research Notes` (creates the section if absent). Entry is timestamped `YYYY-MM-DD HH:MM`.

---

#### `kb_update_index` — Rewrite `_index.md`

```json
{
  "name": "kb_update_index",
  "arguments": {
    "content": "# My Research KB\n\n## Topic Map\n..."
  }
}
```

Overwrites `wiki/_index.md` entirely.

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

  ---
  [detailed source list with chapter-level hits]

You: Save a synthesis note on this topic.

AI:
  → calls kb_write_article(title="Stress and Learning — Synthesis", content="...")
  Article written: wiki/_articles/stress_and_learning_synthesis.md (312 words)
```

---

## Resources Exposed

| URI | Content |
|-----|---------|
| `kb://<kb_id>/index` | `_index.md` |
| `kb://<kb_id>/articles/<name>` | Individual wiki article |

---

## Architecture

```
AI Client (Claude Desktop / Cursor)
    │
    │  MCP stdio transport
    ▼
KnowledgeBaseMCPServer
    │
    ├── kb_query ──────── file_index.json (scoring)
    │                 └── wiki/_articles/*.md (snippets)
    │                 └── LLM synthesis via any configured provider (optional)
    │
    ├── kb_get_summary ─── file_index.json (metadata)
    ├── kb_list_concepts ── concepts.json
    ├── kb_get_concept ──── concepts.json
    ├── kb_stats ────────── file_index.json + concepts.json
    ├── kb_search ──────── file_index.json
    ├── kb_get_document ── wiki/_articles/*.md
    ├── kb_write_article ─ wiki/_articles/ (write)
    ├── kb_append_note ─── wiki/_articles/ (append)
    └── kb_update_index ── wiki/_index.md (write)
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

MIT License — Version 1.2.0
