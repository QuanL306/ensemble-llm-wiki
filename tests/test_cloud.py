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
    - Reject writes by non-members
    - Allow writes when the KB is present and requester has write access
    """

    def test_rejects_traversal_in_kb_id(self):
        err = cloud._guard_kb_write("alice", "../../admin", "alice")
        assert err is not None
        assert err["isError"] is True
        assert "Error" in err["content"][0]["text"]

    def test_rejects_traversal_in_user_id(self):
        err = cloud._guard_kb_write("../root", "my-kb", "../root")
        assert err is not None
        assert err["isError"] is True

    def test_rejects_empty_user_id(self):
        err = cloud._guard_kb_write("", "my-kb", "")
        assert err is not None
        assert err["isError"] is True

    def test_rejects_nonexistent_kb(self, tmp_path):
        """A KB that was never deployed should be rejected."""
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            err = cloud._guard_kb_write("alice", "ghost-kb", "alice")
            assert err is not None
            assert err["isError"] is True
        finally:
            cloud.kb_manager.base_path = orig

    def test_allows_valid_existing_kb(self, tmp_kb):
        """A KB with a .kbaconfig on disk must pass the guard (owner writing own KB)."""
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_kb["base"]
        try:
            err = cloud._guard_kb_write(tmp_kb["user_id"], tmp_kb["kb_id"], tmp_kb["user_id"])
            assert err is None, (
                f"Expected guard to pass for a real KB, got: {err}"
            )
        finally:
            cloud.kb_manager.base_path = orig


# ═══════════════════════════════════════════════════════════════════════════
# Group 3: KB Access Control
# ═══════════════════════════════════════════════════════════════════════════

class TestKBAccessControl:
    """
    Tests for check_access(), _guard_kb_write (with owner_id), and
    the member management HTTP endpoints.
    """

    def _make_kb(self, base_path, owner_id, kb_id, members=None):
        """Create a minimal KB with .kbaconfig containing owner + members."""
        import yaml
        kb_root = base_path / owner_id / kb_id
        kb_root.mkdir(parents=True, exist_ok=True)
        config = {
            "name": kb_id,
            "version": "1.0",
            "owner": owner_id,
            "members": members or [],
        }
        (kb_root / ".kbaconfig").write_text(yaml.safe_dump(config), encoding="utf-8")
        return kb_root

    # ── check_access ─────────────────────────────────────────────────────────

    def test_owner_always_has_read_access(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research")
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        assert mgr.check_access("alice", "research", "alice", "read") is True

    def test_owner_always_has_write_access(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research")
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        assert mgr.check_access("alice", "research", "alice", "write") is True

    def test_read_member_has_read_access(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "read"}])
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        assert mgr.check_access("alice", "research", "bob", "read") is True

    def test_write_member_also_has_read_access(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "write"}])
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        assert mgr.check_access("alice", "research", "bob", "read") is True

    def test_read_member_denied_write_access(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "read"}])
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        assert mgr.check_access("alice", "research", "bob", "write") is False

    def test_non_member_denied_read_access(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research")
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        assert mgr.check_access("alice", "research", "carol", "read") is False

    # ── _guard_kb_write with owner_id ────────────────────────────────────────

    def test_guard_write_denies_non_member(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research")
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            err = cloud._guard_kb_write("bob", "research", "alice")
            assert err is not None
            assert "write access denied" in err["content"][0]["text"]
        finally:
            cloud.kb_manager.base_path = orig

    def test_guard_write_allows_write_member(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "write"}])
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            err = cloud._guard_kb_write("bob", "research", "alice")
            assert err is None
        finally:
            cloud.kb_manager.base_path = orig

    # ── Member management endpoints ──────────────────────────────────────────

    def test_add_kb_member_endpoint(self, tmp_path):
        """POST /api/v1/knowledge-bases/members adds the member to .kbaconfig."""
        import yaml
        from fastapi.testclient import TestClient

        self._make_kb(tmp_path, "alice", "research")
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            client = TestClient(cloud.app)
            resp = client.post(
                "/api/v1/knowledge-bases/members",
                headers={"X-User-ID": "alice", "X-KB-ID": "research"},
                json={"member_user_id": "bob", "role": "read"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["member_user_id"] == "bob"
            assert body["role"] == "read"

            config = yaml.safe_load(
                (tmp_path / "alice" / "research" / ".kbaconfig").read_text()
            )
            members = config.get("members", [])
            assert any(m["user_id"] == "bob" for m in members)
        finally:
            cloud.kb_manager.base_path = orig

    def test_remove_kb_member_endpoint(self, tmp_path):
        """DELETE /api/v1/knowledge-bases/members/{id} removes the member."""
        import yaml
        from fastapi.testclient import TestClient

        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "read"}])
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            client = TestClient(cloud.app)
            resp = client.delete(
                "/api/v1/knowledge-bases/members/bob",
                headers={"X-User-ID": "alice", "X-KB-ID": "research"},
            )
            assert resp.status_code == 200

            config = yaml.safe_load(
                (tmp_path / "alice" / "research" / ".kbaconfig").read_text()
            )
            members = config.get("members", [])
            assert not any(m["user_id"] == "bob" for m in members)
        finally:
            cloud.kb_manager.base_path = orig

    def test_add_member_non_owner_rejected(self, tmp_path):
        """owner field in .kbaconfig != requester → 403."""
        import yaml
        from fastapi.testclient import TestClient

        # Create a KB under bob's path but with alice listed as the owner
        # (simulates direct MCP call bypassing the gateway).
        kb_root = tmp_path / "bob" / "research"
        kb_root.mkdir(parents=True, exist_ok=True)
        config = {"name": "research", "version": "1.0", "owner": "alice", "members": []}
        (kb_root / ".kbaconfig").write_text(yaml.safe_dump(config), encoding="utf-8")

        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            client = TestClient(cloud.app)
            resp = client.post(
                "/api/v1/knowledge-bases/members",
                headers={"X-User-ID": "bob", "X-KB-ID": "research"},
                json={"member_user_id": "carol", "role": "read"},
            )
            assert resp.status_code == 403
        finally:
            cloud.kb_manager.base_path = orig


# ═══════════════════════════════════════════════════════════════════════════
# Group 4: KnowledgeBaseManager.search_documents — retrieval scoring
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


# ═══════════════════════════════════════════════════════════════════════════
# Group 5: Shared-KB cache invalidation
# ═══════════════════════════════════════════════════════════════════════════

class TestSharedKbsCache:
    """
    list_shared_kbs must:
    - cache results (second call is faster / same result)
    - invalidate for the affected member when add_kb_member is called
    - invalidate for the affected member when remove_kb_member is called
    """

    def _make_kb(self, base_path, owner_id, kb_id, members=None):
        import yaml
        kb_root = base_path / owner_id / kb_id
        kb_root.mkdir(parents=True, exist_ok=True)
        config = {"name": kb_id, "version": "1.0", "owner": owner_id, "members": members or []}
        (kb_root / ".kbaconfig").write_text(yaml.safe_dump(config))
        return kb_root

    def test_cache_is_populated_on_first_call(self, tmp_path):
        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "read"}])
        mgr = cloud.KnowledgeBaseManager(str(tmp_path))
        r1 = mgr.list_shared_kbs("bob")
        r2 = mgr.list_shared_kbs("bob")
        assert r1 == r2
        assert any(kb["id"] == "research" for kb in r1)

    def test_cache_invalidated_after_add_member(self, tmp_path):
        """Adding bob to a KB clears bob's shared-KB cache entry."""
        import mcp_http_server as cloud_mod
        self._make_kb(tmp_path, "alice", "research")

        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            # Warm up cache with empty result (bob not yet a member)
            result_before = cloud.kb_manager.list_shared_kbs("bob")
            assert result_before == []

            # Add bob via endpoint
            from fastapi.testclient import TestClient
            client = TestClient(cloud.app)
            resp = client.post(
                "/api/v1/knowledge-bases/members",
                headers={"X-User-ID": "alice", "X-KB-ID": "research"},
                json={"member_user_id": "bob", "role": "read"},
            )
            assert resp.status_code == 200

            # Cache should be invalidated — next call rescans and finds bob
            result_after = cloud.kb_manager.list_shared_kbs("bob")
            assert any(kb["id"] == "research" for kb in result_after)
        finally:
            cloud.kb_manager.base_path = orig

    def test_cache_invalidated_after_remove_member(self, tmp_path):
        """Removing bob from a KB clears bob's shared-KB cache entry."""
        import yaml
        self._make_kb(tmp_path, "alice", "research",
                      members=[{"user_id": "bob", "role": "read"}])

        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            # Warm up cache
            result_before = cloud.kb_manager.list_shared_kbs("bob")
            assert any(kb["id"] == "research" for kb in result_before)

            from fastapi.testclient import TestClient
            client = TestClient(cloud.app)
            resp = client.delete(
                "/api/v1/knowledge-bases/members/bob",
                headers={"X-User-ID": "alice", "X-KB-ID": "research"},
            )
            assert resp.status_code == 200

            # Cache invalidated — rescan returns empty
            result_after = cloud.kb_manager.list_shared_kbs("bob")
            assert not any(kb["id"] == "research" for kb in result_after)
        finally:
            cloud.kb_manager.base_path = orig


# ═══════════════════════════════════════════════════════════════════════════
# Group 6: Owner field auto-migration
# ═══════════════════════════════════════════════════════════════════════════

class TestOwnerFieldMigration:
    """
    Old KBs (no owner field in .kbaconfig) must be migrated transparently
    the first time a member is added or removed by the owner.
    """

    def _make_legacy_kb(self, base_path, user_id, kb_id):
        """Create a KB with no owner field — simulates a pre-migration KB."""
        kb_root = base_path / user_id / kb_id
        kb_root.mkdir(parents=True, exist_ok=True)
        # Intentionally no 'owner' field
        (kb_root / ".kbaconfig").write_text(f"name: {kb_id}\nversion: '1.0'\n")
        return kb_root

    def test_add_member_migrates_owner_field(self, tmp_path):
        import yaml
        from fastapi.testclient import TestClient

        self._make_legacy_kb(tmp_path, "alice", "legacy-kb")
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            client = TestClient(cloud.app)
            resp = client.post(
                "/api/v1/knowledge-bases/members",
                headers={"X-User-ID": "alice", "X-KB-ID": "legacy-kb"},
                json={"member_user_id": "bob", "role": "read"},
            )
            assert resp.status_code == 200

            config = yaml.safe_load(
                (tmp_path / "alice" / "legacy-kb" / ".kbaconfig").read_text()
            )
            assert config.get("owner") == "alice", (
                f"owner field should be written on first member add. Got: {config}"
            )
        finally:
            cloud.kb_manager.base_path = orig

    def test_add_member_legacy_kb_allows_owner(self, tmp_path):
        """Owner (derived from path) must be allowed to add members to a legacy KB."""
        from fastapi.testclient import TestClient

        self._make_legacy_kb(tmp_path, "alice", "legacy-kb")
        orig = cloud.kb_manager.base_path
        cloud.kb_manager.base_path = tmp_path
        try:
            client = TestClient(cloud.app)
            resp = client.post(
                "/api/v1/knowledge-bases/members",
                headers={"X-User-ID": "alice", "X-KB-ID": "legacy-kb"},
                json={"member_user_id": "carol", "role": "write"},
            )
            assert resp.status_code == 200
        finally:
            cloud.kb_manager.base_path = orig
