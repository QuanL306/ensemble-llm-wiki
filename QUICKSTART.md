# Quick Start Guide

Get up and running with Knowledge Base Suite in minutes.

## Prerequisites

- Python 3.9+
- pip

## Installation

```bash
# Install Builder dependencies (required)
pip install -r builder/requirements.txt

# Install Local Server dependencies (optional)
pip install -r local-server/requirements.txt

# Install Cloud Platform dependencies (optional)
pip install -r cloud_platform/requirements.txt

# Install Skill Seekers for web/GitHub/video ingestion (optional)
pip install skill-seekers          # basic
pip install skill-seekers[all]     # full (video, async, all platforms)

# Install Anthropic SDK for LLM-driven compilation (optional)
pip install anthropic
```

## Usage Scenarios

### Scenario 1: Build a Knowledge Base from Local Files

```bash
cd builder/src

# Initialize
python cli.py init ~/my-research --name "My Research"

# Drop PDFs, EPUBs, Markdown files into raw/
cp ~/Downloads/*.pdf ~/my-research/raw/

# Ingest: extract and index all documents
python cli.py ingest

# Compile Option A — LLM-driven (recommended, requires ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
python cli.py compile-llm           # writes real wiki articles via Claude

# Compile Option B — regex-based (no API key, fast structural scaffold)
python cli.py compile

# Open ~/my-research in Obsidian to explore
```

### Scenario 2: Fetch Knowledge from the Web (Skill Seekers)

```bash
cd builder/src
python cli.py init ~/tech-kb --name "Tech KB"
cd ~/tech-kb

# Fetch from documentation sites
python cli.py fetch https://fastapi.tiangolo.com/
python cli.py fetch https://docs.langchain.com/ --async

# Fetch from GitHub repositories
python cli.py fetch tiangolo/fastapi --name fastapi-source

# Fetch and compile in one step
python cli.py fetch https://docs.django.com/ --compile

# Check what you've fetched
python cli.py fetch-list
```

### Scenario 3: LLM-Driven Wiki Compilation

The `compile-llm` command uses Claude to write real wiki articles — not regex-extracted
summaries, but genuine analysis with cross-references, retrieval hints, and concept pages.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
cd ~/my-research

# Full pipeline (articles → index → concept pages)
python src/cli.py compile-llm

# Run individual steps
python src/cli.py compile-llm --docs       # article per document
python src/cli.py compile-llm --index      # rebuild _index.md
python src/cli.py compile-llm --concepts   # concept pages for [[links]]

# Force recompile all (even already-compiled documents)
python src/cli.py compile-llm --full -y

# Use Opus for higher quality on important knowledge bases
python src/cli.py compile-llm --model claude-opus-4-5
```

### Scenario 4: AI-Powered Research Assistant (Local MCP)

```bash
# 1. Build your knowledge base (Scenarios 1–3)

# 2. Start the local MCP server
cd local-server/src
python server.py --kb-path ~/my-research

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
python src/cli.py deploy --host myserver.com --remote-user alice

# 4. Team members access via API
curl -X POST http://myserver.com:8000/mcp/v1/tools/call \
  -H "X-API-Key: kb_live_xxx" \
  -H "X-KB-ID: my-research" \
  -d '{"name": "kb_search", "arguments": {"query": "machine learning"}}'
```

## Common Workflows

### Daily Research Workflow

```bash
cd builder/src

# Add new papers → ~/my-kb/raw/
# Incrementally ingest only new files
python cli.py ingest

# LLM-compile new documents (skips already-compiled ones)
python cli.py compile-llm --docs

# Check health
python cli.py lint

# Check status and LLM coverage
python cli.py status
```

### Maintenance Workflow

```bash
cd builder/src

# Remove source files you deleted from raw/
python cli.py clean --dry-run     # preview first
python cli.py clean               # apply

# Check wiki health
python cli.py lint                # orphan links, missing sections, stale articles

# Test search without starting the server
python cli.py search "transformer attention mechanism"
python cli.py search "RLHF" --limit 10
```

### Deploy Workflow

```bash
cd builder/src

# Preview what would be synced
python cli.py deploy --host myserver.com --remote-user alice --dry-run

# Deploy quietly (summary only, no file-by-file output)
python cli.py deploy --host myserver.com --remote-user alice --quiet

# Full verbose deploy with custom SSH key
python cli.py deploy \
  --host myserver.com \
  --remote-user alice \
  --kb-id my-research \
  --key ~/.ssh/deploy_key
```

### Fetch + Build Workflow (with Skill Seekers)

```bash
cd builder/src

# Fetch knowledge from any source
python cli.py fetch https://docs.python.org/3/
python cli.py fetch openai/openai-python
python cli.py fetch https://www.youtube.com/watch?v=...

# All fetched content lands in raw/skill_seekers/
# Normal ingest + compile follows automatically
```

## CLI Command Reference

| Command | Purpose |
|---------|---------|
| `init <path>` | Initialize a new knowledge base |
| `ingest` | Extract and index documents from `raw/` |
| `compile` | Regex-based wiki compilation (no API key) |
| `compile-llm` | LLM-driven wiki compilation (requires `ANTHROPIC_API_KEY`) |
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
| `compile-llm` fails | Check `ANTHROPIC_API_KEY` is set; `pip install anthropic` |
| Permission denied on CLI | `chmod +x builder/src/cli.py` |
| Port already in use (server) | `python server.py --port 8001` |
| Large PDFs slow | Process in batches; consider splitting large files first |
| `deploy` SSH auth fails | Check `--key` path; ensure remote user has write access to `/data/knowledge-bases/` |

## Next Steps

- [Builder Guide](builder/README.md) — All CLI commands and configuration options
- [Local Server Guide](local-server/README.md) — MCP server setup for AI clients
- [Cloud Platform Guide](cloud_platform/README.md) — Multi-tenant API deployment
- [Architecture](docs/ARCHITECTURE.md) — System design overview
