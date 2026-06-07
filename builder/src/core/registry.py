#!/usr/bin/env python3
"""Content Registry — track every document through the KB pipeline.

Produces .kbregistry.json at the KB root. Each entry records:
  source_path   — original file location (relative to KB)
  added_at      — when it was added
  file_type     — pdf, epub, md, txt
  pipeline      — graphify-first | compile-first | none
  ingest        — {status, completed_at, error}
  compile_llm   — {status, completed_at, error}
  graphify      — {status, completed_at, error}

This is auto-maintained by `kb add` and `kb ingest`. It is the single source
of truth for "what's in this KB and at what stage." The indexer's
file_index.json is upstream raw data; the registry is the consumer-facing
summary.
"""

import json
import os
import re
import tempfile as _tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any


STATUS_PENDING  = "pending"
STATUS_DONE     = "done"
STATUS_FAILED   = "failed"
STATUS_SKIPPED  = "skipped"


class ContentRegistry:
    """Load, update, and query the KB content registry."""

    def __init__(self, kb_path: str):
        self.kb_path = Path(kb_path).resolve()
        self.path = self.kb_path / ".kbregistry.json"
        self.data: Dict[str, Any] = {"entries": {}, "meta": {}}
        self._load()

    # ── I/O ──────────────────────────────────────────────────────────

    def _load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self.data = {"entries": {}, "meta": {}}
        if "entries" not in self.data:
            self.data["entries"] = {}
        if "meta" not in self.data:
            self.data["meta"] = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via unique temp file (avoids race condition on .tmp name)
        fd, tmp_str = _tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=".kbregistry_",
            suffix=".tmp"
        )
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(json.dumps(self.data, indent=2, ensure_ascii=False))
            tmp.replace(self.path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    # ── Register / Update ────────────────────────────────────────────

    def register(self, rel_path: str, file_type: str, pipeline: str,
                 source: str = "") -> str:
        """Register a new entry. Returns the entry key (rel_path)."""
        key = rel_path
        if key not in self.data["entries"]:
            self.data["entries"][key] = {
                "source_path": rel_path,
                "added_at": datetime.now().isoformat(),
                "file_type": file_type,
                "pipeline": pipeline,
                "source": source,
                "ingest": {"status": STATUS_PENDING},
                "compile_llm": {"status": STATUS_PENDING},
                "graphify": {"status": STATUS_PENDING},
            }
        return key

    def set_stage(self, rel_path: str, stage: str,
                  status: str, error: str = ""):
        """Update a pipeline stage for an entry."""
        entry = self.data["entries"].get(rel_path)
        if not entry:
            return
        if stage not in ("ingest", "compile_llm", "graphify"):
            return
        entry[stage] = {
            "status": status,
            "completed_at": datetime.now().isoformat() if status == STATUS_DONE else None,
            "error": error[:500] if error else "",
        }

    # ── Query ────────────────────────────────────────────────────────

    def get(self, rel_path: str) -> Optional[Dict]:
        return self.data["entries"].get(rel_path)

    def list_by_stage(self, stage: str, status: str = STATUS_PENDING) -> List[Dict]:
        """List entries at a given pipeline stage and status."""
        result = []
        for key, entry in self.data["entries"].items():
            s = entry.get(stage, {})
            if isinstance(s, dict) and s.get("status") == status:
                result.append(entry)
        return result

    def stats(self) -> Dict[str, Any]:
        """Return summary counts."""
        total = len(self.data["entries"])
        counts = {
            "total": total,
            "ingested": sum(1 for e in self.data["entries"].values()
                           if e.get("ingest", {}).get("status") == STATUS_DONE),
            "compiled": sum(1 for e in self.data["entries"].values()
                           if e.get("compile_llm", {}).get("status") == STATUS_DONE),
            "graphed": sum(1 for e in self.data["entries"].values()
                          if e.get("graphify", {}).get("status") == STATUS_DONE),
            "failed": sum(1 for e in self.data["entries"].values()
                         if any(e.get(s, {}).get("status") == STATUS_FAILED
                               for s in ("ingest", "compile_llm", "graphify"))),
        }
        return counts


# ── Pipeline Detection ────────────────────────────────────────────

# File extension → default pipeline
EXT_PIPELINE = {
    ".pdf":     "graphify-first",
    ".epub":    "graphify-first",
    ".md":      "compile-first",
    ".markdown":"compile-first",
    ".txt":     "graphify-first",
}

# Markdown patterns that suggest pre-structured content (→ compile-first)
_STRUCTURED_MD_PATTERNS = [
    re.compile(r"^#{1,3}\s+\S", re.MULTILINE),      # has headings
    re.compile(r"^---\s*$", re.MULTILINE),            # has YAML frontmatter
    re.compile(r"\[\[.+\]\]"),                         # has wikilinks
]


def detect_pipeline(file_path: str, config: Optional[dict] = None) -> str:
    """Determine the best pipeline for a file.

    Priority:
      1. config.pipeline.rules (glob match — highest user intent)
      2. Auto-detect from extension + content structure
      3. config.pipeline.default (fallback, only when auto-detect gives up)
    """
    config = config or {}
    pipeline_cfg = config.get("pipeline", {})

    # 1. Config rules (glob match against basename AND full relative path)
    import fnmatch
    fname = os.path.basename(file_path)
    fpath = file_path  # full path
    for rule in pipeline_cfg.get("rules", []):
        pattern = rule.get("match", "")
        if fnmatch.fnmatch(fname, pattern) or fnmatch.fnmatch(fpath, pattern):
            return rule.get("pipeline", "graphify-first")

    # 2. Auto-detect from extension
    ext = os.path.splitext(file_path)[1].lower()
    if ext in EXT_PIPELINE:
        return EXT_PIPELINE[ext]

    # 3. Content sniffing for unknown extensions
    try:
        p = Path(file_path)
        if p.exists() and p.stat().st_size < 1_000_000:
            text = p.read_text(encoding="utf-8", errors="ignore")[:5000]
            for pat in _STRUCTURED_MD_PATTERNS:
                if pat.search(text):
                    return "compile-first"
    except Exception:
        pass

    # 4. Config default (only as ultimate fallback)
    default = pipeline_cfg.get("default")
    if default in ("graphify-first", "compile-first", "none"):
        return default

    return "graphify-first"


def detect_file_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    type_map = {
        "pdf": "pdf", "epub": "epub",
        "md": "md", "markdown": "md",
        "txt": "txt",
    }
    return type_map.get(ext, "unknown")
