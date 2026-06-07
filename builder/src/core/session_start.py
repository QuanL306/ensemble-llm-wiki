#!/usr/bin/env python3
"""
session_start.py — SessionStart hook for auto-sync

Borrowed from: ekadetov/llm-wiki + Pratiyush/llm-wiki (SessionStart pattern)
Pattern: On every agent session start, check for new/changed documents
         in raw/ and auto-trigger ingest + compile-llm.

Usage:
  # Called by agent on session start
  python3 session_start.py --kb-path ~/my-kb

  # Called by cron
  python3 session_start.py --kb-path ~/my-kb --cron
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def find_changed_files(raw_dir: Path, manifest_file: Path) -> List[Path]:
    """
    Compare raw/ files against a manifest to find new/modified files.
    Manifest format: {filepath: mtime_hash}
    """
    manifest = {}
    if manifest_file.exists():
        try:
            manifest = json.loads(manifest_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    changed = []
    current = {}

    if not raw_dir.exists():
        return []

    for f in raw_dir.rglob("*"):
        if not f.is_file():
            continue
        # Skip hidden and system files
        if f.name.startswith(".") or f.name.startswith("_"):
            continue
        # Skip already-processed markers
        if f.suffix in (".tmp", ".swp", ".bak"):
            continue

        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue

        fkey = str(f.relative_to(raw_dir))
        current[fkey] = mtime

        prev = manifest.get(fkey)
        if prev is None or prev != mtime:
            changed.append(f)

    # Save new manifest
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(current, indent=2))

    return changed


def run_ingest(kb_path: Path, python: str = "python3") -> bool:
    """Run the builder's ingest command."""
    # cli.py is at builder/src/cli.py (session_start.py is at builder/src/core/session_start.py)
    script_dir = Path(__file__).resolve().parent  # .../builder/src/core/
    builder_cli = script_dir.parent / "cli.py"     # .../builder/src/cli.py

    if not builder_cli.exists():
        print("[session_start] Builder CLI not found, skipping ingest", file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            [python, str(builder_cli), "ingest"],
            cwd=str(kb_path),
            capture_output=True, text=True, timeout=900,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[session_start] Ingest failed: {e}", file=sys.stderr)
        return False


def run_compile(kb_path: Path, python: str = "python3") -> bool:
    """Run LLM-powered compilation if any backend API key is available."""
    # Ensure core modules are importable (same pattern as run_confidence_scoring)
    core_root = Path(__file__).resolve().parent.parent  # builder/src/
    if str(core_root) not in sys.path:
        sys.path.insert(0, str(core_root))

    from core.llm import list_available, detect_backend

    if not list_available():
        print("[session_start] No LLM API keys — skipping compile-llm")
        return False

    try:
        from core.compiler import WikiCompiler
        compiler = WikiCompiler(str(kb_path))
        result = compiler.compile_with_llm(fallback=True)
        print(f"[session_start] compile-llm: {result['concepts_total']} concepts, "
              f"backend={result.get('backend', 'unknown')}")
        return True
    except Exception as e:
        print(f"[session_start] Compile failed: {e}", file=sys.stderr)
        return False


def run_confidence_scoring(kb_path: Path, python: str = "python3") -> bool:
    """Run confidence scoring on the KB."""
    try:
        # Import and run confidence scoring
        core_dir = Path(__file__).parent
        if str(core_dir) not in sys.path:
            sys.path.insert(0, str(core_dir))

        from confidence import score_all_documents, compute_cross_references

        index_path = kb_path / "wiki" / "_meta" / "file_index.json"
        if not index_path.exists():
            return False

        cross_refs = compute_cross_references(kb_path)
        scores = score_all_documents(index_path, cross_refs)
        return len(scores) > 0
    except Exception as e:
        print(f"[session_start] Confidence scoring failed: {e}", file=sys.stderr)
        return False


def run_exports(kb_path: Path, python: str = "python3") -> bool:
    """Regenerate AI-consumable exports."""
    try:
        core_dir = Path(__file__).parent
        if str(core_dir) not in sys.path:
            sys.path.insert(0, str(core_dir))

        from exports import generate_all_exports
        result = generate_all_exports(kb_path)
        return len(result) > 0
    except Exception as e:
        print(f"[session_start] Exports failed: {e}", file=sys.stderr)
        return False


def run_graphify_on_kb(kb_path: Path, python: str = "python3") -> bool:
    """Run Graphify on the KB's wiki/ directory (headless subprocess path).
    
    For interactive use, call graphify --mcp directly instead.
    This function exists for cron/auto-sync where no agent is available."""
    wiki_dir = kb_path / "wiki"
    if not wiki_dir.exists():
        return False

    graphify_out = wiki_dir / "graphify-out"
    graphify_out.mkdir(parents=True, exist_ok=True)

    # Use graphify_integration module
    try:
        core_dir = Path(__file__).parent
        if str(core_dir) not in sys.path:
            sys.path.insert(0, str(core_dir))

        from graphify_integration import run_graphify, generate_jsonld, split_edges

        success = run_graphify(wiki_dir, "standard", python)
        if success:
            graph_json = graphify_out / "graph.json"
            if graph_json.exists():
                generate_jsonld(graph_json, graphify_out / "graph.jsonld", kb_path.name)
                split_edges(graph_json, graphify_out)
        return success
    except Exception as e:
        print(f"[session_start] Graphify failed: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="SessionStart auto-sync hook")
    parser.add_argument("--kb-path", nargs='?', help="Knowledge base path (omit with --all to scan $KB_ROOT)")
    parser.add_argument("--all", action="store_true", help="Process all knowledge bases under $KB_ROOT")
    parser.add_argument("--cron", action="store_true", help="Cron mode (no user interaction)")
    parser.add_argument("--no-compile", action="store_true", help="Skip LLM compilation")
    args = parser.parse_args()

    if args.all:
        kb_root = os.environ.get('KB_ROOT')
        if not kb_root:
            print("[session_start] ❌ --all requires KB_ROOT environment variable", file=sys.stderr)
            return 1
        kb_root = Path(os.path.expanduser(kb_root)).resolve()
        kb_dirs = find_all_kbs(kb_root)
        if not kb_dirs:
            print(f"[session_start] No knowledge bases found under {kb_root}")
            return 0
        print(f"[session_start] Found {len(kb_dirs)} knowledge base(s) under {kb_root}")
        results = []
        for kb_path in kb_dirs:
            try:
                rc = process_single_kb(kb_path, args)
                results.append((kb_path.name, rc))
            except Exception as e:
                print(f"[session_start] ❌ {kb_path.name} failed: {e}", file=sys.stderr)
                results.append((kb_path.name, 1))
        for name, rc in results:
            status = "✅" if rc == 0 else "❌"
            print(f"[session_start] {status} {name}")
        return 0

    if not args.kb_path:
        print("[session_start] ❌ Either --kb-path or --all is required", file=sys.stderr)
        return 1

    kb_path = Path(args.kb_path).resolve()
    return process_single_kb(kb_path, args)


def find_all_kbs(kb_root: Path) -> list:
    """Find all knowledge bases under kb_root (directories with .kbaconfig)."""
    kbs = []
    if not kb_root.is_dir():
        return kbs
    for entry in sorted(kb_root.iterdir()):
        if entry.is_dir() and (entry / ".kbaconfig").exists():
            kbs.append(entry)
        elif entry.is_symlink() and entry.resolve().is_dir():
            resolved = entry.resolve()
            if (resolved / ".kbaconfig").exists():
                kbs.append(resolved)
    return kbs


def process_single_kb(kb_path: Path, args) -> int:
    """Process a single knowledge base. Returns exit code."""
    raw_dir = kb_path / "raw"
    manifest_file = kb_path / "wiki" / "_meta" / "session_manifest.json"

    print(f"[session_start] {datetime.now().strftime('%H:%M:%S')} — Checking {kb_path.name}")

    # 1. Find changed files
    changed = find_changed_files(raw_dir, manifest_file)
    if not changed:
        print("[session_start] No changes detected")
        # Still run cheap maintenance tasks
        run_graphify_on_kb(kb_path)  # keep graph up to date
        run_exports(kb_path)
        return 0

    print(f"[session_start] {len(changed)} changed file(s):")
    for f in changed[:5]:
        print(f"  - {f.name}")
    if len(changed) > 5:
        print(f"  ... and {len(changed) - 5} more")

    # Detect pipeline from KB config + changed file types
    pipeline = _detect_kb_pipeline(kb_path, changed)
    print(f"[session_start] Pipeline: {pipeline}")

    # 2. Run pipeline in correct order
    import yaml
    config = {}
    config_file = kb_path / ".kbaconfig"
    if config_file.exists():
        try:
            config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    graphify_twice = config.get("pipeline", {}).get("graphify_twice", False)

    if pipeline == "graphify-first":
        print("[session_start] Running ingest...")
        run_ingest(kb_path)
        print("[session_start] Building knowledge graph (Graphify)...")
        run_graphify_on_kb(kb_path)
        if not args.no_compile:
            print("[session_start] Running compile-llm...")
            run_compile(kb_path)
        if graphify_twice and not args.no_compile:
            print("[session_start] Rebuilding knowledge graph (Graphify, pass 2)...")
            run_graphify_on_kb(kb_path)
    elif pipeline == "compile-first":
        print("[session_start] Running ingest...")
        run_ingest(kb_path)
        if not args.no_compile:
            print("[session_start] Running compile-llm...")
            run_compile(kb_path)
        print("[session_start] Building knowledge graph (Graphify)...")
        run_graphify_on_kb(kb_path)
    else:  # "none"
        print("[session_start] Running ingest...")
        run_ingest(kb_path)

    # 5. Confidence scoring
    print("[session_start] Running confidence scoring...")
    run_confidence_scoring(kb_path)

    # 6. Exports
    print("[session_start] Regenerating exports...")
    run_exports(kb_path)

    print(f"[session_start] ✅ Done at {datetime.now().strftime('%H:%M:%S')}")
    return 0


def _detect_kb_pipeline(kb_path: Path, changed_files: list) -> str:
    """Determine the effective pipeline for this KB based on config and files.

    Priority:
      1. If all changed files match a single config rule → use that pipeline
      2. If most files are pre-structured (MD) → compile-first
      3. If most files are raw (PDF/EPUB) → graphify-first
      4. Config default
    """
    import yaml
    try:
        from core.registry import detect_pipeline
    except ImportError:
        # Fallback if registry module not importable (e.g., wrong Python path)
        return "graphify-first"

    config = {}
    config_file = kb_path / ".kbaconfig"
    if config_file.exists():
        try:
            config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    # If no changed files, use config default
    if not changed_files:
        default = config.get("pipeline", {}).get("default", "graphify-first")
        return default if default in ("graphify-first", "compile-first", "none") else "graphify-first"

    # Detect per file, pick majority
    counts = {"graphify-first": 0, "compile-first": 0, "none": 0}
    for f in changed_files:
        p = detect_pipeline(str(f), config)
        counts[p] = counts.get(p, 0) + 1

    return max(counts, key=lambda k: counts[k])


if __name__ == "__main__":
    sys.exit(main())
