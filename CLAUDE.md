# Knowledge Base Suite — Agent Instructions

You are operating a toolkit that turns documents (PDF, EPUB, Markdown, TXT) into an
AI-queryable wiki with a knowledge graph. The output is an Obsidian-compatible folder
served by a local MCP server.

---

## 1. Setup (run once per machine)

```bash
pip install -r requirements.txt

# macOS system deps for PDF processing:
brew install tesseract poppler

# Set exactly one LLM API key — the system auto-detects whichever is present.
# Auto-detection order: DeepSeek → OpenAI → Claude → Gemini → Kimi → Zhipu → MiniMax
export DEEPSEEK_API_KEY=sk-...       # global, cheap, good default
export OPENAI_API_KEY=sk-...         # global
export ANTHROPIC_API_KEY=sk-ant-...  # global
export GEMINI_API_KEY=AIza...        # global
# export MOONSHOT_API_KEY=...        # Kimi — China
# export ZHIPU_API_KEY=...           # Zhipu GLM — China
# export MINIMAX_API_KEY=...         # MiniMax — China
```

---

## 2. Initialize a Knowledge Base

```bash
kb init ~/my-kb --name "My KB"
cd ~/my-kb          # all subsequent kb commands run from inside the KB directory
```

This creates the directory structure and writes `.kbaconfig`.

---

## 3. Adding Content — use `kb add` for everything

`kb add` is the single entry point. It copies the file, auto-detects the right pipeline,
and runs all stages in the correct order.

```bash
kb add path/to/paper.pdf              # auto-detects pipeline
kb add path/to/article.md             # auto-detects pipeline
kb add *.pdf --yes                    # batch, skip confirmation prompt
kb add paper.pdf --pipeline graphify-first   # force a specific pipeline
kb add paper.pdf --no-compile         # ingest + graphify only, skip LLM
kb add paper.pdf --model gpt-4o       # override LLM model for this file
```

Check progress at any time:

```bash
kb status       # document counts by stage (ingested / compiled / graphed)
kb lint         # health check: orphan links, stale articles, missing sections
```

---

## 4. The Two Pipelines — design intent

Both pipelines use Graphify strategically to improve wiki quality. Graphify builds a
knowledge graph from the wiki content and exposes entity relationships as graph context.
When the LLM compiles a wiki article, it receives this context and produces richer,
better cross-referenced output.

### graphify-first (PDF, EPUB, TXT)

```
ingest → graphify → compile-llm → graphify (2nd pass, optional)
```

**Why this order:**
1. **ingest** — extract raw text from the document into `wiki/`
2. **graphify** — build an initial knowledge graph from the raw extracted text;
   discovers entity relationships even before LLM processing
3. **compile-llm** — LLM writes the wiki article with graph context injected
   (related works, relationship types); produces richer cross-references
4. **graphify (2nd pass)** — re-runs on the LLM-compiled articles which now contain
   `[[wikilinks]]`; produces higher-confidence edges than the raw-text pass

The 2nd graphify pass is controlled by `.kbaconfig`:

```yaml
pipeline:
  graphify_twice: true   # default: false
```

### compile-first (Markdown)

```
ingest → compile-llm → graphify
```

**Why this order:**
Markdown files are already structured (headings, existing links). Compiling first lets
the LLM create `[[wikilinks]]` from the structured content. Graphify then runs on this
wikilink-rich output and produces the highest-quality graph edges from the start —
no raw-text pre-pass needed.

### Auto-detection rules (from `.kbaconfig`)

| File type | Default pipeline |
|-----------|-----------------|
| `.pdf`, `.epub`, `.txt` | `graphify-first` |
| `.md`, `.markdown` | `compile-first` |

Custom rules can be added to `.kbaconfig`:

```yaml
pipeline:
  default: graphify-first
  graphify_twice: false
  rules:
    - match: "*.md"
      pipeline: compile-first
    - match: "notes_*.txt"
      pipeline: compile-first
```

---

## 5. Step-by-step commands (when you need fine control)

```bash
# Copy files into raw/ yourself, then:
kb ingest                    # extract text from all new/changed files in raw/
kb graphify                  # build knowledge graph from wiki/
kb compile-llm --docs        # LLM writes one wiki article per document
kb compile-llm --index       # rebuild _index.md
kb compile-llm --concepts    # generate concept pages for [[wikilinks]]
kb graphify                  # 2nd pass after compile (picks up wikilinks)

# Force recompile everything:
kb compile-llm --full --yes

# Retry only failed documents:
kb compile-llm --retry-failed

# Target a single document by name fragment:
kb ingest --file report.pdf
kb compile-llm --file report.pdf

# Skip a broken document permanently (won't be processed again):
kb skip broken-scan.pdf

# Restore a skipped document:
kb unskip broken-scan.pdf
kb ingest --retry-failed          # then re-process it
```

---

## 6. Querying the KB (MCP server — preferred agent interface)

Start the MCP server, then your AI client can query the KB as tools:

```bash
python3 local-server/src/server.py --kb-path ~/my-kb
# or serve multiple KBs at once:
python3 local-server/src/server.py --kb-root ~/knowledge-bases
```

Available MCP tools:

| Tool | Purpose |
|------|---------|
| `kb_list` | List all available knowledge bases |
| `kb_list_docs` | List all documents in a KB with metadata |
| `kb_query` | Natural-language query with LLM synthesis |
| `kb_search` | Keyword search against the index |
| `kb_get_document` | Retrieve a specific wiki article |
| `kb_save_synthesis` | Save a generated synthesis back into the KB |

For quick CLI search without starting the server:

```bash
kb search "transformer attention mechanism"
kb search "RLHF" --limit 10
```

---

## 7. Programmatic API

```python
from builder.src.core.kbapi import KnowledgeBase

kb = KnowledgeBase("~/my-kb")

# Add a single file (full pipeline)
result = kb.add("paper.pdf")
print(result)  # {"status": "ok", "pipeline": "graphify-first", "stages": {...}}

# Add multiple files efficiently (stages run once at the end)
result = kb.add_batch(["paper1.pdf", "paper2.pdf", "notes.md"])

# Check KB state
status = kb.status()
# {"name": "My KB", "registry": {"total": 10, "ingested": 10, "compiled": 8, "graphed": 8}}
```

---

## 8. Auto-sync on session start

To automatically process new files whenever an agent session begins:

```bash
python3 builder/src/core/session_start.py --kb-path ~/my-kb
# or for all KBs under a root:
python3 builder/src/core/session_start.py --all   # requires $KB_ROOT env var
```

This detects changed files in `raw/`, runs the correct pipeline, and updates the graph
and exports. Safe to call repeatedly — it is incremental.

---

## 9. Directory structure

```
my-kb/
├── .kbaconfig              # KB config: name, pipeline rules, graphify_twice
├── .kbregistry.json        # tracks every document through all pipeline stages
├── raw/                    # drop source files here (never modified by the tool)
│   ├── articles/           # markdown, essays
│   ├── books/              # PDFs, EPUBs
│   └── papers/             # academic papers
└── wiki/                   # all compiled output
    ├── _index.md           # master index (start here in Obsidian)
    ├── _articles/          # one wiki article per source document
    ├── _concepts/          # auto-generated concept pages
    ├── _meta/              # file_index.json, embeddings.json (MCP server reads these)
    └── graphify-out/       # graph.json, edges.jsonl, graph.html
```

---

## 10. Common failure modes and fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `compile-llm` does nothing | No LLM API key set | `export DEEPSEEK_API_KEY=sk-...` |
| PDF text is garbled / empty | Scanned PDF, no OCR | `brew install tesseract poppler` |
| `kb` command not found | Not on PATH | `export PATH="$PATH:/path/to/ensemble-llm-wiki"` |
| `kb graphify` fails silently | graphify not installed | `pip install graphifyy` |
| `kb add` skips compile | graphify hasn't run yet | Run `kb graphify` first, or use `--skip-graphify-check` |
| Registry out of sync | Manual file edits | `kb clean` to remove stale entries |
| MCP server finds no documents | compile-llm not run | Run `kb compile-llm` before starting server |
| One PDF fails repeatedly | Corrupt / encrypted file | `kb skip broken.pdf` to exclude it permanently |
| Need to reprocess one file | Hash unchanged since last run | `kb ingest --file myfile.pdf` or `--retry-failed` |
