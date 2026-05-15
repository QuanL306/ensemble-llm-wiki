#!/usr/bin/env python3
"""
MCP HTTP Server - Remote third-party access via HTTP
Exposes knowledge base tools and resources over HTTP for cloud deployment.
"""

import os
import re
import sys
import json
import time
import hashlib
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
import uvicorn

# Logging — log.py lives two levels up (cloud_platform/src/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from log import setup_logging, get_logger

setup_logging("mcp_server")
log = get_logger(__name__)

# Add builder source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'builder', 'src'))

from utils.doc_reader import extract_document, is_supported_format, get_document_summary
from utils.file_utils import load_json, save_json, ensure_dir, read_text
from utils.markdown_utils import extract_section as _extract_section


MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (max {MAX_REQUEST_BODY_BYTES // 1024 // 1024} MB)"},
            )
        # Enforce on actual bytes for chunked-encoding requests (no Content-Length)
        if not content_length:
            total = 0
            chunks: list[bytes] = []
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body too large (max {MAX_REQUEST_BODY_BYTES // 1024 // 1024} MB)"},
                    )
                chunks.append(chunk)
            body = b"".join(chunks)

            async def _receive():
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = _receive
        return await call_next(request)


app = FastAPI(title="Knowledge Base MCP HTTP Server", version="1.0.0")

# CORS — set CORS_ORIGINS to a comma-separated list (e.g. "https://app.example.com")
# Default: empty (no cross-origin access). Set explicitly for production.
_cors_env = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(_BodySizeLimitMiddleware)
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ============ Data Models ============

class InitializeRequest(BaseModel):
    protocolVersion: str = "2024-11-05"
    capabilities: Dict[str, Any] = {}
    clientInfo: Dict[str, str] = {}


class ToolCallRequest(BaseModel):
    name: str
    arguments: Dict[str, Any] = {}


class ResourceRequest(BaseModel):
    uri: str


# ============ Knowledge Base Manager ============

EXPECTED_SCHEMA_VERSION = "1.4"


def _warn_schema(index: dict, user_id: str, kb_id: str) -> None:
    """Log a warning if file_index.json was built by an older builder version."""
    found = index.get("schema_version", "pre-1.2")
    if found != EXPECTED_SCHEMA_VERSION:
        log.warning(
            "index_schema_mismatch",
            extra={
                "user_id":   user_id,
                "kb_id":     kb_id,
                "found":     found,
                "expected":  EXPECTED_SCHEMA_VERSION,
                "error":     "Retrieval quality may be reduced. Run compile-llm --docs.",
            },
        )


import threading

_SHARED_KBS_TTL = 60.0

_embed_model = None
_embed_model_lock = threading.Lock()

# Per-(user_id, kb_id) embedding cache with mtime invalidation
_EMBEDDINGS_CACHE_MAX = 50
_embeddings_cache: OrderedDict = OrderedDict()   # (user_id, kb_id) -> {"mtime": float, "vectors": dict}


def _load_embeddings(kb_path: Path, user_id: str = "", kb_id: str = "") -> dict:
    """Load embeddings.json vectors, cached per tenant by file mtime."""
    emb_file = kb_path / "wiki" / "_meta" / "embeddings.json"
    if not emb_file.exists():
        return {}

    cache_key = (user_id, kb_id) if user_id else None
    try:
        mtime = emb_file.stat().st_mtime
        if cache_key and cache_key in _embeddings_cache:
            cached = _embeddings_cache[cache_key]
            if cached["mtime"] == mtime:
                _embeddings_cache.move_to_end(cache_key)
                return cached["vectors"]

        data = load_json(str(emb_file)).get("vectors", {})
        if cache_key:
            _embeddings_cache[cache_key] = {"mtime": mtime, "vectors": data}
            _embeddings_cache.move_to_end(cache_key)
            if len(_embeddings_cache) > _EMBEDDINGS_CACHE_MAX:
                _embeddings_cache.popitem(last=False)
        return data
    except Exception:
        return {}


def _get_embed_model():
    """Lazy-load fastembed model for query embedding (thread-safe)."""
    global _embed_model
    if _embed_model is not None and _embed_model is not False:
        return _embed_model
    with _embed_model_lock:
        if _embed_model is None:
            try:
                from fastembed import TextEmbedding
                _embed_model = TextEmbedding("BAAI/bge-small-en-v1.5")
            except ImportError:
                _embed_model = False
    return _embed_model if _embed_model is not False else None


def _embed_query(query: str) -> list:
    """Embed a query string. Returns empty list if model unavailable."""
    model = _get_embed_model()
    if model is None:
        return []
    from core import scoring as _scoring
    return _scoring.embed_query(query, model)


class KnowledgeBaseManager:
    """Multi-tenant knowledge base manager"""

    def __init__(self, base_path: str = "/data/knowledge-bases"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        # Shared-KB scan cache: requester_id -> (timestamp, results)
        self._shared_kbs_cache: dict = {}
    
    def get_kb_path(self, user_id: str, kb_id: str) -> Path:
        """Get the filesystem path for a user's knowledge base"""
        return self.base_path / user_id / kb_id
    
    def kb_exists(self, user_id: str, kb_id: str) -> bool:
        """Return True if the knowledge base exists"""
        kb_path = self.get_kb_path(user_id, kb_id)
        return (kb_path / ".kbaconfig").exists()

    def get_kb_config(self, owner_id: str, kb_id: str) -> dict:
        """Load .kbaconfig for the given owner/kb_id pair."""
        return self._load_config(self.get_kb_path(owner_id, kb_id))

    def check_access(self, owner_id: str, kb_id: str,
                     requester_id: str, required_role: str) -> bool:
        """
        Return True if requester can access this KB at required_role level.
        Owner always passes. Write role also grants read.
        """
        if requester_id == owner_id:
            return True
        config = self.get_kb_config(owner_id, kb_id)
        for m in config.get("members", []):
            if m.get("user_id") == requester_id:
                member_role = m.get("role", "read")
                if required_role == "read":
                    return member_role in ("read", "write")
                return member_role == "write"
        return False

    def list_shared_kbs(self, requester_id: str) -> list:
        """Scan all KBs and return those where requester_id is a member (not owner).

        Results are cached per requester for _SHARED_KBS_TTL seconds to avoid
        a full filesystem scan on every list call.
        """
        cached = self._shared_kbs_cache.get(requester_id)
        if cached and (time.time() - cached[0]) < _SHARED_KBS_TTL:
            return cached[1]

        results = []
        try:
            for user_dir in self.base_path.iterdir():
                if not user_dir.is_dir():
                    continue
                owner_id = user_dir.name
                if owner_id == requester_id:
                    continue
                for kb_dir in user_dir.iterdir():
                    if not (kb_dir.is_dir() and (kb_dir / ".kbaconfig").exists()):
                        continue
                    config = self._load_config(kb_dir)
                    for m in config.get("members", []):
                        if m.get("user_id") == requester_id:
                            stats = self._get_kb_stats(kb_dir)
                            results.append({
                                "id": kb_dir.name,
                                "owner_id": owner_id,
                                "name": config.get("name", kb_dir.name),
                                "role": m.get("role", "read"),
                                "stats": stats,
                            })
                            break
        except OSError:
            pass  # base_path not accessible — return partial results

        self._shared_kbs_cache[requester_id] = (time.time(), results)
        return results

    def _invalidate_shared_kbs_cache(self, member_user_id: str) -> None:
        """Drop the cached shared-KB list for a user whose membership changed."""
        self._shared_kbs_cache.pop(member_user_id, None)

    def list_knowledge_bases(self, user_id: str) -> List[Dict]:
        """List all knowledge bases belonging to a user"""
        user_path = self.base_path / user_id
        if not user_path.exists():
            return []
        
        kbs = []
        for kb_dir in user_path.iterdir():
            if kb_dir.is_dir() and (kb_dir / ".kbaconfig").exists():
                config = self._load_config(kb_dir)
                stats = self._get_kb_stats(kb_dir)
                kbs.append({
                    "id": kb_dir.name,
                    "name": config.get("name", kb_dir.name),
                    "description": config.get("description", ""),
                    "stats": stats,
                    "created_at": config.get("created_at", ""),
                    "updated_at": config.get("updated_at", "")
                })
        
        return kbs
    
    def _load_config(self, kb_path: Path) -> Dict:
        """Load .kbaconfig for a knowledge base"""
        config_file = kb_path / ".kbaconfig"
        if config_file.exists():
            try:
                import yaml
                with open(config_file, 'r') as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

    def _get_kb_stats(self, kb_path: Path) -> Dict:
        """Return document and concept counts for a knowledge base"""
        index_file = kb_path / "wiki" / "_meta" / "file_index.json"
        concepts_file = kb_path / "wiki" / "_meta" / "concepts.json"

        stats = {"documents": 0, "concepts": 0}

        if index_file.exists():
            try:
                data = load_json(str(index_file))
                stats["documents"] = len(data.get("files", {}))
            except Exception:
                pass

        if concepts_file.exists():
            try:
                data = load_json(str(concepts_file))
                stats["concepts"] = len(data.get("concepts", {}))
            except Exception:
                pass
        
        return stats
    
    # Per-KB index cache: (user_id, kb_id) -> {"mtime": float, "data": dict}
    _INDEX_CACHE_MAX = 100
    _index_cache: OrderedDict = OrderedDict()

    def _load_index_cached(self, kb_path: Path, user_id: str, kb_id: str) -> Optional[dict]:
        """Load file_index.json with mtime-based cache invalidation."""
        index_file = kb_path / "wiki" / "_meta" / "file_index.json"
        if not index_file.exists():
            return None
        cache_key = (user_id, kb_id)
        try:
            mtime = index_file.stat().st_mtime
            cached = self._index_cache.get(cache_key)
            if cached and cached["mtime"] == mtime:
                self._index_cache.move_to_end(cache_key)
                return cached["data"]
            data = load_json(str(index_file))
            self._index_cache[cache_key] = {"mtime": mtime, "data": data}
            self._index_cache.move_to_end(cache_key)
            if len(self._index_cache) > self._INDEX_CACHE_MAX:
                self._index_cache.popitem(last=False)
            return data
        except Exception:
            return None

    def search_documents(self, user_id: str, kb_id: str, query: str, limit: Optional[int] = None) -> List[Dict]:
        """
        Search documents using hybrid scoring (keyword TF-IDF + embedding
        cosine similarity) with concept-aware boosting.
        """
        kb_path = self.get_kb_path(user_id, kb_id)
        index = self._load_index_cached(kb_path, user_id, kb_id)
        if index is None:
            return []
        _warn_schema(index, user_id, kb_id)

        from core import scoring as _scoring
        keywords = _scoring.split_keywords(query)

        all_files = index.get("files", {})
        idf = _scoring.compute_idf(keywords, all_files)

        # Load concepts for concept boost
        concepts_data = {}
        concepts_file = kb_path / "wiki" / "_meta" / "concepts.json"
        if concepts_file.exists():
            try:
                concepts_data = load_json(str(concepts_file)).get("concepts", {})
            except Exception:
                pass

        # Load embeddings
        embeddings = _load_embeddings(kb_path, user_id, kb_id)
        query_vector = _embed_query(query) if embeddings else []

        results = []
        for file_id, file_info in all_files.items():
            metadata = file_info.get("extracted_metadata", {})
            doc_vector = embeddings.get(file_id) if embeddings else None

            score, best_chunk_title = _scoring.score_hybrid(
                file_info, keywords, idf, concepts_data,
                query_vector or None, doc_vector,
            )

            if score > 0:
                results.append({
                    "id":         file_id,
                    "name":       file_info.get("name", ""),
                    "score":      round(score, 1),
                    "word_count": metadata.get("word_count", 0),
                    "core_claims": metadata.get("core_claims", [])[:2],
                    "best_chunk": best_chunk_title,
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results if limit is None else results[:limit]
    
    def get_document(self, user_id: str, kb_id: str, doc_id: str) -> Optional[str]:
        """Get document content by ID"""
        safe_doc_id = _sanitize_path_component(doc_id)
        if not safe_doc_id:
            return None
        kb_path = self.get_kb_path(user_id, kb_id)

        possible_paths = [
            kb_path / "wiki" / "_articles" / f"{safe_doc_id}.md",
            kb_path / "wiki" / "_articles" / f"{safe_doc_id}_extracted.txt",
        ]
        
        index = self._load_index_cached(kb_path, user_id, kb_id)
        if index:
            for fid, finfo in index.get("files", {}).items():
                if fid == doc_id or finfo.get("name") == doc_id:
                    wiki_path = finfo.get("wiki_path", "")
                    if wiki_path:
                        candidate = (kb_path / wiki_path).resolve()
                        kb_root = kb_path.resolve()
                        if str(candidate).startswith(str(kb_root) + "/"):
                            possible_paths.insert(0, candidate)
        
        for path in possible_paths:
            if path.exists():
                return read_text(str(path))
        
        return None
    
    def get_concept(self, user_id: str, kb_id: str, concept_name: str) -> Optional[Dict]:
        """Get a concept by name"""
        kb_path = self.get_kb_path(user_id, kb_id)
        concepts_file = kb_path / "wiki" / "_meta" / "concepts.json"
        
        if not concepts_file.exists():
            return None
        
        try:
            data = load_json(str(concepts_file))
            return data.get("concepts", {}).get(concept_name)
        except Exception:
            return None
    
    def list_concepts(self, user_id: str, kb_id: str) -> List[str]:
        """List all concept names"""
        kb_path = self.get_kb_path(user_id, kb_id)
        concepts_file = kb_path / "wiki" / "_meta" / "concepts.json"
        
        if not concepts_file.exists():
            return []
        
        try:
            data = load_json(str(concepts_file))
            return list(data.get("concepts", {}).keys())
        except Exception:
            return []


kb_manager = KnowledgeBaseManager(
    os.environ.get("KB_BASE_PATH", "/data/knowledge-bases")
)

# Synthesis response cache
_synthesis_cache: OrderedDict = OrderedDict()
_SYNTHESIS_CACHE_MAX = 200
_SYNTHESIS_CACHE_TTL = 1800  # 30 minutes

# Query rewrite cache
_rewrite_cache: OrderedDict = OrderedDict()
_REWRITE_CACHE_MAX = 100
_REWRITE_CACHE_TTL = 1800    # 30 minutes

# LLM client singleton — created once per process, reused across all requests
_llm_singleton: tuple | None = None  # (backend, config)
_llm_singleton_lock = threading.Lock()


def _get_llm_client():
    """Return cached (backend, config) tuple; create on first call."""
    global _llm_singleton
    if _llm_singleton is not None:
        return _llm_singleton
    with _llm_singleton_lock:
        if _llm_singleton is not None:
            return _llm_singleton
        from core.llm import detect_backend, has_api_key, make_config
        if not has_api_key():
            return None
        backend = detect_backend()
        _llm_singleton = (backend, make_config(backend))
        return _llm_singleton


# Singleflight: prevent cache stampede when many concurrent requests hit the same key.
# Maps cache_key → asyncio.Event; waiting coroutines block on the event.
_rewrite_inflight: dict = {}
_synthesis_inflight: dict = {}


async def _rewrite_query(question: str, req_id: str = "-") -> list:
    """Use LLM to generate 2-3 reformulated query variants.

    Returns [original, variant1, variant2, ...].  Cached by question
    hash with a 30-minute TTL.
    """
    llm = _get_llm_client()
    if llm is None:
        return [question]

    cache_key = hashlib.md5(question.encode()).hexdigest()
    now = time.monotonic()
    if cache_key in _rewrite_cache:
        cached, cached_at = _rewrite_cache[cache_key]
        if now - cached_at < _REWRITE_CACHE_TTL:
            log.info("query_rewrite_cache_hit", extra={"req_id": req_id})
            _rewrite_cache.move_to_end(cache_key)
            return cached

    # Singleflight: if another coroutine is already computing this key, wait for it
    if cache_key in _rewrite_inflight:
        await _rewrite_inflight[cache_key].wait()
        if cache_key in _rewrite_cache:
            return _rewrite_cache[cache_key][0]
        return [question]

    event = asyncio.Event()
    _rewrite_inflight[cache_key] = event

    try:
        from core.llm import chat_create

        prompt = (
            "Generate 2-3 alternative phrasings of this research "
            "question that use different vocabulary, synonyms, or "
            "perspectives but capture the same intent. Each variant "
            "must be a complete question on its own line. Output ONLY "
            "the variant questions, no numbering, no explanation.\n\n"
            f"Question: {question}"
        )

        backend, config = llm

        def _call():
            return chat_create(prompt, backend=backend,
                             model=config["aux_model"], max_tokens=200)

        result_text = await asyncio.to_thread(_call)
        raw = [v.strip() for v in result_text.strip().split("\n") if v.strip()]
        # Strip leading numbers/bullets from LLM output
        clean = [re.sub(r'^[\d\.\-\*]+\s*', '', v).strip() for v in raw]
        clean = [v for v in clean if v]
        # Deduplicate against original question
        seen = {question.lower()}
        variants = [question]
        for v in clean[:3]:
            if v.lower() not in seen:
                seen.add(v.lower())
                variants.append(v)

        _rewrite_cache[cache_key] = (variants, now)
        _rewrite_cache.move_to_end(cache_key)
        if len(_rewrite_cache) > _REWRITE_CACHE_MAX:
            _rewrite_cache.popitem(last=False)

        log.info("query_rewrite", extra={
            "req_id": req_id,
            "num_variants": len(variants) - 1,
        })
        return variants
    except Exception as exc:
        log.warning("query_rewrite_failed", extra={
            "req_id": req_id,
            "error": f"{type(exc).__name__}: {exc}",
        })
        return [question]
    finally:
        event.set()
        _rewrite_inflight.pop(cache_key, None)


async def _synthesize_async(question: str, snippets: list,
                            req_id: str = "-") -> str:
    """
    Optionally synthesise a direct answer from the top result snippets.
    Uses a hash-based LRU cache to avoid redundant LLM calls.
    """
    llm = _get_llm_client()
    if llm is None:
        return ""
    if not snippets:
        return ""

    # Check cache
    cache_key = (
        hashlib.md5(question.encode()).hexdigest(),
        hashlib.md5(str(sorted(snippets)).encode()).hexdigest(),
    )
    now = time.monotonic()
    if cache_key in _synthesis_cache:
        cached_result, cached_at = _synthesis_cache[cache_key]
        if now - cached_at < _SYNTHESIS_CACHE_TTL:
            log.info("synthesis_cache_hit", extra={"req_id": req_id})
            _synthesis_cache.move_to_end(cache_key)
            return cached_result

    # Singleflight: if another coroutine is already computing this key, wait for it
    if cache_key in _synthesis_inflight:
        await _synthesis_inflight[cache_key].wait()
        if cache_key in _synthesis_cache:
            return _synthesis_cache[cache_key][0]
        return ""

    event = asyncio.Event()
    _synthesis_inflight[cache_key] = event

    t0 = time.monotonic()
    try:
        from core.llm import chat

        backend, config = llm
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
            return chat(prompt, backend=backend,
                       model=config["aux_model"], max_tokens=1000)

        result = await asyncio.to_thread(_call)
        result_text = result["content"]
        usage = {"input_tokens": result.get("input_tokens", 0),
                 "output_tokens": result.get("output_tokens", 0)}
        log.info(
            "synthesis_complete",
            extra={
                "req_id": req_id,
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            },
        )

        # Store in cache (LRU eviction)
        _synthesis_cache[cache_key] = (result_text, now)
        _synthesis_cache.move_to_end(cache_key)
        if len(_synthesis_cache) > _SYNTHESIS_CACHE_MAX:
            _synthesis_cache.popitem(last=False)

        return result_text
    except Exception as exc:
        log.warning(
            "synthesis_failed",
            extra={"req_id": req_id,
                   "duration_ms": int((time.monotonic() - t0) * 1000),
                   "error": f"{type(exc).__name__}: {exc}"},
        )
        return ""
    finally:
        event.set()
        _synthesis_inflight.pop(cache_key, None)


# ============ MCP Protocol Handlers ============

def _get_user_context(request: Request) -> Tuple[str, str, str, str]:
    """
    Extract user_id, kb_id, owner_id, and req_id from gateway-forwarded headers.
    Falls back to query params for direct (non-gateway) access.
    X-KB-Owner-ID defaults to X-User-ID (own KB, backward-compatible).
    All IDs are sanitized to prevent path traversal.
    """
    user_id = _sanitize_id(
        request.headers.get("X-User-ID")
        or request.query_params.get("user_id", "anonymous")
    )
    kb_id = _sanitize_id(
        request.headers.get("X-KB-ID")
        or request.query_params.get("kb_id", "default")
    )
    owner_id = _sanitize_id(
        request.headers.get("X-KB-Owner-ID") or user_id
    )
    req_id = request.headers.get("X-Request-ID", "-")
    return user_id or "anonymous", kb_id or "default", owner_id or user_id, req_id


def _sanitize_id(value: str) -> str:
    """
    Strip path-traversal characters from a user-supplied ID.
    Allows alphanumerics, hyphens, and underscores only.
    """
    return re.sub(r'[^\w\-]', '', value)


def _sanitize_path_component(value: str) -> str:
    """
    Strip path-traversal and dangerous characters from a filesystem path segment.
    Rejects empty strings and any component containing '..' or '/' or '\\'.
    """
    cleaned = re.sub(r'[<>"|?*\x00-\x1f]', '', value)
    if '..' in cleaned or '/' in cleaned or '\\' in cleaned:
        return ''
    return cleaned.strip() or ''


# Per-KB write locks — prevent concurrent writes to the same KB from racing.
# No meta-lock needed: asyncio is single-threaded so dict mutations are atomic.
_KB_WRITE_LOCKS_MAX = 1000
_kb_write_locks: Dict[str, asyncio.Lock] = {}


def _get_kb_write_lock(user_id: str, kb_id: str) -> asyncio.Lock:
    key = f"{user_id}/{kb_id}"
    if key not in _kb_write_locks:
        if len(_kb_write_locks) >= _KB_WRITE_LOCKS_MAX:
            for old_key, old_lock in list(_kb_write_locks.items()):
                if not old_lock.locked():
                    del _kb_write_locks[old_key]
                    if len(_kb_write_locks) < _KB_WRITE_LOCKS_MAX:
                        break
        _kb_write_locks[key] = asyncio.Lock()
    return _kb_write_locks[key]


def _guard_kb_write(user_id: str, kb_id: str, owner_id: str) -> Optional[dict]:
    """
    Return an error payload if the write is not allowed. Returns None when allowed.

    Checks:
      - IDs pass sanitisation (prevents path traversal)
      - KB exists under owner_id
      - requester (user_id) has write access to owner's KB
    """
    safe_uid = _sanitize_id(user_id)
    safe_kid = _sanitize_id(kb_id)

    if not safe_uid or not safe_kid:
        return {
            "content": [{"type": "text", "text": "Error: invalid user_id or kb_id"}],
            "isError": True
        }
    if safe_uid != user_id or safe_kid != kb_id:
        return {
            "content": [{"type": "text", "text": "Error: user_id or kb_id contains disallowed characters"}],
            "isError": True
        }
    if not kb_manager.kb_exists(owner_id, kb_id):
        return {
            "content": [{"type": "text", "text": f"Error: knowledge base '{kb_id}' not found"}],
            "isError": True
        }
    if not kb_manager.check_access(owner_id, kb_id, user_id, "write"):
        return {
            "content": [{"type": "text", "text": "Error: write access denied"}],
            "isError": True
        }
    return None


def _guard_kb_read(user_id: str, kb_id: str, owner_id: str) -> Optional[dict]:
    """Return an error payload if the read is not allowed. Returns None when allowed."""
    if not kb_manager.kb_exists(owner_id, kb_id):
        return {
            "content": [{"type": "text", "text": f"Error: knowledge base '{kb_id}' not found"}],
            "isError": True
        }
    if not kb_manager.check_access(owner_id, kb_id, user_id, "read"):
        return {
            "content": [{"type": "text", "text": "Error: read access denied"}],
            "isError": True
        }
    return None


@app.post("/mcp/v1/initialize")
async def mcp_initialize(request: InitializeRequest):
    """MCP initialize handshake"""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {
                "listChanged": True
            },
            "resources": {
                "subscribe": True,
                "listChanged": True
            }
        },
        "serverInfo": {
            "name": "knowledge-base-mcp",
            "version": "1.0.0"
        }
    }


@app.get("/mcp/v1/tools")
async def mcp_list_tools(request: Request):
    """List available MCP tools"""
    _get_user_context(request)  # validates headers; unpacking ignored for listing
    return {
        "tools": [
            {
                "name": "kb_list_docs",
                "description": "List all documents in the knowledge base with metadata. Use when the user wants to browse or discover what's in the KB. Supports pagination and keyword filtering.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "Optional keyword to filter by name or concepts"},
                        "limit": {"type": "integer", "default": 30},
                        "offset": {"type": "integer", "default": 0}
                    }
                }
            },
            {
                "name": "kb_search",
                "description": "Search documents by keyword in the knowledge base. ONLY use for the user's personal documents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                        "offset": {"type": "integer", "default": 0}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "kb_get_document",
                "description": "Get content of a specific document. Use 'section' to retrieve just one heading/chapter instead of the full document when possible. ONLY use when the user explicitly asks to read a document.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string"},
                        "section": {"type": "string", "description": "Optional heading title to extract just that section"}
                    },
                    "required": ["doc_id"]
                }
            },
            {
                "name": "kb_query",
                "description": "Query knowledge base with natural language. Returns a synthesized answer. Do NOT call other tools after this returns.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"}
                    },
                    "required": ["question"]
                }
            },
            {
                "name": "kb_write_article",
                "description": "Write or overwrite a wiki article (Markdown). Use this to file compiled research into the knowledge base.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Article title — becomes the filename"
                        },
                        "content": {
                            "type": "string",
                            "description": "Full Markdown content"
                        },
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                            "description": "Overwrite if file already exists"
                        }
                    },
                    "required": ["title", "content"]
                }
            },
            {
                "name": "kb_append_note",
                "description": "Append a timestamped note to an existing article under a named section.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": "Document ID or partial filename to target"
                        },
                        "note": {
                            "type": "string",
                            "description": "Note text to append"
                        },
                        "section": {
                            "type": "string",
                            "default": "Research Notes",
                            "description": "Section heading to append under (created if absent)"
                        }
                    },
                    "required": ["doc_id", "note"]
                }
            },
            {
                "name": "kb_update_index",
                "description": "Overwrite wiki/_index.md with a new table of contents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Full Markdown content for _index.md"
                        }
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "kb_save_synthesis",
                "description": "Save a query answer as a permanent synthesis page in the knowledge base. Use this after kb_query returns a high-quality answer worth preserving for future queries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The original question"
                        },
                        "answer": {
                            "type": "string",
                            "description": "The synthesized answer to save"
                        },
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Document names that contributed to this answer"
                        }
                    },
                    "required": ["question", "answer"]
                }
            }
        ]
    }


@app.post("/mcp/v1/tools/call")
async def mcp_call_tool(body: ToolCallRequest, request: Request):
    """Dispatch an MCP tool call"""
    user_id, kb_id, owner_id, req_id = _get_user_context(request)

    tool_name = body.name
    args = body.arguments
    t0 = time.monotonic()

    log.info(
        "tool_call_start",
        extra={"req_id": req_id, "user_id": user_id, "kb_id": kb_id, "tool": tool_name},
    )

    try:
        if tool_name == "kb_list_docs":
            guard = _guard_kb_read(user_id, kb_id, owner_id)
            if guard:
                return guard
            keyword = args.get("keyword", "").lower().strip()
            limit = min(int(args.get("limit", 30)), 200)
            offset = max(int(args.get("offset", 0)), 0)
            kb_path = kb_manager.get_kb_path(owner_id, kb_id)
            index = kb_manager._load_index_cached(kb_path, owner_id, kb_id)
            if index is None:
                return {"content": [{"type": "text", "text": "Error: index not found or corrupt."}], "isError": True}

            docs = []
            for fid, finfo in index.get("files", {}).items():
                if finfo.get("status") != "completed":
                    continue
                name = finfo.get("name", "")
                llm = finfo.get("llm_metadata") or {}
                terminology = llm.get("terminology", [])
                summary_text = llm.get("summary", "")
                if keyword:
                    haystack = (name + " " + " ".join(terminology) + " " + summary_text).lower()
                    if keyword not in haystack:
                        continue
                docs.append({
                    "id": fid,
                    "name": name,
                    "word_count": finfo.get("extracted_metadata", {}).get("word_count", 0),
                    "summary": summary_text[:120] if summary_text else "",
                    "concepts": terminology[:6],
                })
            # Also include synthesis pages from wiki/syntheses/
            syntheses_dir = kb_path / "wiki" / "syntheses"
            if syntheses_dir.exists():
                for syn_file in sorted(syntheses_dir.glob("*.md")):
                    try:
                        raw = syn_file.read_text(encoding="utf-8")
                        display_name = ""
                        for line in raw.splitlines():
                            stripped = line.strip()
                            if stripped.startswith("question:"):
                                display_name = stripped[len("question:"):].strip()
                                break
                            if stripped.startswith("# "):
                                display_name = stripped[2:].strip()
                                break
                        display_name = display_name or syn_file.stem
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
                return {"content": [{"type": "text", "text": msg + f" (total: {total})"}], "isError": False}
            content = f"Documents {offset + 1}–{offset + len(page)} of {total}"
            if keyword:
                content += f" (filtered: '{keyword}')"
            content += ":\n\n"
            for d in page:
                content += f"- **{d['name']}** ({d['word_count']:,} words)  ID: `{d['id']}`\n"
                if d["summary"]:
                    content += f"  {d['summary']}\n"
                if d["concepts"]:
                    content += f"  Concepts: {', '.join(d['concepts'])}\n"
            if total > offset + limit:
                content += f"\n(Pass offset={offset + limit} for next page)"
            content += "\n[END OF LIST]"
            return {"content": [{"type": "text", "text": content}], "isError": False}

        elif tool_name == "kb_search":
            guard = _guard_kb_read(user_id, kb_id, owner_id)
            if guard:
                return guard
            query = args.get("query", "")
            limit = min(int(args.get("limit", 5)), 50)
            offset = max(int(args.get("offset", 0)), 0)
            all_results = kb_manager.search_documents(owner_id, kb_id, query)
            total = len(all_results)
            results = all_results[offset: offset + limit]
            content = f"Found {total} match(es)"
            if offset:
                content += f", showing {offset + 1}–{offset + len(results)}"
            content += ":\n\n"
            for r in results:
                content += f"- **{r['name']}** (Relevance: {r['score']}, Words: {r['word_count']})\n"
                if r.get("best_chunk"):
                    content += f"  Section: {r['best_chunk']}\n"
                for claim in r['core_claims']:
                    content += f"  - {claim}\n"
                content += f"  ID: {r['id']} (pass to kb_get_document only if user explicitly asks to read this document)\n"
            if total > offset + limit:
                content += f"\n(Pass offset={offset + limit} for next page)"
            content += "\n[END OF RESULTS — use the key points above to answer the user directly. Do NOT call kb_get_document unless the user explicitly asks to read a specific document.]"
            return {"content": [{"type": "text", "text": content}], "isError": False}

        elif tool_name == "kb_get_document":
            guard = _guard_kb_read(user_id, kb_id, owner_id)
            if guard:
                return guard
            doc_id = args.get("doc_id", "")
            section = args.get("section", "").strip()
            raw_content = kb_manager.get_document(owner_id, kb_id, doc_id)
            if not raw_content:
                return {"content": [{"type": "text", "text": f"Document not found: {doc_id}"}], "isError": True}
            if section:
                extracted = _extract_section(raw_content, section)
                if extracted:
                    return {"content": [{"type": "text", "text": extracted + "\n\n[END OF SECTION — present the content to the user, do not call any more tools]"}], "isError": False}
                return {"content": [{"type": "text", "text": f"Section '{section}' not found in document."}], "isError": True}
            return {"content": [{"type": "text", "text": raw_content + "\n\n[END OF DOCUMENT — present the content to the user, do not call any more tools]"}], "isError": False}

        elif tool_name == "kb_query":
            guard = _guard_kb_read(user_id, kb_id, owner_id)
            if guard:
                return guard
            question = args.get("question", "")

            from core import scoring as _scoring
            original_keywords = _scoring.split_keywords(question)

            if not original_keywords:
                return {
                    "content": [{"type": "text", "text": "Please provide a specific question."}],
                    "isError": False
                }

            kb_path = kb_manager.get_kb_path(owner_id, kb_id)
            results_map: dict = {}   # file_id → best result
            all_files = {}

            # Load shared resources once (using cached loader for mtime-based invalidation)
            concepts_data = {}
            embeddings = {}
            try:
                index = kb_manager._load_index_cached(kb_path, owner_id, kb_id)
                if index:
                    _warn_schema(index, owner_id, kb_id)
                    all_files = index.get("files", {})

                    concepts_file = kb_path / "wiki" / "_meta" / "concepts.json"
                    if concepts_file.exists():
                        try:
                            concepts_data = load_json(str(concepts_file)).get("concepts", {})
                        except Exception:
                            pass

                    embeddings = _load_embeddings(kb_path, owner_id, kb_id)
            except Exception:
                pass

            # ── Query rewriting: score against each variant ──────────
            query_variants = await _rewrite_query(question, req_id=req_id)

            for q in query_variants:
                keywords = _scoring.split_keywords(q)
                if not keywords:
                    continue
                idf = _scoring.compute_idf(keywords, all_files)
                qvec = _embed_query(q) if embeddings else []

                for fid, finfo in all_files.items():
                    meta = finfo.get("extracted_metadata", {})
                    doc_vector = embeddings.get(fid) if embeddings else None

                    score, best_chunk_title = _scoring.score_hybrid(
                        finfo, keywords, idf, concepts_data,
                        qvec or None, doc_vector,
                    )

                    if score > 0:
                        prev = results_map.get(fid)
                        if prev is None or score > prev["score"]:
                            results_map[fid] = {
                                "id":           fid,
                                "name":         finfo.get("name", ""),
                                "score":        round(score, 1),
                                "core_claims":  meta.get("core_claims", [])[:2],
                                "best_chunk":   best_chunk_title,
                                "wiki_path":    finfo.get("wiki_path", ""),
                                "llm_metadata": finfo.get("llm_metadata", {}),
                            }

            scored = sorted(results_map.values(),
                            key=lambda x: x["score"], reverse=True)[:5]

            # ── Multi-hop Stage 2 ──────────────────────────────────────
            if scored and all_files:
                try:
                    hop_results = _scoring.multi_hop_expand(
                        scored, all_files, original_keywords,
                        _scoring.compute_idf(original_keywords, all_files),
                        concepts_data, max_additional=3,
                    )
                    existing_ids = {r["id"] for r in scored}
                    for hr in hop_results:
                        if hr["id"] not in existing_ids:
                            scored.append(hr)
                except Exception:
                    pass

            if not scored:
                content = f"No relevant documents found for: {question}"
            else:
                body_content = f"# Query Results\n\n**Question**: {question}\n\n"
                body_content += f"Found {len(scored)} relevant document(s):\n\n"
                snippets_for_synthesis: list = []

                for r in scored:
                    body_content += f"## {r['name']}\n"
                    if r.get("best_chunk"):
                        body_content += f"*Most relevant section: {r['best_chunk']}*\n"

                    snippet = ""
                    # Prefer llm_metadata from index (no file I/O)
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
                        # Fallback: read article file from disk
                        wp = r.get("wiki_path", "")
                        if wp:
                            article_path = kb_path / "wiki" / "_articles" / (
                                Path(wp).stem.replace("_extracted", "") + ".md"
                            )
                            if article_path.exists():
                                try:
                                    article_text = article_path.read_text(encoding="utf-8")
                                    m = re.search(
                                        r'## Summary\n(.*?)(?=\n## |\Z)',
                                        article_text, re.DOTALL
                                    )
                                    if m:
                                        snippet = m.group(1).strip()[:600]
                                except Exception:
                                    pass

                    if snippet:
                        body_content += f"{snippet[:500]}\n"
                        snippets_for_synthesis.append(f"**{r['name']}**: {snippet}")
                    elif r['core_claims']:
                        body_content += f"Key insight: {r['core_claims'][0][:200]}\n"
                        snippets_for_synthesis.append(f"**{r['name']}**: {r['core_claims'][0]}")
                    body_content += f"Relevance: {r['score']}\n\n"

                # Optional inline synthesis
                synthesis = await _synthesize_async(
                    question, snippets_for_synthesis, req_id=req_id
                )
                if synthesis:
                    content = (
                        f"## Answer\n\n{synthesis}\n\n"
                        f"*Sources: {', '.join(r['name'] for r in scored)}*\n\n"
                        f"---\n\n{body_content}"
                    )
                else:
                    content = body_content

            content += "\n\n[END OF RESULTS — answer the user based on the information above, do not call any more tools. Use kb_save_synthesis to permanently save this answer.]"
            return {
                "content": [{"type": "text", "text": content}],
                "isError": False
            }

        elif tool_name == "kb_write_article":
            guard = _guard_kb_write(user_id, kb_id, owner_id)
            if guard:
                return guard

            title = args.get("title", "").strip()
            content_text = args.get("content", "")
            overwrite = args.get("overwrite", False)

            if not title:
                return {"content": [{"type": "text", "text": "Error: title is required"}], "isError": True}
            if len(title) > 200:
                return {"content": [{"type": "text", "text": "Error: title exceeds 200 characters"}], "isError": True}
            if len(content_text) > 1_000_000:
                return {"content": [{"type": "text", "text": "Error: content exceeds 1MB limit"}], "isError": True}

            async with _get_kb_write_lock(owner_id, kb_id):
              safe_name = re.sub(r'[^\w\s-]', '', title).strip()
              safe_name = re.sub(r'[\s]+', '_', safe_name).lower()
              kb_path = kb_manager.get_kb_path(owner_id, kb_id)
              articles_dir = kb_path / "wiki" / "_articles"
              articles_dir.mkdir(parents=True, exist_ok=True)
              article_path = articles_dir / f"{safe_name}.md"

              if article_path.exists() and not overwrite:
                  return {
                      "content": [{"type": "text", "text": f"Article already exists: {article_path.name}. Use overwrite=true to replace it."}],
                      "isError": True
                  }

              # Auto-inject YAML frontmatter if absent
              if not content_text.startswith("---"):
                  frontmatter = (
                      f"---\ntitle: {title}\ncreated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n---\n\n"
                  )
                  content_text = frontmatter + content_text

              article_path.write_text(content_text, encoding="utf-8")
              word_count = len(content_text.split())
              return {
                  "content": [{"type": "text", "text": f"Article written: wiki/_articles/{article_path.name} ({word_count} words)"}],
                  "isError": False
              }

        elif tool_name == "kb_append_note":
            guard = _guard_kb_write(user_id, kb_id, owner_id)
            if guard:
                return guard

            doc_id = args.get("doc_id", "").strip()
            note = args.get("note", "").strip()
            section = args.get("section", "Research Notes").strip()

            if not doc_id or not note:
                return {"content": [{"type": "text", "text": "Error: doc_id and note are required"}], "isError": True}

            async with _get_kb_write_lock(owner_id, kb_id):
              kb_path = kb_manager.get_kb_path(owner_id, kb_id)
              articles_dir = kb_path / "wiki" / "_articles"
              target = None
              if articles_dir.exists():
                  for md_file in articles_dir.glob("*.md"):
                      if doc_id.lower() in md_file.stem.lower():
                          target = md_file
                          break

              if target is None:
                  return {
                      "content": [{"type": "text", "text": f"No article matching '{doc_id}' found"}],
                      "isError": True
                  }

              existing = target.read_text(encoding="utf-8")
              timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
              entry = f"\n### {timestamp}\n\n{note}\n"
              section_heading = f"## {section}"

              if section_heading in existing:
                  # Insert before the next ## heading or at end
                  idx = existing.index(section_heading) + len(section_heading)
                  next_section = existing.find("\n## ", idx)
                  if next_section == -1:
                      updated = existing + entry
                  else:
                      updated = existing[:next_section] + entry + existing[next_section:]
              else:
                  updated = existing.rstrip() + f"\n\n{section_heading}\n{entry}"

              target.write_text(updated, encoding="utf-8")
              return {
                  "content": [{"type": "text", "text": f"Note appended to: {target.name} (section: {section})"}],
                  "isError": False
              }

        elif tool_name == "kb_update_index":
            guard = _guard_kb_write(user_id, kb_id, owner_id)
            if guard:
                return guard

            content_text = args.get("content", "").strip()
            if not content_text:
                return {"content": [{"type": "text", "text": "Error: content is required"}], "isError": True}
            if len(content_text) > 1_000_000:
                return {"content": [{"type": "text", "text": "Error: content exceeds 1MB limit"}], "isError": True}

            async with _get_kb_write_lock(owner_id, kb_id):
                kb_path = kb_manager.get_kb_path(owner_id, kb_id)
                wiki_dir = kb_path / "wiki"
                wiki_dir.mkdir(parents=True, exist_ok=True)
                index_path = wiki_dir / "_index.md"
                index_path.write_text(content_text, encoding="utf-8")
                word_count = len(content_text.split())
                return {
                    "content": [{"type": "text", "text": f"Index updated: wiki/_index.md ({word_count} words)"}],
                    "isError": False
                }

        elif tool_name == "kb_save_synthesis":
            guard = _guard_kb_write(user_id, kb_id, owner_id)
            if guard:
                return guard

            question = args.get("question", "").strip()
            answer_text = args.get("answer", "").strip()
            sources_list = args.get("sources", [])

            if not question:
                return {"content": [{"type": "text", "text": "Error: question is required"}], "isError": True}
            if not answer_text:
                return {"content": [{"type": "text", "text": "Error: answer is required"}], "isError": True}

            # Slugify the question
            slug = question.lower()
            slug = re.sub(r'\s+', '_', slug)
            slug = re.sub(r'[^\w]', '', slug)
            slug = slug[:60]

            async with _get_kb_write_lock(owner_id, kb_id):
                kb_path = kb_manager.get_kb_path(owner_id, kb_id)
                syntheses_dir = kb_path / "wiki" / "syntheses"
                syntheses_dir.mkdir(parents=True, exist_ok=True)

                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                sources_yaml = "[" + ", ".join(sources_list) + "]"
                sources_inline = ", ".join(sources_list) if sources_list else "none"

                file_content = (
                    f"---\n"
                    f"type: synthesis\n"
                    f"question: {question}\n"
                    f"created: {today}\n"
                    f"sources: {sources_yaml}\n"
                    f"---\n\n"
                    f"# {question}\n\n"
                    f"{answer_text}\n\n"
                    f"---\n"
                    f"*Sources: {sources_inline}*\n"
                )

                file_path = syntheses_dir / f"{slug}.md"
                file_path.write_text(file_content, encoding="utf-8")
                rel_path = f"wiki/syntheses/{slug}.md"
                return {
                    "content": [{"type": "text", "text": f"Synthesis saved: {rel_path}"}],
                    "isError": False
                }

        else:
            log.warning(
                "tool_unknown",
                extra={"req_id": req_id, "user_id": user_id, "kb_id": kb_id,
                       "tool": tool_name},
            )
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True
            }

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.error(
            "tool_call_error",
            extra={
                "req_id":      req_id,
                "user_id":     user_id,
                "kb_id":       kb_id,
                "tool":        tool_name,
                "duration_ms": duration_ms,
                "error":       f"{type(exc).__name__}: {exc}",
            },
            exc_info=True,
        )
        return {
            "content": [{"type": "text", "text": "Internal error processing tool call"}],
            "isError": True
        }



@app.get("/mcp/v1/resources")
async def mcp_list_resources(request: Request):
    """List available MCP resources"""
    user_id, kb_id, owner_id, _ = _get_user_context(request)
    resources = []

    resources.append({
        "uri": f"kb://{kb_id}/index",
        "name": "Knowledge Base Index",
        "mimeType": "text/markdown"
    })

    kb_path = kb_manager.get_kb_path(owner_id, kb_id)
    articles_dir = kb_path / "wiki" / "_articles"

    if articles_dir.exists():
        for md_file in articles_dir.glob("*.md"):
            resources.append({
                "uri": f"kb://{kb_id}/articles/{md_file.stem}",
                "name": md_file.stem,
                "mimeType": "text/markdown"
            })

    return {"resources": resources}


@app.get("/mcp/v1/resources/{uri:path}")
async def mcp_read_resource(uri: str, request: Request):
    """Read an MCP resource by URI"""
    user_id, kb_id, owner_id, _ = _get_user_context(request)

    if uri.startswith(f"kb://{kb_id}/"):
        decoded_uri = unquote(uri)
        path = decoded_uri.replace(f"kb://{kb_id}/", "")

        if path == "index":
            file_path = kb_manager.get_kb_path(owner_id, kb_id) / "wiki" / "_index.md"
        elif path.startswith("articles/"):
            article_name = _sanitize_path_component(path.replace("articles/", ""))
            if not article_name:
                raise HTTPException(status_code=400, detail="Invalid article name")
            file_path = kb_manager.get_kb_path(owner_id, kb_id) / "wiki" / "_articles" / f"{article_name}.md"
        else:
            raise HTTPException(status_code=404, detail="Resource not found")
        
        if file_path.exists():
            content = read_text(str(file_path))
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": content
                }]
            }
    
    raise HTTPException(status_code=404, detail="Resource not found")


# ============ Management API ============

@app.get("/api/v1/knowledge-bases")
async def list_knowledge_bases(request: Request):
    """List all knowledge bases for a user (owned + shared)"""
    user_id, _, __, ___ = _get_user_context(request)
    owned = kb_manager.list_knowledge_bases(user_id)
    shared = kb_manager.list_shared_kbs(user_id)
    return {"knowledge_bases": owned, "shared_knowledge_bases": shared}


@app.post("/api/v1/knowledge-bases")
async def provision_knowledge_base(request: Request):
    """
    Provision a new knowledge base directory for a user.
    Called by the gateway immediately after successful user registration.
    Idempotent: returns 200 if the KB already exists.
    """
    user_id = _sanitize_id(request.headers.get("X-User-ID", ""))
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header required")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    kb_id = _sanitize_id(body.get("kb_id", "default") or "default")

    kb_path = kb_manager.get_kb_path(user_id, kb_id)
    already_exists = kb_manager.kb_exists(user_id, kb_id)

    if not already_exists:
        try:
            for sub in [
                kb_path / "raw",
                kb_path / "wiki" / "_articles",
                kb_path / "wiki" / "_concepts",
                kb_path / "wiki" / "_topics",
                kb_path / "wiki" / "_meta",
                kb_path / "outputs",
            ]:
                sub.mkdir(parents=True, exist_ok=True)

            (kb_path / ".kbaconfig").write_text(
                f"name: {kb_id}\nversion: '1.0'\nowner: {user_id}\nmembers: []\n",
                encoding="utf-8",
            )
            log.info(
                "kb_provisioned",
                extra={"user_id": user_id, "kb_id": kb_id},
            )
        except OSError as exc:
            log.error(
                "kb_provision_failed",
                extra={"user_id": user_id, "kb_id": kb_id, "error": str(exc)},
            )
            raise HTTPException(status_code=500, detail="Failed to provision knowledge base")

    return {
        "user_id": user_id,
        "kb_id": kb_id,
        "status": "exists" if already_exists else "created",
        "path": str(kb_path),
    }


@app.post("/api/v1/knowledge-bases/members")
async def add_kb_member(request: Request):
    """
    Add or update a member on a KB.
    Requester (X-User-ID) must be the KB owner.
    Body: {member_user_id: str, role: "read"|"write"}
    """
    import yaml

    user_id = _sanitize_id(request.headers.get("X-User-ID", ""))
    kb_id = _sanitize_id(request.headers.get("X-KB-ID", ""))
    if not user_id or not kb_id:
        raise HTTPException(status_code=400, detail="X-User-ID and X-KB-ID headers required")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    member_user_id = _sanitize_id(body.get("member_user_id", ""))
    role = body.get("role", "read")
    if not member_user_id:
        raise HTTPException(status_code=400, detail="member_user_id is required")
    if role not in ("read", "write"):
        raise HTTPException(status_code=400, detail="role must be 'read' or 'write'")

    if not kb_manager.kb_exists(user_id, kb_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    config = kb_manager.get_kb_config(user_id, kb_id)
    if config.get("owner", user_id) != user_id:
        raise HTTPException(status_code=403, detail="Only the KB owner can manage members")
    config.setdefault("owner", user_id)  # migrate old KBs that predate this field

    members = config.get("members", []) or []
    updated = False
    for m in members:
        if m.get("user_id") == member_user_id:
            m["role"] = role
            updated = True
            break
    if not updated:
        members.append({"user_id": member_user_id, "role": role})

    config["members"] = members
    kb_path = kb_manager.get_kb_path(user_id, kb_id)
    try:
        (kb_path / ".kbaconfig").write_text(yaml.safe_dump(config), encoding="utf-8")
    except Exception as exc:
        log.error("add_kb_member_write_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Failed to update KB config")

    kb_manager._invalidate_shared_kbs_cache(member_user_id)
    return {"kb_id": kb_id, "member_user_id": member_user_id, "role": role,
            "action": "updated" if updated else "added"}


@app.delete("/api/v1/knowledge-bases/members/{member_user_id}")
async def remove_kb_member(member_user_id: str, request: Request):
    """
    Remove a member from a KB.
    Requester (X-User-ID) must be the KB owner.
    """
    import yaml

    user_id = _sanitize_id(request.headers.get("X-User-ID", ""))
    kb_id = _sanitize_id(request.headers.get("X-KB-ID", ""))
    if not user_id or not kb_id:
        raise HTTPException(status_code=400, detail="X-User-ID and X-KB-ID headers required")

    safe_member = _sanitize_id(member_user_id)
    if not safe_member:
        raise HTTPException(status_code=400, detail="Invalid member_user_id")

    if not kb_manager.kb_exists(user_id, kb_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    config = kb_manager.get_kb_config(user_id, kb_id)
    if config.get("owner", user_id) != user_id:
        raise HTTPException(status_code=403, detail="Only the KB owner can manage members")
    config.setdefault("owner", user_id)  # migrate old KBs that predate this field

    members = config.get("members", []) or []
    new_members = [m for m in members if m.get("user_id") != safe_member]
    if len(new_members) == len(members):
        raise HTTPException(status_code=404, detail="Member not found")

    config["members"] = new_members
    kb_path = kb_manager.get_kb_path(user_id, kb_id)
    try:
        (kb_path / ".kbaconfig").write_text(yaml.safe_dump(config), encoding="utf-8")
    except Exception as exc:
        log.error("remove_kb_member_write_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Failed to update KB config")

    kb_manager._invalidate_shared_kbs_cache(safe_member)
    return {"kb_id": kb_id, "member_user_id": safe_member, "action": "removed"}


@app.get("/health")
async def health():
    """Health check — verifies filesystem is writable."""
    fs_ok = False
    try:
        test_path = kb_manager.base_path / ".health_check"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink()
        fs_ok = True
    except Exception:
        pass
    status = "ok" if fs_ok else "degraded"
    return {
        "status":     status,
        "filesystem": "ok" if fs_ok else "error",
        "version":    "1.0.0",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
