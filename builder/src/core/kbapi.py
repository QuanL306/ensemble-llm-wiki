#!/usr/bin/env python3
"""Knowledge Base API — programmatic interface for KB operations.

Usage:
    from core.kbapi import KnowledgeBase

    kb = KnowledgeBase("~/my-kb")
    kb.add("new_book.pdf")
    kb.add("article.md")
    kb.add("book.pdf", pipeline="graphify-first")
    kb.status()
"""

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Allow importing sibling core modules
_core_dir = Path(__file__).resolve().parent
if str(_core_dir) not in sys.path:
    sys.path.insert(0, str(_core_dir))

from core.registry import (
    ContentRegistry, detect_pipeline, detect_file_type,
    STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED,
)


class KnowledgeBase:
    """Programmatic wrapper around KB pipeline operations."""

    def __init__(self, kb_path: str):
        self.kb_path = Path(kb_path).resolve()
        if not self.kb_path.exists():
            raise FileNotFoundError(f"KB path does not exist: {self.kb_path}")
        self._config_path = self.kb_path / ".kbaconfig"
        self._load_config()
        self.registry = ContentRegistry(str(self.kb_path))

    def _load_config(self):
        self.config = {}
        if self._config_path.exists():
            try:
                import yaml
                self.config = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                self.config = {}
        self.config.setdefault("name", self.kb_path.name)

    # ── The main command ─────────────────────────────────────────────

    def add(
        self,
        source: str,
        pipeline: Optional[str] = None,
        run_ingest: bool = True,
        run_compile: bool = True,
        run_graphify: bool = True,
        model: Optional[str] = None,
    ) -> Dict[str, any]:
        """Add a file to the KB and run the full pipeline.

        source       — path to source file (absolute or relative)
        pipeline     — 'graphify-first' | 'compile-first' | 'none' | None (auto-detect)
        run_ingest   — run ingest after copying
        run_compile  — run compile-llm after ingest
        run_graphify — run graphify after compile (or before, depending on pipeline)
        model        — LLM model override for compile

        Returns dict with keys: status, rel_path, pipeline, stages
        """
        src = Path(source).resolve()
        if not src.exists():
            return {"status": "error", "error": f"Source not found: {source}"}
        if src.is_dir():
            return {"status": "error", "error": f"Source is a directory: {source} — use kb add on individual files"}

        # Determine pipeline
        pipeline = pipeline or detect_pipeline(str(src), self.config)
        file_type = detect_file_type(str(src))

        # Copy into raw/ — place in appropriate subfolder
        dest_dir = self.kb_path / "raw"
        ext = src.suffix.lower()
        if ext in ('.pdf', '.epub'):
            dest_dir = dest_dir / "books"
        elif ext in ('.md', '.markdown'):
            dest_dir = dest_dir / "articles"
        elif ext == '.txt':
            dest_dir = dest_dir / "articles"
        else:
            dest_dir = dest_dir / "articles"
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / src.name
        # Avoid overwrite: append counter if duplicate
        counter = 1
        while dest.exists():
            stem = src.stem
            dest = dest_dir / f"{stem}_{counter}{src.suffix}"
            counter += 1

        shutil.copy2(str(src), str(dest))
        try:
            rel_path = str(dest.relative_to(self.kb_path))
        except ValueError:
            # Different mount points — fall back to raw relative path
            rel_path = str(dest)

        # Register
        self.registry.register(
            rel_path=rel_path,
            file_type=file_type,
            pipeline=pipeline,
            source=str(src),
        )
        self.registry.save()

        result = {
            "status": "ok",
            "rel_path": rel_path,
            "pipeline": pipeline,
            "file_type": file_type,
            "stages": {},
        }

        # ── Run pipeline ────────────────────────────────────────────
        # graphify-first: ingest → graphify → compile → (graphify again if graphify_twice)
        #   (1st graphify on raw extracted text gives compile context)
        #   (2nd graphify on LLM articles picks up wikilinks for higher-confidence edges)
        # compile-first:  ingest → compile → graphify
        #   (MD already has structure — compile creates wiki articles → graphify maps them)
        # none:           ingest only, no graphify or compile

        graphify_twice = self.config.get("pipeline", {}).get("graphify_twice", False)

        if pipeline == "graphify-first":
            if run_ingest:
                ok = self._run_ingest()
                self.registry.set_stage(rel_path, "ingest",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["ingest"] = "done" if ok else "failed"

            if run_graphify:
                ok = self._run_graphify()
                self.registry.set_stage(rel_path, "graphify",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["graphify"] = "done" if ok else "failed"

            if run_compile and run_ingest:
                ok = self._run_compile(model=model)
                self.registry.set_stage(rel_path, "compile_llm",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["compile_llm"] = "done" if ok else "failed"

            if graphify_twice and run_graphify and run_compile:
                self._run_graphify()
                # Update graphify stage timestamp to reflect re-run
                self.registry.set_stage(rel_path, "graphify", STATUS_DONE)

        elif pipeline == "compile-first":
            if run_ingest:
                ok = self._run_ingest()
                self.registry.set_stage(rel_path, "ingest",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["ingest"] = "done" if ok else "failed"

            if run_compile and run_ingest:
                ok = self._run_compile(model=model)
                self.registry.set_stage(rel_path, "compile_llm",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["compile_llm"] = "done" if ok else "failed"

            if run_graphify:
                ok = self._run_graphify()
                self.registry.set_stage(rel_path, "graphify",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["graphify"] = "done" if ok else "failed"

        else:  # "none" — ingest only, no graphify, no compile
            if run_ingest:
                ok = self._run_ingest()
                self.registry.set_stage(rel_path, "ingest",
                    STATUS_DONE if ok else STATUS_FAILED)
                result["stages"]["ingest"] = "done" if ok else "failed"

        self.registry.save()
        return result

    def add_batch(
        self,
        sources: List[str],
        pipeline: Optional[str] = None,
        run_ingest: bool = True,
        run_compile: bool = True,
        run_graphify: bool = True,
        model: Optional[str] = None,
    ) -> Dict[str, any]:
        """Add multiple files. Runs ingest once after all copies, then compile once."""
        results = []
        for src in sources:
            r = self.add(
                source=src,
                pipeline=pipeline,
                run_ingest=False,   # batch: ingest once at end
                run_compile=False,  # batch: compile once at end
                run_graphify=False, # batch: graphify once at end
                model=model,
            )
            results.append(r)

        stages = {}
        graphify_twice = self.config.get("pipeline", {}).get("graphify_twice", False)

        if pipeline == "graphify-first":
            if run_ingest:
                stages["ingest"] = "done" if self._run_ingest() else "failed"
            if run_graphify:
                stages["graphify"] = "done" if self._run_graphify() else "failed"
            if run_compile:
                stages["compile_llm"] = "done" if self._run_compile(model=model) else "failed"
            if graphify_twice and run_graphify and run_compile:
                self._run_graphify()
                stages["graphify"] = "done"  # re-mark after second run
        elif pipeline == "compile-first":
            if run_ingest:
                stages["ingest"] = "done" if self._run_ingest() else "failed"
            if run_compile:
                stages["compile_llm"] = "done" if self._run_compile(model=model) else "failed"
            if run_graphify:
                stages["graphify"] = "done" if self._run_graphify() else "failed"
        else:  # "none"
            if run_ingest:
                stages["ingest"] = "done" if self._run_ingest() else "failed"

        for r in results:
            if r["status"] == "ok":
                for stage, s in stages.items():
                    self.registry.set_stage(r["rel_path"], stage,
                        STATUS_DONE if s == "done" else STATUS_FAILED)
        self.registry.save()

        return {"status": "ok", "results": results, "stages": stages}

    # ── Stage runners ────────────────────────────────────────────────

    def _run_ingest(self) -> bool:
        """Run kb ingest."""
        try:
            from core.ingest import DataIngest
            ingest = DataIngest(str(self.kb_path), self.config)
            scan = ingest.scan(incremental=True)
            to_process = scan["new"] + scan["changed"]
            for fp in to_process:
                ingest.process_file(fp)
            return True
        except Exception as e:
            print(f"[kbapi] ingest failed: {e}", file=sys.stderr)
            return False

    def _run_compile(self, model: Optional[str] = None) -> bool:
        """Run kb compile-llm --docs."""
        try:
            # Build args matching cmd_compile_llm expectations
            from argparse import Namespace
            args = Namespace(
                docs=True, index=False, concepts=False,
                full=False, retry_failed=False,
                model=model, backend=None,
                limit=0, yes=True,  # non-interactive
                skip_graphify_check=True,
                concept_limit=20,   # required by _cmd_compile_llm_inner
                no_index=False,     # required by _cmd_compile_llm_inner
            )
            # _cmd_compile_llm_inner lives in builder/src/cli.py
            cli_dir = Path(__file__).resolve().parent.parent  # builder/src/
            if str(cli_dir) not in sys.path:
                sys.path.insert(0, str(cli_dir))
            import cli as _cli
            _cli._cmd_compile_llm_inner(args, str(self.kb_path))
            return True
        except Exception as e:
            print(f"[kbapi] compile failed: {e}", file=sys.stderr)
            return False

    def _run_graphify(self) -> bool:
        """Run graphify on the KB's wiki directory."""
        wiki_dir = self.kb_path / "wiki"
        if not wiki_dir.exists() or not list(wiki_dir.iterdir()):
            return False

        try:
            from core.graphify_integration import run_graphify
            return run_graphify(wiki_dir, "standard")
        except Exception as e:
            print(f"[kbapi] graphify failed: {e}", file=sys.stderr)
            return False

    # ── Status ───────────────────────────────────────────────────────

    def status(self) -> Dict[str, any]:
        """Return KB summary."""
        reg_stats = self.registry.stats()
        return {
            "name": self.config.get("name", "Unknown"),
            "path": str(self.kb_path),
            "pipeline": {
                "default": self.config.get("pipeline", {}).get("default", "graphify-first"),
                "rules": self.config.get("pipeline", {}).get("rules", []),
            },
            "registry": reg_stats,
        }
