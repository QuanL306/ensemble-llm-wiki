#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Document ingestion module
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_utils import scan_directory, ensure_dir, get_relative_path
from utils.doc_reader import extract_document, is_supported_format
from core.indexer import IndexManager


class DataIngest:
    """Document ingester"""
    
    DEFAULT_FORMATS = ['.pdf', '.epub', '.txt', '.md', '.markdown']
    DEFAULT_IGNORE = ['node_modules/', '.git/', '__pycache__/', '.obsidian/', 'wiki/', 'outputs/']
    
    def __init__(self, kb_path: str, config: Optional[Dict] = None):
        self.kb_path = kb_path
        self.raw_dir = os.path.join(kb_path, "raw")
        self.config = config or {}
        
        self.indexer = IndexManager(kb_path)
        
        self.formats = self.config.get('ingest', {}).get('supported_formats', self.DEFAULT_FORMATS)
        self.ignore = self.config.get('ingest', {}).get('ignore_patterns', self.DEFAULT_IGNORE)
    
    def scan(self, incremental: bool = True) -> Dict[str, List[str]]:
        """Scan raw/ directory and detect new or changed files"""
        result = {
            "new": [],
            "changed": [],
            "unchanged": [],
            "unsupported": []
        }
        
        if not os.path.exists(self.raw_dir):
            ensure_dir(self.raw_dir)
            return result
        
        files = scan_directory(self.raw_dir, self.formats, self.ignore)
        
        for file_meta in files:
            file_path = file_meta["path"]
            
            if not is_supported_format(file_path):
                result["unsupported"].append(file_path)
                continue
            
            if incremental:
                if self.indexer.check_file_changed(file_path):
                    file_id = self.indexer.generate_file_id(file_path)
                    if file_id in self.indexer.index["files"]:
                        result["changed"].append(file_path)
                    else:
                        result["new"].append(file_path)
                else:
                    result["unchanged"].append(file_path)
            else:
                result["new"].append(file_path)
        
        return result
    
    def add_to_index(self, file_path: str, auto_save: bool = True) -> str:
        """Add a file to the index"""
        file_id = self.indexer.add_file(file_path)
        
        if auto_save:
            self.indexer.save_index()
        
        return file_id
    
    def extract_content(self, file_path: str) -> Tuple[str, Dict[str, Any]]:
        """Extract content from a file"""
        wiki_articles_dir = os.path.join(self.kb_path, "wiki", "_articles")
        ensure_dir(wiki_articles_dir)
        
        content, metadata = extract_document(file_path, wiki_articles_dir)
        
        return content, metadata
    
    def process_file(self, file_path: str) -> Dict[str, Any]:
        """Process a single file end-to-end"""
        result = {
            "file_path": file_path,
            "file_id": None,
            "success": False,
            "error": None,
            "metadata": None
        }
        
        try:
            file_id = self.add_to_index(file_path, auto_save=False)
            result["file_id"] = file_id
            
            self.indexer.update_file_status(file_id, "processing")
            
            content, metadata = self.extract_content(file_path)
            
            wiki_dir = os.path.join(self.kb_path, "wiki", "_articles")
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            rel_wiki_path = get_relative_path(
                os.path.join(wiki_dir, f"{base_name}_extracted.txt"),
                self.kb_path
            )
            
            self.indexer.update_file_status(file_id, "completed", rel_wiki_path)

            # Store only lightweight metadata in the index.
            # structured_text is excluded because it can be hundreds of KB per
            # document; at thousands of documents the index JSON becomes
            # unusably large. The full text already lives on disk at wiki_path.
            index_metadata = {k: v for k, v in metadata.items() if k != "structured_text"}
            self.indexer.index["files"][file_id]["extracted_metadata"] = index_metadata
            self.indexer.save_index()
            
            # ── Contradiction detection (from SamurAIGPT) ──
            try:
                from core.contradictions import detect_contradictions, flag_contradictions_in_index
                
                # Get existing document texts for comparison
                existing_docs = {}
                for fid, finfo in self.indexer.index.get("files", {}).items():
                    if fid == file_id:
                        continue
                    if finfo.get("status") != "completed":
                        continue
                    wp = finfo.get("wiki_path", "")
                    if wp:
                        existing_path = os.path.join(self.kb_path, wp)
                        if os.path.exists(existing_path):
                            try:
                                with open(existing_path, encoding="utf-8") as ef:
                                    existing_docs[fid] = ef.read()[:5000]  # first 5KB
                            except Exception:
                                pass
                
                if existing_docs:
                    contradictions = detect_contradictions(content, existing_docs, file_id)
                    if contradictions:
                        flag_contradictions_in_index(
                            Path(self.kb_path) / "wiki" / "_meta" / "file_index.json",
                            file_id,
                            contradictions,
                            list(existing_docs.keys()),
                        )
                        # Reload indexer from disk so in-memory state reflects
                        # the contradiction flags just written by flag_contradictions_in_index,
                        # preventing the next save_index() call from stomping them.
                        self.indexer.index = self.indexer._load_index()
            except Exception as e:
                file_name = os.path.basename(file_path)
                print(f"  ⚠️  Contradiction detection failed for {file_name}: {e}")
            
            result["success"] = True
            result["metadata"] = metadata
            
        except Exception as e:
            error_msg = str(e)
            result["error"] = error_msg
            
            if result["file_id"]:
                self.indexer.update_file_status(result["file_id"], "error", error=error_msg)
                self.indexer.save_index()
        
        return result
    
    def process_all(self, incremental: bool = True) -> List[Dict]:
        """Process all new or changed files"""
        scan_result = self.scan(incremental=incremental)

        to_process = scan_result["new"] + scan_result["changed"]

        results = []
        for file_path in to_process:
            result = self.process_file(file_path)
            results.append(result)

        # Coverage check: ensure no files on disk are missing from the index
        self._validate_coverage()

        return results

    def _is_supported(self, filename: str) -> bool:
        """Check if a filename has a supported extension."""
        return is_supported_format(filename)

    def _validate_coverage(self) -> List[str]:
        """Scan raw/ for files missing from file_index, add them."""
        books_dir = os.path.join(self.kb_path, "raw", "books")
        if not os.path.isdir(books_dir):
            books_dir = self.raw_dir

        # Get all supported files on disk
        disk_files = set()
        for f in os.listdir(books_dir):
            if f.startswith('.'):
                continue
            full = os.path.join(books_dir, f)
            if os.path.islink(full) or os.path.isfile(full):
                if self._is_supported(f):
                    disk_files.add(full)

        # Get indexed files
        indexed_paths = set()
        for fid, finfo in self.indexer.index.get("files", {}).items():
            if isinstance(finfo, dict):
                indexed_paths.add(finfo.get("path", ""))

        # Find missing
        missing = [f for f in disk_files if f not in indexed_paths]
        if missing:
            print(f"[ingest] ⚠️ Coverage check: {len(missing)} files missing from index, auto-adding...")
            for f in missing:
                self.add_to_index(f)
            self.indexer.save_index()
            print(f"[ingest] ✅ Added {len(missing)} missing files to index")
        return missing

    def get_stats(self) -> Dict[str, Any]:
        """Return ingest statistics"""
        scan_result = self.scan(incremental=True)
        index_stats = self.indexer.get_stats()
        
        return {
            "scan": scan_result,
            "index": index_stats
        }
