#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Seekers Integration Module — Headless Wrapper

For interactive workflows, use mcp_skill_seeker_* tools directly.
This module wraps the skill-seekers CLI via subprocess for headless
cron / session_start.py auto-sync where no agent context is available.
"""

import os
import sys
import shutil
import subprocess
import tempfile
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_utils import ensure_dir, write_text


class SkillSeekersNotInstalledError(Exception):
    pass


class SkillSeekersFetchError(Exception):
    pass


class SkillSeekersIntegration:
    """
    Integrates Skill Seekers as an enhanced ingestion frontend for KB Builder.

    Workflow:
        source (URL / path / GitHub repo)
            → skill-seekers create
            → SKILL.md + reference .md files
            → raw/skill_seekers/<slug>/
            → normal ingest + compile
    """

    SUBDIR = "skill_seekers"

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.raw_dir = os.path.join(kb_path, "raw")
        self.ss_dir = os.path.join(self.raw_dir, self.SUBDIR)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_installed(self) -> bool:
        """Return True if skill-seekers CLI is available."""
        try:
            result = subprocess.run(
                ["skill-seekers", "--version"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def fetch(
        self,
        source: str,
        name: Optional[str] = None,
        use_async: bool = False,
        extra_args: Optional[List[str]] = None,
    ) -> Dict:
        """
        Fetch knowledge from *source* using Skill Seekers and copy the
        resulting Markdown files into raw/skill_seekers/<slug>/.

        Args:
            source:     URL, local path, or GitHub repo identifier.
            name:       Optional human-readable name for the skill folder.
                        Derived from source if omitted.
            use_async:  Pass --async to skill-seekers (2-3x faster scraping).
            extra_args: Any additional CLI flags forwarded to skill-seekers.

        Returns:
            dict with keys: slug, dest_dir, files (list of copied paths),
                            skill_md (path to SKILL.md if found), source.
        """
        if not self.check_installed():
            raise SkillSeekersNotInstalledError(
                "skill-seekers is not installed.\n"
                "Install it with:  pip install skill-seekers\n"
                "Full install:     pip install skill-seekers[all]"
            )

        slug = self._make_slug(name or source)
        dest_dir = os.path.join(self.ss_dir, slug)
        # dest_dir is created only after a successful run to avoid orphaned dirs

        with tempfile.TemporaryDirectory(prefix="kba_ss_") as tmp_dir:
            cmd = self._build_command(source, tmp_dir, use_async, extra_args)
            print(f"  Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=False,   # show live output
                timeout=3600,           # 1-hour max (video processing can be slow)
            )

            if result.returncode != 0:
                raise SkillSeekersFetchError(
                    f"skill-seekers exited with code {result.returncode}"
                )

            ensure_dir(dest_dir)
            copied, skill_md = self._collect_output(tmp_dir, dest_dir, slug)

        self._write_manifest(dest_dir, source, slug, copied)

        return {
            "slug": slug,
            "dest_dir": dest_dir,
            "files": copied,
            "skill_md": skill_md,
            "source": source,
        }

    def list_fetched(self) -> List[Dict]:
        """Return metadata for all previously fetched skills."""
        if not os.path.exists(self.ss_dir):
            return []

        results = []
        for entry in sorted(os.listdir(self.ss_dir)):
            entry_path = os.path.join(self.ss_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            manifest_path = os.path.join(entry_path, ".ss_manifest")
            # Skip dirs without a manifest — these are from failed/incomplete fetches
            if not os.path.exists(manifest_path):
                continue
            info = {"slug": entry, "path": entry_path}
            info.update(self._read_manifest(manifest_path))
            results.append(info)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_command(
        self,
        source: str,
        output_dir: str,
        use_async: bool,
        extra_args: Optional[List[str]],
    ) -> List[str]:
        cmd = ["skill-seekers", "create", source, "--output", output_dir]
        if use_async:
            cmd.append("--async")
        if extra_args:
            cmd.extend(extra_args)
        return cmd

    def _collect_output(
        self, tmp_dir: str, dest_dir: str, slug: str
    ) -> Tuple[List[str], Optional[str]]:
        """
        Walk the Skill Seekers output directory, copy all .md files to
        dest_dir, and return (list_of_dest_paths, skill_md_path_or_None).

        Clears existing .md files from dest_dir first so re-fetches don't
        leave stale files behind.  The .ss_manifest is left intact until
        _write_manifest overwrites it at the end of fetch().
        """
        # Remove stale .md files from a previous fetch of the same slug
        if os.path.isdir(dest_dir):
            for existing in os.listdir(dest_dir):
                if existing.lower().endswith(".md"):
                    os.remove(os.path.join(dest_dir, existing))

        copied: List[str] = []
        skill_md: Optional[str] = None
        seen_names: set = set()  # guard against flattened-name collisions

        # Skill Seekers may create a sub-folder named after the project
        # or dump files directly. Walk the whole tree.
        for root, _, files in os.walk(tmp_dir):
            for filename in files:
                if not filename.lower().endswith(".md"):
                    continue

                src_path = os.path.join(root, filename)

                # Preserve relative sub-structure inside skill output
                rel = os.path.relpath(src_path, tmp_dir)
                # Flatten one level: root/project/SKILL.md → project_SKILL.md
                parts = rel.split(os.sep)
                if len(parts) == 1:
                    dest_name = filename
                else:
                    dest_name = "_".join(parts)

                # Resolve any collision by appending a numeric suffix
                base_dest_name = dest_name
                counter = 1
                while dest_name in seen_names:
                    stem, ext = os.path.splitext(base_dest_name)
                    dest_name = f"{stem}_{counter}{ext}"
                    counter += 1
                seen_names.add(dest_name)

                dest_path = os.path.join(dest_dir, dest_name)
                shutil.copy2(src_path, dest_path)
                copied.append(dest_path)

                if filename.upper() == "SKILL.MD":
                    skill_md = dest_path

        return copied, skill_md

    @staticmethod
    def _make_slug(text: str) -> str:
        """Turn a URL or name into an ASCII filesystem-safe slug."""
        # Strip scheme
        slug = re.sub(r'^https?://', '', text)
        # Keep only ASCII alphanumeric and underscores; replace everything else
        slug = re.sub(r'[^a-zA-Z0-9_]+', '_', slug)
        slug = slug.strip('_').lower()
        # Truncate to 64 chars
        slug = slug[:64]
        return slug or "skill"

    @staticmethod
    def _write_manifest(dest_dir: str, source: str, slug: str, files: List[str]):
        lines = [
            f"source: {source}",
            f"slug: {slug}",
            f"fetched_at: {datetime.now().isoformat()}",
            f"files: {len(files)}",
        ]
        manifest_path = os.path.join(dest_dir, ".ss_manifest")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    @staticmethod
    def _read_manifest(path: str) -> Dict:
        info = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if ":" in line:
                        k, v = line.strip().split(":", 1)
                        info[k.strip()] = v.strip()
        except Exception:
            pass
        return info
