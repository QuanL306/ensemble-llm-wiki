#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Knowledge Base Builder - CLI Entry Point (English Version)
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.file_utils import ensure_dir, write_text, read_text, sanitize_filename
from core.ingest import DataIngest
from core.compiler import WikiCompiler
from core.indexer import IndexManager
from core.skill_seekers import SkillSeekersIntegration, SkillSeekersNotInstalledError, SkillSeekersFetchError


def load_config(kb_path: str) -> dict:
    """Load knowledge base configuration"""
    config_file = os.path.join(kb_path, ".kbaconfig")
    
    if os.path.exists(config_file):
        try:
            import yaml
            with open(config_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    
    return {"name": "My Knowledge Base", "version": "1.0", "path": kb_path}


def save_config(kb_path: str, config: dict):
    """Save knowledge base configuration"""
    config_file = os.path.join(kb_path, ".kbaconfig")
    try:
        import yaml
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    except Exception as e:
        print(f"Warning: Cannot save config: {e}")


def get_kb_path() -> str:
    """Get knowledge base path from current directory"""
    cwd = os.getcwd()
    
    while True:
        config_path = os.path.join(cwd, ".kbaconfig")
        if os.path.exists(config_path):
            return cwd
        
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    
    return None


def cmd_init(args):
    """Initialize knowledge base"""
    folder_path = os.path.abspath(args.folder_path)
    kb_name = args.name or os.path.basename(folder_path)
    
    print(f"🚀 Initializing Knowledge Base: {kb_name}")
    print(f"📁 Path: {folder_path}")
    
    dirs = [
        os.path.join(folder_path, "raw"),
        os.path.join(folder_path, "raw", "articles"),
        os.path.join(folder_path, "raw", "papers"),
        os.path.join(folder_path, "raw", "images"),
        os.path.join(folder_path, "raw", "web_clips"),
        os.path.join(folder_path, "wiki"),
        os.path.join(folder_path, "wiki", "_articles"),
        os.path.join(folder_path, "wiki", "_concepts"),
        os.path.join(folder_path, "wiki", "_topics"),
        os.path.join(folder_path, "wiki", "_meta"),
        os.path.join(folder_path, "outputs"),
    ]
    
    for dir_path in dirs:
        ensure_dir(dir_path)
        print(f"  📂 Created: {os.path.basename(dir_path)}/")
    
    config = {
        "name": kb_name,
        "version": "1.0",
        "path": folder_path,
        "ingest": {
            "supported_formats": [".pdf", ".epub", ".txt", ".md", ".markdown"],
            "ignore_patterns": ["node_modules/", ".git/", "__pycache__/", ".obsidian/"]
        }
    }
    
    save_config(folder_path, config)
    print(f"  ⚙️  Created: .kbaconfig")
    
    readme_content = f"""# {kb_name}

A knowledge base managed by Knowledge Base Builder Agent.

## Quick Start

1. Put documents into `raw/` directory (supports PDF, EPUB, Markdown, TXT)
2. Run `python src/cli.py ingest` to ingest documents
3. Run `python src/cli.py compile` to compile the wiki
4. Open this folder in Obsidian to explore

## Directory Structure

- `raw/` - Source documents
- `wiki/` - Compiled knowledge base (Obsidian compatible)
- `outputs/` - Query outputs

## Dependencies

```bash
pip install -r requirements.txt
```

## Documentation

See project documentation for more details.
"""
    
    write_text(os.path.join(folder_path, "README.md"), readme_content)
    print(f"  📝 Created: README.md")
    
    print(f"\n✅ Knowledge Base initialized!")
    print(f"\nNext steps:")
    print(f"  1. cd {folder_path}")
    print(f"  2. Put documents into raw/ directory")
    print(f"  3. Run: python src/cli.py ingest")
    print(f"  4. Run: python src/cli.py compile")


def cmd_ingest(args):
    """Ingest documents"""
    kb_path = get_kb_path()
    
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        print("   Please run: python src/cli.py init <folder_path>")
        return
    
    config = load_config(kb_path)
    print(f"📚 Knowledge Base: {config.get('name', 'Unknown')}")
    print(f"📁 Path: {kb_path}")
    
    ingest = DataIngest(kb_path, config)
    
    print("\n🔍 Scanning documents...")
    scan_result = ingest.scan(incremental=not args.full)
    
    print(f"  New: {len(scan_result['new'])}")
    print(f"  Changed: {len(scan_result['changed'])}")
    print(f"  Unchanged: {len(scan_result['unchanged'])}")
    
    if scan_result['unsupported']:
        print(f"  Unsupported: {len(scan_result['unsupported'])}")
    
    to_process = scan_result['new'] + scan_result['changed']
    
    if not to_process:
        print("\n✅ All documents are up to date")
        return
    
    print(f"\n🔄 Processing {len(to_process)} documents...")
    
    for i, file_path in enumerate(to_process, 1):
        print(f"\n  [{i}/{len(to_process)}] {os.path.basename(file_path)}")
        result = ingest.process_file(file_path)
        
        if result['success']:
            print(f"      ✅ Success")
            metadata = result.get('metadata', {})
            print(f"      📊 Words: {metadata.get('word_count', 0)}")
        else:
            print(f"      ❌ Failed: {result.get('error', 'Unknown error')}")
    
    print(f"\n✅ Ingest complete!")
    
    stats = ingest.get_stats()
    index_stats = stats['index']
    print(f"\n📈 Statistics:")
    print(f"  Total: {index_stats['total']}")
    print(f"  Completed: {index_stats['completed']}")
    print(f"  Pending: {index_stats['pending']}")


def cmd_compile(args):
    """Compile wiki"""
    kb_path = get_kb_path()
    
    if not kb_path:
        print("❌ Error: Knowledge base config not found")
        return
    
    config = load_config(kb_path)
    print(f"🔨 Compiling Knowledge Base: {config.get('name', 'Unknown')}")
    
    compiler = WikiCompiler(kb_path)
    
    print("\n🔄 Compiling...")
    results = compiler.compile_all()
    
    print(f"\n📊 Results:")
    print(f"  Summaries: {len(results['summaries'])}")
    print(f"  Concepts: {len(results['concepts_extracted'])}")
    print(f"  Articles: {len(results['concept_articles'])}")
    
    if results['errors']:
        print(f"  Errors: {len(results['errors'])}")
        for error in results['errors']:
            print(f"    - {error['file']}: {error['error']}")
    
    print(f"\n  Main Index: {results.get('main_index', 'N/A')}")
    
    print(f"\n✅ Compilation complete!")
    print(f"\n💡 Tip: Open {kb_path} in Obsidian to explore")


def cmd_fetch(args):
    """Fetch knowledge from any source via Skill Seekers and ingest into KB"""
    kb_path = get_kb_path()

    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        print("   Please run: python src/cli.py init <folder_path>")
        return

    config = load_config(kb_path)
    print(f"📚 Knowledge Base: {config.get('name', 'Unknown')}")
    print(f"🌐 Source: {args.source}")

    ss = SkillSeekersIntegration(kb_path)

    # ------------------------------------------------------------------
    # Step 1: Fetch via Skill Seekers
    # ------------------------------------------------------------------
    print("\n🔍 Step 1/3: Fetching with Skill Seekers...")

    try:
        extra = args.extra_args if args.extra_args else None
        result = ss.fetch(
            source=args.source,
            name=args.name,
            use_async=args.use_async,
            extra_args=extra,
        )
    except SkillSeekersNotInstalledError as e:
        print(f"\n❌ {e}")
        return
    except SkillSeekersFetchError as e:
        print(f"\n❌ Fetch failed: {e}")
        return

    files = result["files"]
    skill_md = result["skill_md"]
    dest_dir = result["dest_dir"]

    if not files:
        print("⚠️  No Markdown files were produced by Skill Seekers.")
        print(f"   Check the source and try again: {args.source}")
        return

    print(f"\n✅ Fetched {len(files)} file(s) → {os.path.relpath(dest_dir, kb_path)}/")
    for f in files:
        print(f"   📄 {os.path.basename(f)}")
    if skill_md:
        print(f"\n   ⭐ SKILL.md: {os.path.relpath(skill_md, kb_path)}")

    # ------------------------------------------------------------------
    # Step 2: Ingest (unless --no-ingest)
    # ------------------------------------------------------------------
    if not args.no_ingest:
        print("\n🔄 Step 2/3: Ingesting documents...")
        ingest = DataIngest(kb_path, config)
        scan_result = ingest.scan(incremental=True)
        to_process = scan_result["new"] + scan_result["changed"]

        if not to_process:
            print("   (no new documents to process)")
        else:
            for i, file_path in enumerate(to_process, 1):
                print(f"   [{i}/{len(to_process)}] {os.path.basename(file_path)}", end=" ")
                res = ingest.process_file(file_path)
                if res["success"]:
                    print("✅")
                else:
                    print(f"❌ {res.get('error', '')}")
    else:
        print("\n⏭️  Step 2/3: Ingest skipped (--no-ingest)")

    # ------------------------------------------------------------------
    # Step 3: Compile (only if --compile flag given)
    # ------------------------------------------------------------------
    if args.compile:
        print("\n🔨 Step 3/3: Compiling wiki...")
        compiler = WikiCompiler(kb_path)
        compile_results = compiler.compile_all()
        print(f"   Summaries:  {len(compile_results['summaries'])}")
        print(f"   Concepts:   {len(compile_results['concepts_extracted'])}")
        print(f"   Articles:   {len(compile_results['concept_articles'])}")
        if compile_results["errors"]:
            for err in compile_results["errors"]:
                print(f"   ⚠️  {err['file']}: {err['error']}")
    else:
        print("\n⏭️  Step 3/3: Compile skipped (pass --compile to run it now)")

    print(f"\n✅ Fetch complete!")
    print(f"\nNext step:  python src/cli.py compile")


def cmd_fetch_list(args):
    """List previously fetched skills"""
    kb_path = get_kb_path()

    if not kb_path:
        print("❌ Error: Knowledge base config not found")
        return

    ss = SkillSeekersIntegration(kb_path)
    fetched = ss.list_fetched()

    if not fetched:
        print("No skills fetched yet.")
        print("Use: python src/cli.py fetch <url>")
        return

    print(f"📦 Fetched skills ({len(fetched)}):\n")
    for item in fetched:
        print(f"  🗂  {item['slug']}")
        if "source" in item:
            print(f"      source:     {item['source']}")
        if "fetched_at" in item:
            print(f"      fetched at: {item['fetched_at']}")
        if "files" in item:
            print(f"      files:      {item['files']}")
        print()


def cmd_harvest(args):
    """Harvest AI session transcripts into the KB."""
    kb_path = get_kb_path()

    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        print("   Please run: python src/cli.py init <folder_path>")
        return

    from core.transcript_harvester import TranscriptHarvester

    harvester = TranscriptHarvester(kb_path)

    if args.list:
        sessions = harvester.list_harvested()
        if not sessions:
            print("No sessions harvested yet.")
            print("Use: python src/cli.py harvest")
            return
        print(f"📋 Harvested sessions ({len(sessions)}):\n")
        for s in sessions:
            print(f"  📄 {s['slug']}")
            print(f"      source:     {s.get('source', '?')}")
            print(f"      harvested:  {s.get('harvested_at', '?')}")
            print(f"      exchanges:  {s.get('message_count', '?')}")
            print()
        return

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    since_days = args.since

    print(f"📼 Harvesting transcripts from: {', '.join(sources)}")
    if since_days:
        print(f"   Filter: sessions from last {since_days} day(s)")

    result = harvester.harvest(sources=sources, since_days=since_days)

    print(f"\n✅ Harvest complete!")
    print(f"   New sessions:     {result['new']}")
    print(f"   Already ingested: {result['skipped']}")
    if result['errors']:
        print(f"   Errors:           {result['errors']}")

    if result['new'] > 0:
        print(f"\n💡 Next step: python src/cli.py ingest")
        print(f"   Transcripts written to: raw/transcripts/")


# ============================================================
# LLM-driven compilation
# ============================================================

# ---------- prompts -----------------------------------------

_PROMPT_DOCUMENT = """\
You are a knowledge curator building a personal research wiki in Obsidian.

PRIMARY READERS:
1. An AI assistant that will later search this wiki to answer research \
questions — it reads the Summary to decide relevance, then digs deeper.
2. The human researcher who built this KB and wants to reference and \
build on ideas across documents.

PRIMARY RULE: Be specific. Never say "this work explores X" without \
immediately stating what it specifically concludes about X.

Document: {name}  (~{word_count} words)

DOCUMENT TEXT:
{text}

---

Write the wiki article now using this exact structure:

```yaml
---
title: "{name_clean}"
type: book|paper|article|report|other
tags: [{tags}]
word_count: {word_count}
compiled: {date}
---
```

## Summary

One paragraph. Answer three things:
(1) What specific question or problem does this address?
(2) What is the author's specific answer — the actual claim, not a \
paraphrase of the topic?
(3) What makes this work's perspective distinctive compared to conventional \
wisdom on this topic?
If the source text was cut off or unclear in places, note that honestly here.

## Core Arguments

Number each major argument. For each one:
- **Claim**: what exactly is being asserted (be precise, not generic)
- **Evidence type**: experiment / case study / historical analysis / \
statistical data / logical argument / personal observation
- **Key caveat**: any limitation or counter-argument the author acknowledges

## Author's Terminology

Terms this author introduces or uses in a distinctive, non-standard way.
Each one wrapped in [[double brackets]] — these become navigable links.
Format: **[[term]]** — how the author defines or uses it specifically here.

## Evidence & Data

Concrete specifics only. Named experiments, exact statistics with their \
sources, specific dates, named people or organizations, particular case \
studies. Vague references ("studies show...") do not belong here.

## Key Quotes

2–4 verbatim quotes. Select lines that are maximally insight-dense — \
the ones where the author's thinking is sharpest.
Format: > "exact quote" — [chapter / page / section if available]

## Connections

How these ideas interact with other fields, debates, or works.
Be specific: not "this relates to psychology" but "this challenges \
[[cognitive bias]] research by arguing that..."
Use [[double brackets]] for every concept worth linking.

## What This Doesn't Cover

Honest gaps this article must flag:
- What the author explicitly sets out of scope
- Acknowledged weaknesses in the evidence or argument
- What a thoughtful critic would say is missing
- What follow-up work the author themselves calls for

## For Future Queries

Write exactly 15 retrieval hints — one sentence each. Every sentence must \
be specific to this document; never write a placeholder like "[topic]".
Use the author's actual vocabulary so keyword matching works well.

These hints will be stored in the search index and used verbatim to match \
incoming research queries, so precision matters.

Write 5 hints from each of these three angles:

**Conceptual** (what ideas / arguments / findings this work addresses):
"If someone asks about [specific concept], this document is relevant because [specific reason]."

**Methodological / evidence** (how the work argues, what data or cases it uses):
"If someone researching [specific method or evidence type], this document is relevant because [specific reason]."

**Application / implication** (what the work means for practice or related problems):
"If someone wants to [specific action or application], this document is relevant because [specific reason]."

Number the 15 hints 1–15. No category headers in the output — just the 15 numbered sentences.
"""

_PROMPT_INDEX = """\
You are writing the master navigation file for a personal research knowledge \
base. An AI assistant reads this index FIRST whenever it needs to answer a \
research question — it must be able to determine in one scan which documents \
are relevant and where to look.

Current articles (frontmatter + summary + retrieval hints):

{summaries}

Write _index.md with this structure:

## About This Knowledge Base
2–3 sentences: what territory does this KB cover? What are its primary \
questions? What would be a poor use of this KB?

## Topic Map
Group documents into 3–7 thematic clusters. For each cluster:
### [Theme Name]
- [[Document Title]] — one sentence: what specific claim or data does it add?

## Core Concepts
The [[concepts]] that appear across multiple documents. These are the \
intellectual backbone — entry points for navigating the KB.
List each with a one-line role: what role does this concept play in the KB?

## Synthesis Questions
5 questions that require reading multiple documents together to answer. \
Ground each question in actual document titles and concepts:
Example: "How does [[System 1]] thinking (Kahneman) interact with \
[[choice architecture]] (Thaler) in practice?"

## All Documents
Alphabetical quick-lookup. One line per document:
- [[Title]] — type, one-sentence description, top 2 tags
"""

_PROMPT_CONCEPTS = """\
You are writing concept pages for a personal research knowledge base.
Below are the most cross-referenced concepts, each shown with actual \
excerpts from the articles that use it.

{links_data}

Select the top {n} concepts that are genuinely central \
(skip generic terms like "research" or "analysis"). For each, write a \
concept article that synthesises how the term is actually used across \
these specific documents — grounded in the excerpts, not in \
generic knowledge.

Format for each concept article:

---
title: <concept name>
type: concept
source_count: <N>
---

# <concept name>

## Definition
2–3 sentences defining the term exactly as it is used across these \
documents. Ground the definition in the excerpts — do not use a generic \
dictionary definition.

## How Different Sources Use It
One paragraph showing convergence or tension: do all documents use \
the term the same way, or do they differ? Quote or paraphrase specific \
documents by name.

## Source Documents
- [[Article name]] — one sentence on how this document uses the concept

## Related Concepts
- [[concept]] — one-line relationship

Separate each concept article with exactly this line:
---CONCEPT_BREAK---
"""


# ---------- helpers -----------------------------------------

def _llm_client(model_override: str = None):
    """Return (client, provider, model, config) using the unified LLM layer."""
    from utils.llm_client import get_llm_config, create_client
    config = get_llm_config()
    if model_override:
        config["model"] = model_override
    client, provider = create_client(config)
    return client, provider, config["model"], config


def _stream_message(client, provider, model, prompt, max_tokens=4096):
    """Call the API with streaming; print dots as tokens arrive; return full text."""
    from utils.llm_client import stream_message
    return stream_message(client, provider, model, prompt, max_tokens)


def _estimate_cost(files: list, model: str) -> float:
    """Rough cost estimate in USD based on word count."""
    # Input pricing (per 1M tokens)
    if "moonshot" in model or "kimi" in model:
        price_per_m = 1.65       # Kimi moonshot-v1-8k: ~$1.65/M
    elif "opus" in model:
        price_per_m = 15.0
    elif "haiku" in model:
        price_per_m = 0.80
    else:
        price_per_m = 3.0
    total_words = sum(
        f.get("extracted_metadata", {}).get("word_count", 5_000) for f in files
    )
    # ~1.3 tokens/word input + ~1K tokens output per doc
    input_tokens = total_words * 1.3
    output_tokens = len(files) * 1_000
    return (input_tokens + output_tokens) / 1_000_000 * price_per_m


def _clean_extracted_text(raw: str) -> str:
    """
    Strip the heuristic header from _extracted.txt and return only the real
    document text (the '## Structured Text' section).

    The header sections — 'Core Claims', 'Key Data', 'Notable Quotes' — are
    produced by simple regex matching and are too noisy to send to the LLM.
    The actual text is always under '## Structured Text'.
    """
    marker = "\n## Structured Text\n"
    idx = raw.find(marker)
    if idx == -1:
        # File is raw markdown or plain text with no structured header — use as-is
        return raw
    return raw[idx + len(marker):]


def _split_at_boundaries(text: str, boundaries: list) -> list:
    """
    Given a list of character-position split points in *text*, return a list of
    {"title", "preview", "word_count"} dicts — one per section.

    Sections shorter than 150 words are skipped.
    Sections longer than 1200 words are sub-chunked at every 600 words.
    """
    CHUNK_MIN  = 150
    CHUNK_SIZE = 600

    all_bounds = boundaries + [len(text)]
    chunks = []

    for i in range(len(boundaries)):
        section = text[boundaries[i]: all_bounds[i + 1]].strip()
        words   = section.split()
        if len(words) < CHUNK_MIN:
            continue

        # First non-trivial line becomes the title
        title = ""
        for line in section.split('\n'):
            line = line.strip().strip("=# -\t")
            if len(line) > 3:
                title = line[:80]
                break
        title = title or f"Section {i + 1}"

        if len(words) <= CHUNK_SIZE * 2:
            chunks.append({
                "title":      title,
                "preview":    " ".join(words[:200]),
                "word_count": len(words),
            })
        else:
            for j in range(0, len(words), CHUNK_SIZE - 50):
                sub = words[j: j + CHUNK_SIZE]
                if len(sub) < CHUNK_MIN:
                    break
                sub_n = j // (CHUNK_SIZE - 50) + 1
                chunks.append({
                    "title":      f"{title} ({sub_n})",
                    "preview":    " ".join(sub[:200]),
                    "word_count": len(sub),
                })

    return chunks


def _chunk_document(text: str) -> list:
    """
    Split a long document into retrievable sections.

    Returns a list of {"id", "title", "preview", "word_count"} dicts.
    Returns [] for documents under 5 000 words — they're short enough that
    document-level retrieval_queries already cover them well.

    Strategy (tried in order):
      1. EPUB chapter markers  (=== Chapter N ===)
      2. Chapter / Part lines  (Chapter 7: ..., PART III)
      3. Markdown h2/h3 headers
      4. 600-word sliding windows (fallback)
    """
    words = text.split()
    if len(words) < 5_000:
        return []

    SPLIT_PATTERNS = [
        re.compile(r'\n=== .{3,100} ===\n'),
        re.compile(r'\n(?:CHAPTER|Chapter)\s+[\w]+[:\s]*[^\n]{0,60}\n'),
        re.compile(r'\n(?:PART|Part)\s+[\w]+[:\s]*[^\n]{0,60}\n'),
        re.compile(r'\n#{2,3} .{3,80}\n'),
    ]

    for pattern in SPLIT_PATTERNS:
        boundaries = [m.start() for m in pattern.finditer(text)]
        if len(boundaries) >= 3:
            chunks = _split_at_boundaries(text, boundaries)
            if len(chunks) >= 3:
                return [{"id": f"c{i+1:03d}", **c} for i, c in enumerate(chunks)]

    # Fallback: 600-word windows, 50-word overlap
    step = 550
    chunks = []
    for i, start in enumerate(range(0, len(words), step)):
        sub = words[start: start + 600]
        if len(sub) < 150:
            break
        chunks.append({
            "id":         f"c{i+1:03d}",
            "title":      f"Section {i + 1}",
            "preview":    " ".join(sub[:200]),
            "word_count": len(sub),
        })
    return chunks


def _extract_retrieval_queries(article_text: str) -> list:
    """
    Pull the 'For Future Queries' section from a compiled wiki article and
    return a clean list of query strings.

    These are stored in file_index.json under 'retrieval_queries' so the
    MCP servers can use them for semantic-style scoring without reading
    every article file on every query.
    """
    m = re.search(r'## For Future Queries\n(.*?)(?=\n## |\Z)', article_text, re.DOTALL)
    if not m:
        return []

    section = m.group(1).strip()
    queries = []
    for line in section.split('\n'):
        line = line.strip()
        if not line:
            continue
        # Strip leading number + period/dot  ("1. ", "15. ")
        line = re.sub(r'^\d+\.\s*', '', line)
        # Strip surrounding typographic or ASCII quotes
        line = line.strip('\'""\u201c\u201d\u2018\u2019')
        line = line.strip()
        if len(line) > 15:   # skip accidental noise lines
            queries.append(line)
    return queries


def _extract_article_brief(article_text: str, stem: str) -> str:
    """
    Extract a structured brief from an LLM-compiled wiki article for use
    in index generation. Pulls: frontmatter, Summary, For Future Queries.
    """
    parts = [f"### [[{stem}]]"]

    # YAML frontmatter
    fm = re.match(r'^```yaml\n(.*?)\n```', article_text, re.DOTALL) \
      or re.match(r'^---\n(.*?)\n---', article_text, re.DOTALL)
    if fm:
        parts.append(fm.group(0))

    # Summary section
    m = re.search(r'## Summary\n(.*?)(?=\n## |\Z)', article_text, re.DOTALL)
    if m:
        parts.append(f"**Summary**: {m.group(1).strip()[:500]}")

    # For Future Queries — tells the index what this document answers
    m = re.search(r'## For Future Queries\n(.*?)(?=\n## |\Z)', article_text, re.DOTALL)
    if m:
        parts.append(f"**Retrieval hints**:\n{m.group(1).strip()[:400]}")

    return "\n\n".join(parts)


def _parse_llm_article(article_text: str) -> dict:
    """
    Parse a compiled LLM wiki article into structured metadata for the index.

    Extracts sections (Summary, Core Arguments, Evidence, Quotes,
    Connections, Terminology, Gaps) and [[links]] from Terminology and
    Connections sections.  Returns a dict suitable for storing under
    ``file_info["llm_metadata"]`` in the index.
    """
    link_pat = re.compile(r'\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]')

    def _section(name: str) -> str:
        m = re.search(
            r'## ' + re.escape(name) + r'\n(.*?)(?=\n## |\Z)',
            article_text, re.DOTALL,
        )
        return m.group(1).strip() if m else ""

    summary = _section("Summary")[:600]
    core_arguments = _section("Core Arguments")[:1000]

    evidence_raw = _section("Evidence & Data")
    evidence = [l.lstrip("- •").strip() for l in evidence_raw.split("\n") if l.strip()]
    evidence_text = " ".join(evidence)[:500]

    quotes_raw = _section("Key Quotes")
    quotes = [l.lstrip("> ").strip() for l in quotes_raw.split("\n") if l.strip()]

    connections_raw = _section("Connections")
    connection_links = list(set(link_pat.findall(connections_raw)))

    terminology_raw = _section("Author's Terminology")
    terminology = list(set(link_pat.findall(terminology_raw)))

    gaps_raw = _section("What This Doesn't Cover")
    gaps = [l.lstrip("- •").strip() for l in gaps_raw.split("\n") if l.strip()]

    # Pre-computed search body: summary + core_arguments + evidence
    llm_body_search = (summary + " " + core_arguments + " " + evidence_text)[:2200]

    return {
        "summary": summary,
        "core_arguments": core_arguments,
        "evidence": evidence[:20],
        "key_quotes": quotes[:10],
        "terminology": terminology,
        "connections": connection_links,
        "gaps": gaps[:10],
        "llm_body_search": llm_body_search,
    }


def _generate_embeddings(kb_path: str, indexer: 'IndexManager') -> int:
    """
    Generate or update embedding vectors for all compiled documents.

    Uses fastembed (BAAI/bge-small-en-v1.5) to produce one 384-dim vector
    per document.  Stores results in ``wiki/_meta/embeddings.json``.

    Returns the number of vectors generated.
    """
    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("   ⚠️  fastembed not installed — skipping embeddings")
        print("      Install with: pip install fastembed")
        return 0

    from core.scoring import build_doc_embed_text, EMBED_TEXT_VERSION

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    all_files = indexer.index.get("files", {})
    meta_dir = os.path.join(kb_path, "wiki", "_meta")
    embeddings_file = os.path.join(meta_dir, "embeddings.json")

    # Load existing embeddings (if any)
    existing = {}
    existing_created_at = ""
    existing_version = 0
    if os.path.exists(embeddings_file):
        try:
            existing_data = json.loads(read_text(embeddings_file))
            existing = existing_data.get("vectors", {})
            existing_created_at = existing_data.get("created_at", "")
            existing_version = existing_data.get("embed_text_version", 0)
        except json.JSONDecodeError as e:
            print(f"\n   ⚠️  embeddings.json is corrupt ({e}); regenerating all vectors")
        except Exception as e:
            print(f"\n   ⚠️  Could not load embeddings.json ({e}); regenerating all vectors")

    # Force regeneration if embedding text format changed
    if existing_version < EMBED_TEXT_VERSION:
        print(f"   Embedding text format updated (v{existing_version} → v{EMBED_TEXT_VERSION}); regenerating all")
        existing = {}
        existing_created_at = ""   # force re-embed of every doc

    vectors: dict = dict(existing)
    texts_to_embed: list = []   # (file_id, text)
    for fid, finfo in all_files.items():
        if not finfo.get("llm_compiled_at"):
            continue
        embed_text = build_doc_embed_text(finfo)
        if not embed_text.strip():
            continue
        # Re-embed if missing, or if doc was recompiled after embeddings were created
        compiled_at = finfo.get("llm_compiled_at", "")
        if fid in existing and compiled_at and existing_created_at:
            if compiled_at <= existing_created_at:
                continue
        elif fid in existing:
            continue
        texts_to_embed.append((fid, embed_text))

    if not texts_to_embed:
        if existing:
            print(f"   Embeddings up to date ({len(existing)} vectors)")
        return len(existing)

    print(f"   Generating embeddings for {len(texts_to_embed)} document(s)...", end="", flush=True)

    # Batch embed for efficiency
    try:
        batch_texts = [text for _, text in texts_to_embed]
        batch_vecs = list(model.embed(batch_texts))
        for i, (fid, _) in enumerate(texts_to_embed):
            vectors[fid] = batch_vecs[i].tolist()
    except Exception as e:
        # Fallback: embed one at a time
        print(f"\n   ⚠️  Batch embed failed ({e}); trying one-by-one...", flush=True)
        for fid, text in texts_to_embed:
            try:
                vecs = list(model.embed([text]))
                vectors[fid] = vecs[0].tolist()
            except Exception as ex:
                print(f"\n   ⚠️  Embedding failed for {fid}: {ex}")

    # Save
    embeddings_data = {
        "model": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "embed_text_version": EMBED_TEXT_VERSION,
        "created_at": datetime.now().isoformat(),
        "vectors": vectors,
    }
    ensure_dir(meta_dir)
    write_text(embeddings_file, json.dumps(embeddings_data, indent=2))

    print(f" ✅  {len(vectors)} vectors → embeddings.json")
    return len(vectors)


def _extract_wiki_links(articles_dir: str) -> dict:
    """
    Scan all wiki articles for [[concept]] links.
    Returns {concept: {"count": N, "sources": [{"article": name, "excerpt": "..."}]}}
    sorted by count descending.
    Each concept gets one excerpt per source article (not just one global example),
    so the concept prompt can show how different documents use the same term.
    """
    pattern = re.compile(r'\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]')
    result: dict = {}

    for fname in sorted(os.listdir(articles_dir)):
        if not fname.endswith(".md"):
            continue
        article_name = fname[:-3]
        text = read_text(os.path.join(articles_dir, fname))

        seen_in_this_file: set = set()
        for match in pattern.finditer(text):
            concept = match.group(1).strip()
            if not concept:
                continue

            if concept not in result:
                result[concept] = {"count": 0, "sources": []}
            result[concept]["count"] += 1

            # One excerpt per source article
            if concept not in seen_in_this_file:
                seen_in_this_file.add(concept)
                start = max(0, match.start() - 120)
                end = min(len(text), match.end() + 120)
                excerpt = text[start:end].replace("\n", " ").strip()
                result[concept]["sources"].append({
                    "article": article_name,
                    "excerpt": excerpt
                })

    return dict(sorted(result.items(), key=lambda x: -x[1]["count"]))


# ---------- LLM compilation steps --------------------------

def _compile_with_retry(client, provider, model: str, file_info: dict, kb_path: str,
                        max_retries: int = 3) -> Tuple[Optional[str], Optional[str]]:
    """
    Call _compile_document with exponential backoff for transient API errors.

    Returns (article_text, error_message).
    On success: (text, None).
    On permanent failure: (None, error_string).

    Retried errors:
      - RateLimitError       → wait 30 / 60 / 120 s
      - APIConnectionError   → wait 10 / 20 / 40 s
      - APITimeoutError      → wait 10 / 20 / 40 s
      - 5xx APIStatusError   → wait 15 / 30 / 60 s

    Not retried (4xx client errors, bad input) → returns (None, message).
    """
    import time
    from utils.llm_client import get_retry_exceptions

    rate_exc, conn_excs, status_exc = get_retry_exceptions(provider)
    last_error = ""

    for attempt in range(max_retries):
        try:
            result = _compile_document(client, provider, model, file_info, kb_path)
            return result, None

        except rate_exc as exc:
            wait = 30 * (2 ** attempt)          # 30 / 60 / 120 s
            last_error = str(exc)
            print(f"\n   ⏳ Rate limit — waiting {wait}s "
                  f"(attempt {attempt + 1}/{max_retries})...", flush=True)
            time.sleep(wait)

        except conn_excs as exc:
            wait = 10 * (2 ** attempt)          # 10 / 20 / 40 s
            last_error = str(exc)
            print(f"\n   ⏳ Connection/timeout — retrying in {wait}s "
                  f"(attempt {attempt + 1}/{max_retries})...", flush=True)
            time.sleep(wait)

        except status_exc as exc:
            code = getattr(exc, 'status_code', getattr(exc, 'http_status', 500))
            if code >= 500:
                wait = 15 * (2 ** attempt)      # 15 / 30 / 60 s
                last_error = str(exc)
                print(f"\n   ⏳ Server error {code} — retrying in {wait}s "
                      f"(attempt {attempt + 1}/{max_retries})...", flush=True)
                time.sleep(wait)
            else:
                msg = getattr(exc, 'message', str(exc))
                return None, f"API {code}: {msg}"

        except Exception as exc:                # unexpected — don't retry
            return None, str(exc)

    return None, f"Failed after {max_retries} retries: {last_error}"


def _compile_document(client, provider, model: str, file_info: dict, kb_path: str) -> Optional[str]:
    """Call LLM to write a wiki article for one document. Returns article text."""
    wiki_path = file_info.get("wiki_path", "")
    if not wiki_path:
        return None

    full_path = os.path.join(kb_path, wiki_path)
    if not os.path.exists(full_path):
        return None

    raw = read_text(full_path)

    # Strip the noisy heuristic header; send only the real document text
    text = _clean_extracted_text(raw)

    meta = file_info.get("extracted_metadata", {})
    name = file_info.get("name", "Unknown")
    name_clean = re.sub(r'\.[^.]+$', '', name)   # strip extension for YAML title

    # Fit within context budget (~37K tokens at 150K chars; leaves room for output)
    MAX_CHARS = 150_000
    original_word_count = meta.get("word_count", "unknown")
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = (
            text[:120_000]
            + "\n\n[NOTE: source text truncated — middle section omitted. "
            + f"Showing first ~{120_000//6} and last ~{20_000//6} words of "
            + f"~{original_word_count} total.]\n\n"
            + text[-20_000:]
        )

    # Use the actual word count of what we send, not the original full count
    word_count = len(text.split()) if truncated else original_word_count

    # Suggest tags from the filename for the prompt (Claude will refine them)
    stem_words = re.sub(r'[_\-.]', ' ', name_clean).lower().split()
    tag_hint = ", ".join(w for w in stem_words[:4] if len(w) > 3)

    prompt = _PROMPT_DOCUMENT.format(
        name=name,
        name_clean=name_clean,
        word_count=word_count,
        tags=tag_hint,
        date=datetime.now().strftime("%Y-%m-%d"),
        text=text,
    )
    return _stream_message(client, provider, model, prompt, max_tokens=6000)


def _compile_index(client, provider, model: str, articles_dir: str) -> str:
    """Call LLM to generate _index.md from structured briefs of all articles."""
    summaries = []
    for fname in sorted(os.listdir(articles_dir)):
        if not fname.endswith(".md"):
            continue
        text = read_text(os.path.join(articles_dir, fname))
        brief = _extract_article_brief(text, fname[:-3])
        summaries.append(brief)

    if not summaries:
        return ""

    prompt = _PROMPT_INDEX.format(summaries="\n\n---\n\n".join(summaries))
    return _stream_message(client, provider, model, prompt, max_tokens=4096)


def _compile_concepts(client, provider, model: str, articles_dir: str, n: int = 20) -> list:
    """
    Call LLM to write concept articles for the top-N cross-referenced concepts.
    Returns list of (concept_name, article_markdown).
    Passes per-source excerpts so Claude can see how each document uses the concept.
    """
    links = _extract_wiki_links(articles_dir)

    # Only concepts referenced across 2+ distinct articles
    multi_ref = {k: v for k, v in links.items() if v["count"] >= 2}
    if not multi_ref:
        return []

    top = list(multi_ref.items())[:n]

    # Build rich input: for each concept, list actual excerpts from each source
    sections = []
    for concept, data in top:
        lines = [f"### [[{concept}]] — referenced in {data['count']} article(s)"]
        for src in data["sources"][:5]:   # cap at 5 excerpts per concept
            excerpt = src["excerpt"].replace("|", "/")[:200]
            lines.append(f"- **{src['article']}**: ...{excerpt}...")
        sections.append("\n".join(lines))

    prompt = _PROMPT_CONCEPTS.format(
        links_data="\n\n".join(sections),
        n=min(n, len(top)),
    )
    raw = _stream_message(client, provider, model, prompt, max_tokens=8192)

    # Split on the separator the LLM was told to use
    parts = raw.split("---CONCEPT_BREAK---")
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Derive concept name from first H1
        m = re.search(r'^#\s+(.+)$', part, re.MULTILINE)
        name = m.group(1).strip() if m else f"concept_{len(results)+1}"
        results.append((name, part))
    return results


# ---------- main command ------------------------------------

def cmd_compile_llm(args):
    """
    LLM-driven wiki compilation.

    Three optional steps (all run by default):
      --docs      Write a wiki article for each ingested document
      --index     Regenerate _index.md from all articles
      --concepts  Write concept pages for the most-referenced [[links]]

    Pass exactly one flag to run only that step. Without any flag, all three run.
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌  Knowledge base not found (.kbaconfig). Run 'init' first.")
        return

    # ── Lock file to prevent concurrent compile ────────────────────────
    lock_file = os.path.join(kb_path, "wiki", "_meta", ".compile.lock")
    if os.path.exists(lock_file):
        try:
            pid = int(read_text(lock_file).strip())
            os.kill(pid, 0)   # raises if PID doesn't exist
            print(f"❌  Another compile-llm is running (PID {pid}).")
            print(f"   If stale, delete: {lock_file}")
            return
        except (ValueError, ProcessLookupError, PermissionError):
            os.unlink(lock_file)  # stale lock
        except Exception:
            pass

    ensure_dir(os.path.dirname(lock_file))
    write_text(lock_file, str(os.getpid()))
    try:
        _cmd_compile_llm_inner(args, kb_path)
    finally:
        try:
            os.unlink(lock_file)
        except OSError:
            pass


def _cmd_compile_llm_inner(args, kb_path: str):
    """Body of cmd_compile_llm, extracted for lock-file wrapping."""
    config = load_config(kb_path)
    articles_dir = os.path.join(kb_path, "wiki", "_articles")
    concepts_dir = os.path.join(kb_path, "wiki", "_concepts")
    ensure_dir(articles_dir)
    ensure_dir(concepts_dir)

    # Determine which steps to run
    explicit = args.docs or args.index or args.concepts
    run_docs     = args.docs     or not explicit
    run_index    = args.index    or not explicit
    run_concepts = args.concepts or not explicit

    client, provider, model, llm_config = _llm_client(args.model)

    # Auxiliary model for simpler tasks (index, concepts)
    _MODEL_AUX = llm_config["aux_model"]

    from utils.llm_client import get_model_display
    print(f"📚  {config.get('name', 'Knowledge Base')}")
    print(f"🤖  Model : {get_model_display()}")
    print(f"📋  Steps : {'docs ' if run_docs else ''}{'index ' if run_index else ''}{'concepts' if run_concepts else ''}")

    # ── Step 1: compile documents ──────────────────────────────────────────
    if run_docs:
        indexer = IndexManager(kb_path)
        completed = indexer.get_completed_files()

        if not completed:
            print("\n⚠️   No completed documents found. Run 'ingest' first.")
            run_docs = False
        else:
            retry_failed = getattr(args, 'retry_failed', False)

            if args.full:
                pending = completed
                # Clear all failure records when doing a full recompile
                for f in completed:
                    fid = indexer.generate_file_id(f["path"])
                    indexer.index["files"][fid].pop("compile_failed_at", None)
                    indexer.index["files"][fid].pop("compile_error", None)
                indexer.save_index()
            elif retry_failed:
                # Retry previously failed docs + still-uncompiled docs
                pending = [
                    f for f in completed
                    if not f.get("llm_compiled_at") or f.get("compile_failed_at")
                ]
                # Clear failure records so they get a fresh attempt
                for f in pending:
                    fid = indexer.generate_file_id(f["path"])
                    indexer.index["files"][fid].pop("compile_failed_at", None)
                    indexer.index["files"][fid].pop("compile_error", None)
                indexer.save_index()
            else:
                # Normal run: skip compiled docs AND previously failed docs
                pending = [
                    f for f in completed
                    if not f.get("llm_compiled_at")
                    and not f.get("compile_failed_at")
                ]

            n_failed_prev = sum(
                1 for f in completed if f.get("compile_failed_at")
                and not f.get("llm_compiled_at")
            )

            print(f"\n── Documents ──────────────────────────────────────────")
            print(f"   Total completed : {len(completed)}")
            print(f"   To compile      : {len(pending)}")
            if n_failed_prev and not retry_failed and not args.full:
                print(f"   Previously failed (skipped): {n_failed_prev}"
                      f"  — run --retry-failed to retry")

            if pending:
                est = _estimate_cost(pending, model)
                print(f"   Est. cost       : ~${est:.2f}")

                if not args.yes:
                    if input("\n   Proceed? [y/N] ").strip().lower() != "y":
                        print("   Cancelled.")
                        return

                ok_count = 0
                failed_count = 0
                interrupted = False

                try:
                    for i, file_info in enumerate(pending, 1):
                        name = file_info.get("name", "?")
                        print(f"\n   [{i}/{len(pending)}] {name}")
                        print("   ", end="", flush=True)

                        file_id = indexer.generate_file_id(file_info["path"])

                        article, error = _compile_with_retry(
                            client, provider, model, file_info, kb_path
                        )

                        if error:
                            print(f"\n   ❌ {error[:120]}")
                            indexer.index["files"][file_id]["compile_failed_at"] = \
                                datetime.now().isoformat()
                            indexer.index["files"][file_id]["compile_error"] = \
                                error[:200]
                            indexer.save_index()
                            failed_count += 1
                            continue

                        if not article:
                            print("⚠️   No extracted text, skipping")
                            continue

                        stem = os.path.splitext(name)[0]
                        out_path = os.path.join(
                            articles_dir, f"{sanitize_filename(stem)}.md"
                        )
                        write_text(out_path, article)

                        # Extract and store retrieval queries
                        retrieval_queries = _extract_retrieval_queries(article)

                        # Parse LLM article structure into index metadata
                        llm_meta = _parse_llm_article(article)

                        # Chunk long documents so chapter-level retrieval works
                        wiki_path = file_info.get("wiki_path", "")
                        chunks: list = []
                        if wiki_path:
                            extracted_path = os.path.join(kb_path, wiki_path)
                            if os.path.exists(extracted_path):
                                raw = read_text(extracted_path)
                                doc_text = _clean_extracted_text(raw)
                                chunks = _chunk_document(doc_text)

                        indexer.index["files"][file_id]["llm_compiled_at"] = \
                            datetime.now().isoformat()
                        indexer.index["files"][file_id]["retrieval_queries"] = \
                            retrieval_queries
                        if llm_meta:
                            indexer.index["files"][file_id]["llm_metadata"] = llm_meta
                        # Clear any previous failure record now that it succeeded
                        indexer.index["files"][file_id].pop("compile_failed_at", None)
                        indexer.index["files"][file_id].pop("compile_error", None)
                        if chunks:
                            indexer.index["files"][file_id]["chunks"] = chunks
                        indexer.save_index()

                        n_q = len(retrieval_queries)
                        n_c = len(chunks)
                        chunk_note = f", {n_c} chunks" if n_c else ""
                        print(f"   ✅  {os.path.basename(out_path)}"
                              f"  ({n_q} queries{chunk_note})")
                        ok_count += 1

                except KeyboardInterrupt:
                    interrupted = True
                    print(f"\n\n⚠️  Interrupted after {ok_count} doc(s).")
                    print(f"   Completed docs are saved — run again to continue.")
                    run_index    = False   # don't auto-run index on partial run
                    run_concepts = False

                if not interrupted and (ok_count > 0 or failed_count > 0):
                    print(f"\n   Docs: {ok_count} compiled"
                          + (f", {failed_count} failed"
                             f" (run --retry-failed to retry)" if failed_count else ""))

    # ── Auto-enable index if docs were compiled and --no-index was not set ──
    if run_docs and not run_index and not getattr(args, 'no_index', False):
        run_index = True
        print(f"\n   (auto-running --index to keep _index.md current; pass --no-index to skip)")

    # ── Step 2: regenerate index ───────────────────────────────────────────
    if run_index:
        print(f"\n── Index ───────────────────────────────────────────────")
        print("   Writing _index.md ", end="", flush=True)

        index_text = _compile_index(client, provider, _MODEL_AUX, articles_dir)
        if index_text:
            write_text(os.path.join(kb_path, "wiki", "_index.md"), index_text)
            print("   ✅  _index.md")
        else:
            print("   ⚠️   No articles found to index")

    # ── Step 3: concept pages ──────────────────────────────────────────────
    if run_concepts:
        print(f"\n── Concepts ────────────────────────────────────────────")
        links = _extract_wiki_links(articles_dir)
        multi = {k: v for k, v in links.items() if v["count"] >= 2}
        print(f"   [[links]] referenced in 2+ articles: {len(multi)}")

        if not multi:
            print("   ⚠️   No cross-referenced concepts yet. Compile more documents first.")
        else:
            n = min(args.concept_limit, len(multi))
            print(f"   Generating top {n} concept pages ", end="", flush=True)

            concept_articles = _compile_concepts(client, provider, _MODEL_AUX, articles_dir, n=n)
            for concept_name, article_text in concept_articles:
                fname = f"{sanitize_filename(concept_name)}.md"
                write_text(os.path.join(concepts_dir, fname), article_text)

            print(f"   ✅  {len(concept_articles)} concept page(s) → wiki/_concepts/")

        # Merge LLM-discovered concepts into concepts.json
        try:
            from core.indexer import ConceptIndex
            ci = ConceptIndex(kb_path)
            n_merged = ci.merge_llm_concepts(links, articles_dir)
            ci.save()
            if n_merged:
                print(f"   Merged {n_merged} LLM concepts into concepts.json")
        except Exception as e:
            print(f"   ⚠️  Concept merge failed: {e}")

    # ── Step 4: generate embeddings (only when docs were compiled) ─────────
    if run_docs:
        print(f"\n── Embeddings ──────────────────────────────────────────")
        try:
            _generate_embeddings(kb_path, indexer)
        except Exception as e:
            print(f"   ⚠️  Embedding generation failed: {e}")

    print(f"\n✅  Done — open {kb_path} in Obsidian to explore")


def cmd_deploy(args):
    """
    Sync the compiled wiki to a remote cloud server.

    Uses rsync over SSH. The remote path is:
      <user>@<host>:/data/knowledge-bases/<remote_user_id>/<kb_id>/wiki/

    Environment variables (override CLI flags):
      KBA_DEPLOY_HOST       Remote host
      KBA_DEPLOY_USER       SSH user
      KBA_DEPLOY_KEY        Path to SSH private key
      KBA_DEPLOY_REMOTE_USER  Remote user_id (determines folder on server)
      KBA_DEPLOY_KB_ID      Knowledge base ID on server
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        return

    config = load_config(kb_path)
    wiki_dir = os.path.join(kb_path, "wiki")

    if not os.path.isdir(wiki_dir):
        print("❌ Error: wiki/ directory not found. Run 'compile' first.")
        return

    # Resolve config from args → env → error
    host       = args.host       or os.getenv("KBA_DEPLOY_HOST")
    ssh_user   = args.ssh_user   or os.getenv("KBA_DEPLOY_USER", "root")
    key_path   = args.key        or os.getenv("KBA_DEPLOY_KEY")
    remote_uid = args.remote_user or os.getenv("KBA_DEPLOY_REMOTE_USER")
    kb_id      = args.kb_id      or os.getenv("KBA_DEPLOY_KB_ID",
                                               os.path.basename(kb_path))

    if not host:
        print("❌ Error: --host is required (or set KBA_DEPLOY_HOST)")
        return
    if not remote_uid:
        print("❌ Error: --remote-user is required (or set KBA_DEPLOY_REMOTE_USER)")
        print("   This is the user_id folder on the server, e.g. 'alice' or 'user-123'")
        return

    dry_run = getattr(args, 'dry_run', False)
    quiet   = getattr(args, 'quiet', False)
    force   = getattr(args, 'force', False)

    remote_path = f"{ssh_user}@{host}:/data/knowledge-bases/{remote_uid}/{kb_id}/"
    print(f"📚 Knowledge Base : {config.get('name', 'Unknown')}")
    print(f"📂 Local wiki/    : {wiki_dir}")
    print(f"🌐 Remote         : {remote_path}")
    if dry_run:
        print("🔍 DRY RUN — no files will be transferred")

    import subprocess

    # ── Build rsync flags ─────────────────────────────────────────────────
    # -a  archive (preserves permissions, timestamps, symlinks)
    # -z  compress during transfer
    # --delete  remove remote files that no longer exist locally
    rsync_flags = "-az" if quiet else "-avz"
    rsync_cmd = ["rsync", rsync_flags, "--delete"]
    if dry_run:
        rsync_cmd.append("--dry-run")
    if key_path:
        rsync_cmd += ["-e", f"ssh -i {key_path}"]

    # ── Ensure remote directory exists (skip for dry-run) ─────────────────
    if not dry_run:
        ssh_cmd = ["ssh"]
        if key_path:
            ssh_cmd += ["-i", key_path]
        ssh_cmd += [
            f"{ssh_user}@{host}",
            f"mkdir -p /data/knowledge-bases/{remote_uid}/{kb_id}/wiki"
        ]
        print("\n🔧 Creating remote directory...")
        r = subprocess.run(ssh_cmd, capture_output=quiet)
        if r.returncode != 0:
            print("❌ Failed to create remote directory. Check SSH access.")
            if quiet and r.stderr:
                print(r.stderr.decode(errors='replace'))
            return

    # ── Conflict detection ────────────────────────────────────────────────────
    # Check whether the remote _index.md is newer than the local one.
    # If so, the remote may have appended notes or articles that would be
    # overwritten by this deploy.  Warn and prompt (unless --force or -y).
    if not dry_run and not force:
        local_index = os.path.join(wiki_dir, "_index.md")
        if os.path.exists(local_index):
            ssh_check = ["ssh"]
            if key_path:
                ssh_check += ["-i", key_path]
            ssh_check += [
                "-o", "ConnectTimeout=5",
                f"{ssh_user}@{host}",
                f"stat -c %Y /data/knowledge-bases/{remote_uid}/{kb_id}/wiki/_index.md "
                f"2>/dev/null || echo 0",
            ]
            try:
                r_check = subprocess.run(
                    ssh_check, capture_output=True, text=True, timeout=10
                )
                remote_mtime = int((r_check.stdout.strip() or "0").split()[0])
                local_mtime  = int(os.path.getmtime(local_index))

                if remote_mtime > local_mtime:
                    from datetime import datetime as _dt
                    remote_ts = _dt.fromtimestamp(remote_mtime).strftime("%Y-%m-%d %H:%M")
                    local_ts  = _dt.fromtimestamp(local_mtime).strftime("%Y-%m-%d %H:%M")
                    print(f"\n⚠️  Remote wiki has changes newer than local:")
                    print(f"   Remote _index.md : {remote_ts}")
                    print(f"   Local  _index.md : {local_ts}")
                    print(f"   Deploying will overwrite remote notes and articles.")
                    if not args.yes:
                        answer = input(
                            "\n   Proceed and overwrite remote? [y/N] "
                        ).strip().lower()
                        if answer != "y":
                            print("   Cancelled.  Use --force to skip this check.")
                            return
            except (subprocess.TimeoutExpired, ValueError, Exception):
                # Can't determine remote state — proceed without blocking
                pass

    # ── Sync wiki/ ────────────────────────────────────────────────────────
    rsync_cmd += [f"{wiki_dir}/", f"{remote_path}wiki/"]
    print(f"\n{'🔍' if dry_run else '🚀'} {'Previewing' if dry_run else 'Syncing'} wiki/...")

    r = subprocess.run(rsync_cmd, capture_output=quiet)
    if quiet and r.stdout:
        # Still show a brief summary even in quiet mode
        lines = r.stdout.decode(errors='replace').splitlines()
        transferred = [l for l in lines if not l.startswith("sending") and l.strip()]
        if transferred:
            print(f"   {len(transferred)} file(s) transferred")

    if r.returncode == 0:
        if dry_run:
            print(f"\n✅ Dry run complete — no files were modified.")
            print(f"   Run without --dry-run to deploy.")
        else:
            print(f"\n✅ Deploy complete!")
            print(f"   Knowledge base '{kb_id}' is now live for user '{remote_uid}'")
            print(f"   API access: GET /api/v1/knowledge-bases (with X-KB-ID: {kb_id})")
    else:
        print("\n❌ rsync failed. Check connection and path settings.")
        if quiet and r.stderr:
            print(r.stderr.decode(errors='replace'))


def cmd_lint(args):
    """
    Static wiki health check.

    Checks:
      1. Orphan [[links]] — wiki links in articles with no matching file
      2. Missing required sections — articles lacking ## Summary or frontmatter
      3. Stale articles — compiled files whose source was re-ingested afterward
      4. Broken concept refs — _concepts/ pages referencing unknown article names
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        return

    config = load_config(kb_path)
    articles_dir = os.path.join(kb_path, "wiki", "_articles")
    concepts_dir = os.path.join(kb_path, "wiki", "_concepts")
    index_file   = os.path.join(kb_path, "wiki", "_meta", "file_index.json")

    print(f"📚 {config.get('name', 'Knowledge Base')} — wiki lint")
    print()

    # ── Collect known article stems ────────────────────────────────────────
    article_files = {}
    if os.path.isdir(articles_dir):
        for fname in os.listdir(articles_dir):
            if fname.endswith(".md"):
                article_files[fname[:-3].lower()] = os.path.join(articles_dir, fname)

    concept_files = {}
    if os.path.isdir(concepts_dir):
        for fname in os.listdir(concepts_dir):
            if fname.endswith(".md"):
                concept_files[fname[:-3].lower()] = os.path.join(concepts_dir, fname)

    known_stems = set(article_files) | set(concept_files)

    # ── 1. Orphan [[links]] ────────────────────────────────────────────────
    print("── 1. Orphan [[links]] ──────────────────────────────────────────")
    link_pat = re.compile(r'\[\[([^\]|#]+)')
    orphans = {}  # link_target → [source_files]
    all_links = {}

    for stem, path in article_files.items():
        try:
            text = read_text(path)
        except Exception:
            continue
        for m in link_pat.finditer(text):
            target = m.group(1).strip()
            target_key = re.sub(r'[^\w\s-]', '', target).strip().replace(' ', '_').lower()
            all_links.setdefault(target_key, set()).add(stem)
            if target_key not in known_stems:
                orphans.setdefault(target, []).append(stem)

    if orphans:
        print(f"  ⚠️  {len(orphans)} orphan link(s) found:")
        for link, sources in sorted(orphans.items())[:20]:
            print(f"     [[{link}]]  ← referenced in: {', '.join(sorted(sources)[:3])}")
        if len(orphans) > 20:
            print(f"     ... and {len(orphans) - 20} more")
    else:
        print("  ✅ No orphan links")
    print()

    # ── 2. Missing required sections ──────────────────────────────────────
    print("── 2. Missing sections ─────────────────────────────────────────")
    REQUIRED_SECTIONS = ["## Summary", "## For Future Queries"]
    missing_sections = {}

    for stem, path in article_files.items():
        try:
            text = read_text(path)
        except Exception:
            continue
        issues = []
        if not text.startswith("---"):
            issues.append("no frontmatter")
        for sec in REQUIRED_SECTIONS:
            if sec not in text:
                issues.append(f"missing '{sec}'")
        if issues:
            missing_sections[stem] = issues

    if missing_sections:
        print(f"  ⚠️  {len(missing_sections)} article(s) with section issues:")
        for stem, issues in sorted(missing_sections.items())[:20]:
            print(f"     {stem}.md  — {', '.join(issues)}")
        if len(missing_sections) > 20:
            print(f"     ... and {len(missing_sections) - 20} more")
    else:
        print("  ✅ All articles have required sections")
    print()

    # ── 3. Stale articles ─────────────────────────────────────────────────
    print("── 3. Stale articles ───────────────────────────────────────────")
    stale = []

    if os.path.exists(index_file):
        try:
            import json as _json
            with open(index_file, 'r', encoding='utf-8') as f:
                index_data = _json.load(f)
            for fid, finfo in index_data.get("files", {}).items():
                llm_at  = finfo.get("llm_compiled_at")
                ingested = finfo.get("ingested_at") or finfo.get("updated_at")
                if llm_at and ingested and ingested > llm_at:
                    stale.append(finfo.get("name", fid))
        except Exception:
            pass

    if stale:
        print(f"  ⚠️  {len(stale)} article(s) re-ingested after last LLM compile:")
        for name in sorted(stale)[:20]:
            print(f"     {name}")
        if len(stale) > 20:
            print(f"     ... and {len(stale) - 20} more")
        print("   → Run: python src/cli.py compile-llm --docs --full")
    else:
        print("  ✅ No stale articles detected")
    print()

    # ── 4. Broken concept refs ────────────────────────────────────────────
    print("── 4. Broken concept refs ─────────────────────────────────────")
    broken_concept_refs = []

    for stem, path in concept_files.items():
        try:
            text = read_text(path)
        except Exception:
            continue
        for m in link_pat.finditer(text):
            target = m.group(1).strip()
            target_key = re.sub(r'[^\w\s-]', '', target).strip().replace(' ', '_').lower()
            if target_key not in known_stems:
                broken_concept_refs.append((stem, target))

    if broken_concept_refs:
        print(f"  ⚠️  {len(broken_concept_refs)} broken ref(s) in concept pages:")
        for src, link in sorted(broken_concept_refs)[:20]:
            print(f"     {src}.md → [[{link}]]")
    else:
        print("  ✅ No broken concept references")
    print()

    # ── Summary ───────────────────────────────────────────────────────────
    total_issues = len(orphans) + len(missing_sections) + len(stale) + len(broken_concept_refs)
    print("── Summary ─────────────────────────────────────────────────────")
    print(f"  Articles   : {len(article_files)}")
    print(f"  Concepts   : {len(concept_files)}")
    print(f"  Total issues: {total_issues}")
    if total_issues == 0:
        print("\n  ✅ Wiki looks healthy!")
    else:
        print(f"\n  ⚠️  {total_issues} issue(s) found — see above for details")


def cmd_search(args):
    """
    Keyword search against the local knowledge base index.

    Uses the same stop-word filter and TF scoring as the MCP servers,
    so results here match what the AI will see — without needing to
    start the MCP server.
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        return

    import json as _json

    index_file = os.path.join(kb_path, "wiki", "_meta", "file_index.json")
    if not os.path.exists(index_file):
        print("❌ No index found. Run 'ingest' and 'compile' first.")
        return

    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            index_data = _json.load(f)
    except Exception as e:
        print(f"❌ Could not load index: {e}")
        return

    query = " ".join(args.query)
    limit = args.limit

    _STOP_WORDS = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "it", "its",
        "this", "that", "these", "those", "i", "you", "he", "she", "we",
        "they", "what", "which", "who", "how", "when", "where", "why",
        "not", "no", "can", "if", "then", "than", "so", "as",
    }

    keywords = [w for w in query.lower().split() if len(w) > 2 and w not in _STOP_WORDS]

    if not keywords:
        print("⚠️  Query contains only stop-words. Please be more specific.")
        return

    print(f"🔍 Searching: \"{query}\"")
    print(f"   Keywords  : {keywords}")
    print()

    scored = []
    for fid, finfo in index_data.get("files", {}).items():
        meta  = finfo.get("extracted_metadata", {})
        title = finfo.get("name", "").lower()
        body  = " ".join(
            meta.get("core_claims", [])
            + meta.get("key_data", [])
            + meta.get("quotes", [])
        ).lower()
        rq_list = finfo.get("retrieval_queries", [])
        rq_body = " ".join(rq_list).lower()

        score = sum(body.count(kw) for kw in keywords)
        score += sum(title.count(kw) * 5 for kw in keywords)
        score += sum(rq_body.count(kw) * 2 for kw in keywords)
        # Sentence-level: a single retrieval query anticipated ≥2 keywords
        for rq in rq_list:
            rq_lower = rq.lower()
            hits = sum(1 for kw in keywords if kw in rq_lower)
            if hits >= 2:
                score += hits * 5

        if score > 0:
            scored.append({
                "id":          fid,
                "name":        finfo.get("name", ""),
                "score":       score,
                "word_count":  meta.get("word_count", 0),
                "core_claims": meta.get("core_claims", [])[:3],
                "wiki_path":   finfo.get("wiki_path", ""),
                "rq_match":    [rq for rq in rq_list
                                if sum(1 for kw in keywords if kw in rq.lower()) >= 2],
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    scored = scored[:limit]

    if not scored:
        print("No results found.")
        return

    print(f"Found {len(scored)} result(s):\n")
    for i, r in enumerate(scored, 1):
        print(f"  {i}. {r['name']}  (score: {r['score']}, words: {r['word_count']})")
        print(f"     ID: {r['id']}")
        if r['wiki_path']:
            print(f"     File: {r['wiki_path']}")
        # Show matched retrieval queries if any (explains *why* this result ranked)
        if r.get('rq_match'):
            for rq in r['rq_match'][:2]:
                print(f"     ↳ \"{rq[:120]}\"")
        elif r['core_claims']:
            print(f"     Key:  {r['core_claims'][0][:120]}")
        print()


def cmd_clean(args):
    """
    Remove stale index entries whose source files no longer exist on disk.

    For each entry in file_index.json whose `path` is missing:
      - removes the entry from the index
      - removes the corresponding _extracted.txt file (if present)
      - optionally removes the corresponding wiki article (--articles flag)

    Pass --dry-run to preview what would be deleted without touching anything.
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        return

    config = load_config(kb_path)
    print(f"📚 {config.get('name', 'Knowledge Base')} — clean")

    import json as _json

    index_file = os.path.join(kb_path, "wiki", "_meta", "file_index.json")
    if not os.path.exists(index_file):
        print("❌ No index found. Run 'ingest' first.")
        return

    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            index_data = _json.load(f)
    except Exception as e:
        print(f"❌ Could not load index: {e}")
        return

    dry_run = args.dry_run
    remove_articles = args.articles

    stale_ids = []
    for fid, finfo in index_data.get("files", {}).items():
        src_path = finfo.get("path", "")
        if src_path and not os.path.exists(src_path):
            stale_ids.append((fid, finfo))

    if not stale_ids:
        print("✅ Index is clean — all source files still exist.")
        return

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Found {len(stale_ids)} stale entry/entries:\n")

    deleted_entries = 0
    deleted_extracted = 0
    deleted_articles = 0

    for fid, finfo in stale_ids:
        name = finfo.get("name", fid)
        src = finfo.get("path", "?")
        wiki_path = finfo.get("wiki_path", "")
        print(f"  🗑  {name}")
        print(f"      source  : {src}  [missing]")

        # _extracted.txt
        if wiki_path:
            extracted_path = os.path.join(kb_path, wiki_path)
            if os.path.exists(extracted_path):
                print(f"      extract : {wiki_path}")
                if not dry_run:
                    os.remove(extracted_path)
                    deleted_extracted += 1
            else:
                deleted_extracted += 0  # already gone

        # wiki article
        if remove_articles:
            stem = os.path.splitext(name)[0]
            article_path = os.path.join(
                kb_path, "wiki", "_articles", f"{sanitize_filename(stem)}.md"
            )
            if os.path.exists(article_path):
                print(f"      article : wiki/_articles/{os.path.basename(article_path)}")
                if not dry_run:
                    os.remove(article_path)
                    deleted_articles += 1

        if not dry_run:
            del index_data["files"][fid]
            deleted_entries += 1

        print()

    if dry_run:
        print(f"[DRY RUN] Would remove {len(stale_ids)} index entries.")
        if remove_articles:
            print(f"[DRY RUN] Would also delete matching wiki articles (--articles).")
        print("  Run without --dry-run to apply.")
        return

    # Save updated index
    try:
        with open(index_file, 'w', encoding='utf-8') as f:
            _json.dump(index_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Failed to save index: {e}")
        return

    print(f"✅ Cleaned {deleted_entries} index entries")
    if deleted_extracted:
        print(f"   Removed {deleted_extracted} extracted text file(s)")
    if deleted_articles:
        print(f"   Removed {deleted_articles} wiki article(s)")


def cmd_status(args):
    """Show status"""
    kb_path = get_kb_path()
    
    if not kb_path:
        print("❌ Error: Knowledge base config not found")
        return
    
    config = load_config(kb_path)
    print(f"📚 Knowledge Base: {config.get('name', 'Unknown')}")
    print(f"📁 Path: {kb_path}")
    print(f"📌 Version: {config.get('version', '1.0')}")
    
    indexer = IndexManager(kb_path)
    stats = indexer.get_stats()

    # Count LLM-compiled / failed documents
    all_files = indexer.index.get("files", {}).values()
    llm_compiled   = sum(1 for f in all_files if f.get("llm_compiled_at"))
    llm_failed     = sum(1 for f in all_files
                         if f.get("compile_failed_at") and not f.get("llm_compiled_at"))
    total_completed = stats['completed']

    print(f"\n📊 Document Statistics:")
    print(f"  Total: {stats['total']}")
    print(f"  ✅ Completed (ingested): {stats['completed']}")
    print(f"  🤖 LLM compiled: {llm_compiled} / {total_completed}"
          + ("  ✅" if llm_compiled == total_completed and total_completed > 0 else
             "  ⚠️  run compile-llm" if llm_compiled < total_completed else ""))
    if llm_failed:
        print(f"  ❌ LLM compile failed: {llm_failed}"
              f"  — run: compile-llm --retry-failed")
        # Show which ones failed
        for finfo in indexer.index.get("files", {}).values():
            if finfo.get("compile_failed_at") and not finfo.get("llm_compiled_at"):
                err = finfo.get("compile_error", "unknown error")[:80]
                print(f"     • {finfo.get('name', '?')}  ({err})")
    print(f"  ⏳ Pending: {stats['pending']}")
    print(f"  🔄 Processing: {stats['processing']}")
    print(f"  ❌ Ingest error: {stats['error']}")

    print(f"\n📂 Directory Status:")
    for dir_name in ["raw", "wiki", "outputs"]:
        dir_path = os.path.join(kb_path, dir_name)
        if os.path.exists(dir_path):
            count = sum(len(files) for _, _, files in os.walk(dir_path))
            print(f"  {dir_name}/: exists ({count} files)")
        else:
            print(f"  {dir_name}/: not found ❌")


def main():
    """Main entry"""
    parser = argparse.ArgumentParser(
        description="Knowledge Base Builder Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/cli.py init ~/my-kb --name "My KB"
  python src/cli.py ingest
  python src/cli.py compile
  python src/cli.py status
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    init_parser = subparsers.add_parser('init', help='Initialize knowledge base')
    init_parser.add_argument('folder_path', help='Knowledge base directory path')
    init_parser.add_argument('--name', '-n', help='Knowledge base name')

    ingest_parser = subparsers.add_parser('ingest', help='Ingest documents')
    ingest_parser.add_argument('--full', '-f', action='store_true',
                               help='Full re-ingest (not incremental)')

    compile_parser = subparsers.add_parser('compile', help='Compile wiki')
    compile_parser.add_argument('--full', '-f', action='store_true',
                                help='Full re-compile')

    status_parser = subparsers.add_parser('status', help='Show status')

    # ------------------------------------------------------------------
    # fetch: Skill Seekers integration
    # ------------------------------------------------------------------
    fetch_parser = subparsers.add_parser(
        'fetch',
        help='Fetch knowledge from any source via Skill Seekers',
        description=(
            'Scrape a documentation site, GitHub repo, PDF, video, or any '
            'other source using Skill Seekers, then automatically ingest the '
            'resulting Markdown files into this knowledge base.'
        ),
    )
    fetch_parser.add_argument(
        'source',
        help='Source to fetch: URL, GitHub repo (owner/repo), local path, etc.'
    )
    fetch_parser.add_argument(
        '--name', '-n',
        help='Custom name for the skill folder (default: derived from source)'
    )
    fetch_parser.add_argument(
        '--async', dest='use_async', action='store_true',
        help='Use async scraping mode (2-3x faster, requires skill-seekers[async])'
    )
    fetch_parser.add_argument(
        '--no-ingest', action='store_true',
        help='Skip auto-ingest after fetching (manual ingest later)'
    )
    fetch_parser.add_argument(
        '--compile', action='store_true',
        help='Also compile the wiki after ingesting'
    )
    fetch_parser.add_argument(
        'extra_args', nargs='*',
        help='Extra arguments forwarded to skill-seekers create'
    )

    fetch_list_parser = subparsers.add_parser(
        'fetch-list',
        help='List all previously fetched skills'
    )

    # ------------------------------------------------------------------
    # harvest: import AI session transcripts
    # ------------------------------------------------------------------
    harvest_parser = subparsers.add_parser(
        'harvest',
        help='Import AI session transcripts into KB',
        description=(
            'Read Claude Code (and optionally Cursor) session transcripts '
            'and write them as structured Markdown files into raw/transcripts/, '
            'ready for the normal ingest → compile pipeline.'
        ),
    )
    harvest_parser.add_argument(
        '--sources', default='claude-code',
        help='Adapters to use: claude-code,cursor (default: claude-code)'
    )
    harvest_parser.add_argument(
        '--since', type=int, metavar='DAYS',
        help='Only harvest sessions from the last N days'
    )
    harvest_parser.add_argument(
        '--list', action='store_true',
        help='List previously harvested sessions'
    )

    # ------------------------------------------------------------------
    # compile-llm: LLM-driven wiki compilation
    # ------------------------------------------------------------------
    compile_llm_parser = subparsers.add_parser(
        'compile-llm',
        help='LLM-driven wiki compilation (requires API key, see LLM_PROVIDER)',
        description=(
            'Use Claude to write proper wiki articles, regenerate the index, '
            'and create concept pages — replacing the regex-based compile step '
            'with genuine semantic understanding.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps (all run by default; pass one flag to run only that step):
  --docs      Write a wiki article for each ingested document
  --index     Regenerate _index.md from all current articles
  --concepts  Write concept pages for the most cross-referenced [[links]]

Examples:
  python src/cli.py compile-llm                          # all three steps
  python src/cli.py compile-llm --docs                   # articles only
  python src/cli.py compile-llm --index                  # rebuild index only
  python src/cli.py compile-llm --concepts               # concept pages only
  python src/cli.py compile-llm --full -y                # recompile everything, no prompt

Environment:
  LLM_PROVIDER        "claude" (default) or "kimi"
  ANTHROPIC_API_KEY   required when LLM_PROVIDER=claude
  KIMI_API_KEY        required when LLM_PROVIDER=kimi
        """
    )
    compile_llm_parser.add_argument(
        '--model', default=None,
        help='LLM model override (default: auto-selected by LLM_PROVIDER)'
    )
    compile_llm_parser.add_argument(
        '--docs', action='store_true',
        help='Run document compilation step only'
    )
    compile_llm_parser.add_argument(
        '--index', action='store_true',
        help='Run index regeneration step only'
    )
    compile_llm_parser.add_argument(
        '--concepts', action='store_true',
        help='Run concept page generation step only'
    )
    compile_llm_parser.add_argument(
        '--full', '-f', action='store_true',
        help='Recompile all documents, even those already compiled'
    )
    compile_llm_parser.add_argument(
        '--yes', '-y', action='store_true',
        help='Skip cost confirmation prompt'
    )
    compile_llm_parser.add_argument(
        '--concept-limit', type=int, default=20, metavar='N',
        help='Max number of concept pages to generate (default: 20)'
    )
    compile_llm_parser.add_argument(
        '--no-index', action='store_true',
        help='Skip auto-index regeneration after compiling docs'
    )
    compile_llm_parser.add_argument(
        '--retry-failed', action='store_true',
        help='Retry documents that failed in a previous run'
    )

    # ------------------------------------------------------------------
    # deploy: sync wiki/ to cloud server
    # ------------------------------------------------------------------
    deploy_parser = subparsers.add_parser(
        'deploy',
        help='Sync compiled wiki to cloud server via rsync/SSH',
        description=(
            'Push the local wiki/ directory to a remote server so third-party '
            'users can access it through the cloud MCP API.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/cli.py deploy --host myserver.com --remote-user alice
  python src/cli.py deploy --host myserver.com --remote-user alice --kb-id research --key ~/.ssh/id_rsa

Environment variables (alternative to flags):
  KBA_DEPLOY_HOST          Remote host
  KBA_DEPLOY_USER          SSH login user (default: root)
  KBA_DEPLOY_KEY           Path to SSH private key
  KBA_DEPLOY_REMOTE_USER   user_id on server (required)
  KBA_DEPLOY_KB_ID         KB folder name on server (default: local directory name)
        """
    )
    deploy_parser.add_argument('--host', help='Remote server hostname or IP')
    deploy_parser.add_argument('--ssh-user', default='root', help='SSH login user (default: root)')
    deploy_parser.add_argument('--key', help='Path to SSH private key')
    deploy_parser.add_argument('--remote-user', help='user_id on the server (determines storage path)')
    deploy_parser.add_argument('--kb-id', help='KB identifier on server (default: local directory name)')
    deploy_parser.add_argument('--dry-run', action='store_true',
                               help='Preview what would be transferred without syncing')
    deploy_parser.add_argument('--quiet', '-q', action='store_true',
                               help='Suppress rsync file-by-file output; show only summary')
    deploy_parser.add_argument('--force', action='store_true',
                               help='Skip remote conflict check and overwrite without prompting')
    deploy_parser.add_argument('--yes', '-y', action='store_true',
                               help='Auto-confirm conflict prompt (useful in CI)')

    # ------------------------------------------------------------------
    # clean: prune stale index entries
    # ------------------------------------------------------------------
    clean_parser = subparsers.add_parser(
        'clean',
        help='Remove stale index entries whose source files no longer exist',
        description=(
            'Scan file_index.json for entries whose source path is missing. '
            'Removes those entries plus their _extracted.txt files. '
            'Use --articles to also delete matching wiki articles.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/cli.py clean --dry-run    # preview without deleting
  python src/cli.py clean              # remove stale entries + extracted files
  python src/cli.py clean --articles   # also delete matching wiki articles
        """
    )
    clean_parser.add_argument(
        '--dry-run', action='store_true',
        help='Preview what would be deleted without making any changes'
    )
    clean_parser.add_argument(
        '--articles', action='store_true',
        help='Also delete matching wiki articles in wiki/_articles/'
    )

    # ------------------------------------------------------------------
    # lint: static wiki health check
    # ------------------------------------------------------------------
    subparsers.add_parser(
        'lint',
        help='Static wiki health check: orphan links, missing sections, stale articles',
        description=(
            'Scan the compiled wiki for structural issues without calling the LLM. '
            'Checks orphan [[links]], missing required sections (Summary, For Future Queries), '
            'stale articles (re-ingested since last LLM compile), and broken concept refs.'
        ),
    )

    # ------------------------------------------------------------------
    # search: keyword search against local index
    # ------------------------------------------------------------------
    search_parser = subparsers.add_parser(
        'search',
        help='Keyword search against local knowledge base index',
        description=(
            'Search the local file_index.json using the same stop-word filter and '
            'TF scoring as the MCP servers — no server needed.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/cli.py search "machine learning"
  python src/cli.py search gradient descent --limit 10
        """
    )
    search_parser.add_argument('query', nargs='+', help='Search query (one or more words)')
    search_parser.add_argument('--limit', type=int, default=5, metavar='N',
                               help='Max results to show (default: 5)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        'init': cmd_init,
        'ingest': cmd_ingest,
        'compile': cmd_compile,
        'compile-llm': cmd_compile_llm,
        'status': cmd_status,
        'fetch': cmd_fetch,
        'fetch-list': cmd_fetch_list,
        'harvest': cmd_harvest,
        'deploy': cmd_deploy,
        'lint': cmd_lint,
        'search': cmd_search,
        'clean': cmd_clean,
    }
    
    command_func = commands.get(args.command)
    if command_func:
        command_func(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
