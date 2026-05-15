# Knowledge Base Suite — Architecture

## Design Origins

This architecture was originally inspired by **Karpathy's LLM Wiki** — the core pattern of interlinked markdown files as a knowledge base purpose-built for AI consumption. All subsequent enhancements (dual edges from OmegaWiki, confidence scoring from Pratiyush, contradiction detection from SamurAIGPT, SessionStart from ekadetov) are layers built on top of this foundation.

## System Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│                       Knowledge Base Suite                             │
│                                                                        │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │  Builder │   │ Local Server │   │  Dashboard   │   │   Cloud    │ │
│  │          │   │   (MCP)      │   │  (FastAPI)   │   │  Platform  │ │
│  │ ingest   │──►│ kb_query     │   │ /api/stats   │   │ HTTP API   │ │
│  │ graphify │   │ kb_search    │   │ /api/graph   │   │ auth+quota │ │
│  │ compile  │   │ kb_list_docs │   │ god nodes    │   │            │ │
│  │ confide. │   │ +synthesis   │   │ communities  │   │            │ │
│  │ exports  │   │ +write-back  │   │              │   │            │ │
│  └────┬─────┘   └──────────────┘   └──────────────┘   └────────────┘ │
│       │                                                                │
│  ┌────┴──────┐   ┌──────────┐                                         │
│  │   Skill   │   │ Graphify │  knowledge graph                        │
│  │  Seekers  │   │          │  JSON-LD + dual edges                   │
│  └───────────┘   └──────────┘  community detection                   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Pipeline

**Two execution paths — same architecture:**

### Interactive (MCP-first)
```
Hermes agent
  ├─ mcp_skill_seeker_*        ← native Skill Seekers MCP
  │  → raw/
  ├─ graphify --mcp            ← native Graphify MCP
  │  → graphify-out/
  └─ KB ingest → compile → serve
```

### Headless (cron / session_start.py)
```
                    SessionStart Hook
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                  ▼
    1. SCAN           2. INGEST          3. GRAPHIFY
    raw/ changes      extract text       knowledge graph
    manifest diff     contradiction      graph.json
                      detection          graph.jsonld
                           │             citations.jsonl
                           ▼             edges.jsonl
                     4. COMPILE              │
                     LLM wiki articles       │
                     _index.md               │
                     _concepts/              │
                           │                 │
                           ▼                 │
                     5. CONFIDENCE ◄─────────┘
                     ★★★/★★☆/★☆☆
                     cross-references
                     lifecycle stale
                          │
                          ▼
                    6. EXPORTS
                    llms.txt
                    llms-full.txt
                    overview.md
```

Skill Seekers and Graphify both expose native MCP servers. Interactive workflows go MCP-first — no wrappers needed.
The Builder wrappers (`skill_seekers.py`, `graphify_integration.py`) exist for headless cron/auto-sync where no agent is available.

---

## Component 1 — Builder

### Ingestion (`ingest.py`)

Powered by **yusufkaraaslan/Skill_Seekers** — fetches knowledge from 17 source types (docs, repos, video, PDFs, etc.) into the KB's `raw/` directory.

```
raw/*.pdf / *.epub / *.md
    │
    ▼ DataIngest.process_file()
    │   • PyMuPDF / EbookLib / plain text extraction
    │   • OCR for scanned PDFs (Tesseract)
    │   • Heuristic extraction: core_claims, key_data, quotes
    │   • Contradiction detection (from contradictions.py)
    │     → compares new doc against existing docs
    │     → negation pair matching (increase↔decrease, etc.)
    │     → flags written to file_index.json
    ▼
wiki/_meta/file_index.json
```

### Graphify Integration (`graphify_integration.py`)

Headless wrapper for cron/auto-sync. For interactive workflows, use `graphify --mcp` directly.

Powered by **safishamsi/graphify** — turns raw content into knowledge graphs with community detection. Adds three post-processing steps not provided by Graphify natively:

```
Skill Seekers output / raw/ directory
    │
    ▼ run_graphify()  →  graph.json + graph.html
    │
    ├── generate_jsonld()  →  graph.jsonld (Schema.org)
    │
    ├── split_edges()  →  citations.jsonl + edges.jsonl
    │                     (citation edges vs semantic edges)
    │
    └── tag_edge_provenance()  →  graph_provenance.json
                                  edges_tagged.jsonl
```

Borrowed from OmegaWiki's dual edge system — separates pure citation relationships from semantic ones to prevent citation noise from diluting the knowledge graph.

**Edge provenance tags:** `EXTRACTED` (wikilink `[[…]]` found verbatim in an article), `INFERRED` (LLM-detected semantic relationship), `AMBIGUOUS` (inferred with weight < 0.3). Downstream consumers can filter by tag — e.g. use only `EXTRACTED` edges for a strict citation graph.

### Transcript Harvester (`transcript_harvester.py`)

Imports AI session transcripts into the KB's `raw/transcripts/` directory so they flow through the normal ingest → compile pipeline.

```
~/.claude/projects/**/*.jsonl   (Claude Code sessions)
Cursor SQLite store              (Cursor sessions, macOS)
    │
    ▼ ClaudeCodeAdapter / CursorAdapter
    │   • Parse user/assistant exchanges
    │   • Skip sessions with <3 exchanges
    │   • Scrub sensitive patterns (API keys, passwords, Bearer tokens)
    │
    ▼ session_to_markdown()
    │   • YAML frontmatter: type, source, session_id, harvested date
    │   • User/assistant exchange pairs (assistant text truncated at 2 000 chars)
    │
    ▼ raw/transcripts/<slug>.md
        + .harvest_manifest.json  (dedup: slug → {harvested_at, source, count})
```

Triggered via `python cli.py harvest [--since N] [--sources …]`.

### Confidence Scoring (`confidence.py`)

4-factor model borrowed from Pratiyush/llm-wiki:

| Factor | Weight | Computation |
|--------|--------|-------------|
| Source count | 20% | min(N/5, 1.0) — caps at 5 independent sources |
| Source quality | 25% | Average tier: academic(5) → major media(4) → news(3) → blog(2) → social(1) |
| Recency | 30% | Ebbinghaus decay: 0.5^(days/half_life) — half-life varies by type |
| Cross-references | 25% | min(N/10, 1.0) — caps at 10 wiki pages linking in |

Recency half-life by content type: news(7d), analysis(30d), report(90d), paper(365d), book(730d).

Tiers: ★★★ (≥0.8), ★★☆ (≥0.6), ★☆☆ (≥0.4), --- (<0.4).

### Lifecycle (`lifecycle.py`)

5-state machine borrowed from Pratiyush/llm-wiki:

```
draft ──→ reviewed ──→ verified
  ↑          ↑  ↓         │
  │          │  └─→ draft  │
  │          │             │
  └── stale ◄──────────────┘  (auto: 90 days no update)
       │
       └──→ archived  (auto: 180 days total)
              │
              └──→ draft  (manual restore)
```

### Exports (`exports.py`)

Generates after every `compile-llm`:
- `wiki/llms.txt` — short index per llmstxt.org spec
- `wiki/llms-full.txt` — flattened plain-text dump (5MB cap)
- `wiki/overview.md` — living synthesis updated on every compile

Borrowed from Pratiyush/llm-wiki (llms.txt) and SamurAIGPT/llm-wiki-agent (overview.md).

### Extended Entity Types (`entity_types.py`)

Borrowed from OmegaWiki's 9-entity system. Knowledge-base-suite-en now supports:
- `_articles/` — source document summaries
- `_concepts/` — cross-referenced technical concepts
- `_methods/` — reusable analytical frameworks (with parent/child chains)
- `_topics/` — research direction maps (SOTA + benchmarks + open problems)
- `foundations/` — terminal background pages (receive links, never emit)

### Contradiction Detection (`contradictions.py`)

Ingest-time detection borrowed from SamurAIGPT/llm-wiki-agent:
- Extracts claim-like sentences from new and existing documents
- Matches negation pairs (increase↔decrease, support↔oppose, etc.)
- Requires ≥3 shared topic words for a match
- Flags written to both the new document and the contradicted existing documents in `file_index.json`

---

## Component 2 — Local Server

MCP stdio server exposing 6 tools: `kb_query`, `kb_search`, `kb_list_docs`, `kb_get_document`, `kb_list`, `kb_save_synthesis`.

Confidence-aware:
- `kb_list` shows verified document count
- `kb_list_docs` shows ★★★/★★☆/★☆☆ tier labels + lifecycle state + ⚠️ contradiction flags
- `kb_search` shows confidence tier per result

**Syntheses** — `kb_save_synthesis(question, answer, sources)` writes a permanent wiki page to `wiki/syntheses/<slug>.md` with YAML frontmatter (`type: synthesis`). Syntheses are:
- Searchable alongside compiled articles via `kb_search` and `kb_list_docs`
- Listed with a `(synthesis)` suffix in `kb_list_docs`
- Written by the AI at query time rather than compiled from source documents

### Retrieval scoring model

Three-layer hybrid scoring (`scoring.py`):
- **Layer 1 — Keyword TF-IDF** (40%): title×50 + body×10 + retrieval queries×20 + sentence bonus×5
- **Layer 2 — Embedding cosine** (40%): `BAAI/bge-small-en-v1.5` (384-dim, ONNX via fastembed)
- **Layer 3 — Concept boost** (20%): keyword ↔ concept name ↔ document link matching

Multi-hop retrieval: Stage 1 (top 5) → extract terminology → Stage 2 (additional 3 docs via concept bridge).

---

## Component 3 — Dashboard

FastAPI web dashboard (`dashboard/app.py`). No separate MCP wrapper — Graphify's native `--mcp` is used directly.

| Endpoint | Description |
|----------|-------------|
| `/` | Dashboard UI (HTML) |
| `/api/stats` | KB doc counts + graph node/edge/community counts |
| `/api/graph/{id}` | God nodes + community summaries |
| `/api/graph/{id}/html` | Full interactive graph.html (iframe) |
| `/api/graph/{id}/god-nodes` | Top N nodes by degree centrality |
| `/health` | Health check |

Auto-discovers knowledge bases via `.kbaconfig` files and Graphify outputs via `wiki/graphify-out/graph.json`.

---

## Component 4 — Cloud Platform

HTTP API gateway + MCP HTTP server with Redis-backed auth, rate limiting, monthly quotas, and multi-tenant KB isolation. Same tool surface as the local server.

### Shared KB Access

Team members can be granted read or write access to another user's KB without provisioning a separate copy.

```
Membership stored in .kbaconfig (YAML):
  owner: <owner_user_id>
  members:
    - user_id: <bob_uuid>
      role: write
    - user_id: <carol_uuid>
      role: read

Access control flow:
  X-User-ID      — authenticated caller (from API key or JWT sub)
  X-KB-Owner-ID  — whose KB to access (defaults to X-User-ID → backward-compatible)

  check_access(owner_id, kb_id, requester_id, required_role):
    owner   → always allowed
    member  → role hierarchy: write > read (write also grants read)
    other   → denied

Management endpoints (gateway, JWT-authenticated):
  POST   /api/v1/knowledge-bases/{kb_id}/members        add/update member by email
  DELETE /api/v1/knowledge-bases/{kb_id}/members/{email} remove member

  Gateway resolves email → user_id via Redis before calling MCP server.
```

`list_shared_kbs` results are cached per requester (60 s TTL, instance-scoped); cache entry is invalidated immediately when a member is added or removed.

Old KBs without an `owner` field in `.kbaconfig` are migrated transparently — the field is written on the first member-management operation.

---

## Key Design Decisions

**MCP-first pipeline**: For interactive use, both Skill Seekers and Graphify are accessed via their native MCP servers — no Builder wrappers needed. The wrappers exist for headless cron/auto-sync where no agent context is available.

**Skill Seekers → Graphify → compile-llm**: Graphify runs BEFORE LLM compilation so the knowledge graph structure can inform cross-document linking and concept discovery.

**Confidence at query time**: Confidence scores are computed at compile time, stored in `file_index.json`, and exposed through every MCP tool response — no extra computation at query time.

**Dual edge system**: Citations (`cites`, `references`) are stored separately from semantic edges (`builds_on`, `complements`, `contains`, etc.) to prevent citation noise from overwhelming the semantic graph.

**Terminal foundation pages**: Background knowledge pages only receive incoming `[[wikilinks]]` — they never emit links. This prevents basic concepts from becoming gravity wells in the knowledge graph.

**Contradiction at ingest**: When a new document contradicts an existing claim, it's flagged immediately in `file_index.json` — not deferred to query time. Both the new document and the contradicted existing document get annotated.

**Non-blocking enrichment**: Contradiction detection and Graphify are non-blocking — failure doesn't halt the pipeline.

**Embedding-assisted without vector DB**: Vectors stored in `embeddings.json` (~1.5KB per doc). No FAISS/Pinecone/ChromaDB needed. Graceful fallback to pure keyword TF-IDF.

**Shared KB via header, not URL**: `X-KB-Owner-ID` defaults to `X-User-ID` — existing single-user clients continue working without any change. Ownership and membership are resolved entirely server-side from `.kbaconfig`; the gateway never needs to know the KB directory structure.

**Syntheses vs articles**: Articles are compiled by the LLM from source documents at build time. Syntheses are written by the AI at query time in response to a specific question. They are stored separately (`wiki/syntheses/`) but searched alongside articles. The separation makes provenance clear — a synthesis has no confidence score because it has no independent sources.

**Transcript harvesting with credential scrubbing**: Session transcripts often contain sensitive data typed during coding work. The harvester redacts credential patterns (API keys, passwords, Bearer tokens) before writing to disk — failing safe (over-redacting) rather than under-redacting.
