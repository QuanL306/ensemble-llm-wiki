# Knowledge Base Builder

Build structured, Obsidian-compatible knowledge bases from your documents — and from any website, GitHub repo, or video via Skill Seekers.

## Features

- **Multi-format ingestion**: PDF (text + OCR), EPUB, Markdown, TXT
- **LLM-driven compilation**: LLM writes real wiki articles with analysis, cross-references, and retrieval hints
- **Semantic retrieval index**: 15 LLM-generated retrieval queries per document, stored in the index for fast keyword-semantic scoring
- **Chapter-level chunking**: Long documents (books) split into sections — each chapter becomes independently retrievable
- **Skill Seekers integration**: Fetch knowledge from 17+ source types (documentation sites, GitHub, video, PDFs, and more)
- **Obsidian compatible**: Wiki links, frontmatter, graph view

## CLI Commands

### `init` — Initialize

```bash
kb init <folder_path> [--name <name>]
```

Creates the directory structure:
```
my-research/
├── .kbaconfig            # KB configuration
├── raw/                  # Source documents
│   ├── articles/
│   ├── papers/
│   └── skill_seekers/    # Auto-fetched content lands here
├── wiki/
│   ├── _index.md         # Navigation index (AI reads this first)
│   ├── _articles/        # One wiki article per document
│   ├── _concepts/        # Cross-document concept pages
│   ├── _topics/
│   └── _meta/            # file_index.json, concepts.json
└── outputs/
```

---

### `ingest` — Extract and Index Documents

```bash
kb ingest [--full]
```

Scans `raw/` and extracts content from all supported documents.

- **Incremental** (default): only processes new or changed files
- **Full** (`--full`): re-processes everything

For each document, stores in `file_index.json`:
- word count, file hash, status
- `core_claims`, `key_data`, `quotes` (heuristic extraction)
- `wiki_path` (path to the `_extracted.txt` file)

---

### `compile` — Regex-based Wiki Compilation

```bash
kb compile [--full]
```

Fast structural scaffold with no API key required. Generates document summaries and concept articles using regex extraction. Use `compile-llm` for production-quality output.

---

### `compile-llm` — LLM-driven Wiki Compilation ⭐

```bash
kb compile-llm [options]
```

Uses Claude to write genuine wiki articles — analysis, cross-references, and retrieval hints — replacing the regex scaffold with real semantic understanding.

**Requirements**: any LLM API key — see [LLM API Key Setup in QUICKSTART.md](../QUICKSTART.md)

**Steps** (all run by default; pass a flag to run only that step):

| Step | Flag | What it does |
|------|------|------|
| Documents | `--docs` | Writes a wiki article per ingested document |
| Index | `--index` | Regenerates `_index.md` from all articles |
| Concepts | `--concepts` | Writes concept pages for cross-referenced `[[links]]` |

After `--docs` completes, `--index` runs automatically (keeps `_index.md` current). Pass `--no-index` to suppress this.

**Each compiled article includes:**
- `## Summary` — specific claim + distinctive angle (not a topic description)
- `## Core Arguments` — numbered, with claim / evidence type / caveat
- `## Author's Terminology` — `[[linked]]` terms the author uses distinctively
- `## Evidence & Data` — named experiments, exact statistics, specific cases
- `## Key Quotes` — 2–4 insight-dense verbatim quotes
- `## Connections` — links to other fields and `[[concepts]]`
- `## What This Doesn't Cover` — honest gaps and limitations
- `## For Future Queries` — **15 retrieval hints** (conceptual / methodological / application) stored in `file_index.json` for semantic-style scoring without vector embeddings

**Chapter-level chunking** (automatic for documents > 5 000 words):  
Long documents are split into sections (chapters, Markdown headers, or 600-word windows). Each chunk's title and preview are stored in the index, enabling chapter-level retrieval.

**Options:**

```bash
kb compile-llm                       # all three steps
kb compile-llm --docs                # articles only
kb compile-llm --docs --no-index     # articles, skip auto-index
kb compile-llm --index               # rebuild index only
kb compile-llm --concepts            # concept pages only
kb compile-llm --full -y             # recompile everything, no prompt
kb compile-llm --model claude-opus-4-5
kb compile-llm --concept-limit 30
```

**Cost estimate** is shown before any API calls. Confirm with `y` or skip with `--yes`.

**Checkpoint / resume** — progress is saved after each document. If the run is interrupted (Ctrl+C, API timeout, network error):
- Already-compiled documents are saved and will be skipped on the next run
- Documents that failed due to transient API errors are marked with `compile_failed_at` in the index and skipped by default
- Re-run with `--retry-failed` to retry them; `--full` to recompile everything

```bash
kb compile-llm --retry-failed   # retry previously failed docs
kb compile-llm --full -y        # recompile everything
```

`status` shows compile failures with error details:
```
❌ LLM compile failed: 2  — run: compile-llm --retry-failed
   • book.pdf  (RateLimitError: ...)
```

---

### `search` — Test Keyword Search Locally

```bash
kb search <query> [--limit N]
```

Searches `file_index.json` using the same scoring as the MCP servers — no server needed. Useful for verifying retrieval quality before deploying.

**Scoring** (same logic as `kb_query`):
- Title match: 5×
- `core_claims` / `key_data` / `quotes` match: 1×
- Retrieval query word-level match: 2×
- Retrieval query sentence-level match (≥2 keywords in one query): +5 per hit
- Chunk title / preview match: up to 0.8× bonus

Results show **which retrieval query matched** so you can see exactly why a document ranked.

```bash
kb search "monetary policy inflation"
kb search "attention mechanism transformers" --limit 10
```

---

### `lint` — Wiki Health Check

```bash
kb lint
```

Static checks — no API calls:

1. **Orphan `[[links]]`** — wiki links with no matching article or concept file
2. **Missing sections** — articles without frontmatter, `## Summary`, or `## For Future Queries`
3. **Stale articles** — documents re-ingested after their last LLM compile
4. **Broken concept refs** — `_concepts/` pages referencing unknown targets

---

### `clean` — Remove Stale Index Entries

```bash
kb clean [--dry-run] [--articles]
```

For every index entry whose source file no longer exists on disk:
- Removes the entry from `file_index.json`
- Removes the corresponding `_extracted.txt`
- `--articles`: also deletes matching wiki article in `_articles/`

Always preview with `--dry-run` first.

---

### `status` — Show Status

```bash
kb status
```

Shows document counts, LLM compile coverage (`🤖 LLM compiled: 12 / 20`), and directory status.

---

### `fetch` — Fetch from Any Source

```bash
kb fetch <source> [options]
```

Scrapes any source via Skill Seekers, deposits Markdown in `raw/skill_seekers/<slug>/`, and auto-ingests.

| Flag | Description |
|------|-------------|
| `--name <name>` | Custom folder name |
| `--async` | 2–3× faster (requires `skill-seekers[async]`) |
| `--no-ingest` | Fetch only; ingest manually later |
| `--compile` | Also run `compile` after ingesting |

```bash
kb fetch https://fastapi.tiangolo.com/
kb fetch tiangolo/fastapi --name fastapi
kb fetch https://docs.langchain.com/ --async --compile
```

### `fetch-list` — List Fetched Skills

```bash
kb fetch-list
```

---

### `deploy` — Sync Wiki to Cloud Server

```bash
kb deploy [options]
```

Pushes `wiki/` to a remote server via rsync/SSH so third-party users can access it through the cloud MCP API.

| Flag | Description |
|------|-------------|
| `--host` | Remote hostname or IP (required) |
| `--remote-user` | `user_id` on the server (required) |
| `--ssh-user` | SSH login user (default: `root`) |
| `--key` | Path to SSH private key |
| `--kb-id` | KB folder name on server (default: local directory name) |
| `--dry-run` | Preview without transferring |
| `--quiet / -q` | Suppress file-by-file output; show summary only |
| `--force` | Skip remote conflict check and overwrite without prompting |
| `--yes / -y` | Auto-confirm conflict prompt (for CI/scripts) |

Environment variable equivalents: `KBA_DEPLOY_HOST`, `KBA_DEPLOY_REMOTE_USER`, `KBA_DEPLOY_USER`, `KBA_DEPLOY_KEY`, `KBA_DEPLOY_KB_ID`.

**Conflict detection** — before syncing, `deploy` compares the modification time of the remote `_index.md` against the local one. If the remote is newer (e.g. an AI appended notes via `kb_append_note`), you are warned and prompted:

```
⚠️  Remote wiki has changes newer than local:
   Remote _index.md : 2026-04-17 14:23
   Local  _index.md : 2026-04-17 10:05
   Deploying will overwrite remote notes and articles.

   Proceed and overwrite remote? [y/N]
```

Use `--force` to skip the check in automated pipelines.

```bash
kb deploy --host myserver.com --remote-user alice --dry-run
kb deploy --host myserver.com --remote-user alice --quiet
kb deploy --host myserver.com --remote-user alice --force   # skip conflict check
```

---

## How Retrieval Works

The system uses a two-tier retrieval approach without vector embeddings:

```
Query: "How does sleep affect memory consolidation?"

Tier 1 — Document level (file_index.json, no file I/O):
  • Title match × 5
  • core_claims / key_data / quotes × 1
  • Retrieval queries (word-level) × 2
  • Retrieval queries (sentence-level, ≥2 keywords hit) × 5  ← key innovation

Tier 2 — Chunk level (stored alongside document metadata):
  • Chapter title match × 3 + preview match × 1
  • Best chunk score × 0.8 added to document score
  • Best matching chapter shown in results: "Chapter 7: REM Sleep and Memory"
```

The **retrieval queries** are 15 natural-language sentences written by Claude during `compile-llm`, covering three angles:
- Conceptual (what ideas does the document address?)
- Methodological (what evidence or methods does it use?)
- Application (what practical problems does it help with?)

Because these queries are written in the same vocabulary a researcher would use, keyword matching against them acts as a proxy for semantic search.

---

## Supported Input Formats

| Format | Support | Notes |
|--------|---------|-------|
| PDF | ✅ | Text extraction + OCR for scanned documents |
| EPUB | ✅ | Chapter structure preserved |
| Markdown | ✅ | Native |
| TXT | ✅ | Plain text |
| Web / GitHub / Video | ✅ via Skill Seekers | `pip install skill-seekers` |

---

## Configuration

`.kbaconfig` in your knowledge base root:

```yaml
name: "My Research KB"
version: "1.0"
path: /Users/username/my-research

ingest:
  supported_formats: [".pdf", ".epub", ".md", ".txt"]
  ignore_patterns: ["node_modules/", ".git/"]
```

---

## Installation

```bash
pip install -r requirements.txt

# Optional: OCR for scanned PDFs
brew install tesseract poppler   # macOS
sudo apt install tesseract-ocr poppler-utils   # Ubuntu/Debian

# Optional: web/GitHub/video ingestion
pip install skill-seekers[all]
```

Set any supported LLM API key before running `compile-llm` — see [LLM API Key Setup in QUICKSTART.md](../QUICKSTART.md).

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Scanned PDF not recognized | Install Tesseract (see above) |
| `compile-llm` fails | Check that an LLM API key is set — see [QUICKSTART.md](../QUICKSTART.md) |
| `compile-llm` shows `❌ LLM compile failed` | Run `compile-llm --retry-failed`; check error in `status` |
| `skill-seekers` not found | `pip install skill-seekers` |
| Encoding issues | `iconv -f GBK -t UTF-8 input.txt > output.txt` |
| Index stale after adding docs | Run `compile-llm --docs` (auto-rebuilds index) |
| Articles not found in search | Run `compile-llm --docs` to generate retrieval queries |
| Deploy warns about remote changes | Pull remote notes first, or use `--force` to overwrite |

## License

MIT License
