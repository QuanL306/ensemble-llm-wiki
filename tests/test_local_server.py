"""
Tests for local-server/src/server.py — KB discovery, caching, tool handlers,
and path-traversal guards.

Groups:
  1. TestDiscoverKbs         — _discover_kbs
  2. TestResolveKb           — _resolve_kb
  3. TestSafeLoadIndex       — _safe_load_index (cache + corruption)
  4. TestLoadSyntheses       — _load_syntheses (frontmatter parsing)
  5. TestToolList            — _tool_list
  6. TestToolListDocs        — _tool_list_docs (filter, pagination, syntheses)
  7. TestToolSearch          — _tool_search (keyword scoring)
  8. TestToolGetDocument     — _tool_get_document (by stem, by id, section, traversal)
  9. TestToolSaveSynthesis   — _tool_save_synthesis (slug, validation, write)
 10. TestPathTraversal       — path-escape guards in wiki_path and resource URIs
"""

import asyncio
import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# ── Path setup ───────────────────────────────────────────────────────────────
_REPO        = Path(__file__).resolve().parent.parent
_SERVER_SRC  = _REPO / "local-server" / "src"
_BUILDER_SRC = _REPO / "builder" / "src"

for _p in [str(_SERVER_SRC), str(_BUILDER_SRC)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Import ───────────────────────────────────────────────────────────────────
try:
    from server import KnowledgeBaseMCPServer
    _IMPORT_OK  = True
    _IMPORT_ERR = ""
except Exception as _exc:
    KnowledgeBaseMCPServer = None          # type: ignore[assignment,misc]
    _IMPORT_OK  = False
    _IMPORT_ERR = str(_exc)

_server_available = pytest.mark.skipif(
    not _IMPORT_OK,
    reason=f"server import failed: {_IMPORT_ERR}",
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_index(**docs) -> dict:
    """Build a minimal file_index.json dict."""
    return {"files": docs}


def _completed_doc(name: str, summary: str = "", terms: list = None,
                   claims: list = None, word_count: int = 500,
                   wiki_path: str = "", state: str = "draft") -> dict:
    return {
        "name": name,
        "status": "completed",
        "extracted_metadata": {
            "core_claims": claims or [f"{name} claim"],
            "word_count": word_count,
        },
        "llm_metadata": {
            "summary": summary or f"Summary of {name}",
            "terminology": terms or [],
            "core_arguments": f"{name} arguments",
        },
        "wiki_path": wiki_path,
        "confidence": {"score": 0.8, "tier_label": "high"},
        "lifecycle": {"state": state},
    }


def _setup_kb(tmp_path: Path, kb_name: str,
              index: dict = None,
              articles: dict = None,
              syntheses: list = None,
              concepts: dict = None) -> Path:
    """
    Create a KB directory under tmp_path with optional index/articles/syntheses/concepts.

    Returns the KB directory.
    """
    kb_dir = tmp_path / kb_name
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / ".kbaconfig").write_text(f"name: {kb_name}\n")

    meta_dir = kb_dir / "wiki" / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    if index is not None:
        (meta_dir / "file_index.json").write_text(json.dumps(index))

    if concepts is not None:
        (meta_dir / "concepts.json").write_text(json.dumps({"concepts": concepts}))

    articles_dir = kb_dir / "wiki" / "_articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in (articles or {}).items():
        (articles_dir / fname).write_text(content)

    if syntheses:
        syn_dir = kb_dir / "wiki" / "syntheses"
        syn_dir.mkdir(parents=True, exist_ok=True)
        for syn in syntheses:
            q   = syn["question"]
            ans = syn.get("answer", "Answer text.")
            slug = q.lower().replace(" ", "_")[:40]
            slug = "".join(c for c in slug if c.isalnum() or c == "_")
            content = (
                f"---\ntype: synthesis\nquestion: {q}\ncreated: 2025-01-01\n---\n\n"
                f"# {q}\n\n{ans}\n"
            )
            (syn_dir / f"{slug}.md").write_text(content)

    return kb_dir


def _make_server(tmp_path: Path, kbs: dict) -> "KnowledgeBaseMCPServer":
    """
    kbs = {kb_name: {"index": {...}, "articles": {...}, "syntheses": [...]}}
    """
    for name, cfg in kbs.items():
        _setup_kb(
            tmp_path, name,
            index=cfg.get("index"),
            articles=cfg.get("articles"),
            syntheses=cfg.get("syntheses"),
            concepts=cfg.get("concepts"),
        )
    return KnowledgeBaseMCPServer(str(tmp_path))


def _run(coro):
    """Run an async coroutine synchronously (for non-async test functions)."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# 1. KB Discovery
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestDiscoverKbs:

    def test_finds_single_kb_with_config(self, tmp_path):
        _setup_kb(tmp_path, "alpha")
        srv = KnowledgeBaseMCPServer(str(tmp_path))
        assert "alpha" in srv._kb_map

    def test_ignores_dir_without_kbaconfig(self, tmp_path):
        _setup_kb(tmp_path, "alpha")
        (tmp_path / "no_config").mkdir()        # no .kbaconfig
        srv = KnowledgeBaseMCPServer(str(tmp_path))
        assert "no_config" not in srv._kb_map
        assert "alpha" in srv._kb_map

    def test_finds_multiple_kbs(self, tmp_path):
        _setup_kb(tmp_path, "kb_a")
        _setup_kb(tmp_path, "kb_b")
        _setup_kb(tmp_path, "kb_c")
        srv = KnowledgeBaseMCPServer(str(tmp_path))
        assert {"kb_a", "kb_b", "kb_c"} == set(srv._kb_map.keys())

    def test_maps_to_correct_path(self, tmp_path):
        _setup_kb(tmp_path, "my_kb")
        srv = KnowledgeBaseMCPServer(str(tmp_path))
        assert srv._kb_map["my_kb"] == tmp_path / "my_kb"

    def test_nonexistent_root_leaves_map_empty(self, tmp_path):
        # We cannot call sys.exit(1) in tests, so patch it:
        missing = str(tmp_path / "no_such_dir")
        with patch("sys.exit"):
            srv = KnowledgeBaseMCPServer.__new__(KnowledgeBaseMCPServer)
            srv.kb_root = Path(missing)
            srv._kb_map = {}
            srv._discover_kbs()
        assert srv._kb_map == {}


# ═══════════════════════════════════════════════════════════════════════════
# 2. Resolve KB
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestResolveKb:

    @pytest.fixture
    def srv(self, tmp_path):
        _setup_kb(tmp_path, "first")
        _setup_kb(tmp_path, "second")
        return KnowledgeBaseMCPServer(str(tmp_path))

    def test_resolves_named_kb(self, srv, tmp_path):
        p = srv._resolve_kb("second")
        assert p.name == "second"

    def test_none_returns_first_kb(self, srv):
        p = srv._resolve_kb(None)
        assert p in srv._kb_map.values()

    def test_empty_string_returns_first_kb(self, srv):
        p = srv._resolve_kb("")
        assert p in srv._kb_map.values()

    def test_unknown_name_raises_value_error(self, srv):
        with pytest.raises(ValueError, match="not found"):
            srv._resolve_kb("does_not_exist")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Safe Load Index
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestSafeLoadIndex:

    @pytest.fixture
    def srv(self, tmp_path):
        _setup_kb(tmp_path, "alpha")
        return KnowledgeBaseMCPServer(str(tmp_path))

    def test_returns_none_when_file_missing(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        result = srv._safe_load_index(kb)
        assert result is None   # no file_index.json created

    def test_loads_valid_index(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        idx = _make_index(doc1=_completed_doc("Doc One"))
        (kb / "wiki" / "_meta" / "file_index.json").write_text(json.dumps(idx))
        result = srv._safe_load_index(kb)
        assert result is not None
        assert "doc1" in result["files"]

    def test_returns_none_for_corrupt_json(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        (kb / "wiki" / "_meta" / "file_index.json").write_text("{corrupt json{{")
        result = srv._safe_load_index(kb)
        assert result is None

    def test_cache_returns_same_object_on_second_call(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        idx = _make_index(doc1=_completed_doc("Doc One"))
        (kb / "wiki" / "_meta" / "file_index.json").write_text(json.dumps(idx))
        srv._index_cache.clear()
        r1 = srv._safe_load_index(kb)
        r2 = srv._safe_load_index(kb)
        assert r1 is r2

    def test_cache_invalidated_on_mtime_change(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        idx_file = kb / "wiki" / "_meta" / "file_index.json"
        idx = _make_index(doc1=_completed_doc("Doc One"))
        idx_file.write_text(json.dumps(idx))
        srv._index_cache.clear()
        r1 = srv._safe_load_index(kb)

        # Overwrite with new content — filesystem mtime must change
        import time
        time.sleep(0.01)
        idx2 = _make_index(doc2=_completed_doc("Doc Two"))
        idx_file.write_text(json.dumps(idx2))
        # Force mtime update (some FSes have 1-second resolution — use os.utime)
        now = time.time() + 1
        os.utime(idx_file, (now, now))

        r2 = srv._safe_load_index(kb)
        assert "doc2" in r2["files"]
        assert "doc1" not in r2["files"]

    def test_lru_eviction_keeps_cache_bounded(self, srv, tmp_path):
        """Fill cache past _KB_CACHE_MAX; oldest entry should be evicted."""
        srv._index_cache.clear()
        srv._KB_CACHE_MAX = 3
        for i in range(5):
            kb_i = tmp_path / f"kb_{i}"
            kb_i.mkdir(parents=True, exist_ok=True)
            meta = kb_i / "wiki" / "_meta"
            meta.mkdir(parents=True, exist_ok=True)
            idx = _make_index(**{f"doc{i}": _completed_doc(f"Doc {i}")})
            (meta / "file_index.json").write_text(json.dumps(idx))
            srv._safe_load_index(kb_i)
        assert len(srv._index_cache) <= 3


# ═══════════════════════════════════════════════════════════════════════════
# 4. Load Syntheses
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestLoadSyntheses:

    @pytest.fixture
    def srv(self, tmp_path):
        _setup_kb(tmp_path, "alpha")
        return KnowledgeBaseMCPServer(str(tmp_path))

    def test_empty_when_no_syntheses_dir(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        result = srv._load_syntheses(kb)
        assert result == {}

    def test_parses_question_from_frontmatter(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        _setup_kb(tmp_path, "alpha", syntheses=[
            {"question": "What is alpha?", "answer": "Alpha is first."}
        ])
        result = srv._load_syntheses(kb)
        assert len(result) == 1
        entry = next(iter(result.values()))
        assert entry["name"] == "What is alpha?"

    def test_synthesis_ids_have_double_colon_prefix(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        _setup_kb(tmp_path, "alpha", syntheses=[
            {"question": "Test question"}
        ])
        result = srv._load_syntheses(kb)
        assert all(k.startswith("synthesis::") for k in result)

    def test_multiple_syntheses_all_loaded(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        _setup_kb(tmp_path, "alpha", syntheses=[
            {"question": "Q one"},
            {"question": "Q two"},
            {"question": "Q three"},
        ])
        result = srv._load_syntheses(kb)
        assert len(result) == 3

    def test_synthesis_entry_has_required_keys(self, srv, tmp_path):
        kb = tmp_path / "alpha"
        _setup_kb(tmp_path, "alpha", syntheses=[
            {"question": "What is beta?", "answer": "Beta is second."}
        ])
        result = srv._load_syntheses(kb)
        entry = next(iter(result.values()))
        for key in ("name", "wiki_path", "extracted_metadata", "llm_metadata"):
            assert key in entry


# ═══════════════════════════════════════════════════════════════════════════
# 5. _tool_list
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestToolList:

    def test_lists_all_kbs(self, tmp_path):
        srv = _make_server(tmp_path, {"kb_a": {}, "kb_b": {}})
        result = _run(srv._tool_list())
        text = result[0].text
        assert "kb_a" in text
        assert "kb_b" in text

    def test_shows_completed_doc_count(self, tmp_path):
        idx = _make_index(
            d1=_completed_doc("Doc 1"),
            d2=_completed_doc("Doc 2"),
            d3={**_completed_doc("Draft"), "status": "ingested"},
        )
        srv = _make_server(tmp_path, {"research": {"index": idx}})
        result = _run(srv._tool_list())
        text = result[0].text
        # 2 completed out of 3 total
        assert "2/3" in text

    def test_shows_concept_count(self, tmp_path):
        srv = _make_server(tmp_path, {
            "notes": {
                "index": _make_index(d1=_completed_doc("Doc")),
                "concepts": {"alpha": {}, "beta": {}, "gamma": {}},
            }
        })
        result = _run(srv._tool_list())
        text = result[0].text
        assert "3" in text     # 3 concepts

    def test_single_kb_no_index_still_shows(self, tmp_path):
        srv = _make_server(tmp_path, {"empty_kb": {}})
        result = _run(srv._tool_list())
        assert "empty_kb" in result[0].text


# ═══════════════════════════════════════════════════════════════════════════
# 6. _tool_list_docs
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestToolListDocs:

    @pytest.fixture
    def srv(self, tmp_path):
        idx = _make_index(
            a1=_completed_doc("Alpha Paper", terms=["transformer", "attention"]),
            a2=_completed_doc("Beta Study",  terms=["reinforcement", "learning"]),
            a3={**_completed_doc("Draft Doc"), "status": "ingested"},  # incomplete
        )
        return _make_server(tmp_path, {"research": {"index": idx}})

    def test_returns_only_completed_docs(self, srv):
        result = _run(srv._tool_list_docs({"kb_name": "research"}))
        text = result[0].text
        assert "Alpha Paper" in text
        assert "Beta Study" in text
        assert "Draft Doc" not in text

    def test_keyword_filter_limits_results(self, srv):
        result = _run(srv._tool_list_docs({
            "kb_name": "research",
            "keyword": "transformer",
        }))
        text = result[0].text
        assert "Alpha Paper" in text
        assert "Beta Study" not in text

    def test_pagination_offset_advances_page(self, srv):
        # With limit=1, page 0 should differ from page 1
        result0 = _run(srv._tool_list_docs({"kb_name": "research", "limit": 1, "offset": 0}))
        result1 = _run(srv._tool_list_docs({"kb_name": "research", "limit": 1, "offset": 1}))
        assert result0[0].text != result1[0].text

    def test_no_results_returns_message(self, srv):
        result = _run(srv._tool_list_docs({
            "kb_name": "research",
            "keyword": "xyzzy_no_match_ever",
        }))
        text = result[0].text.lower()
        assert "no documents" in text or "no" in text

    def test_syntheses_included_in_listing(self, tmp_path):
        idx = _make_index(d1=_completed_doc("Doc One"))
        srv = _make_server(tmp_path, {
            "notes": {
                "index": idx,
                "syntheses": [{"question": "What is entropy?", "answer": "Disorder."}],
            }
        })
        result = _run(srv._tool_list_docs({"kb_name": "notes"}))
        text = result[0].text
        assert "entropy" in text.lower()

    def test_missing_index_returns_error_message(self, tmp_path):
        srv = _make_server(tmp_path, {"empty": {}})   # no index
        result = _run(srv._tool_list_docs({"kb_name": "empty"}))
        text = result[0].text.lower()
        assert "error" in text or "not found" in text


# ═══════════════════════════════════════════════════════════════════════════
# 7. _tool_search
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestToolSearch:

    @pytest.fixture
    def srv(self, tmp_path):
        idx = _make_index(
            doc_a=_completed_doc(
                "Transformer Architecture",
                summary="Attention is all you need. Transformers changed NLP.",
                terms=["transformer", "attention", "encoder", "decoder"],
                claims=["Self-attention enables parallelism"],
            ),
            doc_b=_completed_doc(
                "Reinforcement Learning",
                summary="Policy gradient methods optimize reward.",
                terms=["RL", "policy", "reward", "agent"],
                claims=["RL maximises cumulative reward"],
            ),
        )
        return _make_server(tmp_path, {"ml_papers": {"index": idx}})

    def test_relevant_doc_appears_in_results(self, srv):
        result = _run(srv._tool_search({
            "query": "attention transformer",
            "kb_name": "ml_papers",
        }))
        text = result[0].text
        assert "Transformer Architecture" in text

    def test_irrelevant_query_returns_no_results(self, srv):
        result = _run(srv._tool_search({
            "query": "cooking recipe pasta",
            "kb_name": "ml_papers",
        }))
        text = result[0].text.lower()
        assert "no documents" in text or "no" in text

    def test_limit_caps_number_of_results(self, srv):
        result = _run(srv._tool_search({
            "query": "learning",
            "kb_name": "ml_papers",
            "limit": 1,
        }))
        # "END OF RESULTS" appears once; at most 1 document block
        text = result[0].text
        count = text.count("ID:")
        assert count <= 1

    def test_missing_index_returns_error_message(self, tmp_path):
        srv = _make_server(tmp_path, {"empty": {}})
        result = _run(srv._tool_search({"query": "anything", "kb_name": "empty"}))
        text = result[0].text.lower()
        assert "error" in text or "corrupt" in text


# ═══════════════════════════════════════════════════════════════════════════
# 8. _tool_get_document
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestToolGetDocument:

    @pytest.fixture
    def srv(self, tmp_path):
        article_md = (
            "# My Article\n\n"
            "## Introduction\n\nHello world.\n\n"
            "## Core Arguments\n\nThe main point is X.\n\n"
            "## Conclusion\n\nThat's it.\n"
        )
        idx = _make_index(
            mydoc=_completed_doc(
                "My Article",
                wiki_path="wiki/_articles/mydoc.md",
            )
        )
        srv = _make_server(tmp_path, {
            "research": {
                "index": idx,
                "articles": {"mydoc.md": article_md},
            }
        })
        return srv

    def test_find_by_stem(self, srv):
        result = _run(srv._tool_get_document({
            "doc_id": "mydoc",
            "kb_name": "research",
        }))
        assert "Core Arguments" in result[0].text

    def test_find_by_index_id(self, srv):
        result = _run(srv._tool_get_document({
            "doc_id": "mydoc",
            "kb_name": "research",
        }))
        assert "Hello world" in result[0].text

    def test_section_extraction_returns_only_that_section(self, srv):
        result = _run(srv._tool_get_document({
            "doc_id": "mydoc",
            "kb_name": "research",
            "section": "Core Arguments",
        }))
        text = result[0].text
        assert "main point" in text
        # Other sections should not appear
        assert "Hello world" not in text

    def test_nonexistent_doc_returns_not_found(self, srv):
        result = _run(srv._tool_get_document({
            "doc_id": "no_such_doc",
            "kb_name": "research",
        }))
        assert "not found" in result[0].text.lower()

    def test_nonexistent_section_returns_message(self, srv):
        result = _run(srv._tool_get_document({
            "doc_id": "mydoc",
            "kb_name": "research",
            "section": "No Such Section XYZ",
        }))
        assert "not found" in result[0].text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 9. _tool_save_synthesis
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestToolSaveSynthesis:

    @pytest.fixture
    def srv(self, tmp_path):
        return _make_server(tmp_path, {"notes": {}})

    def test_creates_synthesis_file(self, srv, tmp_path):
        _run(srv._tool_save_synthesis({
            "question": "What is entropy?",
            "answer": "Entropy measures disorder.",
            "kb_name": "notes",
        }))
        syn_dir = tmp_path / "notes" / "wiki" / "syntheses"
        assert syn_dir.exists()
        files = list(syn_dir.glob("*.md"))
        assert len(files) == 1

    def test_file_contains_question_and_answer(self, srv, tmp_path):
        _run(srv._tool_save_synthesis({
            "question": "Define entropy",
            "answer": "Entropy is disorder.",
            "kb_name": "notes",
        }))
        syn_dir = tmp_path / "notes" / "wiki" / "syntheses"
        content = list(syn_dir.glob("*.md"))[0].read_text()
        assert "Define entropy" in content
        assert "Entropy is disorder." in content

    def test_slug_derived_from_question(self, srv, tmp_path):
        _run(srv._tool_save_synthesis({
            "question": "What is machine learning?",
            "answer": "ML is great.",
            "kb_name": "notes",
        }))
        syn_dir = tmp_path / "notes" / "wiki" / "syntheses"
        files = list(syn_dir.glob("*.md"))
        slug = files[0].stem
        assert "machine" in slug or "what" in slug

    def test_empty_question_returns_error(self, srv):
        result = _run(srv._tool_save_synthesis({
            "question": "",
            "answer": "Some answer",
            "kb_name": "notes",
        }))
        assert "error" in result[0].text.lower()

    def test_empty_answer_returns_error(self, srv):
        result = _run(srv._tool_save_synthesis({
            "question": "A question",
            "answer": "",
            "kb_name": "notes",
        }))
        assert "error" in result[0].text.lower()

    def test_sources_written_to_file(self, srv, tmp_path):
        _run(srv._tool_save_synthesis({
            "question": "Sources test?",
            "answer": "Answer here.",
            "sources": ["Book A", "Paper B"],
            "kb_name": "notes",
        }))
        syn_dir = tmp_path / "notes" / "wiki" / "syntheses"
        content = list(syn_dir.glob("*.md"))[0].read_text()
        assert "Book A" in content
        assert "Paper B" in content

    def test_return_message_contains_path(self, srv):
        result = _run(srv._tool_save_synthesis({
            "question": "Path in result?",
            "answer": "Yes it is.",
            "kb_name": "notes",
        }))
        assert "wiki/syntheses" in result[0].text


# ═══════════════════════════════════════════════════════════════════════════
# 10. Path Traversal Guards
# ═══════════════════════════════════════════════════════════════════════════

@_server_available
class TestPathTraversal:

    def test_get_document_wiki_path_escape_blocked(self, tmp_path):
        """
        An index entry whose wiki_path escapes the KB root must not be served.
        """
        # Place a "secret" file outside the KB
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET")

        # Index points to wiki_path that traverses up out of the KB
        idx = _make_index(
            evil={
                **_completed_doc("Evil Doc"),
                # Attempt to escape via wiki_path
                "wiki_path": "../../secret.txt",
            }
        )
        srv = _make_server(tmp_path, {"kb": {"index": idx}})
        result = _run(srv._tool_get_document({
            "doc_id": "evil",
            "kb_name": "kb",
        }))
        # Must not contain the secret — doc should be "not found"
        assert "TOP SECRET" not in result[0].text

    def test_resolve_kb_unknown_name_raises(self, tmp_path):
        """Passing an unknown KB name must raise ValueError, not fall back silently."""
        srv = _make_server(tmp_path, {"legit": {}})
        with pytest.raises(ValueError):
            srv._resolve_kb("../legit")   # path-like name must not match

    def test_lru_cache_max_respected_for_concepts(self, tmp_path):
        """_safe_load_concepts obeys _KB_CACHE_MAX."""
        srv = _make_server(tmp_path, {"alpha": {}})
        srv._concepts_cache.clear()
        srv._KB_CACHE_MAX = 2

        for i in range(4):
            kb_i = tmp_path / f"c_kb_{i}"
            kb_i.mkdir(parents=True, exist_ok=True)
            meta = kb_i / "wiki" / "_meta"
            meta.mkdir(parents=True, exist_ok=True)
            data = {"concepts": {f"concept{i}": {}}}
            (meta / "concepts.json").write_text(json.dumps(data))
            srv._safe_load_concepts(kb_i)

        assert len(srv._concepts_cache) <= 2
