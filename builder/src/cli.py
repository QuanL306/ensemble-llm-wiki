#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Knowledge Base Builder - CLI Entry Point (English Version)
"""

import os
import re
import sys
import json
import shlex
import argparse
import re as _re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# ── Input validation for deploy args (C3) ────────────────────────
_SAFE_ID_RE = _re.compile(r'^[A-Za-z0-9._\-]+$')


def _validate_deploy_arg(name: str, value: str):
    """Reject deploy arguments containing shell-unsafe characters."""
    if not value:
        return
    if not _SAFE_ID_RE.match(value):
        print(f"❌ Invalid {name} '{value}': only letters, digits, dots, hyphens, underscores allowed")
        sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.file_utils import ensure_dir, write_text, read_text, sanitize_filename
from core.ingest import DataIngest
from core.compiler import WikiCompiler
from core.indexer import IndexManager
from core.skill_seekers import SkillSeekersIntegration, SkillSeekersNotInstalledError, SkillSeekersFetchError
from core.prompts import _PROMPT_DOCUMENT, _PROMPT_INDEX, _PROMPT_CONCEPTS
from core.chaptered import (_CHAPTER_SPLIT_PATTERNS, _split_into_chapters,
                             _compile_document_chaptered)
from core.registry import detect_pipeline, detect_file_type


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


_VALID_PIPELINES = {"graphify-first", "compile-first", "none"}


def _validate_config(config: dict) -> dict:
    """Warn and correct invalid pipeline.default in .kbaconfig (M13).

    Returns config (possibly mutated in-memory) so callers can use it directly.
    """
    pipeline_default = config.get("pipeline", {}).get("default", "graphify-first")
    if pipeline_default not in _VALID_PIPELINES:
        print(f"⚠️  Warning: .kbaconfig pipeline.default='{pipeline_default}' is not valid. "
              f"Valid values: {sorted(_VALID_PIPELINES)}. Using 'graphify-first'.")
        config.setdefault("pipeline", {})["default"] = "graphify-first"
    return config


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
    if args.folder_path:
        folder_path = os.path.abspath(args.folder_path)
    else:
        kb_root = os.environ.get('KB_ROOT')
        if not kb_root:
            print("❌ Error: No path specified and KB_ROOT environment variable not set")
            print("   Set KB_ROOT or provide a path:")
            print("   python src/cli.py init <folder_path>")
            return
        kb_name = args.name or 'new-kb'
        folder_path = os.path.join(os.path.expanduser(kb_root), kb_name)
        folder_path = os.path.abspath(folder_path)
    kb_name = args.name or os.path.basename(folder_path)

    # Guard against silently overwriting an existing KB (M8)
    existing_config = os.path.join(folder_path, ".kbaconfig")
    if os.path.exists(existing_config) and not getattr(args, 'force', False):
        print(f"❌  Knowledge base already exists at: {folder_path}")
        print(f"   Use --force to reinitialize (WARNING: overwrites .kbaconfig)")
        return
    if os.path.exists(existing_config) and getattr(args, 'force', False):
        print(f"   ⚠️  Reinitializing — preserving custom pipeline rules from existing config")
    
    print(f"🚀 Initializing Knowledge Base: {kb_name}")
    print(f"📁 Path: {folder_path}")
    
    dirs = [
        os.path.join(folder_path, "raw"),
        os.path.join(folder_path, "raw", "articles"),
        os.path.join(folder_path, "raw", "books"),
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
        },
        "pipeline": {
            "default": "graphify-first",
            "graphify_twice": False,
            "rules": [
                {"match": "*.md", "pipeline": "compile-first"},
                {"match": "*.markdown", "pipeline": "compile-first"}
            ]
        }
    }
    
    # L7: when --force is used, preserve any custom pipeline rules from the old config
    if os.path.exists(existing_config) and getattr(args, 'force', False):
        existing = load_config(folder_path)
        existing_rules = existing.get("pipeline", {}).get("rules", [])
        if existing_rules:
            config["pipeline"]["rules"] = existing_rules
            print(f"   Preserved {len(existing_rules)} custom pipeline rule(s)")

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

- `raw/` - Source documents (drop files here before ingesting)
  - `articles/` - Blog posts, essays, standalone web articles
  - `books/` - EPUBs, PDFs of full-length books
  - `papers/` - Academic papers, research reports, whitepapers
  - `images/` - Standalone image files (embedded images in PDFs/EPUBs are handled automatically)
  - `web_clips/` - Saved web pages, HTML exports, browser clippings
- `wiki/` - Compiled knowledge base (Obsidian compatible)
- `outputs/` - Query outputs

### Notes on `raw/`

- Subfolders are organizational only — the ingest pipeline scans all of `raw/` recursively, so file placement does not affect processing.
- Supported formats: PDF, EPUB, Markdown (`.md`), plain text (`.txt`).
- Your originals are never modified or deleted. All processed output goes into `wiki/`.
- You can add your own subfolders freely; they will be picked up automatically.

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
    print(f"  3. kb ingest")
    print(f"  4. kb compile-llm")
    print(f"\n  (If 'kb' is not on your PATH, use: python3 {os.path.abspath(__file__)})")


def cmd_ingest(args):
    """Ingest documents"""
    kb_path = get_kb_path()

    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        print("   Please run: python src/cli.py init <folder_path>")
        return

    config = _validate_config(load_config(kb_path))
    print(f"📚 Knowledge Base: {config.get('name', 'Unknown')}")
    print(f"📁 Path: {kb_path}")

    ingest = DataIngest(kb_path, config)

    retry_failed = getattr(args, 'retry_failed', False)
    file_filter  = getattr(args, 'file', None)

    print("\n🔍 Scanning documents...")
    scan_result = ingest.scan(incremental=not args.full)

    print(f"  New: {len(scan_result['new'])}")
    print(f"  Changed: {len(scan_result['changed'])}")
    print(f"  Unchanged: {len(scan_result['unchanged'])}")

    if scan_result['unsupported']:
        print(f"  Unsupported: {len(scan_result['unsupported'])}")

    to_process = scan_result['new'] + scan_result['changed']

    # --retry-failed: add files that errored in a previous run
    if retry_failed:
        failed_entries = ingest.indexer.get_all_files('error')
        failed_paths   = [f.get('path') for f in failed_entries if f.get('path')]
        existing_set   = set(to_process)
        added = [p for p in failed_paths if p and p not in existing_set]
        if added:
            print(f"  Retrying: {len(added)} previously failed file(s)")
            to_process += added
        else:
            print("  No previously failed files to retry.")

    # --file: target a single document by name fragment
    if file_filter:
        before = len(to_process)
        to_process = [
            p for p in to_process
            if file_filter.lower() in os.path.basename(p).lower()
        ]
        if not to_process:
            print(f"\n⚠️  No files matched --file '{file_filter}' "
                  f"(searched {before} candidates)")
            return
        print(f"  Filter '--file {file_filter}': {len(to_process)} match(es)")

    if not to_process:
        print("\n✅ All documents are up to date")
        print_kb_status(kb_path)
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
    print_kb_status(kb_path)


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
    print_kb_status(kb_path)


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
# add — one-command content ingestion with auto-pipeline
# ============================================================

def cmd_add(args):
    """Add one or more files to the KB and run the appropriate pipeline.

    Pipeline options (auto-detected by default):
      graphify-first  — PDF/EPUB: graphify → ingest → compile-llm
      compile-first   — MD/TXT:  ingest → compile-llm → graphify
      none            — ingest only, skip graphify and compile
    """
    from core.kbapi import KnowledgeBase

    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        print("   Please run: kb init <folder_path>")
        return

    kb = KnowledgeBase(kb_path)
    config = _validate_config(load_config(kb_path))
    print(f"📚 {config.get('name', kb_path)}")

    sources = args.sources
    if not sources:
        print("❌ Error: No source files specified.")
        print("   Usage: kb add file1.pdf file2.md ...")
        return

    # Pre-flight check: filter out directories and non-existent files
    valid = []
    for s in sources:
        p = Path(s)
        if not p.exists():
            print(f"⚠️  Skipping (not found): {s}")
            continue
        if p.is_dir():
            print(f"⚠️  Skipping (is a directory): {s}")
            continue
        valid.append(s)

    if not valid:
        print("❌ No valid source files to add.")
        return
    sources = valid

    # Resolve explicit pipeline
    pipeline = args.pipeline or None

    # Show plan
    lines = []
    for s in sources[:10]:
        detected = pipeline or detect_pipeline(s, config)
        lines.append(f"   {os.path.basename(s):<40} → {detected}")
    if len(sources) > 10:
        lines.append(f"   ... and {len(sources) - 10} more")
    print("\n📋 Plan:")
    for l in lines:
        print(l)

    if not args.yes:
        if input("\n   Proceed? [y/N] ").strip().lower() != "y":
            print("   Cancelled.")
            return

    if len(sources) == 1:
        # Single file — full inline pipeline
        result = kb.add(
            source=sources[0],
            pipeline=pipeline,
            run_ingest=not args.no_ingest,
            run_compile=not args.no_compile,
            run_graphify=not args.no_graphify,
            model=args.model,
        )
        if result["status"] == "ok":
            print(f"\n✅ Added: {result['rel_path']}")
            print(f"   Pipeline: {result['pipeline']}")
            stages = result.get("stages", {})
            if stages:
                for stage, s in stages.items():
                    icon = "✅" if s == "done" else "❌"
                    print(f"   {icon} {stage}")
        else:
            print(f"\n❌ {result.get('error', 'Unknown error')}")
    else:
        # Batch — copy all, then run stages once
        result = kb.add_batch(
            sources=sources,
            pipeline=pipeline,
            run_ingest=not args.no_ingest,
            run_compile=not args.no_compile,
            run_graphify=not args.no_graphify,
            model=args.model,
        )
        print(f"\n✅ Batch complete: {len(sources)} files")
        stages = result.get("stages", {})
        if stages:
            for stage, s in stages.items():
                icon = "✅" if s == "done" else "❌"
                print(f"   {icon} {stage}")

    # Status report
    print_kb_status(kb_path)


def cmd_graphify(args):
    """Run graphify on the KB's wiki/ directory to build a knowledge graph."""
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found (.kbaconfig)")
        return

    from core.graphify_integration import (
        run_graphify, generate_jsonld, split_edges, tag_edge_provenance,
    )

    wiki_dir = os.path.join(kb_path, "wiki")
    if not os.path.isdir(wiki_dir):
        print("❌ wiki/ not found. Run ingest + compile first.")
        return

    # Pass only compiled content dirs to graphify — exclude _meta/, graphify-out/,
    # syntheses/ etc. so internal JSON indexes (file_index.json, hashes, paths)
    # don't pollute the knowledge graph with noise nodes.
    # Priority: _articles (LLM-compiled) > raw wiki/ (pre-compile fallback)
    wiki_path = Path(wiki_dir)
    articles_dir = wiki_path / "_articles"
    if articles_dir.is_dir() and any(articles_dir.glob("*.md")):
        graphify_input = articles_dir
    else:
        # Pre-compile fallback: no articles yet, run on full wiki/ so the
        # graphify-first pipeline can still build an initial graph from raw text.
        # _meta/ noise is expected at this stage.
        graphify_input = wiki_path

    # Always write output to wiki/graphify-out/ regardless of input dir,
    # so the dashboard can find it at the canonical location.
    graphify_output = wiki_path
    print(f"🔍 Building knowledge graph from: {graphify_input}")
    ok = run_graphify(graphify_input, "standard", output_dir=graphify_output)
    if ok:
        # Post-processing: generate edges, JSON-LD, and provenance
        # (these always read/write wiki/graphify-out/ — the canonical output dir)
        graph_json = wiki_path / "graphify-out" / "graph.json"
        graph_out_dir = wiki_path / "graphify-out"
        config = load_config(kb_path) or {}
        kb_name = config.get("name", os.path.basename(kb_path))

        if graph_json.exists():
            generate_jsonld(graph_json, graph_out_dir / "graph.jsonld", kb_name)
            split_edges(graph_json, graph_out_dir)
            if graphify_input.is_dir():
                counts = tag_edge_provenance(graph_json, graphify_input)
                print(f"   Provenance: {counts.get('extracted',0)} extracted · "
                      f"{counts.get('inferred',0)} inferred · {counts.get('ambiguous',0)} ambiguous")

        print("✅ Graphify complete.")
        print(f"   Output: {graph_out_dir}/")
        print_kb_status(kb_path)
    else:
        print("❌ Graphify failed. Check that graphify is installed: pip install graphifyy")


# ============================================================
# LLM-driven compilation
# ============================================================

# Prompt templates and chapter-by-chapter compilation extracted to:
#   core/prompts.py  (_PROMPT_DOCUMENT, _PROMPT_INDEX, _PROMPT_CONCEPTS)
#   core/chaptered.py (_split_into_chapters, _compile_document_chaptered)

# ---------- helpers -----------------------------------------

def _llm_client(model_override: str = None, backend_override: str = None):
    """Return (backend, model, config) using the unified LLM layer."""
    from core.llm import detect_backend, list_available, BACKENDS, make_config
    backend = backend_override or detect_backend()
    if backend is None:
        available = list_available()
        if available:
            backend = available[0]
        else:
            raise RuntimeError("No LLM backend available. Set an API key env var.")
    model = model_override or BACKENDS[backend]["model"]
    config = make_config(backend)
    config["model"] = model
    return backend, model, config


def _stream_message(backend, model, prompt, max_tokens=4096):
    """Call the API with a progress indicator; return full text."""
    from core.llm import chat_blocking
    return chat_blocking(prompt, backend=backend, model=model, max_tokens=max_tokens)


def _estimate_cost(files: list, model: str) -> float:
    """Rough cost estimate in USD based on word count and model pricing.

    Prices per 1M input tokens (May 2026). Single-pass books send ~25%
    of full text (150K chars ≈ 37K tokens); chaptered books (>300K words)
    send ~400 tokens/chapter × 16 chapters ≈ 6.4K tokens.
    """
    model_lower = (model or "").lower()

    # Input pricing per 1M tokens
    if "kimi" in model_lower or "moonshot" in model_lower:
        input_price = 1.65
    elif "deepseek" in model_lower:
        input_price = 0.27
    elif "glm" in model_lower or "zhipu" in model_lower:
        input_price = 1.00
    elif "opus" in model_lower:
        input_price = 15.0
    elif "sonnet" in model_lower:
        input_price = 3.0
    elif "haiku" in model_lower:
        input_price = 0.80
    elif "gpt-4o" in model_lower:
        input_price = 2.50
    elif "gemini" in model_lower:
        input_price = 0.10
    elif "minimax" in model_lower or "abab" in model_lower:
        input_price = 0.50
    else:
        input_price = 1.50  # conservative default

    # Output pricing per 1M tokens (typically 3-5x input price)
    if "kimi" in model_lower or "moonshot" in model_lower:
        output_price = 6.60
    elif "deepseek" in model_lower:
        output_price = 1.10
    elif "glm" in model_lower or "zhipu" in model_lower:
        output_price = 1.00
    elif "opus" in model_lower:
        output_price = 75.0
    elif "sonnet" in model_lower:
        output_price = 15.0
    elif "haiku" in model_lower:
        output_price = 4.0
    elif "gpt-4o" in model_lower:
        output_price = 10.0
    elif "gemini" in model_lower:
        output_price = 0.40
    elif "minimax" in model_lower or "abab" in model_lower:
        output_price = 0.50
    else:
        output_price = 4.0  # conservative default

    total_words = sum(
        f.get("extracted_metadata", {}).get("word_count", 5_000) for f in files
    )
    # For single-pass: ~25% of words sent (150K char cap)
    # Output: ~3000 tokens per doc (post-fix)
    input_tokens = total_words * 0.33   # ~0.33 tokens/word avg after truncation
    output_tokens = len(files) * 3_000
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


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
        # Strip any residual header block (e.g. "# Document Analysis\n## Basic Information\n...")
        header_end = re.search(r'\n## (?!Document Analysis|Basic Information|Structured Text)', raw)
        if header_end and header_end.start() < 500:
            return raw[header_end.start():].lstrip()
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
    assert CHUNK_SIZE > CHUNK_MIN, f"CHUNK_SIZE ({CHUNK_SIZE}) must be > CHUNK_MIN ({CHUNK_MIN})"

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

    SPLIT_PATTERNS = _CHAPTER_SPLIT_PATTERNS

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
        # fastembed is optional. Without it, search falls back to TF-IDF keyword
        # scoring (still works well). Install with: pip install fastembed
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

    # Save — stream directly to disk to avoid building the full JSON string in memory
    import tempfile as _tf2, os as _os2

    embeddings_data = {
        "model": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "embed_text_version": EMBED_TEXT_VERSION,
        "created_at": datetime.now().isoformat(),
        "vectors": vectors,
    }
    meta_dir_path = Path(embeddings_file).parent
    meta_dir_path.mkdir(parents=True, exist_ok=True)
    if len(vectors) > 1000:
        print(f"\n   ℹ️  {len(vectors)} vectors — writing directly to disk (streaming mode)")
    fd, tmp_path = _tf2.mkstemp(dir=str(meta_dir_path), suffix=".tmp")
    try:
        with _os2.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(embeddings_data, f, indent=2, ensure_ascii=False)
        _os2.replace(tmp_path, embeddings_file)
    except Exception:
        try:
            _os2.unlink(tmp_path)
        except OSError:
            pass
        raise

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

def _compile_with_retry(backend, model: str, file_info: dict, kb_path: str, max_retries: int = 3) -> Tuple[Optional[str], Optional[str]]:
    """Call _compile_document with retry logic for transient errors.

    Retries up to *max_retries* times on LLMTransientError with exponential
    backoff (2 ** attempt seconds). Fails immediately on LLMPermanentError
    without any retry.

    Returns (article_text, error_message).
    On success: (text, None).
    On failure: (None, error_string).
    """
    import time
    from core.llm import LLMTransientError, LLMPermanentError

    last_error: str = ""
    for attempt in range(max_retries):
        try:
            result = _compile_document(backend, model, file_info, kb_path)
            return result, None
        except LLMPermanentError as exc:
            # No retry — configuration or auth error, won't resolve itself.
            return None, str(exc)
        except LLMTransientError as exc:
            last_error = f"LLM transient error (attempt {attempt + 1}/{max_retries}): {exc}"
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except Exception as exc:
            return None, str(exc)

    return None, last_error


def _build_graph_context(file_info: dict, kb_path: str) -> str:
    """Build graph context for a document from graphify edges.

    Reads wiki/graphify-out/edges.jsonl and finds edges where this
    document appears as source or target.  Formats a compact hint
    listing related works and their relationships.

    Returns empty string if no graph data exists (backward compatible).
    """
    edges_path = os.path.join(kb_path, "wiki", "graphify-out", "edges.jsonl")
    if not os.path.exists(edges_path):
        return ""
    name = file_info.get("name", "")
    slug = re.sub(r'\.[^.]+$', '', name)
    slug_norm = slug.lower().replace("_", " ").strip()
    related = []
    try:
        with open(edges_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    edge = json.loads(line)
                except json.JSONDecodeError:
                    continue
                src = edge.get("source_label", "")
                tgt = edge.get("target_label", "")
                src_norm = src.lower().replace("_", " ").strip()
                tgt_norm = tgt.lower().replace("_", " ").strip()
                relation = edge.get("relation", "related")
                if slug_norm == src_norm:
                    related.append((tgt, relation, "→"))
                elif slug_norm == tgt_norm:
                    related.append((src, relation, "←"))
    except Exception:
        return ""
    if not related:
        return ""
    # Deduplicate and format
    seen = set()
    lines = ["## Knowledge Graph Context",
             "This document is connected to other works in this KB:"]
    for other, rel, arrow in related[:12]:  # cap at 12 edges
        key = (other, rel)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {arrow} [[{other}]] — {rel}")
    lines.append("Use these connections in the ## Connections section.\n")
    return "\n".join(lines) + "\n"


def _compile_document(backend, model: str, file_info: dict, kb_path: str) -> Optional[str]:
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

    # ── Large book → chapter-by-chapter compilation ──
    # Single-pass compilation risks timeout for books with very long
    # chapters that still fit under the 150K char cap but force the LLM
    # to generate long responses. Chaptered mode is safer and produces
    # better coverage of the full book.
    word_count_est = len(text.split())
    if word_count_est > 300_000:
        print(f"     massive book ({word_count_est:,} words) → chaptered mode", flush=True)
        result = _compile_document_chaptered(backend, model, file_info, kb_path)
        if result is not None:
            return result
        # Fall through to single-pass if chaptered returned None

    meta = file_info.get("extracted_metadata", {})
    name = file_info.get("name", "Unknown")
    name_clean = re.sub(r'\.[^.]+$', '', name)   # strip extension for YAML title

    # Fit within context budget (~37K tokens at 150K chars; leaves room for output)
    MAX_CHARS = 150_000
    original_word_count = meta.get("word_count", "unknown")
    truncated = len(text) > MAX_CHARS
    if truncated:
        # Sample beginning, middle, and end equally (M15)
        chunk = 45_000  # ~45K chars each ≈ 135K total
        mid_start = max(0, len(text) // 2 - chunk // 2)
        text = (
            text[:chunk]
            + f"\n\n[NOTE: source text too long (~{original_word_count:,} words). "
            f"Sampling beginning, middle, and end. Middle section starts ~{mid_start//6:,} words in.]\n\n"
            + text[mid_start: mid_start + chunk]
            + "\n\n[...middle section end...]\n\n"
            + text[-chunk:]
        )

    # Use the actual word count of what we send, not the original full count
    word_count = len(text.split()) if truncated else original_word_count

    # Suggest tags from the filename for the prompt (Claude will refine them)
    stem_words = re.sub(r'[_\-. ]', ' ', name_clean).lower().split()
    tag_hint = ", ".join(w for w in stem_words[:4] if len(w) > 3)

    graph_context = _build_graph_context(file_info, kb_path)

    prompt = _PROMPT_DOCUMENT.format(
        name=name,
        name_clean=name_clean,
        word_count=word_count,
        tags=tag_hint,
        date=datetime.now().strftime("%Y-%m-%d"),
        text=text,
        graph_context=graph_context,
    )
    return _stream_message(backend, model, prompt, max_tokens=3000)


def _append_to_index(kb_path: str, articles_dir: str, kb_name: str):
    """Append new articles to a simple alphabetical index (no LLM cost).

    Writes _index.md as a plain list grouped by first letter.  Kept
    deliberately simple — the LLM-powered index (--index) handles
    thematic clustering, concept maps, and cross-references.
    """
    import os, re
    from datetime import datetime

    index_path = os.path.join(kb_path, "wiki", "_index.md")

    articles = []
    for fname in sorted(os.listdir(articles_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(articles_dir, fname)
        text = read_text(path)
        # Extract title from YAML frontmatter
        m = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
        title = m.group(1).strip() if m else fname[:-3]
        articles.append((fname[:-3], title))

    lines = [
        f"# {kb_name} — Article Index",
        f"",
        f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        f"",
        f"**{len(articles)} articles** compiled.",
        f"",
        f"Run `compile-llm --index` for a thematic index with concept maps.",
        f"",
    ]

    current_letter = ""
    for slug, title in articles:
        first = title[0].upper() if title else "#"
        if first != current_letter:
            current_letter = first
            lines.append(f"## {current_letter}")
            lines.append("")
        lines.append(f"- [[{slug}|{title}]]")

    write_text(index_path, "\n".join(lines))


def _compile_index(backend, model: str, articles_dir: str) -> str:
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
    return _stream_message(backend, model, prompt, max_tokens=4096)


def _compile_concepts(backend, model: str, articles_dir: str, n: int = 20) -> list:
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
    raw = _stream_message(backend, model, prompt, max_tokens=8192)

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

    # ── Graphify check (pipeline-aware) ──────────────────────────────────
    # Only block if graphify-first pipeline AND graph hasn't run yet.
    # compile-first pipeline: graphify comes after compile, so skip this guard.
    if not getattr(args, 'skip_graphify_check', False):
        config = load_config(kb_path)
        pipeline_default = config.get("pipeline", {}).get("default", "graphify-first")

        # Check if any completed files are graphify-first type
        from core.registry import detect_pipeline
        indexer = IndexManager(kb_path)
        completed = indexer.get_completed_files()
        needs_graphify = False
        for f in completed:
            if detect_pipeline(f.get("path", ""), config) == "graphify-first":
                needs_graphify = True
                break

        graph_edges = os.path.join(kb_path, "wiki", "graphify-out", "edges.jsonl")
        if needs_graphify and not os.path.exists(graph_edges):
            print("")
            print("⚠️  Knowledge graph not found (wiki/graphify-out/edges.jsonl).")
            print("   Some documents use the graphify-first pipeline.")
            print("   Run 'graphify' first, then re-run compile.")
            print("   Or skip this check with: --skip-graphify-check")
            sys.exit(1)

    # ── Lock file to prevent concurrent compile (atomic O_CREAT|O_EXCL) ────
    lock_file = os.path.join(kb_path, "wiki", "_meta", ".compile.lock")
    ensure_dir(os.path.dirname(lock_file))

    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(lock_fd, str(os.getpid()).encode())
        os.close(lock_fd)
        lock_fd = None
    except FileExistsError:
        # Lock exists — check if the owning process is still alive
        try:
            pid = int(read_text(lock_file).strip())
            os.kill(pid, 0)  # raises ProcessLookupError if dead
            # Verify it's actually a compile-llm process
            import subprocess as _sp
            result = _sp.run(["ps", "-p", str(pid), "-o", "args="],
                             capture_output=True, text=True, timeout=3)
            if "cli.py" in result.stdout or "kb" in result.stdout:
                print(f"❌  Another compile-llm is running (PID {pid}).")
                print(f"   If stale, delete: {lock_file}")
                return
            # PID reused by unrelated process — steal the lock
            os.unlink(lock_file)
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(lock_fd, str(os.getpid()).encode())
            os.close(lock_fd)
            lock_fd = None
        except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
            # Stale lock — remove and re-create
            try:
                os.unlink(lock_file)
            except OSError:
                pass
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(lock_fd, str(os.getpid()).encode())
            os.close(lock_fd)
            lock_fd = None
        except Exception:
            pass
    finally:
        if lock_fd is not None:
            os.close(lock_fd)

    try:
        _cmd_compile_llm_inner(args, kb_path)
    finally:
        # Only unlink if we own the lock
        try:
            stored = int(read_text(lock_file).strip())
            if stored == os.getpid():
                os.unlink(lock_file)
        except (OSError, ValueError):
            pass


def _cmd_compile_llm_inner(args, kb_path: str):
    """Body of cmd_compile_llm, extracted for lock-file wrapping."""
    config = _validate_config(load_config(kb_path))
    articles_dir = os.path.join(kb_path, "wiki", "_articles")
    concepts_dir = os.path.join(kb_path, "wiki", "_concepts")
    ensure_dir(articles_dir)
    ensure_dir(concepts_dir)

    # Determine which steps to run
    explicit = args.docs or args.index or args.concepts
    run_docs     = args.docs     or not explicit
    run_index    = args.index    or not explicit
    run_concepts = args.concepts or not explicit

    backend, model, llm_config = _llm_client(args.model, getattr(args, 'backend', None))

    # Auxiliary model for simpler tasks (index, concepts)
    _MODEL_AUX = llm_config["aux_model"]

    print(f"📚  {config.get('name', 'Knowledge Base')}")
    print(f"🤖  Backend: {backend} / {model}")
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
                # Clear all previous compile results so this run starts fresh
                for f in completed:
                    fid = indexer.generate_file_id(f["path"])
                    entry = indexer.index["files"].get(fid, {})
                    for key in ("compile_failed_at", "compile_error", "llm_compiled_at",
                                "retrieval_queries", "llm_metadata", "chunks"):
                        entry.pop(key, None)
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

            # --file: target a single document by name fragment
            file_filter = getattr(args, 'file', None)
            if file_filter:
                before = len(pending)
                pending = [
                    f for f in pending
                    if file_filter.lower() in f.get("name", "").lower()
                ]
                if not pending:
                    print(f"\n⚠️  No pending documents matched --file '{file_filter}' "
                          f"(searched {before} candidates)")
                    run_docs = False
                else:
                    print(f"  Filter '--file {file_filter}': {len(pending)} match(es)")

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

            # Apply --limit if set
            doc_limit = getattr(args, 'limit', 0) or 0
            if doc_limit > 0 and len(pending) > doc_limit:
                print(f"   Batch limit      : {doc_limit} (of {len(pending)} pending)")
                pending = pending[:doc_limit]

            if pending:
                est = _estimate_cost(pending, model)
                print(f"   Est. cost       : ~${est:.2f}")  # always shown (M11)

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
                            backend, model, file_info, kb_path
                        )

                        if error:
                            print(f"\n   ❌ {error[:120]}")
                            # Only mark permanently failed for non-transient errors.
                            # Transient errors (rate limits, timeouts, network) leave
                            # the doc as uncompiled so the next normal run retries it.
                            is_transient = (
                                "transient" in error.lower()
                                or "429" in error
                                or "timeout" in error.lower()
                                or "rate" in error.lower()
                                or "network" in error.lower()
                            )
                            if not is_transient:
                                indexer.index["files"][file_id]["compile_failed_at"] = \
                                    datetime.now().isoformat()
                                indexer.index["files"][file_id]["compile_error"] = \
                                    error[:200]
                                indexer.save_index()
                            else:
                                print(f"   ⚡ Transient error — will retry on next run")
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

    # ── After docs: append to non-LLM listing (fast, no token cost) ──
    # Full LLM index is expensive (sends all article summaries) — only on --index
    if run_docs and not run_index and not getattr(args, 'no_index', False):
        _append_to_index(kb_path, articles_dir, config.get('name', 'KB'))
        print(f"\n   (updated _index.md; pass --index for full LLM rebuild)")

    # ── Step 2: regenerate index ───────────────────────────────────────────
    if run_index:
        print(f"\n── Index ───────────────────────────────────────────────")
        print("   Writing _index.md ", end="", flush=True)

        index_text = _compile_index(backend, _MODEL_AUX, articles_dir)
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

            concept_articles = _compile_concepts(backend, _MODEL_AUX, articles_dir, n=n)
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

    # Validate all shell-interpolated arguments to prevent injection (C3)
    _validate_deploy_arg("host", host)
    _validate_deploy_arg("remote-user", remote_uid)
    _validate_deploy_arg("kb-id", kb_id)
    if ssh_user:
        _validate_deploy_arg("ssh-user", ssh_user)
    if key_path:
        if not os.path.isfile(key_path):
            print(f"❌ SSH key not found: {key_path}")
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
        rsync_cmd += ["-e", f"ssh -i {shlex.quote(key_path)}"]

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

    # Save updated index atomically (H6)
    try:
        import tempfile as _tempfile2
        tmp_fd, tmp_path = _tempfile2.mkstemp(
            dir=os.path.dirname(index_file), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                _json.dump(index_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, index_file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"❌ Failed to save index: {e}")
        return

    print(f"✅ Cleaned {deleted_entries} index entries")
    if deleted_extracted:
        print(f"   Removed {deleted_extracted} extracted text file(s)")
    if deleted_articles:
        print(f"   Removed {deleted_articles} wiki article(s)")


def cmd_skip(args):
    """Mark a document as permanently skipped in both ContentRegistry and IndexManager.

    Finds the document by partial filename match. All non-DONE pipeline stages
    are set to STATUS_SKIPPED so compile-llm and graphify won't attempt them.
    The IndexManager entry is also marked 'skipped' so ingest retries skip it.

    Usage: kb skip <name-fragment>
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found")
        return

    from core.registry import ContentRegistry, STATUS_DONE, STATUS_FAILED, STATUS_PENDING, STATUS_SKIPPED

    pattern = args.file.lower()
    reg     = ContentRegistry(kb_path)
    indexer = IndexManager(kb_path)

    # Find matching entries in ContentRegistry
    matches = [
        (key, entry) for key, entry in reg.data["entries"].items()
        if pattern in os.path.basename(entry.get("source_path", "")).lower()
    ]

    if not matches:
        # Fallback: search IndexManager for entries not yet in registry
        index_matches = [
            f for f in indexer.get_all_files()
            if pattern in (f.get("name") or "").lower()
        ]
        if not index_matches:
            print(f"❌ No documents found matching '{args.file}'")
            return
        print(f"⚠️  Found {len(index_matches)} match(es) only in IndexManager (not yet in registry):")
        for f in index_matches[:5]:
            print(f"   • {f.get('name', '?')}")
        print("   Run 'kb ingest' first to register these files, then skip them.")
        return

    if len(matches) > 1:
        print(f"⚠️  '{args.file}' matched {len(matches)} documents — be more specific:")
        for key, entry in matches[:8]:
            print(f"   • {os.path.basename(entry.get('source_path', key))}")
        return

    key, entry = matches[0]
    name = os.path.basename(entry.get("source_path", key))

    # Mark non-DONE stages as SKIPPED in registry
    changed_stages = []
    for stage in ("ingest", "compile_llm", "graphify"):
        stage_info = entry.get(stage, {})
        if stage_info.get("status") != STATUS_DONE:
            reg.set_stage(key, stage, STATUS_SKIPPED)
            changed_stages.append(stage)
    reg.save()

    # Mark IndexManager entry as skipped
    fid = indexer.generate_file_id(entry.get("source_path", key)
                                   if os.path.isabs(entry.get("source_path", ""))
                                   else os.path.join(kb_path, entry.get("source_path", key)))
    if fid in indexer.index.get("files", {}):
        indexer.index["files"][fid]["status"] = "skipped"
        indexer.save_index()
    else:
        print(f"   ⚠️  Note: document not found in IndexManager (file_index.json).")
        print(f"      Only ContentRegistry was updated. Run 'kb ingest' if the file was")
        print(f"      recently added, then run 'kb skip {args.file}' again.")

    print(f"⏭️  Skipped: {name}")
    if changed_stages:
        print(f"   Stages marked skipped: {', '.join(changed_stages)}")
    else:
        print("   (all stages were already done — nothing to skip)")
    print(f"\n   To undo: kb unskip '{args.file}'")


def cmd_unskip(args):
    """Restore a previously skipped document to pending/retry state.

    Resets STATUS_SKIPPED stages back to STATUS_PENDING in ContentRegistry
    and restores the IndexManager status so ingest + compile will process it.

    Usage: kb unskip <name-fragment>
    """
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found")
        return

    from core.registry import ContentRegistry, STATUS_DONE, STATUS_PENDING, STATUS_SKIPPED

    pattern = args.file.lower()
    reg     = ContentRegistry(kb_path)
    indexer = IndexManager(kb_path)

    matches = [
        (key, entry) for key, entry in reg.data["entries"].items()
        if pattern in os.path.basename(entry.get("source_path", "")).lower()
    ]

    if not matches:
        print(f"❌ No documents found matching '{args.file}'")
        return

    if len(matches) > 1:
        print(f"⚠️  '{args.file}' matched {len(matches)} documents — be more specific:")
        for key, entry in matches[:8]:
            print(f"   • {os.path.basename(entry.get('source_path', key))}")
        return

    key, entry = matches[0]
    name = os.path.basename(entry.get("source_path", key))

    # Reset SKIPPED stages back to PENDING
    reset_stages = []
    for stage in ("ingest", "compile_llm", "graphify"):
        stage_info = entry.get(stage, {})
        if stage_info.get("status") == STATUS_SKIPPED:
            reg.set_stage(key, stage, STATUS_PENDING)
            reset_stages.append(stage)
    reg.save()

    # Reset IndexManager status to "error" so --retry-failed or --full picks it up
    src = entry.get("source_path", key)
    abs_src = src if os.path.isabs(src) else os.path.join(kb_path, src)
    fid = indexer.generate_file_id(abs_src)
    if fid in indexer.index.get("files", {}):
        current_status = indexer.index["files"][fid].get("status", "pending")
        if current_status == "skipped":
            # Reset to "error" so --retry-failed picks it up, or "pending" if never completed
            completed_at = indexer.index["files"][fid].get("completed_at")
            indexer.index["files"][fid]["status"] = "error" if completed_at else "pending"
        indexer.save_index()

    print(f"✅ Unskipped: {name}")
    if reset_stages:
        print(f"   Stages reset to pending: {', '.join(reset_stages)}")
        print(f"\n   Run: kb ingest --retry-failed && kb compile-llm --retry-failed")
    else:
        print("   (no skipped stages found — was it already unskipped?)")


def _progress_bar(done: int, total: int, width: int = 20) -> str:
    """Return a filled/empty block progress bar string."""
    if total == 0:
        return "░" * width
    filled = int(width * done / total)
    return "█" * filled + "░" * (width - filled)


def print_kb_status(kb_path: str, header: bool = True) -> None:
    """Print a structured pipeline status report for a knowledge base.

    Reads from ContentRegistry (.kbregistry.json) for per-stage counts and
    from IndexManager for ingest-level detail. Safe to call with no registry
    (falls back gracefully to index-only stats).
    """
    from core.registry import (ContentRegistry,
                                STATUS_DONE, STATUS_FAILED, STATUS_PENDING, STATUS_SKIPPED)

    config = load_config(kb_path)
    name   = config.get("name", os.path.basename(kb_path))
    width  = 46

    if header:
        print(f"\n📚 {name}")
        print("─" * width)

    # ── Registry stats (ingest / graphify / compile_llm per document) ──
    reg = ContentRegistry(kb_path)
    entries = list(reg.data.get("entries", {}).values())
    total = len(entries)

    if total == 0:
        # Fall back to IndexManager (file_index.json) when .kbregistry.json is empty.
        # This handles KBs built with the legacy pipeline that writes file_index.json
        # directly rather than going through ContentRegistry.
        indexer = IndexManager(kb_path)
        s = indexer.get_stats()
        n = s["total"]
        if n == 0:
            print(f"  0 documents  (run kb add or kb ingest to get started)")
            print("─" * width)
            return

        ing  = s["completed"]
        cmp  = s["compiled"]
        # Graphify: check for graph.json output
        graph_file = os.path.join(kb_path, "wiki", "graphify-out", "graph.json")
        if not os.path.exists(graph_file):
            graph_file = os.path.join(kb_path, "wiki", "_articles", "graphify-out", "graph.json")
        if os.path.exists(graph_file):
            try:
                import json as _json
                g = _json.loads(open(graph_file).read())
                grf_nodes = len(g.get("nodes", []))
                grf_info  = f"  ✅  ({grf_nodes} nodes)"
            except Exception:
                grf_info = "  ✅"
        else:
            grf_info = ""

        print(f"  {n} document{'s' if n != 1 else ''}  (legacy pipeline — file_index.json)\n")
        print(f"  Ingest      {_progress_bar(ing, n)}  {ing}/{n}{'  ✅' if ing == n else ''}")
        grf_done = 1 if grf_info else 0
        print(f"  Graphify    {_progress_bar(grf_done, 1)}  {'done' if grf_info else 'pending'}{grf_info}")
        print(f"  Compile LLM {_progress_bar(cmp, n)}  {cmp}/{n}{'  ✅' if cmp == n else ''}")
        print("─" * width)
        return

    def _count(stage, status):
        return sum(1 for e in entries
                   if e.get(stage, {}).get("status") == status)

    ing_done  = _count("ingest",      STATUS_DONE)
    ing_fail  = _count("ingest",      STATUS_FAILED)
    ing_skip  = _count("ingest",      STATUS_SKIPPED)
    grf_done  = _count("graphify",    STATUS_DONE)
    grf_fail  = _count("graphify",    STATUS_FAILED)
    cmp_done  = _count("compile_llm", STATUS_DONE)
    cmp_fail  = _count("compile_llm", STATUS_FAILED)

    # Effective total for progress bars excludes globally-skipped docs
    # (a doc is "globally skipped" when ALL three stages are skipped)
    n_all_skipped = sum(
        1 for e in entries
        if all(e.get(s, {}).get("status") == STATUS_SKIPPED
               for s in ("ingest", "compile_llm", "graphify"))
    )
    effective = total - n_all_skipped

    suffix = f"  ({n_all_skipped} skipped)" if n_all_skipped else ""
    print(f"  {total} document{'s' if total != 1 else ''}{suffix}\n")

    # ── Stage progress bars ──
    stages = [
        ("Ingest     ", ing_done, ing_fail, ing_skip, effective),
        ("Graphify   ", grf_done, grf_fail, 0,        effective),
        ("Compile LLM", cmp_done, cmp_fail, 0,        effective),
    ]
    for label, done, fail, skip, tot in stages:
        bar  = _progress_bar(done, tot)
        pct  = f"{done}/{tot}"
        tick = "  ✅" if done == tot and tot > 0 else ""
        warn = f"  ❌ {fail} failed" if fail else ""
        skp  = f"  ⏭ {skip} skipped" if skip else ""
        print(f"  {label}  {bar}  {pct}{tick}{warn}{skp}")

    # ── Failed documents ──
    failures = []
    for e in entries:
        name_short = os.path.basename(e.get("source_path", "?"))
        for stage in ("ingest", "compile_llm", "graphify"):
            s = e.get(stage, {})
            if s.get("status") == STATUS_FAILED:
                err = (s.get("error") or "unknown error")[:70]
                failures.append((name_short, stage.replace("_", " "), err))

    if failures:
        print(f"\n  ❌ Failed ({len(failures)}):")
        for fname, stage, err in failures[:10]:
            print(f"     • {fname[:45]:<45}  [{stage}] {err}")
        if len(failures) > 10:
            print(f"     … and {len(failures) - 10} more")
        print(f"     → kb compile-llm --retry-failed  |  kb skip <name>")

    # ── Skipped documents (all-stage skips only) ──
    if n_all_skipped:
        skipped_names = [
            os.path.basename(e.get("source_path", "?"))
            for e in entries
            if all(e.get(s, {}).get("status") == STATUS_SKIPPED
                   for s in ("ingest", "compile_llm", "graphify"))
        ]
        print(f"\n  ⏭  Skipped ({n_all_skipped}):")
        for sname in skipped_names[:5]:
            print(f"     • {sname}")
        if len(skipped_names) > 5:
            print(f"     … and {len(skipped_names) - 5} more")
        print(f"     → kb unskip <name>  to restore")

    # ── "What to run next" recommendation ──
    suggestions = []
    if ing_done < effective:
        suggestions.append("kb ingest")
    if ing_done > 0 and grf_done < ing_done:
        suggestions.append("kb graphify")
    if ing_done > 0 and cmp_done < ing_done:
        suggestions.append("kb compile-llm --docs")
    if cmp_fail or ing_fail:
        suggestions.append("kb compile-llm --retry-failed")

    if suggestions:
        print(f"\n  ▶  Next:  {' && '.join(suggestions[:2])}")

    print("─" * width)


def cmd_status(args):
    """Show knowledge base pipeline status"""
    kb_path = get_kb_path()
    if not kb_path:
        print("❌ Error: Knowledge base config not found")
        return
    config = load_config(kb_path)
    print(f"📁 {kb_path}  (v{config.get('version', '1.0')})")
    print_kb_status(kb_path)


def main():
    """Main entry"""
    parser = argparse.ArgumentParser(
        description="Knowledge Base Builder Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python src/cli.py init ~/my-kb --name "My KB"
  python src/cli.py add book.pdf article.md
  python src/cli.py ingest
  python src/cli.py ingest --retry-failed
  python src/cli.py ingest --file report.pdf
  python src/cli.py compile-llm --docs
  python src/cli.py compile-llm --file report.pdf
  python src/cli.py graphify
  python src/cli.py skip broken.pdf
  python src/cli.py unskip broken.pdf
  python src/cli.py status
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    init_parser = subparsers.add_parser('init', help='Initialize knowledge base')
    init_parser.add_argument('folder_path', nargs='?', help='Knowledge base directory path (uses $KB_ROOT/{name} if omitted)')
    init_parser.add_argument('--name', '-n', help='Knowledge base name')
    init_parser.add_argument('--force', action='store_true',
                             help='Overwrite existing .kbaconfig (WARNING: destroys custom config)')

    ingest_parser = subparsers.add_parser('ingest', help='Ingest documents')
    ingest_parser.add_argument('--full', '-f', action='store_true',
                               help='Full re-ingest (not incremental)')
    ingest_parser.add_argument('--retry-failed', action='store_true',
                               help='Retry documents that failed in a previous ingest run')
    ingest_parser.add_argument('--file', metavar='NAME',
                               help='Process only files whose name contains NAME (case-insensitive)')

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
    # add: one-command content addition with auto-pipeline
    # ------------------------------------------------------------------
    add_parser = subparsers.add_parser(
        'add',
        help='Add files to KB with automatic pipeline (ingest → compile → graphify)',
        description=(
            'Copy files into raw/, detect the optimal pipeline, and run '
            'ingest → compile-llm → graphify in the correct order.\n\n'
            'Pipelines (auto-detected, override with --pipeline):\n'
            '  graphify-first  PDF/EPUB: ingest → graphify → compile-llm\n'
            '  compile-first   MD/TXT:  ingest → compile-llm → graphify\n'
            '  none            Ingest only, skip graphify and compile'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  kb add book.pdf                    # auto: graphify-first pipeline
  kb add article.md                  # auto: compile-first pipeline
  kb add book.pdf --pipeline compile-first   # force pipeline
  kb add essay.md --no-graphify             # skip graphify
  kb add --batch docs/*.pdf --yes          # batch mode, no prompt
        """
    )
    add_parser.add_argument(
        'sources', nargs='+',
        help='Source files to add (absolute or relative paths)'
    )
    add_parser.add_argument(
        '--pipeline', choices=['graphify-first', 'compile-first', 'none'],
        help='Force a specific pipeline (default: auto-detect)'
    )
    add_parser.add_argument(
        '--no-ingest', action='store_true',
        help='Skip ingest step'
    )
    add_parser.add_argument(
        '--no-compile', action='store_true',
        help='Skip compile-llm step'
    )
    add_parser.add_argument(
        '--no-graphify', action='store_true',
        help='Skip graphify step'
    )
    add_parser.add_argument(
        '--model',
        help='LLM model override for compile step'
    )
    add_parser.add_argument(
        '--yes', '-y', action='store_true',
        help='Skip confirmation prompt'
    )

    # ------------------------------------------------------------------
    # graphify: build knowledge graph from wiki articles
    # ------------------------------------------------------------------
    graphify_parser = subparsers.add_parser(
        'graphify',
        help='Build knowledge graph from compiled wiki articles',
        description=(
            'Run Graphify on wiki/ to build a knowledge graph with nodes, '
            'edges, communities, and interactive HTML visualization.'
        ),
        epilog="""Examples:
  kb graphify        # build graph from wiki/ articles
        """
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
  python src/cli.py compile-llm --backend deepseek            # use DeepSeek
  python src/cli.py compile-llm --backend openai --model gpt-4o-mini

Environment:
  Auto-detects backend from available API keys:
    ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY,
    MOONSHOT_API_KEY, ZHIPU_API_KEY, MINIMAX_API_KEY, GEMINI_API_KEY
  LLM_PROVIDER  (deprecated) — use --backend instead
        """
    )
    compile_llm_parser.add_argument(
        '--backend', default=None,
        choices=['claude', 'openai', 'deepseek', 'kimi', 'zhipu', 'minimax', 'gemini'],
        help='LLM backend (default: auto-detect from available API keys)'
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
    compile_llm_parser.add_argument(
        '--limit', type=int, default=0, metavar='N',
        help='Max number of documents to compile (0 = unlimited, default: 0)'
    )
    compile_llm_parser.add_argument(
        '--skip-graphify-check', action='store_true',
        help='Skip the knowledge graph existence check (not recommended)'
    )
    compile_llm_parser.add_argument(
        '--file', metavar='NAME',
        help='Compile only documents whose filename contains NAME (case-insensitive)'
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
    # skip / unskip: permanently exclude a document from the pipeline
    # ------------------------------------------------------------------
    skip_parser = subparsers.add_parser(
        'skip',
        help='Permanently skip a document so ingest/compile/graphify ignore it',
        description=(
            'Mark a document as skipped in both the ContentRegistry and IndexManager. '
            'Useful for problematic files (corrupt PDFs, irrelevant docs) you never '
            'want to process. Undo with: kb unskip <name>'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kb skip broken-scan.pdf          # skip by partial filename match
  kb skip "chapter 3"              # skip all docs whose name contains "chapter 3"
        """
    )
    skip_parser.add_argument(
        'file',
        help='Filename fragment to match (case-insensitive, partial match)'
    )

    unskip_parser = subparsers.add_parser(
        'unskip',
        help='Restore a previously skipped document to the pipeline',
        description=(
            'Reset all SKIPPED stages back to PENDING and restore the IndexManager '
            'status so the document will be processed on the next ingest/compile run.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kb unskip broken-scan.pdf        # restore the skipped document
  kb ingest --retry-failed         # then re-process it
        """
    )
    unskip_parser.add_argument(
        'file',
        help='Filename fragment to match (case-insensitive, partial match)'
    )

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
        'add': cmd_add,
        'ingest': cmd_ingest,
        'compile': cmd_compile,
        'compile-llm': cmd_compile_llm,
        'graphify': cmd_graphify,
        'status': cmd_status,
        'fetch': cmd_fetch,
        'fetch-list': cmd_fetch_list,
        'harvest': cmd_harvest,
        'deploy': cmd_deploy,
        'lint': cmd_lint,
        'search': cmd_search,
        'clean': cmd_clean,
        'skip': cmd_skip,
        'unskip': cmd_unskip,
    }
    
    command_func = commands.get(args.command)
    if command_func:
        command_func(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
