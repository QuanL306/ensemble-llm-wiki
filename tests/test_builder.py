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


class TestCompileWithRetry:
    """
    API retry wrapper: LLMTransientError triggers retry,
    LLMPermanentError fails immediately.
    """

    def test_success_on_first_call_returns_article(self):
        expected = "# Article\n\nContent here."
        with patch.object(cli, "_compile_document", return_value=expected):
            result, error = cli._compile_with_retry(
                "deepseek", "deepseek-chat", {}, "/tmp"
            )
        assert error is None
        assert result == expected

    def test_retries_after_transient_error_then_succeeds(self):
        """LLMTransientError triggers retry; succeed on second attempt."""
        call_count = 0
        from core.llm import LLMTransientError

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMTransientError("transient failure")
            return "# Article after retry"

        with patch.object(cli, "_compile_document", side_effect=side_effect), \
             patch("time.sleep"):
            result, error = cli._compile_with_retry(
                "deepseek", "deepseek-chat", {}, "/tmp", max_retries=3
            )

        assert error is None, f"Expected success, got error: {error}"
        assert result == "# Article after retry"
        assert call_count == 2, f"Expected 2 calls (1 fail + 1 success), got {call_count}"

    def test_permanent_error_fails_immediately(self):
        """LLMPermanentError must NOT be retried."""
        call_count = 0
        from core.llm import LLMPermanentError

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise LLMPermanentError("bad auth")

        with patch.object(cli, "_compile_document", side_effect=side_effect), \
             patch("time.sleep"):
            result, error = cli._compile_with_retry(
                "deepseek", "deepseek-chat", {}, "/tmp", max_retries=3
            )

        assert result is None
        assert error is not None
        assert call_count == 1, (
            f"Expected exactly 1 call (no retry for permanent), got {call_count}"
        )

    def test_exhausted_retries_returns_error_string(self):
        """After all retries fail, returns (None, non-empty error string)."""
        from core.llm import LLMTransientError
        with patch.object(
            cli,
            "_compile_document",
            side_effect=LLMTransientError("persistent failure"),
        ), patch("time.sleep"):
            result, error = cli._compile_with_retry(
                "deepseek", "deepseek-chat", {}, "/tmp", max_retries=2
            )

        assert result is None
        assert isinstance(error, str) and len(error) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Group 4: _scrub_sensitive — transcript privacy scrubbing
# ═══════════════════════════════════════════════════════════════════════════

import importlib.util as _ilu
import os as _os

def _load_harvester():
    path = _os.path.abspath(_os.path.join(
        _os.path.dirname(__file__), "..", "builder", "src", "core", "transcript_harvester.py"
    ))
    spec = _ilu.spec_from_file_location("transcript_harvester", path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_harvester = _load_harvester()


class TestScrubSensitive:
    """_scrub_sensitive must redact common credential patterns."""

    def test_password_eq_value_is_redacted(self):
        out = _harvester._scrub_sensitive("password=hunter2")
        assert "hunter2" not in out
        assert "[REDACTED]" in out

    def test_password_colon_value_is_redacted(self):
        out = _harvester._scrub_sensitive("password: mysecret")
        assert "mysecret" not in out

    def test_token_eq_value_is_redacted(self):
        out = _harvester._scrub_sensitive("token=eyJhbGciOiJIUzI1NiJ9.abc123")
        assert "eyJhbGciOiJIUzI1NiJ9" not in out

    def test_openai_key_is_redacted(self):
        out = _harvester._scrub_sensitive("key is sk-abcdefghij1234567890ABCDEF")
        assert "sk-abcdefghij" not in out
        assert "sk-[REDACTED]" in out

    def test_aws_key_is_redacted(self):
        out = _harvester._scrub_sensitive("AKIAIOSFODNN7EXAMPLE is the key")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert "AKIA[REDACTED]" in out

    def test_github_token_is_redacted(self):
        tok = "ghp_" + "A" * 36
        out = _harvester._scrub_sensitive(f"token={tok}")
        assert tok not in out

    def test_bearer_token_is_redacted(self):
        out = _harvester._scrub_sensitive("Authorization: Bearer eyJsomeLongToken1234567890")
        assert "eyJsomeLongToken" not in out
        assert "[REDACTED]" in out

    def test_plain_text_unchanged(self):
        text = "The quick brown fox jumps over the lazy dog."
        assert _harvester._scrub_sensitive(text) == text

    def test_scrub_applied_in_session_to_markdown(self, tmp_path):
        """session_to_markdown must scrub both user and assistant text."""
        from datetime import date
        session = _harvester.Session(
            slug="test-session",
            source="claude-code",
            exchanges=[
                _harvester.Exchange(
                    user="my password=topsecret123",
                    assistant="here is sk-abc123defghijklmnopqrstuv",
                )
            ],
        )
        md = _harvester.session_to_markdown(session)
        assert "topsecret123" not in md
        assert "sk-abc123defghijklmnopqrstuv" not in md
        assert "[REDACTED]" in md
