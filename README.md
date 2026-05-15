# Knowledge Base Suite

A complete ecosystem for building, accessing, and sharing knowledge with AI — from any source. Integrates Skill Seekers, Graphify, LLM compilation, confidence scoring, and multi-tenant cloud serving.

**Original design inspiration:** Karpathy's LLM Wiki (interlinked markdown knowledge base pattern for AI consumption).

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                       Knowledge Base Suite                             │
│                                                                        │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │  Builder │   │ Local Server │   │  Dashboard   │   │   Cloud    │ │
│  │          │   │   (MCP)      │   │  (FastAPI)   │   │  Platform  │ │
│  │ ingest   │──►│ kb_query     │   │ stats + graph│   │ HTTP API   │ │
│  │ graphify │   │ kb_search    │   │ god nodes    │   │ auth+quota │ │
│  │ compile  │   │ +synthesis   │   │ communities  │   │ multi-tenant│ │
│  │ confide. │   │ +write-back  │   │              │   │            │ │
│  │ exports  │   │              │   │              │   │            │ │
│  └────┬─────┘   └──────────────┘   └──────────────┘   └────────────┘ │
│       │                                                                │
│  ┌────┴──────┐   ┌──────────┐                                         │
│  │   Skill   │   │ Graphify │  knowledge graph + JSON-LD +            │
│  │  Seekers  │   │          │  community detection + dual edges       │
│  └───────────┘   └──────────┘                                         │
└───────────────────────────────────────────────────────────────────────┘
```

## Pipeline

**Two execution paths — same architecture:**

```
Interactive (MCP-first)          Headless (cron / session_start.py)
─────────────────────────        ────────────────────────────────
Hermes agent                     session_start.py
  │                                │
  ├─ mcp_skill_seeker_*            ├─ skill_seekers.py (subprocess)
  │  → raw/                        │  → raw/
  │                                │
  ├─ graphify --mcp                ├─ graphify_integration.py (subprocess)
  │  → graphify-out/               │  → graphify-out/
  │                                │     ├─ generate_jsonld()
  │                                │     └─ split_edges()
  └─ KB ingest → compile → serve   └─ ingest → compile → confidence → exports
```

Skill Seekers and Graphify both expose native MCP servers. Interactive workflows go MCP-first.
The Builder wrappers (`skill_seekers.py`, `graphify_integration.py`) exist for headless cron/auto-sync.

## Components

### 1. Builder — Create & Maintain

| Module | Borrowed from | What it does |
|--------|--------------|-------------|
| `ingest.py` | — | PDF/EPUB/MD → extracted text + metadata |
| `graphify_integration.py` | **safishamsi/graphify** | Headless wrapper: subprocess → Graphify + JSON-LD + dual edges |
| `compiler.py` | — | LLM/wiki compilation |
| `scoring.py` | — | Hybrid retrieval: TF-IDF + embeddings |
| `skill_seekers.py` | **yusufkaraaslan/Skill_Seekers** | Headless wrapper: subprocess → fetch docs/repos/video |
| `confidence.py` | **Pratiyush/llm-wiki** | 4-factor scoring: source count/quality/recency/cross-refs |
| `exports.py` | **Pratiyush/llm-wiki** | `llms.txt` + `llms-full.txt` + `overview.md` |
| `contradictions.py` | **SamurAIGPT/llm-wiki-agent** | Ingest-time negation pair detection |
| `lifecycle.py` | **Pratiyush/llm-wiki** | 5-state: draft→reviewed→verified→stale→archived |
| `foundations.py` | **OmegaWiki** | Terminal pages: receive links, never emit |
| `entity_types.py` | **OmegaWiki** | Extended types: methods/ + topics/ |
| `session_start.py` | **ekadetov+Pratiyush** | Auto-sync: changed files → full 6-step pipeline |
| `transcript_harvester.py` | — | Import AI session transcripts (Claude Code, Cursor) into the KB |
| *Architecture* | **Karpathy/llm-wiki** | Original interlinked markdown KB + AI consumption design |

### 2. Local Server — MCP Integration

| Tool | Description |
|------|-------------|
| `kb_query` | Retrieval (TF-IDF + embedding hybrid) + Haiku synthesis |
| `kb_search` | Keyword search with confidence tier + lifecycle display |
| `kb_list_docs` | Browse with ★★★/★★☆/★☆☆ confidence + verified count |
| `kb_get_document` | Full article with section extraction |
| `kb_list` | KB overview with document/verified/concept counts |
| `kb_save_synthesis` | Permanently save a query answer as a `wiki/syntheses/` page |

### 3. Dashboard — Web Visualization

FastAPI web UI at `http://127.0.0.1:8765`:
- **Stats cards** — KB doc counts, graph node/edge/community counts
- **God Nodes panel** — highest-degree nodes ranked by centrality
- **Community detection** — louvain community summary with sample labels
- **Graph iframe** — full interactive graph.html from Graphify
- **Auto-discovery** — finds `wiki/graphify-out/` under any KB

Start: `python3 dashboard/app.py`

### 4. Cloud Platform — Multi-tenant HTTP API

HTTP API with API Key + JWT auth, bcrypt passwords, monthly quotas, multi-tenant KB isolation.

**Shared KB access** — team members can be granted `read` or `write` access to another user's KB:

```
Owner adds member:
  POST /api/v1/knowledge-bases/{kb_id}/members
  Body: {"email": "bob@example.com", "role": "read"}

Member accesses shared KB:
  X-KB-Owner-ID: <owner_user_id>   ← add to every MCP tool call
  (omit → defaults to own KB, backward-compatible)

Owner removes member:
  DELETE /api/v1/knowledge-bases/{kb_id}/members/{email}
```

Role hierarchy: `write` also grants `read`. Only the owner can manage membership.

## Quick Start

```bash
# 1. Build a knowledge base
cd builder/src
python cli.py init ~/my-kb --name "My KB"
# Add files to ~/my-kb/raw/, then:
python cli.py ingest && python cli.py compile-llm

# 2. Run confidence + exports
python -c "from core.confidence import *; from core.exports import *; ..."

# 3. Build knowledge graph (with edge provenance tagging)
python core/graphify_integration.py --input ~/my-kb --project my-kb --jsonld

# 4. Harvest AI session transcripts into the KB
python cli.py harvest --since 7          # last 7 days of Claude Code sessions
python cli.py harvest --sources cursor   # Cursor sessions instead
python cli.py harvest --list             # show what's already imported

# 5. Serve via MCP
cd ../../local-server/src
python server.py --kb-path ~/my-kb

# 6. Auto-sync (SessionStart)
python ../builder/src/core/session_start.py --kb-path ~/my-kb
```

## Building from Local PDFs

You already have a folder of PDFs. Which path to take depends on content type:

| Content type | Examples | Recommended path |
|---|---|---|
| Prose-heavy | Books, essays, research papers, reports | Direct ingest |
| Technical / code-heavy | Programming textbooks, API docs, papers with algorithms | Skill Seekers first |

Skill Seekers does structured extraction that the builder's raw PDF reader does not: it detects code blocks across pages (3 methods + quality scoring), identifies 19+ programming languages, merges split code blocks, and organises content into chapters. For prose-only PDFs the output is equivalent, so the extra step is unnecessary.

---

### Case 1 — Prose PDFs (books, papers, reports)

```bash
cd builder/src

# Initialise a KB (skip if you already have one)
python cli.py init ~/my-kb --name "Research Library"

# Copy your PDFs into the raw/ intake folder
cp /path/to/your/pdfs/*.pdf ~/my-kb/raw/

# Ingest: extract text + metadata from every file in raw/
python cli.py ingest

# Compile: LLM writes wiki articles + builds search index + embeddings
python cli.py compile-llm --docs
```

Then serve it:

```bash
cd ../../local-server/src
python server.py --kb-path ~/my-kb
```

---

### Case 2 — Technical / code-heavy PDFs

Use the built-in `fetch` command, which runs Skill Seekers and auto-ingests the result in one step:

```bash
cd builder/src

# Initialise a KB (skip if you already have one)
python cli.py init ~/my-kb --name "Tech Library"

# Fetch + ingest each PDF via Skill Seekers
# (repeat for every PDF you want to include)
python cli.py fetch /path/to/your/pdfs/book.pdf
python cli.py fetch /path/to/your/pdfs/paper.pdf

# Compile once all PDFs are fetched
python cli.py compile-llm --docs
```

Skill Seekers output lands in `~/my-kb/raw/skill_seekers/<slug>/` as structured Markdown. The `fetch` command ingests it automatically, so you go straight to compile when done.

**Batch fetch a whole folder:**

```bash
for pdf in /path/to/your/pdfs/*.pdf; do
    python cli.py fetch "$pdf"
done
python cli.py compile-llm --docs
```

**Check what was fetched:**

```bash
python cli.py fetch-list
```

Then serve:

```bash
cd ../../local-server/src
python server.py --kb-path ~/my-kb
```

## Directory Structure

```
knowledge-base-suite-en/
├── builder/
│   └── src/
│       ├── cli.py                       # CLI entry point
│       └── core/
│           ├── ingest.py                # Document ingestion + contradiction detection
│           ├── compiler.py              # LLM/wiki compilation
│           ├── indexer.py               # file_index.json management
│           ├── scoring.py               # Hybrid retrieval (TF-IDF + embeddings)
│           ├── skill_seekers.py         # Skill Seekers integration
│           ├── graphify_integration.py  # Graphify pipeline + JSON-LD + dual edges + provenance
│           ├── confidence.py            # 4-factor confidence scoring
│           ├── exports.py               # llms.txt + overview.md generation
│           ├── contradictions.py        # Ingest-time contradiction flags
│           ├── lifecycle.py             # 5-state lifecycle machine
│           ├── foundations.py           # Terminal foundation pages
│           ├── entity_types.py          # Extended entities (methods/topics)
│           ├── session_start.py         # Auto-sync hook (6-step pipeline)
│           └── transcript_harvester.py  # AI session transcript import (Claude Code, Cursor)
├── local-server/
│   └── src/server.py                    # MCP server (confidence-aware + syntheses)
├── dashboard/
│   ├── app.py                           # FastAPI web dashboard
│   └── templates/index.html             # Dashboard UI
├── cloud_platform/                      # Multi-tenant HTTP API + shared KB access
├── docs/
│   └── ARCHITECTURE.md
└── README.md
```

## How Retrieval Works

Three-layer hybrid scoring: keyword TF-IDF (40%) + embedding cosine similarity (40%) + concept boost (20%). Multi-hop retrieval: A→B→C discovery chains. Embeds docs at compile time via `BAAI/bge-small-en-v1.5` (384-dim). Falls back gracefully to pure keyword if `fastembed` not installed.

## Syntheses

Answers generated by `kb_query` can be saved permanently as first-class wiki pages:

```
kb_save_synthesis(question="What is X?", answer="...", sources=["doc1", "doc2"])
```

Saved to `wiki/syntheses/<slug>.md` with YAML frontmatter (`type: synthesis`). Syntheses are searchable via `kb_query`, `kb_search`, and `kb_list_docs` (where they appear with a `(synthesis)` suffix). Unlike articles, syntheses are written by the AI rather than compiled from source documents — the frontmatter records the originating question and source articles.

This creates a **knowledge growth loop**: each saved synthesis becomes a retrievable source for future queries. The KB accumulates derived knowledge over time without manual curation.

## Transcript Harvesting

Import your AI coding session history into the KB so it becomes searchable knowledge:

```bash
cd builder/src

# Import Claude Code sessions from the last 30 days
python cli.py harvest --since 30

# Import Cursor sessions too
python cli.py harvest --sources claude-code,cursor --since 7

# Show what's already been imported (manifest)
python cli.py harvest --list
```

Sessions land in `raw/transcripts/<slug>.md` and flow through the normal ingest → compile pipeline. A manifest file (`.harvest_manifest.json`) deduplicates: re-running harvest only imports new sessions. Sensitive data (API keys, passwords, tokens) is scrubbed before writing.

Supported sources: `claude-code` (`~/.claude/projects/**/*.jsonl`), `cursor` (macOS SQLite store).

## Graph Edge Provenance

After Graphify builds `graph.json`, `graphify_integration.py` automatically tags every edge with its evidence type:

| Tag | Meaning |
|-----|---------|
| `EXTRACTED` | A `[[wikilink]]` found verbatim in an article |
| `INFERRED` | LLM-detected semantic relationship (no explicit link) |
| `AMBIGUOUS` | Inferred with low confidence (weight < 0.3) |

Output files written alongside `graph.json`:

- `graph_provenance.json` — full graph with `provenance` field on every edge
- `edges_tagged.jsonl` — one JSON line per edge, for easy downstream filtering

To skip provenance tagging: `--no-provenance`.

## Confidence Scoring

| Factor | Weight | Source |
|--------|--------|--------|
| Source count | 20% | How many independent sources cite this claim |
| Source quality | 25% | Academic (5) → major media (4) → news (3) → blog (2) → social (1) |
| Recency | 30% | Ebbinghaus decay: half-life varies by content type (news 7d, analysis 30d, paper 365d) |
| Cross-references | 25% | How many other wiki pages link to this one |

Tiers: ★★★ (high, ≥0.8) · ★★☆ (medium, ≥0.6) · ★☆☆ (low, ≥0.4) · --- (unverified)

## License

MIT License — Version 2.0.0
