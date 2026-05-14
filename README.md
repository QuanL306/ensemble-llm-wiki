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
| *Architecture* | **Karpathy/llm-wiki** | Original interlinked markdown KB + AI consumption design |

### 2. Local Server — MCP Integration

| Tool | Description |
|------|-------------|
| `kb_query` | Retrieval (TF-IDF + embedding hybrid) + Haiku synthesis |
| `kb_search` | Keyword search with confidence tier + lifecycle display |
| `kb_list_docs` | Browse with ★★★/★★☆/★☆☆ confidence + verified count |
| `kb_get_document` | Full article with section extraction |
| `kb_list` | KB overview with document/verified/concept counts |

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

## Quick Start

```bash
# 1. Build a knowledge base
cd builder/src
python cli.py init ~/my-kb --name "My KB"
# Add files to ~/my-kb/raw/, then:
python cli.py ingest && python cli.py compile-llm

# 2. Run confidence + exports
python -c "from core.confidence import *; from core.exports import *; ..."

# 3. Build knowledge graph
python core/graphify_integration.py --input ~/my-kb --project my-kb --jsonld

# 4. Serve via MCP
cd ../../local-server/src
python server.py --kb-path ~/my-kb

# 5. Auto-sync (SessionStart)
python ../builder/src/core/session_start.py --kb-path ~/my-kb
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
│           ├── graphify_integration.py  # Graphify pipeline + JSON-LD + dual edges
│           ├── confidence.py            # 4-factor confidence scoring
│           ├── exports.py               # llms.txt + overview.md generation
│           ├── contradictions.py        # Ingest-time contradiction flags
│           ├── lifecycle.py             # 5-state lifecycle machine
│           ├── foundations.py           # Terminal foundation pages
│           ├── entity_types.py          # Extended entities (methods/topics)
│           └── session_start.py         # Auto-sync hook (6-step pipeline)
├── local-server/
│   └── src/server.py                    # MCP server (confidence-aware)
├── dashboard/
│   ├── app.py                           # FastAPI web dashboard
│   └── templates/index.html             # Dashboard UI
├── cloud_platform/                      # Multi-tenant HTTP API
├── docs/
│   └── ARCHITECTURE.md
└── README.md
```

## How Retrieval Works

Three-layer hybrid scoring: keyword TF-IDF (40%) + embedding cosine similarity (40%) + concept boost (20%). Multi-hop retrieval: A→B→C discovery chains. Embeds docs at compile time via `BAAI/bge-small-en-v1.5` (384-dim). Falls back gracefully to pure keyword if `fastembed` not installed.

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
