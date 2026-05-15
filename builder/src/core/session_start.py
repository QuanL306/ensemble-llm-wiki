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
    builder_cli = kb_path.parent / "builder" / "src" / "cli.py"
    if not builder_cli.exists():
        # Try relative to this script
        script_dir = Path(__file__).parent
        builder_cli = script_dir.parent / "src" / "cli.py"

    if not builder_cli.exists():
        print("[session_start] Builder CLI not found, skipping ingest", file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            [python, str(builder_cli), "ingest"],
            cwd=str(kb_path),
            capture_output=True, text=True, timeout=300,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[session_start] Ingest failed: {e}", file=sys.stderr)
        return False


def run_compile(kb_path: Path, python: str = "python3") -> bool:
    """Run compile-llm if ANTHROPIC_API_KEY is set."""
    if "ANTHROPIC_API_KEY" not in os.environ:
        return False

    builder_cli = kb_path.parent / "builder" / "src" / "cli.py"
    if not builder_cli.exists():
        script_dir = Path(__file__).parent
        builder_cli = script_dir.parent / "src" / "cli.py"

    if not builder_cli.exists():
        return False

    try:
        result = subprocess.run(
            [python, str(builder_cli), "compile-llm", "-y"],
            cwd=str(kb_path),
            capture_output=True, text=True, timeout=600,
        )
        return result.returncode == 0
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
    parser.add_argument("--kb-path", required=True, help="Knowledge base path")
    parser.add_argument("--cron", action="store_true", help="Cron mode (no user interaction)")
    parser.add_argument("--no-compile", action="store_true", help="Skip LLM compilation")
    args = parser.parse_args()

    kb_path = Path(args.kb_path).resolve()
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

    # 2. Ingest
    print("[session_start] Running ingest...")
    if not run_ingest(kb_path):
        print("[session_start] ⚠️ Ingest failed, continuing anyway")

    # 3. Graphify — build knowledge graph BEFORE compile-llm
    #    so the graph structure can inform cross-document linking
    print("[session_start] Building knowledge graph (Graphify)...")
    run_graphify_on_kb(kb_path)

    # 4. Compile (if API key available)
    if not args.no_compile:
        print("[session_start] Running compile-llm...")
        run_compile(kb_path)

    # 5. Confidence scoring
    print("[session_start] Running confidence scoring...")
    run_confidence_scoring(kb_path)

    # 6. Exports
    print("[session_start] Regenerating exports...")
    run_exports(kb_path)

    print(f"[session_start] ✅ Done at {datetime.now().strftime('%H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
