#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Index management module
"""

import os
import sys
import json
import hashlib
import shutil
from datetime import datetime
from typing import Dict, List, Any, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_utils import load_json, save_json, get_file_metadata


# Increment this when the file_index.json schema changes in a
# backward-incompatible way.  MCP servers check this on load.
SCHEMA_VERSION = "1.4"


class IndexManager:
    """File index manager"""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.meta_dir = os.path.join(kb_path, "wiki", "_meta")
        self.index_file = os.path.join(self.meta_dir, "file_index.json")

        os.makedirs(self.meta_dir, exist_ok=True)
        self.index = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        """Load index from disk, with fallback to backup on corruption."""
        if os.path.exists(self.index_file):
            try:
                return load_json(self.index_file)
            except (json.JSONDecodeError, ValueError) as e:
                print(
                    f"WARNING: file_index.json is corrupt ({e}); "
                    f"attempting backup restore.",
                    file=sys.stderr,
                )
                bak = self.index_file + ".bak"
                if os.path.exists(bak):
                    try:
                        data = load_json(bak)
                        print("  Restored from .bak backup.", file=sys.stderr)
                        return data
                    except Exception:
                        print("  Backup is also corrupt; starting fresh.",
                              file=sys.stderr)
                else:
                    print("  No backup found; starting fresh.", file=sys.stderr)
            except Exception as e:
                print(
                    f"WARNING: Cannot read file_index.json ({e}); "
                    f"starting fresh.",
                    file=sys.stderr,
                )

        return {
            "schema_version": SCHEMA_VERSION,
            "created": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "files": {}
        }

    def save_index(self):
        """Persist index to disk with backup. Stamps schema_version."""
        # Backup existing file before overwrite
        if os.path.exists(self.index_file):
            bak = self.index_file + ".bak"
            try:
                shutil.copy2(self.index_file, bak)
            except OSError:
                pass  # non-fatal: backup failure shouldn't block save

        self.index["schema_version"] = SCHEMA_VERSION
        self.index["last_updated"] = datetime.now().isoformat()
        save_json(self.index_file, self.index)

    def add_file(self, file_path: str, metadata: Optional[Dict] = None) -> str:
        """Add a file to the index"""
        file_id = self._generate_file_id(file_path)

        file_meta = get_file_metadata(file_path)

        if metadata:
            file_meta.update(metadata)

        file_meta["status"] = "pending"
        file_meta["added_at"] = datetime.now().isoformat()
        file_meta["processed_at"] = None
        file_meta["wiki_path"] = None

        self.index["files"][file_id] = file_meta
        return file_id

    def update_file_status(
        self,
        file_id: str,
        status: str,
        wiki_path: Optional[str] = None,
        error: Optional[str] = None
    ):
        """Update file processing status"""
        if file_id in self.index["files"]:
            self.index["files"][file_id]["status"] = status
            self.index["files"][file_id]["processed_at"] = datetime.now().isoformat()

            if wiki_path:
                self.index["files"][file_id]["wiki_path"] = wiki_path

            if error:
                self.index["files"][file_id]["error"] = error

    def get_file(self, file_id: str) -> Optional[Dict]:
        """Get file info by ID"""
        return self.index["files"].get(file_id)

    def get_all_files(self, status: Optional[str] = None) -> List[Dict]:
        """Get all files, optionally filtered by status"""
        files = self.index["files"].values()

        if status:
            files = [f for f in files if f.get("status") == status]

        return list(files)

    def get_pending_files(self) -> List[Dict]:
        return self.get_all_files("pending")

    def get_completed_files(self) -> List[Dict]:
        return self.get_all_files("completed")

    def check_file_changed(self, file_path: str) -> bool:
        """Return True if file is new or its MD5 hash has changed"""
        file_id = self._generate_file_id(file_path)

        if file_id not in self.index["files"]:
            return True

        try:
            current_meta = get_file_metadata(file_path)
            stored_meta = self.index["files"][file_id]

            return current_meta["hash"] != stored_meta["hash"]
        except Exception:
            return True

    def remove_file(self, file_id: str):
        """Remove a file from the index"""
        if file_id in self.index["files"]:
            del self.index["files"][file_id]

    def get_stats(self) -> Dict[str, int]:
        """Return document count by status"""
        files = self.index["files"].values()

        return {
            "total": len(files),
            "pending": sum(1 for f in files if f.get("status") == "pending"),
            "processing": sum(1 for f in files if f.get("status") == "processing"),
            "completed": sum(1 for f in files if f.get("status") == "completed"),
            "error": sum(1 for f in files if f.get("status") == "error")
        }

    def generate_file_id(self, file_path: str) -> str:
        """Generate stable 16-char file ID from relative path MD5"""
        rel_path = os.path.relpath(file_path, self.kb_path)
        return hashlib.md5(rel_path.encode()).hexdigest()[:16]


    def _generate_file_id(self, file_path: str) -> str:
        """Backward-compatible alias for generate_file_id."""
        return self.generate_file_id(file_path)


class ConceptIndex:
    """Concept index manager"""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.concepts_file = os.path.join(kb_path, "wiki", "_meta", "concepts.json")
        os.makedirs(os.path.dirname(self.concepts_file), exist_ok=True)

        self.concepts = self._load_concepts()

    def _load_concepts(self) -> Dict[str, Any]:
        """Load concept index from disk, with fallback to backup."""
        if os.path.exists(self.concepts_file):
            try:
                return load_json(self.concepts_file)
            except (json.JSONDecodeError, ValueError) as e:
                print(
                    f"WARNING: concepts.json is corrupt ({e}); "
                    f"attempting backup restore.",
                    file=sys.stderr,
                )
                bak = self.concepts_file + ".bak"
                if os.path.exists(bak):
                    try:
                        data = load_json(bak)
                        print("  Restored from .bak backup.", file=sys.stderr)
                        return data
                    except Exception:
                        print("  Backup is also corrupt; starting fresh.",
                              file=sys.stderr)
            except Exception as e:
                print(
                    f"WARNING: Cannot read concepts.json ({e}); "
                    f"starting fresh.",
                    file=sys.stderr,
                )

        return {
            "version": "1.0",
            "concepts": {},
            "last_updated": datetime.now().isoformat()
        }

    def save(self):
        """Persist concept index to disk with backup."""
        if os.path.exists(self.concepts_file):
            bak = self.concepts_file + ".bak"
            try:
                shutil.copy2(self.concepts_file, bak)
            except OSError:
                pass

        self.concepts["last_updated"] = datetime.now().isoformat()
        save_json(self.concepts_file, self.concepts)

    def add_concept(
        self,
        name: str,
        definition: str,
        source_file: str,
        related_concepts: Optional[List[str]] = None
    ):
        """Add or update a concept"""
        name = name.strip()
        
        if name not in self.concepts["concepts"]:
            self.concepts["concepts"][name] = {
                "definition": definition,
                "files": [],
                "related_concepts": related_concepts or [],
                "created_at": datetime.now().isoformat()
            }
        
        if source_file not in self.concepts["concepts"][name]["files"]:
            self.concepts["concepts"][name]["files"].append(source_file)
        
        if related_concepts:
            existing = set(self.concepts["concepts"][name]["related_concepts"])
            existing.update(related_concepts)
            self.concepts["concepts"][name]["related_concepts"] = list(existing)
    
    def get_concept(self, name: str) -> Optional[Dict]:
        """Get a concept by name"""
        return self.concepts["concepts"].get(name)

    def get_all_concepts(self) -> Dict[str, Dict]:
        """Return all concepts"""
        return self.concepts["concepts"]

    def merge_llm_concepts(
        self,
        concepts_data: Dict[str, Dict],
        articles_dir: str = "",
    ) -> int:
        """Merge LLM-discovered [[concept]] links into this index.

        Args:
            concepts_data: Output of ``_extract_wiki_links()``, i.e.
                ``{name: {"count": N, "sources": [...]}}``
            articles_dir: Path to wiki/_articles/ (used to resolve
                source file paths).

        Returns:
            Number of concepts added or updated.
        """
        updated = 0
        for name, data in concepts_data.items():
            name = name.strip()
            if not name or len(name) < 3:
                continue

            # Build source file list from article names
            source_files = []
            for src in data.get("sources", []):
                article_name = src.get("article", "")
                if article_name:
                    source_files.append(article_name)

            # Derive a definition from the first excerpt
            definition = ""
            for src in data.get("sources", []):
                excerpt = src.get("excerpt", "").strip()
                if 20 < len(excerpt) < 300:
                    definition = excerpt
                    break
            if not definition:
                definition = f"Referenced in {data.get('count', 1)} document(s)"

            # Find co-occurring concepts for related_concepts
            related: list = []

            if name not in self.concepts["concepts"]:
                self.concepts["concepts"][name] = {
                    "definition": definition,
                    "files": source_files,
                    "related_concepts": related,
                    "created_at": datetime.now().isoformat(),
                }
                updated += 1
            else:
                existing = self.concepts["concepts"][name]
                # Merge file lists
                for sf in source_files:
                    if sf not in existing["files"]:
                        existing["files"].append(sf)
                        updated += 1
                # Update definition if the existing one is a placeholder
                if existing["definition"].startswith("Extracted from") and definition:
                    existing["definition"] = definition
                    updated += 1

        return updated
