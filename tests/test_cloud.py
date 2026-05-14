"""
Unit tests for cloud_platform/src/server/mcp_http_server.py

Groups:
  1. _sanitize_id          — path-traversal prevention
  2. _guard_kb_write        — write ownership + existence guard
  3. KnowledgeBaseManager.search_documents — retrieval scoring
"""

import os
import sys
import json
import pytest

# Skip entire module if FastAPI is not installed or if there is a
# FastAPI/Starlette version mismatch (e.g. Starlette 1.0 + FastAPI 0.115).
pytest.importorskip("fastapi", reason="fastapi not installed — skipping cloud tests")
try:
    import mcp_http_server as cloud
except Exception as exc:
    pytest.skip(
        f"mcp_http_server failed to import ({exc}). "
        "Fix: pip install 'starlette>=0.40,<1.0'",
        allow_module_level=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Group 1: _sanitize_id
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitizeId:
    """Strip path-traversal and special characters from user-supplied IDs."""

    def test_strips_dotdot_slash_traversal(self):
        result = cloud._sanitize_id("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_strips_spaces(self):
        result = cloud._sanitize_id("my kb name")
        assert " " not in result

    def test_allows_alphanumeric_hyphen_underscore(self):
        valid = "my-kb_123"
        assert cloud._sanitize_id(valid) == valid

    def test_empty_string_remains_empty(self):
        assert cloud._sanitize_id("") == ""

    def test_strips_at_sign_and_exclamation(self):
        result = cloud._sanitize_id("user@domain!")
        assert "@" not in result
        assert "!" not in result


# ═══════════════════════════════════════════════════════════════════════════
# Group 2: _guard_kb_write
# ═══════════════════════════════════════════════════════════════════════════

class TestGuardKbWrite:
    """
    _guard_kb_write must:
    - Reject IDs containing path-traversal characters
    - Reject writes to KBs that don't exist on disk
    - Allow writes when the KB is present
    """

    def test_rejects_traversal_in_kb_id(self):
        err = cloud._guard_kb_write("alice", "../../admin")
        assert err is not None
        assert err["isError"] is True
        assert "Error" in err["content"][0]["text"]

    def test_rejects_traversal_in_user_id(self):
        err = cloud._guard_kb_write("../root", "my-kb")
        assert err is not None
        assert err["isError"] is True

    def test_rejects_empty_user_id(self):
        err = cloud._guard_kb_write("", "my-kb")
        assert err is not None
        assert err["isError"] is True

    def test_rejects_nonexistent_kb(self, tmp_path):
        """A KB that was never deployed should be rejected."""
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            err = cloud._guard_kb_write("alice", "ghost-kb")
            assert err is not None
            assert err["isError"] is True
        finally:
            cloud.kb_manager.base_path = orig

    def test_allows_valid_existing_kb(self, tmp_kb):
        """A KB with a .kbaconfig on disk must pass the guard."""
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_kb["base"]
        try:
            err = cloud._guard_kb_write(tmp_kb["user_id"], tmp_kb["kb_id"])
            assert err is None, (
                f"Expected guard to pass for a real KB, got: {err}"
            )
        finally:
            cloud.kb_manager.base_path = orig


# ═══════════════════════════════════════════════════════════════════════════
# Group 3: KnowledgeBaseManager.search_documents — retrieval scoring
# ═══════════════════════════════════════════════════════════════════════════

class TestSearchScoring:
    """
    Scoring formula:
      title hits × 5  +  body hits × 1  +  rq_text hits × 2
      + chunk title bonus (× 0.8)

    search_documents uses full-string substring matching per field,
    so tests use single-word queries that appear verbatim in one field.
    """

    def _manager(self, tmp_path):
        return cloud.KnowledgeBaseManager(str(tmp_path))

    def _setup_kb(self, tmp_path, user_id, kb_id, index_data):
        kb_root = tmp_path / user_id / kb_id
        meta = kb_root / "wiki" / "_meta"
        meta.mkdir(parents=True)
        (kb_root / ".kbaconfig").write_text("name: test\n")
        (meta / "file_index.json").write_text(json.dumps(index_data))

    def test_title_match_outscores_body_match(self, tmp_path):
        """
        Title weight is 5×; body weight is 1×.
        A doc with the query term in its title should rank above one where
        the term only appears in core_claims.
        """
        index = {
            "files": {
                "docA": {
                    "name": "sleep research overview",   # 'sleep' in title
                    "extracted_metadata": {
                        "word_count": 1000,
                        "core_claims": ["no matching words"],
                        "key_data": [], "quotes": [],
                    },
                    "retrieval_queries": [],
                    "chunks": [],
                },
                "docB": {
                    "name": "general health guide",      # 'sleep' not in title
                    "extracted_metadata": {
                        "word_count": 1000,
                        "core_claims": ["sleep is important for recovery"],
                        "key_data": [], "quotes": [],
                    },
                    "retrieval_queries": [],
                    "chunks": [],
                },
            }
        }
        self._setup_kb(tmp_path, "u1", "kb1", index)
        mgr = self._manager(tmp_path)
        results = mgr.search_documents("u1", "kb1", "sleep", limit=5)

        assert len(results) == 2
        assert results[0]["name"] == "sleep research overview", (
            f"Title match should rank first. Got: {[r['name'] for r in results]}"
        )

    def test_retrieval_query_match_boosts_score(self, tmp_path):
        """
        A retrieval query hit scores 2×.
        Doc A has the query word only in retrieval_queries (score = 2).
        Doc B has the query word only in body text (score = 1).
        → Doc A should rank higher.
        """
        index = {
            "files": {
                "docA": {
                    "name": "introduction to science",
                    "extracted_metadata": {
                        "word_count": 1000,
                        "core_claims": ["general principles"],
                        "key_data": [], "quotes": [],
                    },
                    "retrieval_queries": [
                        "If someone researches hippocampal memory, this is relevant."
                    ],
                    "chunks": [],
                },
                "docB": {
                    "name": "another overview",
                    "extracted_metadata": {
                        "word_count": 1000,
                        "core_claims": ["hippocampal involvement noted"],
                        "key_data": [], "quotes": [],
                    },
                    "retrieval_queries": [],
                    "chunks": [],
                },
            }
        }
        self._setup_kb(tmp_path, "u2", "kb1", index)
        mgr = self._manager(tmp_path)
        results = mgr.search_documents("u2", "kb1", "hippocampal", limit=5)

        assert len(results) == 2
        scores = {r["name"]: r["score"] for r in results}
        assert scores["introduction to science"] > scores["another overview"], (
            f"RQ match (2×) should outscore body match (1×). Scores: {scores}"
        )

    def test_unrelated_query_returns_no_results(self, tmp_path, sample_index):
        """A query with no matching terms returns an empty list."""
        self._setup_kb(tmp_path, "u3", "kb1", sample_index)
        mgr = self._manager(tmp_path)
        results = mgr.search_documents("u3", "kb1", "quantum chromodynamics", limit=5)
        assert results == []

    def test_full_fixture_returns_ranked_results(self, tmp_path, sample_index):
        """Smoke test: the sample index returns results for a known term."""
        self._setup_kb(tmp_path, "u4", "kb1", sample_index)
        mgr = self._manager(tmp_path)
        results = mgr.search_documents("u4", "kb1", "sleep", limit=5)

        assert len(results) >= 1
        # "Why We Sleep.pdf" has 'sleep' in both title and retrieval queries
        top_name = results[0]["name"]
        assert "Sleep" in top_name or "sleep" in top_name.lower(), (
            f"Expected sleep-related doc at top. Got: {top_name}"
        )
