#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File utility functions
"""

import os
import json
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional


def get_file_hash(file_path: str) -> str:
    """Compute MD5 hash of a file"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_file_metadata(file_path: str) -> Dict[str, Any]:
    """Get file metadata (path, name, size, hash, timestamps)"""
    path = Path(file_path)
    stat = path.stat()
    
    return {
        "path": str(path.absolute()),
        "name": path.name,
        "stem": path.stem,
        "suffix": path.suffix.lower(),
        "size": stat.st_size,
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "hash": get_file_hash(file_path)
    }


def scan_directory(
    directory: str,
    extensions: Optional[List[str]] = None,
    ignore_patterns: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Recursively scan a directory and return metadata for all matching files"""
    if extensions:
        extensions = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' 
                     for ext in extensions]
    
    ignore_patterns = ignore_patterns or []
    files = []

    def _is_ignored(path: str) -> bool:
        """Check if a path matches any ignore pattern.

        Patterns ending with '/' match directory names (basename only).
        Patterns without '/' match as path segments (basename).
        """
        basename = os.path.basename(path.rstrip(os.sep))
        for pattern in ignore_patterns:
            pat_stripped = pattern.rstrip('/')
            if not pat_stripped:
                continue
            # Match against the directory/file basename only
            if basename == pat_stripped:
                return True
            # Also match if the pattern appears as a full path segment
            if pat_stripped in path.split(os.sep):
                return True
        return False

    for root, dirs, filenames in os.walk(directory):
        dirs[:] = [d for d in dirs if not _is_ignored(os.path.join(root, d))]

        for filename in filenames:
            file_path = os.path.join(root, filename)

            if _is_ignored(file_path):
                continue
            
            if extensions:
                suffix = Path(filename).suffix.lower()
                if suffix not in extensions:
                    continue
            
            try:
                metadata = get_file_metadata(file_path)
                files.append(metadata)
            except Exception as e:
                print(f"Warning: cannot read {file_path}: {e}")
    
    return sorted(files, key=lambda x: x["path"])


def load_json(file_path: str) -> Dict[str, Any]:
    """Load a JSON file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(file_path: str, data: Dict[str, Any], indent: int = 2):
    """Save data to a JSON file atomically (write-to-temp + os.replace).

    Creates parent dirs as needed.  Uses tempfile + os.replace so a crash
    mid-write never leaves a truncated file.
    """
    parent = os.path.dirname(file_path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(tmp_path, file_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_text(file_path: str) -> str:
    """Read a text file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def write_text(file_path: str, content: str):
    """Write a text file atomically (write-to-temp + os.replace).

    Creates parent dirs as needed.  Uses tempfile + os.replace so a crash
    mid-write never leaves a truncated file.
    """
    parent = os.path.dirname(file_path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tmp_", suffix=".txt")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def ensure_dir(directory: str):
    """Ensure a directory exists, creating it if necessary"""
    os.makedirs(directory, exist_ok=True)


def get_relative_path(file_path: str, base_path: str) -> str:
    """Get path of file_path relative to base_path"""
    return os.path.relpath(file_path, base_path)


def sanitize_filename(filename: str) -> str:
    """Strip illegal filesystem characters from a filename"""
    import re
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:200 - len(ext)] + ext
    return sanitized
