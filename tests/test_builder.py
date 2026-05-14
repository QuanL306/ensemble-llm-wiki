"""
Unit tests for builder/src/cli.py helper functions.

Groups:
  1. _extract_retrieval_queries  — wiki article section parser
  2. _chunk_document             — long-document chunker
  3. _compile_with_retry         — API retry wrapper (requires anthropic SDK)
"""

import os
import sys
import importlib.util
import pytest
from unittest.mock import patch, MagicMock


# ── Load cli module ──────────────────────────────────────────────────────────

def _load_cli():
    cli_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "builder", "src", "cli.py")
    )
    spec = importlib.util.spec_from_file_location("cli", cli_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()


# ═══════════════════════════════════════════════════════════════════════════
# Group 1: _extract_retrieval_queries
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractRetrievalQueries:
    """Parse '## For Future Queries' section from a compiled wiki article."""

    def test_returns_all_15_queries(self, sample_article):
        queries = cli._extract_retrieval_queries(sample_article)
        assert len(queries) == 15, (
            f"Expected 15 retrieval queries, got {len(queries)}"
        )

    def test_strips_leading_number_and_period(self, sample_article):
        queries = cli._extract_retrieval_queries(sample_article)
        for q in queries:
            assert not (q and q[0].isdigit()), (
                f"Query still starts with a digit: {q!r}"
            )

    def test_returns_empty_when_section_absent(self):
        article = "## Summary\n\nNo retrieval section present.\n\n## Core Arguments\n\nStuff."
        result = cli._extract_retrieval_queries(article)
        assert result == []

    def test_strips_surrounding_ascii_and_curly_quotes(self):
        article = (
            "## For Future Queries\n\n"
            '1. "If someone asks about X, this is relevant because Y."\n'
            "2. \u201cIf someone asks about A, this is relevant because B.\u201d\n"
        )
        queries = cli._extract_retrieval_queries(article)
        assert len(queries) == 2
        for q in queries:
            assert not q.startswith('"'), f"ASCII quote not stripped: {q!r}"
            assert not q.startswith("\u201c"), f"Curly quote not stripped: {q!r}"

    def test_each_query_is_a_non_empty_string(self, sample_article):
        queries = cli._extract_retrieval_queries(sample_article)
        for q in queries:
            assert isinstance(q, str)
            assert len(q) > 15, f"Query suspiciously short: {q!r}"


# ═══════════════════════════════════════════════════════════════════════════
# Group 2: _chunk_document
# ═══════════════════════════════════════════════════════════════════════════

class TestChunkDocument:
    """Split long documents into retrievable chapter-level sections."""

    # ── threshold ────────────────────────────────────────────────────────────

    def test_short_document_returns_empty_list(self):
        """Documents under 5 000 words should not be chunked."""
        text = "word " * 4_500
        assert cli._chunk_document(text) == []

    def test_4999_words_returns_empty(self):
        """The threshold is strictly < 5000, so 4999 words should not be chunked."""
        text = "word " * 4_999
        assert cli._chunk_document(text) == []

    def test_5000_words_is_chunked(self):
        """Exactly 5000 words is NOT below the threshold — should produce chunks."""
        text = "word " * 5_000
        chunks = cli._chunk_document(text)
        assert len(chunks) >= 1, "5000-word doc should produce at least one chunk"

    # ── header-based splitting ───────────────────────────────────────────────

    def test_markdown_h2_headers_produce_chunks(self):
        section = "content word " * 250   # 500 words per section
        text = "\n".join(
            f"## Section {i}\n\n{section}" for i in range(1, 15)
        )
        chunks = cli._chunk_document(text)
        assert len(chunks) >= 3, (
            f"Expected ≥3 chunks from ## headers, got {len(chunks)}"
        )

    def test_chunk_titles_come_from_headers(self):
        section = "content word " * 250
        text = "\n".join(
            f"## Chapter {i}: My Title\n\n{section}" for i in range(1, 15)
        )
        chunks = cli._chunk_document(text)
        titles = [c["title"] for c in chunks]
        assert any("Chapter" in t or "My Title" in t for t in titles), (
            f"None of the chunk titles look like the headers: {titles}"
        )

    # ── fallback window chunking ─────────────────────────────────────────────

    def test_plain_text_falls_back_to_windows(self):
        """No structural markers → fall back to 600-word sliding windows."""
        text = "uniqueword " * 6_000
        chunks = cli._chunk_document(text)
        assert len(chunks) >= 2, (
            "Expected sliding-window chunks for plain long text"
        )

    # ── chunk schema ─────────────────────────────────────────────────────────

    def test_chunks_have_required_fields(self):
        text = "word " * 6_000
        chunks = cli._chunk_document(text)
        for c in chunks:
            assert "id" in c,         f"Chunk missing 'id': {c}"
            assert "title" in c,      f"Chunk missing 'title': {c}"
            assert "preview" in c,    f"Chunk missing 'preview': {c}"
            assert "word_count" in c, f"Chunk missing 'word_count': {c}"

    def test_chunk_ids_are_unique(self):
        text = "word " * 6_000
        chunks = cli._chunk_document(text)
        ids = [c["id"] for c in chunks]
        assert len(set(ids)) == len(ids), "Duplicate chunk IDs found"


# ═══════════════════════════════════════════════════════════════════════════
# Group 3: _compile_with_retry
# ═══════════════════════════════════════════════════════════════════════════

try:
    import anthropic as _anthropic_mod
    _HAS_ANTHROPIC = True
except ImportError:
    _anthropic_mod = None
    _HAS_ANTHROPIC = False


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="anthropic SDK not installed")
class TestCompileWithRetry:
    """
    API retry wrapper: exponential backoff on transient errors,
    immediate failure on 4xx errors.
    """

    _DUMMY_CLIENT = MagicMock()

    def test_success_on_first_call_returns_article(self):
        expected = "# Article\n\nContent here."
        with patch.object(cli, "_compile_document", return_value=expected):
            result, error = cli._compile_with_retry(
                self._DUMMY_CLIENT, "claude", "claude-sonnet-4-5", {}, "/tmp"
            )
        assert error is None
        assert result == expected

    def test_retries_after_rate_limit_then_succeeds(self):
        """Should retry and succeed on the second attempt."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                raise _anthropic_mod.RateLimitError(
                    "rate limited", response=mock_resp, body={}
                )
            return "# Article after retry"

        with patch.object(cli, "_compile_document", side_effect=side_effect), \
             patch("time.sleep"):
            result, error = cli._compile_with_retry(
                self._DUMMY_CLIENT, "claude", "claude-sonnet-4-5", {}, "/tmp", max_retries=3
            )

        assert error is None, f"Expected success, got error: {error}"
        assert result == "# Article after retry"
        assert call_count == 2, f"Expected 2 calls (1 fail + 1 success), got {call_count}"

    def test_4xx_error_returns_error_without_retry(self):
        """Client errors (4xx) must not be retried."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            raise _anthropic_mod.BadRequestError(
                "bad request", response=mock_resp, body={}
            )

        with patch.object(cli, "_compile_document", side_effect=side_effect), \
             patch("time.sleep"):
            result, error = cli._compile_with_retry(
                self._DUMMY_CLIENT, "claude", "claude-sonnet-4-5", {}, "/tmp", max_retries=3
            )

        assert result is None
        assert error is not None
        assert call_count == 1, (
            f"Expected exactly 1 call (no retry for 4xx), got {call_count}"
        )

    def test_exhausted_retries_returns_error_string(self):
        """After all retries fail, returns (None, non-empty error string)."""
        with patch.object(
            cli,
            "_compile_document",
            side_effect=_anthropic_mod.APIConnectionError(request=MagicMock()),
        ), patch("time.sleep"):
            result, error = cli._compile_with_retry(
                self._DUMMY_CLIENT, "claude", "claude-sonnet-4-5", {}, "/tmp", max_retries=2
            )

        assert result is None
        assert isinstance(error, str) and len(error) > 0
