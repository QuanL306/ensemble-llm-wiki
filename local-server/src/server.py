#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Knowledge Base MCP Server (Multi-KB)

Exposes multiple knowledge bases via a single MCP server.
Each KB is a subdirectory under --kb-root that contains a .kbaconfig file.

Usage:
  python server.py --kb-root /path/to/parent/folder

In MCP config, set the kb-root to a directory whose children are KBs:
  /path/to/parent/
    my-research/   (contains .kbaconfig)
    book-notes/    (contains .kbaconfig)

All tools accept an optional `kb_name` parameter (defaults to first KB).
"""

import os
import re
import sys
import json
import time
import hashlib
import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
from urllib.parse import unquote

# MCP SDK
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel
)
import mcp.server.stdio


EXPECTED_SCHEMA_VERSION = "1.4"

# Shared markdown utilities (from builder/src)
_BUILDER_SRC = str(Path(__file__).resolve().parent.parent.parent / "builder" / "src")
if _BUILDER_SRC not in sys.path:
    sys.path.insert(0, _BUILDER_SRC)
from utils.markdown_utils import extract_section as _extract_section_fn


class KnowledgeBaseMCPServer:
    """Multi-KB MCP Server — manages all knowledge bases under a root dir."""

    def __init__(self, kb_root: str):
        self.kb_root = Path(kb_root)
        self.server = Server("knowledge-base-mcp")

        # Discover all KBs under root
        self._kb_map: Dict[str, Path] = {}  # kb_name → Path
        self._discover_kbs()

        if not self._kb_map:
            print(f"Error: No knowledge bases found under {kb_root}", file=sys.stderr)
            print("Each KB must contain a .kbaconfig file.", file=sys.stderr)
            sys.exit(1)

        # Per-KB caches — each entry is (data, mtime) so stale files auto-reload
        _KB_CACHE_MAX = 50
        self._index_cache: OrderedDict = OrderedDict()
        self._concepts_cache: OrderedDict = OrderedDict()
        self._embeddings_cache: OrderedDict = OrderedDict()
        self._KB_CACHE_MAX = _KB_CACHE_MAX
        self._embed_model = None

        # LLM client (shared across all KBs)
        self._llm_client = None
        self._llm_provider = None
        self._llm_config = None

        # Rewrite/synthesis caches (OrderedDict for O(1) LRU eviction)
        self._rewrite_cache: OrderedDict = OrderedDict()
        self._REWRITE_CACHE_MAX = 50
        self._REWRITE_CACHE_TTL = 1800
        self._synthesis_cache: OrderedDict = OrderedDict()
        self._SYNTHESIS_CACHE_MAX = 100
        self._SYNTHESIS_CACHE_TTL = 3600

        # Scoring module (lazy)
        self._scoring_module = None

        self._register_handlers()

    # ── KB Discovery ─────────────────────────────────────────────────────

    def _discover_kbs(self):
        """Find all subdirs with .kbaconfig under kb_root."""
        if not self.kb_root.is_dir():
            return
        for child in sorted(self.kb_root.iterdir()):
            if child.is_dir() and (child / ".kbaconfig").exists():
                self._kb_map[child.name] = child
        print(f"Discovered {len(self._kb_map)} knowledge base(s):", file=sys.stderr)
        for name in self._kb_map:
            print(f"  - {name}", file=sys.stderr)

    def _resolve_kb(self, kb_name: str = None) -> Path:
        """Resolve kb_name to a Path. Defaults to first KB if not specified."""
        if kb_name and kb_name in self._kb_map:
            return self._kb_map[kb_name]
        if not kb_name:
            # Default to first KB
            return next(iter(self._kb_map.values()))
        raise ValueError(
            f"Knowledge base '{kb_name}' not found. "
            f"Available: {list(self._kb_map.keys())}"
        )

    def _default_kb_name(self) -> str:
        return next(iter(self._kb_map))

    # ── LLM ──────────────────────────────────────────────────────────────

    def _get_llm(self):
        if self._llm_client is None:
            src = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))), "builder", "src"
            )
            if src not in sys.path:
                sys.path.insert(0, src)
            from utils.llm_client import get_llm_config, create_client
            self._llm_config = get_llm_config()
            self._llm_client, self._llm_provider = create_client(self._llm_config)
        return self._llm_client, self._llm_provider, self._llm_config

    # ── Scoring ──────────────────────────────────────────────────────────

    def _get_scoring_module(self):
        if self._scoring_module is None:
            scoring_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))), "builder", "src"
            )
            if scoring_dir not in sys.path:
                sys.path.insert(0, scoring_dir)
            from core import scoring
            self._scoring_module = scoring
        return self._scoring_module

    # ── Per-KB Data Loading ──────────────────────────────────────────────

    def _safe_load_index(self, kb_path: Path) -> Optional[dict]:
        key = str(kb_path)
        index_file = kb_path / "wiki" / "_meta" / "file_index.json"
        if not index_file.exists():
            return None
        try:
            mtime = index_file.stat().st_mtime
            if key in self._index_cache:
                cached_data, cached_mtime = self._index_cache[key]
                if cached_mtime == mtime:
                    self._index_cache.move_to_end(key)
                    return cached_data
            with open(index_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._index_cache[key] = (data, mtime)
            self._index_cache.move_to_end(key)
            if len(self._index_cache) > self._KB_CACHE_MAX:
                self._index_cache.popitem(last=False)
            return data
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def _safe_load_concepts(self, kb_path: Path) -> Optional[dict]:
        key = str(kb_path)
        concepts_file = kb_path / "wiki" / "_meta" / "concepts.json"
        if not concepts_file.exists():
            return None
        try:
            mtime = concepts_file.stat().st_mtime
            if key in self._concepts_cache:
                cached_data, cached_mtime = self._concepts_cache[key]
                if cached_mtime == mtime:
                    self._concepts_cache.move_to_end(key)
                    return cached_data
            with open(concepts_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._concepts_cache[key] = (data, mtime)
            self._concepts_cache.move_to_end(key)
            if len(self._concepts_cache) > self._KB_CACHE_MAX:
                self._concepts_cache.popitem(last=False)
            return data
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def _load_embeddings(self, kb_path: Path) -> dict:
        key = str(kb_path)
        emb_file = kb_path / "wiki" / "_meta" / "embeddings.json"
        if not emb_file.exists():
            return {}
        try:
            mtime = emb_file.stat().st_mtime
            if key in self._embeddings_cache:
                cached_data, cached_mtime = self._embeddings_cache[key]
                if cached_mtime == mtime:
                    self._embeddings_cache.move_to_end(key)
                    return cached_data
            with open(emb_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            vectors = data.get("vectors", {})
            self._embeddings_cache[key] = (vectors, mtime)
            self._embeddings_cache.move_to_end(key)
            if len(self._embeddings_cache) > self._KB_CACHE_MAX:
                self._embeddings_cache.popitem(last=False)
            return vectors
        except Exception:
            return {}

    def _load_concepts(self, kb_path: Path) -> dict:
        data = self._safe_load_concepts(kb_path)
        if data is None:
            return {}
        return data.get("concepts", {})

    def _load_syntheses(self, kb_path: Path) -> dict:
        """Load saved syntheses from wiki/syntheses/ as pseudo index entries."""
        syntheses_dir = kb_path / "wiki" / "syntheses"
        if not syntheses_dir.exists():
            return {}
        entries = {}
        for f in syntheses_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                # Parse frontmatter question field
                question = ""
                body_lines = []
                in_frontmatter = False
                past_frontmatter = False
                for line in text.splitlines():
                    if line.strip() == "---":
                        if not past_frontmatter:
                            in_frontmatter = not in_frontmatter
                            if not in_frontmatter:
                                past_frontmatter = True
                        continue
                    if in_frontmatter:
                        if line.startswith("question:"):
                            question = line[len("question:"):].strip()
                    elif past_frontmatter:
                        body_lines.append(line)
                body = "\n".join(body_lines).strip()
                # Strip trailing sources line
                if body.endswith("*"):
                    body = body[:body.rfind("\n---\n")].strip()
                name = question or f.stem
                file_id = f"synthesis::{f.stem}"
                entries[file_id] = {
                    "name": name,
                    "wiki_path": str(f.relative_to(kb_path)),
                    "extracted_metadata": {
                        "core_claims": [name],
                        "key_data": [],
                        "quotes": [],
                    },
                    "llm_metadata": {
                        "summary": body[:500],
                        "core_arguments": body,
                        "llm_body_search": body.lower(),
                    },
                    "retrieval_queries": [name],
                    "chunks": [],
                    "_is_synthesis": True,
                }
            except Exception:
                continue
        return entries

    def _get_embed_model(self):
        if self._embed_model is None:
            try:
                from fastembed import TextEmbedding
                self._embed_model = TextEmbedding("BAAI/bge-small-en-v1.5")
            except ImportError:
                self._embed_model = False
        return self._embed_model if self._embed_model is not False else None

    def _embed_query(self, query: str) -> list:
        model = self._get_embed_model()
        if model is None:
            return []
        return self._get_scoring_module().embed_query(query, model)

    # ── Handler Registration ─────────────────────────────────────────────

    def _kb_name_param(self, desc_extra=""):
        """Return the standard kb_name property dict for tool schemas."""
        return {
            "kb_name": {
                "type": "string",
                "description": (
                    f"Knowledge base name{desc_extra}. "
                    f"Defaults to first available KB."
                ),
            }
        }

    def _register_handlers(self):
        server = self.server

        @server.list_resources()
        async def handle_list_resources() -> List[Resource]:
            resources = []
            for kb_name, kb_path in self._kb_map.items():
                resources.append(
                    Resource(
                        uri=f"kb://{kb_name}/index",
                        name=f"{kb_name} — Index",
                        mimeType="text/markdown",
                    )
                )
                articles_dir = kb_path / "wiki" / "_articles"
                if articles_dir.exists():
                    for md_file in sorted(articles_dir.glob("*.md")):
                        if md_file.stem.endswith("_extracted"):
                            continue
                        resources.append(
                            Resource(
                                uri=f"kb://{kb_name}/articles/{md_file.stem}",
                                name=f"{kb_name} — {md_file.stem[:60]}",
                                mimeType="text/markdown",
                            )
                        )
                concepts_dir = kb_path / "wiki" / "_concepts"
                if concepts_dir.exists():
                    for md_file in sorted(concepts_dir.glob("*.md")):
                        resources.append(
                            Resource(
                                uri=f"kb://{kb_name}/concepts/{md_file.stem}",
                                name=f"{kb_name} — Concept: {md_file.stem}",
                                mimeType="text/markdown",
                            )
                        )
            return resources

        @server.read_resource()
        async def handle_read_resource(uri: str) -> str:
            decoded_uri = unquote(uri)
            if not decoded_uri.startswith("kb://"):
                raise ValueError(f"Invalid URI: {uri}")

            path_parts = decoded_uri[5:].split("/", 1)
            if len(path_parts) < 2:
                raise ValueError(f"Invalid URI format: {uri}")

            kb_name = path_parts[0]
            rest = path_parts[1]

            try:
                kb_path = self._resolve_kb(kb_name)
            except ValueError:
                raise ValueError(f"Unknown knowledge base: {kb_name}")

            if rest == "index":
                file_path = kb_path / "wiki" / "_index.md"
            elif rest.startswith("articles/"):
                name = rest[9:]
                if ".." in name or "/" in name or "\\" in name:
                    raise ValueError(f"Invalid resource name: {uri}")
                file_path = kb_path / "wiki" / "_articles" / f"{name}.md"
            elif rest.startswith("concepts/"):
                name = rest[9:]
                if ".." in name or "/" in name or "\\" in name:
                    raise ValueError(f"Invalid resource name: {uri}")
                file_path = kb_path / "wiki" / "_concepts" / f"{name}.md"
            else:
                raise ValueError(f"Unknown resource: {uri}")

            if file_path.exists():
                return file_path.read_text(encoding='utf-8')
            raise ValueError(f"Resource not found: {uri}")

        @server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            kb_list_desc = (
                f"Available KBs: {', '.join(self._kb_map.keys())}. "
                "Defaults to first if omitted."
            )
            return [
                Tool(
                    name="kb_list",
                    description=(
                        "List all available knowledge bases and their stats. "
                        "ONLY call this when the user explicitly asks about the knowledge base. "
                        "Do NOT call for general questions."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="kb_query",
                    description=(
                        "Search the user's personal knowledge base (books, documents, notes) "
                        "and return a synthesized answer. "
                        "ONLY use when the user's question is about content in the knowledge base "
                        "(e.g. asking about specific books, authors, concepts, or documents). "
                        "Do NOT use for general knowledge, coding, math, or casual questions. "
                        "Do NOT call any other tool after this one returns. "
                        + kb_list_desc
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The user's question in natural language",
                            },
                            **self._kb_name_param(" (optional, search all KBs if omitted)"),
                        },
                        "required": ["question"],
                    },
                ),
                Tool(
                    name="kb_list_docs",
                    description=(
                        "List all documents in a knowledge base with their metadata. "
                        "Use when the user wants to browse or discover what's in the KB. "
                        "Supports pagination (offset) and keyword filtering. "
                        + kb_list_desc
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "keyword": {
                                "type": "string",
                                "description": "Optional keyword to filter documents by name or key concepts",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max results per page (default: 30)",
                                "default": 30,
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Pagination offset (default: 0)",
                                "default": 0,
                            },
                            **self._kb_name_param(),
                        },
                    },
                ),
                Tool(
                    name="kb_search",
                    description=(
                        "Search documents by keyword in the knowledge base. "
                        "ONLY use when you need raw search results from the user's personal documents. "
                        "Do NOT use for general questions unrelated to the knowledge base. "
                        + kb_list_desc
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query (keywords)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max results (default: 5)",
                                "default": 5,
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Pagination offset (default: 0)",
                                "default": 0,
                            },
                            **self._kb_name_param(),
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="kb_get_document",
                    description=(
                        "Get content of a specific document from the knowledge base. "
                        "ONLY use when the user explicitly asks to read a specific document. "
                        "Use the 'section' parameter to retrieve just one chapter or heading "
                        "instead of the full document when possible. "
                        "Do NOT use for general questions. "
                        + kb_list_desc
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "doc_id": {
                                "type": "string",
                                "description": "Document ID or filename",
                            },
                            "section": {
                                "type": "string",
                                "description": "Optional: heading title to extract just that section (e.g. 'Core Arguments')",
                            },
                            **self._kb_name_param(),
                        },
                        "required": ["doc_id"],
                    },
                ),
                Tool(
                    name="kb_save_synthesis",
                    description=(
                        "Save a query answer as a permanent synthesis page in the knowledge base. "
                        "Use this after kb_query returns a high-quality answer worth preserving for future queries."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The original question",
                            },
                            "answer": {
                                "type": "string",
                                "description": "The synthesized answer to save",
                            },
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Document names that contributed to this answer",
                            },
                            **self._kb_name_param(),
                        },
                        "required": ["question", "answer"],
                    },
                ),
            ]

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> List[TextContent]:
            if name == "kb_list":
                return await self._tool_list()
            elif name == "kb_list_docs":
                return await self._tool_list_docs(arguments)
            elif name == "kb_query":
                return await self._tool_query(arguments)
            elif name == "kb_search":
                return await self._tool_search(arguments)
            elif name == "kb_get_document":
                return await self._tool_get_document(arguments)
            elif name == "kb_save_synthesis":
                return await self._tool_save_synthesis(arguments)
            else:
                raise ValueError(f"Unknown tool: {name}")

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_section(content: str, section_title: str) -> str:
        return _extract_section_fn(content, section_title)

    # ── Tool Implementations ─────────────────────────────────────────────

    async def _tool_list_docs(self, arguments: dict) -> List[TextContent]:
        kb_name = arguments.get("kb_name")
        kb_path = self._resolve_kb(kb_name)
        limit = min(int(arguments.get("limit", 30)), 200)
        offset = max(int(arguments.get("offset", 0)), 0)
        keyword = arguments.get("keyword", "").lower().strip()

        index = self._safe_load_index(kb_path)
        if index is None:
            return [TextContent(type="text", text="Error: index not found or corrupt.")]

        docs = []
        for file_id, file_info in index.get("files", {}).items():
            if file_info.get("status") != "completed":
                continue
            name = file_info.get("name", "")
            llm = file_info.get("llm_metadata") or {}
            terminology = llm.get("terminology", [])
            summary = llm.get("summary", "")

            if keyword:
                haystack = (name + " " + " ".join(terminology) + " " + summary).lower()
                if keyword not in haystack:
                    continue

            # Confidence + lifecycle
            conf = file_info.get("confidence") or {}
            lc = file_info.get("lifecycle") or {}

            docs.append({
                "id": file_id,
                "name": name,
                "word_count": file_info.get("extracted_metadata", {}).get("word_count", 0),
                "summary": summary[:120] if summary else "",
                "concepts": terminology[:6],
                "confidence": conf.get("tier_label", ""),
                "confidence_score": conf.get("score"),
                "lifecycle": lc.get("state", "draft"),
                "contradictions": bool(file_info.get("_has_contradictions")),
            })

        # Also include synthesis pages from wiki/syntheses/
        syntheses_dir = kb_path / "wiki" / "syntheses"
        if syntheses_dir.exists():
            for syn_file in sorted(syntheses_dir.glob("*.md")):
                try:
                    first_line = ""
                    raw = syn_file.read_text(encoding="utf-8")
                    for line in raw.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("question:"):
                            first_line = stripped[len("question:"):].strip()
                            break
                        if stripped.startswith("# "):
                            first_line = stripped[2:].strip()
                            break
                    display_name = first_line or syn_file.stem
                    if keyword:
                        haystack = (display_name + " " + raw).lower()
                        if keyword not in haystack:
                            continue
                    docs.append({
                        "id": f"syntheses/{syn_file.stem}",
                        "name": display_name + " (synthesis)",
                        "word_count": len(raw.split()),
                        "summary": "",
                        "concepts": [],
                        "confidence": "",
                        "confidence_score": None,
                        "lifecycle": "synthesis",
                        "contradictions": False,
                    })
                except Exception:
                    pass

        docs.sort(key=lambda x: x["name"].lower())
        total = len(docs)
        page = docs[offset: offset + limit]

        if not page:
            msg = f"No documents found"
            if keyword:
                msg += f" matching '{keyword}'"
            msg += f" (total in KB: {total})"
            return [TextContent(type="text", text=msg)]

        label = kb_path.name
        text = f"**{label}** — documents {offset + 1}–{offset + len(page)} of {total}"
        if keyword:
            text += f" (filtered: '{keyword}')"
        text += ":\n\n"

        for d in page:
            meta_parts = []
            if d.get("confidence"):
                meta_parts.append(d["confidence"])
            if d.get("lifecycle"):
                meta_parts.append(d["lifecycle"])
            if d.get("contradictions"):
                meta_parts.append("⚠️ contradictions")
            meta = f" [{', '.join(meta_parts)}]" if meta_parts else ""
            text += f"- **{d['name']}** ({d['word_count']:,} words){meta}  ID: `{d['id']}`\n"
            if d["summary"]:
                text += f"  {d['summary']}\n"
            if d["concepts"]:
                text += f"  Concepts: {', '.join(d['concepts'])}\n"

        if total > offset + limit:
            text += f"\n(Pass offset={offset + limit} to see the next page)"
        text += "\n[END OF LIST]"
        return [TextContent(type="text", text=text)]

    async def _tool_list(self) -> List[TextContent]:
        lines = [f"# Knowledge Bases ({len(self._kb_map)})\n"]
        for name, path in sorted(self._kb_map.items()):
            index = self._safe_load_index(path)
            n_docs = 0
            n_completed = 0
            n_concepts = 0
            n_verified = 0
            if index:
                files = index.get("files", {})
                n_docs = len(files)
                for f in files.values():
                    if f.get("status") == "completed":
                        n_completed += 1
                        lc = f.get("lifecycle") or {}
                        if lc.get("state") == "verified":
                            n_verified += 1
            concepts_data = self._safe_load_concepts(path)
            if concepts_data:
                n_concepts = len(concepts_data.get("concepts", {}))
            lines.append(f"## {name}\n- Documents: {n_completed}/{n_docs}")
            if n_verified:
                lines[-1] += f" ({n_verified} verified)"
            lines[-1] += f"\n- Concepts: {n_concepts}\n"
        lines.append("\nUse **kb_query** to ask questions, **kb_search** for keyword search.")
        return [TextContent(type="text", text="\n".join(lines))]

    async def _tool_search(self, arguments: dict) -> List[TextContent]:
        raw_query = arguments.get("query", "")
        limit = arguments.get("limit", 5)
        offset = max(int(arguments.get("offset", 0)), 0)
        kb_name = arguments.get("kb_name")
        kb_path = self._resolve_kb(kb_name)

        _scoring = self._get_scoring_module()
        keywords = _scoring.split_keywords(raw_query)

        index = self._safe_load_index(kb_path)
        if index is None:
            return [TextContent(type="text", text="Error: file_index.json is corrupt. Run 'compile-llm' to rebuild.")]

        all_files = index.get("files", {})
        concepts_data = self._load_concepts(kb_path)
        embeddings = self._load_embeddings(kb_path)
        query_vector = self._embed_query(raw_query) if embeddings else []
        idf = _scoring.compute_idf(keywords, all_files)

        results = []
        for file_id, file_info in all_files.items():
            metadata = file_info.get("extracted_metadata", {})
            doc_vector = embeddings.get(file_id) if embeddings else None
            score, best_chunk_title = _scoring.score_hybrid(
                file_info, keywords, idf, concepts_data,
                query_vector or None, doc_vector,
            )
            if score > 0:
                conf = file_info.get("confidence") or {}
                lc = file_info.get("lifecycle") or {}
                results.append({
                    "id": file_id,
                    "name": file_info.get("name", ""),
                    "score": round(score, 1),
                    "word_count": metadata.get("word_count", 0),
                    "core_claims": metadata.get("core_claims", [])[:3],
                    "best_chunk": best_chunk_title,
                    "confidence": conf.get("tier_label", ""),
                    "lifecycle": lc.get("state", "draft"),
                })

        # Also search synthesis files by direct scan
        syntheses_dir = kb_path / "wiki" / "syntheses"
        if syntheses_dir.exists():
            for syn_file in sorted(syntheses_dir.glob("*.md")):
                try:
                    raw = syn_file.read_text(encoding="utf-8")
                    question_line = ""
                    for line in raw.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("question:"):
                            question_line = stripped[len("question:"):].strip()
                            break
                        if stripped.startswith("# "):
                            question_line = stripped[2:].strip()
                            break
                    # Build a minimal file_info-like dict for scoring
                    syn_info = {
                        "name": question_line or syn_file.stem,
                        "extracted_metadata": {
                            "core_claims": [question_line] if question_line else [],
                            "word_count": len(raw.split()),
                        },
                        "llm_metadata": {
                            "summary": raw[:600],
                            "terminology": keywords,
                        },
                        "status": "completed",
                    }
                    score, best_chunk_title = _scoring.score_hybrid(
                        syn_info, keywords, idf, concepts_data, None, None
                    )
                    if score > 0:
                        results.append({
                            "id": f"syntheses/{syn_file.stem}",
                            "name": (question_line or syn_file.stem) + " (synthesis)",
                            "score": round(score, 1),
                            "word_count": len(raw.split()),
                            "core_claims": [question_line] if question_line else [],
                            "best_chunk": None,
                            "confidence": "",
                            "lifecycle": "synthesis",
                        })
                except Exception:
                    pass

        results.sort(key=lambda x: x["score"], reverse=True)
        total = len(results)
        results = results[offset: offset + limit]

        if not results:
            return [TextContent(type="text", text=f"No documents found matching '{raw_query}'.")]

        text = f"Found {total} match(es)"
        if offset:
            text += f", showing {offset + 1}–{offset + len(results)}"
        text += ":\n\n"
        for r in results:
            text += f"- **{r['name']}** (Relevance: {r['score']}, Words: {r['word_count']})"
            if r.get("best_chunk"):
                text += f"\n  Section: {r['best_chunk']}"
            if r.get("confidence"):
                text += f"\n  Confidence: {r['confidence']}"
            if r.get("lifecycle") and r["lifecycle"] != "draft":
                text += f" | State: {r['lifecycle']}"
            if r['core_claims']:
                for claim in r['core_claims']:
                    text += f"\n  - {claim}"
            text += f"\n  ID: {r['id']} (pass to kb_get_document only if user explicitly asks to read this document)\n"

        if total > offset + limit:
            text += f"\n(Pass offset={offset + limit} for the next page)"
        text += "\n[END OF RESULTS — use the key points above to answer the user directly. Do NOT call kb_get_document unless the user explicitly asks to read a specific document.]"
        return [TextContent(type="text", text=text)]

    async def _tool_get_document(self, arguments: dict) -> List[TextContent]:
        doc_id = arguments.get("doc_id", "")
        section = arguments.get("section", "").strip()
        kb_name = arguments.get("kb_name")
        kb_path = self._resolve_kb(kb_name)

        content = None
        articles_dir = kb_path / "wiki" / "_articles"
        if articles_dir.exists():
            for md_file in sorted(articles_dir.glob("*.md")):
                if doc_id == md_file.stem or doc_id == md_file.name:
                    content = md_file.read_text(encoding='utf-8')
                    break

        if content is None:
            index = self._safe_load_index(kb_path)
            if index is None:
                return [TextContent(type="text", text="Error: file_index.json is corrupt.")]
            for file_id, file_info in index.get("files", {}).items():
                if file_id == doc_id or file_info.get("name") == doc_id:
                    wiki_path = file_info.get("wiki_path", "")
                    if wiki_path:
                        candidate = (kb_path / wiki_path).resolve()
                        kb_root = kb_path.resolve()
                        if str(candidate).startswith(str(kb_root) + "/") and candidate.exists():
                            content = candidate.read_text(encoding='utf-8')
                            break

        if content is None:
            return [TextContent(type="text", text=f"Document not found: {doc_id}")]

        if section:
            extracted = self._extract_section(content, section)
            if extracted:
                return [TextContent(type="text", text=extracted + "\n\n[END OF SECTION — present the content to the user, do not call any more tools]")]
            return [TextContent(type="text", text=f"Section '{section}' not found in document.")]

        return [TextContent(type="text", text=content + "\n\n[END OF DOCUMENT — present the content to the user, do not call any more tools]")]

    async def _tool_save_synthesis(self, arguments: dict) -> List[TextContent]:
        question = arguments.get("question", "").strip()
        answer = arguments.get("answer", "").strip()
        sources = arguments.get("sources", [])
        kb_name = arguments.get("kb_name")
        kb_path = self._resolve_kb(kb_name)

        if not question:
            return [TextContent(type="text", text="Error: question is required")]
        if not answer:
            return [TextContent(type="text", text="Error: answer is required")]

        # Slugify the question
        slug = question.lower()
        slug = re.sub(r'\s+', '_', slug)
        slug = re.sub(r'[^\w]', '', slug)
        slug = slug[:60]

        syntheses_dir = kb_path / "wiki" / "syntheses"
        syntheses_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        sources_list = sources if sources else []
        sources_yaml = "[" + ", ".join(sources_list) + "]"
        sources_inline = ", ".join(sources_list) if sources_list else "none"

        content = (
            f"---\n"
            f"type: synthesis\n"
            f"question: {question}\n"
            f"created: {today}\n"
            f"sources: {sources_yaml}\n"
            f"---\n\n"
            f"# {question}\n\n"
            f"{answer}\n\n"
            f"---\n"
            f"*Sources: {sources_inline}*\n"
        )

        file_path = syntheses_dir / f"{slug}.md"
        file_path.write_text(content, encoding="utf-8")

        rel_path = f"wiki/syntheses/{slug}.md"
        return [TextContent(type="text", text=f"Synthesis saved: {rel_path}")]

    async def _tool_query(self, arguments: dict) -> List[TextContent]:
        question = arguments.get("question", "")
        kb_name = arguments.get("kb_name")

        # If no kb_name specified, search ALL KBs
        if kb_name:
            kb_paths = [(kb_name, self._resolve_kb(kb_name))]
        else:
            kb_paths = list(self._kb_map.items())

        _scoring = self._get_scoring_module()
        query_variants = await self._rewrite_query(question)
        original_keywords = _scoring.split_keywords(question)

        if not original_keywords:
            return [TextContent(type="text", text="Please provide a specific question.")]

        all_results = []

        for cur_kb_name, kb_path in kb_paths:
            index = self._safe_load_index(kb_path)
            if index is None:
                continue
            all_files = {**index.get("files", {}), **self._load_syntheses(kb_path)}
            concepts_data = self._load_concepts(kb_path)
            embeddings = self._load_embeddings(kb_path)
            results_map: dict = {}

            for q in query_variants:
                keywords = _scoring.split_keywords(q)
                if not keywords:
                    continue
                idf = _scoring.compute_idf(keywords, all_files)
                qvec = self._embed_query(q) if embeddings else []

                for file_id, file_info in all_files.items():
                    metadata = file_info.get("extracted_metadata", {})
                    doc_vector = embeddings.get(file_id) if embeddings else None
                    score, best_chunk_title = _scoring.score_hybrid(
                        file_info, keywords, idf, concepts_data,
                        qvec or None, doc_vector,
                    )
                    if score > 0:
                        prev = results_map.get(file_id)
                        if prev is None or score > prev["score"]:
                            results_map[file_id] = {
                                "id": file_id,
                                "name": file_info.get("name", ""),
                                "score": round(score, 1),
                                "core_claims": metadata.get("core_claims", [])[:2],
                                "best_chunk": best_chunk_title,
                                "wiki_path": file_info.get("wiki_path", ""),
                                "llm_metadata": file_info.get("llm_metadata", {}),
                                "_kb_name": cur_kb_name,
                                "_kb_path": kb_path,
                            }

            # Multi-hop expansion
            if results_map and all_files:
                try:
                    top = sorted(results_map.values(), key=lambda x: x["score"], reverse=True)[:5]
                    hop_results = _scoring.multi_hop_expand(
                        top, all_files, original_keywords,
                        _scoring.compute_idf(original_keywords, all_files),
                        concepts_data, max_additional=3,
                    )
                    if hop_results:
                        existing_ids = set(results_map.keys())
                        for hr in hop_results:
                            if hr["id"] not in existing_ids:
                                hr["_kb_name"] = cur_kb_name
                                hr["_kb_path"] = kb_path
                                results_map[hr["id"]] = hr
                except Exception:
                    pass

            all_results.extend(results_map.values())

        # Sort across all KBs
        all_results.sort(key=lambda x: x["score"], reverse=True)
        results = all_results[:8]

        if not results:
            return [TextContent(type="text", text=f"No relevant documents found for: {question}")]

        # Collect snippets
        snippets_for_synthesis: list = []
        body_text = f"# Query Results\n\n**Question**: {question}\n\n"
        if len(self._kb_map) > 1 and not kb_name:
            body_text += f"Searched across all {len(self._kb_map)} knowledge bases.\n\n"
        body_text += f"Found {len(results)} relevant document(s):\n\n"

        for r in results:
            kb_label = f" [{r['_kb_name']}]" if len(self._kb_map) > 1 else ""
            body_text += f"## {r['name']}{kb_label}\n"
            if r["best_chunk"]:
                body_text += f"*Most relevant section: {r['best_chunk']}*\n"

            snippet = ""
            llm = r.get("llm_metadata") or {}
            if llm:
                parts = []
                if llm.get("summary"):
                    parts.append(llm["summary"][:600])
                if llm.get("core_arguments"):
                    parts.append(llm["core_arguments"][:600])
                if llm.get("evidence"):
                    parts.append(" ".join(llm["evidence"][:3])[:400])
                snippet = "\n\n".join(parts)[:1800] if parts else ""
            else:
                wp = r.get("wiki_path", "")
                if wp:
                    article_path = r["_kb_path"] / "wiki" / "_articles" / (
                        Path(wp).stem.replace("_extracted", "") + ".md"
                    )
                    if article_path.exists():
                        try:
                            article_text = article_path.read_text(encoding="utf-8")
                            m = re.search(r'## Summary\n(.*?)(?=\n## |\Z)', article_text, re.DOTALL)
                            if m:
                                snippet = m.group(1).strip()[:600]
                        except Exception:
                            pass

            if snippet:
                body_text += f"{snippet[:500]}\n"
                snippets_for_synthesis.append(f"**{r['name']}**: {snippet}")
            elif r["core_claims"]:
                body_text += f"Key insight: {r['core_claims'][0][:200]}\n"
                snippets_for_synthesis.append(f"**{r['name']}**: {r['core_claims'][0]}")
            body_text += f"Relevance: {r['score']}\n\n"

        synthesis = await self._synthesize(question, snippets_for_synthesis)
        if synthesis:
            full_text = (
                f"## Answer\n\n{synthesis}\n\n"
                f"*Sources: {', '.join(r['name'] for r in results)}*\n\n"
                f"---\n\n{body_text}"
            )
        else:
            full_text = body_text

        # Explicit stop signal for AI assistants
        full_text += "\n\n[END OF RESULTS — answer the user based on the information above, do not call any more tools. Use kb_save_synthesis to permanently save this answer.]"

        return [TextContent(type="text", text=full_text)]

    # ── LLM Helpers ──────────────────────────────────────────────────────

    async def _rewrite_query(self, question: str) -> list:
        from utils.llm_client import has_api_key, chat_create
        if not has_api_key():
            return [question]

        cache_key = hashlib.md5(question.encode()).hexdigest()
        now = time.monotonic()
        if cache_key in self._rewrite_cache:
            cached, cached_at = self._rewrite_cache[cache_key]
            if now - cached_at < self._REWRITE_CACHE_TTL:
                self._rewrite_cache.move_to_end(cache_key)
                return cached

        try:
            prompt = (
                "Generate 2-3 alternative phrasings of this research "
                "question that use different vocabulary, synonyms, or "
                "perspectives but capture the same intent. Each variant "
                "must be a complete question on its own line. Output ONLY "
                "the variant questions, no numbering, no explanation.\n\n"
                f"Question: {question}"
            )

            def _call():
                client, provider, config = self._get_llm()
                return chat_create(client, provider, config["aux_model"], prompt, max_tokens=200)

            result_text = await asyncio.to_thread(_call)
            raw = [v.strip() for v in result_text.strip().split("\n") if v.strip()]
            clean = [re.sub(r'^[\d\.\-\*]+\s*', '', v).strip() for v in raw]
            clean = [v for v in clean if v]
            seen = {question.lower()}
            variants = [question]
            for v in clean[:3]:
                if v.lower() not in seen:
                    seen.add(v.lower())
                    variants.append(v)

            self._rewrite_cache[cache_key] = (variants, now)
            self._rewrite_cache.move_to_end(cache_key)
            if len(self._rewrite_cache) > self._REWRITE_CACHE_MAX:
                self._rewrite_cache.popitem(last=False)

            return variants
        except Exception:
            return [question]

    async def _synthesize(self, question: str, snippets: list) -> str:
        from utils.llm_client import has_api_key, chat_create
        if not has_api_key():
            return ""
        if not snippets:
            return ""

        cache_key = (
            hashlib.md5(question.encode()).hexdigest(),
            hashlib.md5(str(sorted(snippets)).encode()).hexdigest(),
        )
        now = time.monotonic()
        if cache_key in self._synthesis_cache:
            cached_result, cached_at = self._synthesis_cache[cache_key]
            if now - cached_at < self._SYNTHESIS_CACHE_TTL:
                self._synthesis_cache.move_to_end(cache_key)
                return cached_result

        try:
            sources = "\n\n---\n\n".join(snippets[:5])
            prompt = (
                f"Answer this research question in 4–6 sentences based solely on "
                f"the document summaries below. Be specific and direct. "
                f"Identify connections or complementary insights across sources. "
                f"Do NOT instruct the reader to consult any document — your answer "
                f"must be self-contained and final.\n\n"
                f"Question: {question}\n\n"
                f"Sources:\n{sources}"
            )

            def _call():
                client, provider, config = self._get_llm()
                return chat_create(client, provider, config["aux_model"], prompt, max_tokens=1000)

            result = await asyncio.to_thread(_call)

            self._synthesis_cache[cache_key] = (result, now)
            self._synthesis_cache.move_to_end(cache_key)
            if len(self._synthesis_cache) > self._SYNTHESIS_CACHE_MAX:
                self._synthesis_cache.popitem(last=False)

            return result
        except Exception:
            return ""

    # ── Server Entry ─────────────────────────────────────────────────────

    async def run(self):
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="knowledge-base-mcp",
                    server_version="2.0.0",
                    capabilities=self.server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={}
                    )
                )
            )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge Base MCP Server (Multi-KB)")
    parser.add_argument(
        "--kb-root", "-r",
        help="Parent directory containing multiple KBs (auto-discovered)"
    )
    parser.add_argument(
        "--kb-path", "-k",
        help="Single knowledge base path (backward compatible)"
    )
    args = parser.parse_args()

    # Backward compatible: --kb-path works as before
    if args.kb_path:
        kb_path = os.path.abspath(args.kb_path)
        if not os.path.exists(os.path.join(kb_path, ".kbaconfig")):
            print(f"Error: Not a valid knowledge base: {kb_path}", file=sys.stderr)
            sys.exit(1)
        # Use parent dir as root so this KB is discovered
        kb_root = os.path.dirname(kb_path)
    elif args.kb_root:
        kb_root = os.path.abspath(args.kb_root)
    else:
        print("Error: specify --kb-root (multi-KB) or --kb-path (single KB)", file=sys.stderr)
        sys.exit(1)

    print(f"Knowledge Base MCP Server v2.0 (multi-KB)", file=sys.stderr)
    print(f"Root: {kb_root}", file=sys.stderr)

    server = KnowledgeBaseMCPServer(kb_root)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
