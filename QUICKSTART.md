# Quick Start Guide

Get up and running with Knowledge Base Suite in minutes.

## Prerequisites

- Python 3.9+
- pip

## Installation

```bash
git clone https://github.com/QuanL306/ensemble-llm-wiki.git
cd ensemble-llm-wiki

# (Recommended) create an isolated virtual environment first
python3 -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

# Install all dependencies
pip install -r requirements.txt

# System dependencies for PDF processing (not installed by pip):
#   macOS:  brew install tesseract poppler
#   Linux:  sudo apt install tesseract-ocr poppler-utils

# Add the repo to your PATH so the 'kb' command works from any directory.
# Replace /path/to with the actual location where you cloned the repo.
# macOS / Linux — paste the export line into ~/.zshrc or ~/.bashrc:
export PATH="$PATH:/path/to/ensemble-llm-wiki"

# Windows — add the cloned folder to your system PATH, or invoke kb.bat directly

# Optional extras
pip install -r local-server/requirements.txt    # local MCP server
pip install -r cloud_platform/requirements.txt  # cloud platform
pip install skill-seekers                       # web/GitHub/video ingestion (basic)
pip install skill-seekers[all]                  # full (video, async, all platforms)
```

> **Note for Chinese users:** `requirements.txt` includes a commented-out `jieba` line.
> Uncomment it if you are building Chinese-language knowledge bases.


## LLM API Key Setup

`compile-llm` and the MCP server's query synthesis work with any of these providers —
set whichever key you have and the system auto-detects it:

| Provider | Env var | Sign up | Region |
|----------|---------|---------|--------|
| DeepSeek | `DEEPSEEK_API_KEY` | platform.deepseek.com | Global |
| OpenAI | `OPENAI_API_KEY` | platform.openai.com | Global |
| Claude (Anthropic) | `ANTHROPIC_API_KEY` | console.anthropic.com | Global |
| Gemini (Google) | `GEMINI_API_KEY` | aistudio.google.com | Global |
| Kimi (Moonshot) | `MOONSHOT_API_KEY` | platform.moonshot.cn | China |
| Zhipu GLM | `ZHIPU_API_KEY` | open.bigmodel.cn | China |
| MiniMax | `MINIMAX_API_KEY` | api.minimax.chat | China |

Auto-detection picks the first available key in the order above.
To override, set `LLM_BACKEND=openai` (or any provider name).
No extra SDK installation is needed — all providers use the standard library only.

```bash
# Example — pick whichever you have:
export DEEPSEEK_API_KEY=sk-...
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export GEMINI_API_KEY=AIza...
```

## Usage Scenarios

### Scenario 1: Build a Knowledge Base from Local Files

```bash
# Initialize (run from anywhere)
kb init ~/my-research --name "My Research"

# All subsequent commands run from inside the KB directory
cd ~/my-research

# ONE COMMAND: add files with automatic pipeline
kb add ~/Downloads/book.pdf                  # PDF → graphify-first
kb add ~/Downloads/article.md                # MD → compile-first
kb add ~/Downloads/*.pdf --yes              # batch mode, no prompt

# The 'add' command automatically:
#   1. Symlinks the file into raw/ (no copying — saves disk space)
#   2. Detects optimal pipeline (graphify-first for PDF, compile-first for MD)
#   3. Runs ingest → compile-llm → graphify in the correct order

# Manual control (if you prefer step-by-step):
#   Symlink or copy files to raw/ yourself, then run stages individually:
kb ingest                          # extract and index
kb graphify                        # build knowledge graph (requires LLM API key)
kb compile-llm --docs              # LLM writes wiki articles

# Open ~/my-research in Obsidian to explore
```

### Scenario 2: Fetch Knowledge from the Web (Skill Seekers)

```bash
kb init ~/tech-kb --name "Tech KB"
cd ~/tech-kb

# Fetch from documentation sites
kb fetch https://fastapi.tiangolo.com/
kb fetch https://docs.langchain.com/ --async

# Fetch from GitHub repositories
kb fetch tiangolo/fastapi --name fastapi-source

# Fetch and compile in one step
kb fetch https://docs.django.com/ --compile

# Check what you've fetched
kb fetch-list
```

### Scenario 3: LLM-Driven Wiki Compilation

The `compile-llm` command uses an LLM to write real wiki articles — not regex-extracted
summaries, but genuine analysis with cross-references, retrieval hints, and concept pages.
Works with any supported provider (see **LLM API Key Setup** above).

```bash
export DEEPSEEK_API_KEY=sk-...      # or any other supported key
cd ~/my-research

# Full pipeline (articles → index → concept pages)
kb compile-llm

# Run individual steps
kb compile-llm --docs       # article per document
kb compile-llm --index      # rebuild _index.md
kb compile-llm --concepts   # concept pages for [[links]]

# Force recompile all (even already-compiled documents)
kb compile-llm --full -y

# Use a specific model (overrides provider default)
kb compile-llm --model gpt-4o
kb compile-llm --model claude-opus-4-7    # most capable
kb compile-llm --model claude-sonnet-4-6  # balanced (default when using Claude)
kb compile-llm --model claude-haiku-4-5   # fastest, cheapest
```

### Scenario 4: AI-Powered Research Assistant (Local MCP)

```bash
# 1. Build your knowledge base (Scenarios 1–3)

# 2. Start the local MCP server (from the cloned repo)
cd local-server/src
python3 server.py --kb-path ~/my-research    # single KB
python3 server.py --kb-root ~/knowledge-bases # or point at a folder of KBs

# 3. Configure Claude Desktop: add to claude_desktop_config.json
# {
#   "mcpServers": {
#     "knowledge-base": {
#       "command": "python",
#       "args": ["/path/to/local-server/src/server.py", "--kb-path", "~/my-research"]
#     }
#   }
# }
```

### Scenario 5: Team Knowledge Sharing (Cloud Platform)

```bash
# 1. Build your knowledge base

# 2. Start the cloud platform
cd cloud_platform/deploy
docker-compose up -d

# 3. Deploy your wiki to the server
cd ~/my-research
kb deploy --host myserver.com --remote-user alice

# 4. Team members access via API
curl -X POST http://myserver.com:8000/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "X-KB-ID: my-research" \
  -d '{"name": "kb_search", "arguments": {"query": "machine learning"}}'
```

## Common Workflows

### Daily Research Workflow

```bash
cd ~/my-kb

# ONE COMMAND to add new content
kb add new-paper.pdf new-essay.md --yes

# Or step-by-step for fine control
kb ingest                         # incremental — only processes new files
kb graphify                       # update knowledge graph (requires LLM API key)
kb compile-llm --docs             # skips already-compiled documents

# Check health
kb lint

# Check status and LLM coverage
kb status
```

### Maintenance Workflow

```bash
cd ~/my-kb

# Remove source files you deleted from raw/
kb clean --dry-run     # preview first
kb clean               # apply

# Check wiki health
kb lint                # orphan links, missing sections, stale articles

# Test search without starting the server
kb search "transformer attention mechanism"
kb search "RLHF" --limit 10
```

### Deploy Workflow

```bash
cd ~/my-research

# Preview what would be synced
kb deploy --host myserver.com --remote-user alice --dry-run

# Deploy quietly (summary only, no file-by-file output)
kb deploy --host myserver.com --remote-user alice --quiet

# Full verbose deploy with custom SSH key
kb deploy \
  --host myserver.com \
  --remote-user alice \
  --kb-id my-research \
  --key ~/.ssh/deploy_key
```

### Fetch + Build Workflow (with Skill Seekers)

```bash
cd ~/my-kb

# Fetch knowledge from any source
kb fetch https://docs.python.org/3/
kb fetch openai/openai-python
kb fetch https://www.youtube.com/watch?v=...

# All fetched content lands in raw/skill_seekers/
# Normal ingest + compile follows automatically
```

## CLI Command Reference

| Command | Purpose |
|---------|---------|
| `init <path>` | Initialize a new knowledge base |
| `add <files...>` | Add files with automatic pipeline (ingest→compile→graphify) |
| `ingest` | Extract and index documents from `raw/` |
| `ingest --retry-failed` | Re-process documents that errored in a previous run |
| `ingest --file <name>` | Process only documents whose filename contains `<name>` |
| `compile` | Regex-based wiki compilation (no API key) |
| `compile-llm` | LLM-driven wiki compilation (requires any LLM API key) |
| `compile-llm --retry-failed` | Retry documents that failed LLM compilation |
| `compile-llm --file <name>` | Compile only documents whose filename contains `<name>` |
| `graphify` | Build knowledge graph from compiled wiki articles |
| `skip <name>` | Permanently skip a document (won't be processed again) |
| `unskip <name>` | Restore a skipped document to the pipeline |
| `fetch <source>` | Fetch from web/GitHub/video via Skill Seekers |
| `fetch-list` | List previously fetched skills |
| `search <query>` | Keyword search against local index |
| `lint` | Static wiki health check (orphan links, missing sections, stale articles) |
| `clean` | Remove stale index entries for deleted source files |
| `status` | Show document counts and pipeline progress with status bars |
| `deploy` | Sync wiki to cloud server via rsync/SSH |

## Directory Layout After Setup

```
my-research/
├── .kbaconfig            # KB configuration (name, version, pipeline rules)
├── .kbregistry.json       # Content registry — tracks every document through pipeline
├── raw/                  # Source documents (subfolders are organizational only)
│   ├── articles/         # Blog posts, essays, web articles
│   ├── books/            # EPUBs, full-length book PDFs
│   ├── papers/           # Academic papers, research reports
│   ├── images/           # Standalone image files
│   ├── web_clips/        # Saved web pages, browser clippings
│   └── skill_seekers/    # Fetched via `fetch` command
│       ├── docs_langchain_com/
│       │   ├── SKILL.md
│       │   └── api_reference.md
│       └── tiangolo_fastapi/
│           └── SKILL.md
└── wiki/                 # Compiled knowledge base
    ├── _index.md         # Start here in Obsidian
    ├── _articles/        # One wiki article per document
    ├── _concepts/        # Auto-extracted concept pages
    ├── _meta/            # Index files (JSON)
    └── graphify-out/     # Knowledge graph (graph.json, edges.jsonl, graph.html)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| OCR not working | `brew install tesseract` (macOS) or `apt install tesseract-ocr` |
| `skill-seekers` not found | `pip install skill-seekers` |
| `compile-llm` fails | Set at least one LLM API key (see **LLM API Key Setup**); no extra SDK needed |
| Permission denied on CLI | `chmod +x builder/src/cli.py` |
| Port already in use (server) | `python3 server.py --port 8001` |
| Scanned PDFs fail (pdf2image) | `brew install poppler` (macOS) or `apt install poppler-utils` |
| Semantic search not working | `pip install fastembed` (optional; enables embedding-based ranking) |
| Large PDFs slow | Process in batches; consider splitting large files first |
| `deploy` SSH auth fails | Check `--key` path; ensure remote user has write access to `/data/knowledge-bases/` |

## Next Steps

- [Builder Guide](builder/README.md) — All CLI commands and configuration options
- [Local Server Guide](local-server/README.md) — MCP server setup for AI clients
- [Cloud Platform Guide](cloud_platform/README.md) — Multi-tenant API deployment
- [Architecture](docs/ARCHITECTURE.md) — System design overview
