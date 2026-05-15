# Quick Start Guide

Get up and running with Knowledge Base Suite in minutes.

## Prerequisites

- Python 3.9+
- pip

## Installation

```bash
git clone https://github.com/QuanL306/ensemble-llm-wiki.git
cd ensemble-llm-wiki

# Install all dependencies
pip install -r requirements.txt

# Add the repo to your PATH so the 'kb' command works from any directory
# macOS / Linux — add to ~/.zshrc or ~/.bashrc:
export PATH="$PATH:$(pwd)"

# Windows — add the cloned folder to your system PATH, or run kb.bat directly

# Optional extras
pip install -r local-server/requirements.txt    # local MCP server
pip install -r cloud_platform/requirements.txt  # cloud platform
pip install skill-seekers                       # web/GitHub/video ingestion (basic)
pip install skill-seekers[all]                  # full (video, async, all platforms)
```

> **Note for English-only users:** `requirements.txt` includes `jieba`, a Chinese NLP
> library used for Chinese-language knowledge bases. If you don't need it, comment out
> the `jieba` line before running `pip install`.


## LLM API Key Setup

`compile-llm` and the MCP server's query synthesis work with any of these providers —
set whichever key you have and the system auto-detects it:

| Provider | Env var | Sign up |
|----------|---------|---------|
| DeepSeek | `DEEPSEEK_API_KEY` | platform.deepseek.com |
| OpenAI | `OPENAI_API_KEY` | platform.openai.com |
| Kimi (Moonshot) | `MOONSHOT_API_KEY` | platform.moonshot.cn |
| Claude (Anthropic) | `ANTHROPIC_API_KEY` | console.anthropic.com |
| Gemini (Google) | `GEMINI_API_KEY` | aistudio.google.com |
| Zhipu GLM | `ZHIPU_API_KEY` | open.bigmodel.cn |
| MiniMax | `MINIMAX_API_KEY` | api.minimax.chat |

To override auto-detection, set `LLM_BACKEND=deepseek` (or any provider name above).
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

# Drop PDFs, EPUBs, Markdown files into raw/
cp ~/Downloads/*.pdf raw/

# Ingest: extract and index all documents
kb ingest

# Compile Option A — LLM-driven (recommended, requires any LLM API key)
export DEEPSEEK_API_KEY=sk-...      # or OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
kb compile-llm                      # writes real wiki articles via LLM

# Compile Option B — regex-based (no API key, fast structural scaffold)
kb compile

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
kb compile-llm --model claude-opus-4-7
```

### Scenario 4: AI-Powered Research Assistant (Local MCP)

```bash
# 1. Build your knowledge base (Scenarios 1–3)

# 2. Start the local MCP server (from the cloned repo)
cd local-server/src
python server.py --kb-path ~/my-research    # single KB
python server.py --kb-root ~/knowledge-bases # or point at a folder of KBs

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

# Add new papers → raw/
# Incrementally ingest only new files
kb ingest

# LLM-compile new documents (skips already-compiled ones)
kb compile-llm --docs

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
| `ingest` | Extract and index documents from `raw/` |
| `compile` | Regex-based wiki compilation (no API key) |
| `compile-llm` | LLM-driven wiki compilation (requires any LLM API key) |
| `fetch <source>` | Fetch from web/GitHub/video via Skill Seekers |
| `fetch-list` | List previously fetched skills |
| `search <query>` | Keyword search against local index |
| `lint` | Static wiki health check (orphan links, missing sections, stale articles) |
| `clean` | Remove stale index entries for deleted source files |
| `status` | Show document counts and LLM compile coverage |
| `deploy` | Sync wiki to cloud server via rsync/SSH |

## Directory Layout After Setup

```
my-research/
├── .kbaconfig            # KB configuration
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
    └── _meta/            # Index files (JSON)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| OCR not working | `brew install tesseract` (macOS) or `apt install tesseract-ocr` |
| `skill-seekers` not found | `pip install skill-seekers` |
| `compile-llm` fails | Set at least one LLM API key (see **LLM API Key Setup**); no extra SDK needed |
| Permission denied on CLI | `chmod +x builder/src/cli.py` |
| Port already in use (server) | `python server.py --port 8001` |
| Large PDFs slow | Process in batches; consider splitting large files first |
| `deploy` SSH auth fails | Check `--key` path; ensure remote user has write access to `/data/knowledge-bases/` |

## Next Steps

- [Builder Guide](builder/README.md) — All CLI commands and configuration options
- [Local Server Guide](local-server/README.md) — MCP server setup for AI clients
- [Cloud Platform Guide](cloud_platform/README.md) — Multi-tenant API deployment
- [Architecture](docs/ARCHITECTURE.md) — System design overview
