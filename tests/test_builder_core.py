"""
Tests for builder core modules.

Coverage targets (previously zero):
  lifecycle.py  — 5-state machine, auto-stale/archive, index apply
  registry.py   — ContentRegistry CRUD + atomic save, detect_pipeline, detect_file_type
  llm.py        — _strip_code_fence, detect_backend, compile_concepts (mocked), retry
  ingest.py     — DataIngest.scan, add_to_index
  kbapi.py      — KnowledgeBase.add routing, pipeline order, status()

All tests are offline (no real LLM calls, no real PDF extraction).
"""

import json
import os
import sys
import time
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ── Path setup ───────────────────────────────────────────────────────────────
_REPO      = Path(__file__).resolve().parent.parent
_CORE      = _REPO / "builder" / "src" / "core"
_BUILDER   = _REPO / "builder" / "src"

for _p in (str(_BUILDER), str(_CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ═══════════════════════════════════════════════════════════════════════════
# 1. lifecycle.py
# ═══════════════════════════════════════════════════════════════════════════

import lifecycle as lc


class TestLifecycleTransitions:

    def _fresh(self):
        """Minimal doc_info dict with no lifecycle state."""
        return {}

    def test_new_doc_defaults_to_draft(self):
        info = self._fresh()
        assert lc.get_lifecycle(info)["state"] == "draft"

    def test_draft_to_reviewed_succeeds(self):
        info = self._fresh()
        ok = lc.transition(info, "reviewed", updated_by="alice")
        assert ok is True
        assert lc.get_lifecycle(info)["state"] == "reviewed"

    def test_draft_to_verified_is_invalid(self):
        """verified is not reachable directly from draft."""
        info = self._fresh()
        ok = lc.transition(info, "verified")
        assert ok is False

    def test_draft_to_archived_is_invalid(self):
        info = self._fresh()
        ok = lc.transition(info, "archived")
        assert ok is False

    def test_reviewed_to_verified_succeeds(self):
        info = self._fresh()
        lc.transition(info, "reviewed")
        ok = lc.transition(info, "verified")
        assert ok is True

    def test_reviewed_back_to_draft(self):
        """Reviewer finds issues — rolls back to draft."""
        info = self._fresh()
        lc.transition(info, "reviewed")
        ok = lc.transition(info, "draft", updated_by="bob", reason="needs revision")
        assert ok is True
        assert lc.get_lifecycle(info)["state"] == "draft"

    def test_invalid_state_name_rejected(self):
        info = self._fresh()
        ok = lc.transition(info, "published")  # not a valid state
        assert ok is False
        assert lc.get_lifecycle(info)["state"] == "draft"

    def test_history_appended_on_each_transition(self):
        info = self._fresh()
        lc.transition(info, "reviewed")
        lc.transition(info, "verified")
        h = lc.get_lifecycle(info)["history"]
        assert len(h) == 2
        assert h[0]["from"] == "draft"   and h[0]["to"] == "reviewed"
        assert h[1]["from"] == "reviewed" and h[1]["to"] == "verified"

    def test_history_capped_at_20_entries(self):
        info = self._fresh()
        # Pump transitions until history > 20
        for _ in range(12):
            lc.transition(info, "reviewed")
            lc.transition(info, "draft")
        h = lc.get_lifecycle(info)["history"]
        assert len(h) <= 20

    def test_updated_by_recorded(self):
        info = self._fresh()
        lc.transition(info, "reviewed", updated_by="charlie")
        assert lc.get_lifecycle(info)["updated_by"] == "charlie"


class TestAutoAdvanceStale:

    def _doc_last_updated(self, days_ago: int, state: str = "reviewed") -> dict:
        """Build a doc_info that was last updated `days_ago` days ago."""
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        return {
            "lifecycle": {
                "state": state,
                "updated_at": ts,
                "entered_at": ts,
                "history": [],
            }
        }

    def test_fresh_doc_not_staled(self):
        info = self._doc_last_updated(10)
        result = lc.auto_advance_stale(info)
        assert result is None
        assert info["lifecycle"]["state"] == "reviewed"

    def test_doc_staled_after_90_days(self):
        info = self._doc_last_updated(91, state="reviewed")
        result = lc.auto_advance_stale(info)
        assert result == "stale"
        assert info["lifecycle"]["state"] == "stale"

    def test_verified_doc_staled_after_90_days(self):
        info = self._doc_last_updated(91, state="verified")
        result = lc.auto_advance_stale(info)
        assert result == "stale"

    def test_stale_doc_archived_after_180_days(self):
        info = self._doc_last_updated(181, state="stale")
        result = lc.auto_advance_stale(info)
        assert result == "archived"

    def test_doc_with_no_timestamp_not_changed(self):
        info = {"lifecycle": {"state": "reviewed"}}
        result = lc.auto_advance_stale(info)
        assert result is None


class TestApplyLifecycleToIndex:

    def test_changes_written_to_index_file(self, tmp_path):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=95)).isoformat()
        idx = {
            "files": {
                "doc1": {
                    "status": "completed",
                    "lifecycle": {
                        "state": "reviewed",
                        "updated_at": old_ts,
                        "entered_at": old_ts,
                        "history": [],
                    },
                },
                "doc2": {
                    "status": "completed",
                    "lifecycle": {
                        "state": "draft",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "entered_at": datetime.now(timezone.utc).isoformat(),
                        "history": [],
                    },
                },
            }
        }
        index_path = tmp_path / "file_index.json"
        index_path.write_text(json.dumps(idx))

        changes = lc.apply_lifecycle_to_index(index_path)
        assert "doc1" in changes
        assert changes["doc1"] == "stale"
        assert "doc2" not in changes

        # Verify written to disk
        written = json.loads(index_path.read_text())
        assert written["files"]["doc1"]["lifecycle"]["state"] == "stale"
        assert written["files"]["doc2"]["lifecycle"]["state"] == "draft"

    def test_missing_index_returns_empty(self, tmp_path):
        result = lc.apply_lifecycle_to_index(tmp_path / "does_not_exist.json")
        assert result == {}

    def test_lifecycle_stats_counts_states(self, tmp_path):
        now = datetime.now(timezone.utc).isoformat()
        idx = {
            "files": {
                f"d{i}": {
                    "status": "completed",
                    "lifecycle": {"state": s, "updated_at": now, "history": []},
                }
                for i, s in enumerate(["draft", "reviewed", "reviewed", "stale", "archived"])
            }
        }
        index_path = tmp_path / "file_index.json"
        index_path.write_text(json.dumps(idx))
        stats = lc.lifecycle_stats(index_path)
        assert stats["total"] == 5
        assert stats["states"]["draft"]    == 1
        assert stats["states"]["reviewed"] == 2
        assert stats["states"]["stale"]    == 1
        assert stats["states"]["archived"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. registry.py
# ═══════════════════════════════════════════════════════════════════════════

from registry import ContentRegistry, detect_pipeline, detect_file_type, STATUS_DONE, STATUS_FAILED, STATUS_PENDING


class TestContentRegistry:

    @pytest.fixture
    def reg(self, tmp_path):
        return ContentRegistry(str(tmp_path))

    def test_register_creates_entry(self, reg):
        key = reg.register("raw/books/doc.pdf", "pdf", "graphify-first", source="/tmp/doc.pdf")
        assert key == "raw/books/doc.pdf"
        entry = reg.get("raw/books/doc.pdf")
        assert entry is not None
        assert entry["file_type"] == "pdf"
        assert entry["pipeline"] == "graphify-first"

    def test_register_is_idempotent(self, reg):
        reg.register("raw/a.pdf", "pdf", "graphify-first")
        reg.register("raw/a.pdf", "pdf", "graphify-first")  # second call
        assert len(reg.data["entries"]) == 1

    def test_set_stage_done(self, reg):
        reg.register("raw/a.pdf", "pdf", "graphify-first")
        reg.set_stage("raw/a.pdf", "ingest", STATUS_DONE)
        entry = reg.get("raw/a.pdf")
        assert entry["ingest"]["status"] == STATUS_DONE
        assert entry["ingest"]["completed_at"] is not None

    def test_set_stage_failed_records_error(self, reg):
        reg.register("raw/a.pdf", "pdf", "graphify-first")
        reg.set_stage("raw/a.pdf", "compile_llm", STATUS_FAILED, error="API timeout")
        entry = reg.get("raw/a.pdf")
        assert entry["compile_llm"]["status"] == STATUS_FAILED
        assert "API timeout" in entry["compile_llm"]["error"]

    def test_set_stage_rejects_unknown_stage(self, reg):
        reg.register("raw/a.pdf", "pdf", "graphify-first")
        reg.set_stage("raw/a.pdf", "nonexistent_stage", STATUS_DONE)
        entry = reg.get("raw/a.pdf")
        assert "nonexistent_stage" not in entry

    def test_set_stage_silently_ignores_missing_entry(self, reg):
        reg.set_stage("no/such/file.pdf", "ingest", STATUS_DONE)  # must not raise

    def test_stats_counts_correctly(self, reg):
        reg.register("a.pdf", "pdf", "graphify-first")
        reg.register("b.pdf", "pdf", "graphify-first")
        reg.set_stage("a.pdf", "ingest", STATUS_DONE)
        reg.set_stage("a.pdf", "compile_llm", STATUS_DONE)
        reg.set_stage("b.pdf", "ingest", STATUS_FAILED)
        stats = reg.stats()
        assert stats["total"] == 2
        assert stats["ingested"] == 1
        assert stats["compiled"] == 1
        assert stats["failed"] == 1

    def test_list_by_stage_filters_pending(self, reg):
        reg.register("a.pdf", "pdf", "graphify-first")
        reg.register("b.pdf", "pdf", "graphify-first")
        reg.set_stage("a.pdf", "ingest", STATUS_DONE)
        pending = reg.list_by_stage("ingest", STATUS_PENDING)
        assert len(pending) == 1
        assert pending[0]["source_path"] == "b.pdf"

    def test_save_load_roundtrip(self, tmp_path):
        reg = ContentRegistry(str(tmp_path))
        reg.register("raw/c.pdf", "pdf", "graphify-first")
        reg.set_stage("raw/c.pdf", "ingest", STATUS_DONE)
        reg.save()

        reg2 = ContentRegistry(str(tmp_path))
        entry = reg2.get("raw/c.pdf")
        assert entry is not None
        assert entry["ingest"]["status"] == STATUS_DONE

    def test_save_is_atomic_no_partial_write(self, tmp_path):
        """save() must use a temp file + rename — no partial JSON on crash."""
        reg = ContentRegistry(str(tmp_path))
        reg.register("raw/x.pdf", "pdf", "graphify-first")
        reg.save()
        # Verify the file is valid JSON (not half-written)
        data = json.loads((tmp_path / ".kbregistry.json").read_text())
        assert "entries" in data
        assert "raw/x.pdf" in data["entries"]

    def test_corrupted_file_resets_to_empty(self, tmp_path):
        (tmp_path / ".kbregistry.json").write_text("not json {{{")
        reg = ContentRegistry(str(tmp_path))
        assert reg.data == {"entries": {}, "meta": {}}


class TestDetectPipeline:

    def test_pdf_is_graphify_first(self):
        assert detect_pipeline("paper.pdf") == "graphify-first"

    def test_epub_is_graphify_first(self):
        assert detect_pipeline("book.epub") == "graphify-first"

    def test_txt_is_graphify_first(self):
        assert detect_pipeline("notes.txt") == "graphify-first"

    def test_md_is_compile_first(self):
        assert detect_pipeline("article.md") == "compile-first"

    def test_markdown_is_compile_first(self):
        assert detect_pipeline("notes.markdown") == "compile-first"

    def test_config_rule_overrides_extension(self, tmp_path):
        """A config rule matching 'notes_*.txt' → compile-first overrides default."""
        config = {"pipeline": {"rules": [
            {"match": "notes_*.txt", "pipeline": "compile-first"}
        ]}}
        assert detect_pipeline("notes_weekly.txt", config) == "compile-first"
        assert detect_pipeline("paper.txt",        config) == "graphify-first"

    def test_config_default_used_for_unknown_extension(self, tmp_path):
        config = {"pipeline": {"default": "compile-first"}}
        assert detect_pipeline("file.xyz", config) == "compile-first"

    def test_content_sniffing_finds_headings(self, tmp_path):
        """A .xyz file with markdown headings → compile-first via content sniff."""
        f = tmp_path / "structured.xyz"
        f.write_text("## Introduction\n\nSome content here.\n")
        assert detect_pipeline(str(f)) == "compile-first"

    def test_unknown_extension_no_config_falls_back_to_graphify_first(self):
        assert detect_pipeline("archive.tar") == "graphify-first"


class TestDetectFileType:

    def test_pdf(self):   assert detect_file_type("doc.pdf")      == "pdf"
    def test_epub(self):  assert detect_file_type("book.epub")     == "epub"
    def test_md(self):    assert detect_file_type("note.md")       == "md"
    def test_markdown(self): assert detect_file_type("x.markdown") == "md"
    def test_txt(self):   assert detect_file_type("raw.txt")       == "txt"
    def test_unknown(self): assert detect_file_type("data.csv")    == "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# 3. llm.py — pure helpers + mocked network
# ═══════════════════════════════════════════════════════════════════════════

import urllib.error
import core.llm as llm_mod


class TestStripCodeFence:

    def test_plain_text_unchanged(self):
        assert llm_mod._strip_code_fence("hello world") == "hello world"

    def test_bare_fence_stripped(self):
        result = llm_mod._strip_code_fence("```\nhello\n```")
        assert result == "hello"

    def test_language_tag_stripped(self):
        result = llm_mod._strip_code_fence("```python\nprint('hi')\n```")
        assert result == "print('hi')"

    def test_unclosed_fence_unchanged(self):
        text = "```python\nprint('hi')"
        assert llm_mod._strip_code_fence(text) == text

    def test_empty_fence_stripped_to_empty(self):
        result = llm_mod._strip_code_fence("```\n\n```")
        assert result == ""


class TestDetectBackend:

    def test_returns_none_when_no_keys_set(self, monkeypatch):
        for cfg in llm_mod.BACKENDS.values():
            monkeypatch.delenv(cfg["api_key_env"], raising=False)
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        assert llm_mod.detect_backend() is None

    def test_detects_openai_from_env(self, monkeypatch):
        # Clear all, then set only OpenAI
        for cfg in llm_mod.BACKENDS.values():
            monkeypatch.delenv(cfg["api_key_env"], raising=False)
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        backend = llm_mod.detect_backend()
        assert backend == "openai"

    def test_llm_backend_env_overrides_auto(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        monkeypatch.setenv("OPENAI_API_KEY",   "sk-openai")
        monkeypatch.setenv("LLM_BACKEND", "openai")
        assert llm_mod.detect_backend() == "openai"

    def test_list_available_returns_only_backends_with_keys(self, monkeypatch):
        for cfg in llm_mod.BACKENDS.values():
            monkeypatch.delenv(cfg["api_key_env"], raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        available = llm_mod.list_available()
        assert available == ["deepseek"]

    def test_make_config_returns_dict_with_model(self):
        cfg = llm_mod.make_config("openai")
        assert "model" in cfg
        assert "aux_model" in cfg
        assert cfg["model"] == cfg["aux_model"]


class TestRetryWithBackoff:

    def test_succeeds_on_first_try(self):
        calls = []
        def fn():
            calls.append(1)
            return "ok"
        result = llm_mod._retry_with_backoff(fn, max_attempts=3, base_delay=0)
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)
            return "ok"
        result = llm_mod._retry_with_backoff(fn, max_attempts=3, base_delay=0.001)
        assert result == "ok"
        assert len(calls) == 2

    def test_does_not_retry_on_400(self):
        def fn():
            raise urllib.error.HTTPError(None, 400, "Bad Request", {}, None)
        with pytest.raises(urllib.error.HTTPError) as exc:
            llm_mod._retry_with_backoff(fn, max_attempts=3, base_delay=0)
        assert exc.value.code == 400

    def test_exhausted_retries_raises(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        def fn():
            raise urllib.error.HTTPError(None, 500, "Server Error", {}, None)
        with pytest.raises(urllib.error.HTTPError):
            llm_mod._retry_with_backoff(fn, max_attempts=2, base_delay=0.001)


class TestCompileConceptsMocked:

    def test_returns_parsed_json_on_success(self, monkeypatch):
        fake_response = {
            "content": '{"concepts": ["A", "B"], "summary": "Test", "topics": ["T"]}',
            "backend": "openai",
            "input_tokens": 10,
            "output_tokens": 20,
        }
        monkeypatch.setattr(llm_mod, "chat", lambda *a, **kw: fake_response)
        result = llm_mod.compile_concepts("Some article text.", "test.pdf")
        assert result["concepts"] == ["A", "B"]
        assert result["summary"] == "Test"
        assert result["_backend"] == "openai"

    def test_handles_json_parse_error_gracefully(self, monkeypatch):
        fake_response = {
            "content": "This is not JSON at all.",
            "backend": "openai",
            "input_tokens": 5,
            "output_tokens": 5,
        }
        monkeypatch.setattr(llm_mod, "chat", lambda *a, **kw: fake_response)
        result = llm_mod.compile_concepts("Some text", "doc.pdf")
        assert result["concepts"] == []
        assert result["_parse_error"] is True

    def test_strips_code_fence_before_parse(self, monkeypatch):
        fake_response = {
            "content": '```json\n{"concepts": ["X"], "summary": "S", "topics": []}\n```',
            "backend": "deepseek",
            "input_tokens": 5,
            "output_tokens": 5,
        }
        monkeypatch.setattr(llm_mod, "chat", lambda *a, **kw: fake_response)
        result = llm_mod.compile_concepts("text", "doc.pdf")
        assert result["concepts"] == ["X"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. ingest.py — scan and index (no real doc extraction)
# ═══════════════════════════════════════════════════════════════════════════

from ingest import DataIngest


class TestDataIngestScan:

    @pytest.fixture
    def kb(self, tmp_path):
        """Minimal KB directory structure."""
        raw = tmp_path / "raw"
        raw.mkdir()
        (tmp_path / ".kbaconfig").write_text("name: Test\n")
        return tmp_path

    def test_scan_empty_raw_returns_all_empty(self, kb):
        ingest = DataIngest(str(kb))
        result = ingest.scan()
        assert result["new"] == []
        assert result["changed"] == []
        assert result["unchanged"] == []

    def test_scan_picks_up_new_pdf(self, kb):
        pdf = kb / "raw" / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")
        ingest = DataIngest(str(kb))
        result = ingest.scan()
        assert len(result["new"]) == 1
        assert result["new"][0].endswith("paper.pdf")

    def test_scan_unchanged_after_index(self, kb):
        pdf = kb / "raw" / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")
        ingest = DataIngest(str(kb))
        ingest.add_to_index(str(pdf))
        # Now scan — file is indexed and unchanged
        result = ingest.scan()
        assert str(pdf) in result["unchanged"]
        assert result["new"] == []

    def test_add_to_index_returns_file_id(self, kb):
        pdf = kb / "raw" / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        ingest = DataIngest(str(kb))
        file_id = ingest.add_to_index(str(pdf))
        assert isinstance(file_id, str)
        assert len(file_id) > 0

    def test_scan_missing_raw_dir_creates_it(self, tmp_path):
        """DataIngest should create raw/ if it doesn't exist."""
        (tmp_path / ".kbaconfig").write_text("name: Test\n")
        ingest = DataIngest(str(tmp_path))
        result = ingest.scan()
        assert (tmp_path / "raw").exists()
        assert result["new"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. kbapi.py — pipeline routing and status (stage runners mocked)
# ═══════════════════════════════════════════════════════════════════════════

from kbapi import KnowledgeBase


@pytest.fixture
def minimal_kb(tmp_path):
    """A minimal KB directory with .kbaconfig and .kbregistry.json."""
    (tmp_path / ".kbaconfig").write_text("name: Test KB\n")
    (tmp_path / "raw").mkdir()
    return tmp_path


@pytest.fixture
def source_pdf(tmp_path):
    """A fake PDF source file to add."""
    f = tmp_path / "source_files" / "paper.pdf"
    f.parent.mkdir()
    f.write_bytes(b"%PDF-1.4 fake content")
    return f


@pytest.fixture
def source_md(tmp_path):
    """A fake Markdown source file to add."""
    f = tmp_path / "source_files" / "article.md"
    f.parent.mkdir()
    f.write_text("# Introduction\n\nSome text.\n")
    return f


class TestKnowledgeBaseAdd:

    def test_add_missing_source_returns_error(self, minimal_kb):
        kb = KnowledgeBase(str(minimal_kb))
        result = kb.add("/nonexistent/file.pdf")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_add_directory_returns_error(self, minimal_kb):
        kb = KnowledgeBase(str(minimal_kb))
        result = kb.add(str(minimal_kb))   # pass the KB dir itself
        assert result["status"] == "error"
        assert "directory" in result["error"].lower()

    def test_add_pdf_copies_to_raw_books(self, minimal_kb, source_pdf):
        kb = KnowledgeBase(str(minimal_kb))
        with patch.object(kb, "_run_ingest",   return_value=True), \
             patch.object(kb, "_run_graphify", return_value=True), \
             patch.object(kb, "_run_compile",  return_value=True):
            result = kb.add(str(source_pdf))
        assert result["status"] == "ok"
        assert "raw/books" in result["rel_path"]
        dest = minimal_kb / result["rel_path"]
        assert dest.exists()

    def test_add_md_copies_to_raw_articles(self, minimal_kb, source_md):
        kb = KnowledgeBase(str(minimal_kb))
        with patch.object(kb, "_run_ingest",   return_value=True), \
             patch.object(kb, "_run_graphify", return_value=True), \
             patch.object(kb, "_run_compile",  return_value=True):
            result = kb.add(str(source_md))
        assert "raw/articles" in result["rel_path"]

    def test_add_pdf_auto_detects_graphify_first_pipeline(self, minimal_kb, source_pdf):
        kb = KnowledgeBase(str(minimal_kb))
        with patch.object(kb, "_run_ingest",   return_value=True), \
             patch.object(kb, "_run_graphify", return_value=True), \
             patch.object(kb, "_run_compile",  return_value=True):
            result = kb.add(str(source_pdf))
        assert result["pipeline"] == "graphify-first"

    def test_add_md_auto_detects_compile_first_pipeline(self, minimal_kb, source_md):
        kb = KnowledgeBase(str(minimal_kb))
        with patch.object(kb, "_run_ingest",   return_value=True), \
             patch.object(kb, "_run_graphify", return_value=True), \
             patch.object(kb, "_run_compile",  return_value=True):
            result = kb.add(str(source_md))
        assert result["pipeline"] == "compile-first"

    def test_graphify_first_runs_ingest_then_graphify_then_compile(self, minimal_kb, source_pdf):
        """Stage order: ingest → graphify → compile for graphify-first."""
        call_log = []
        kb = KnowledgeBase(str(minimal_kb))
        kb._run_ingest   = lambda **kw: call_log.append("ingest")   or True
        kb._run_graphify = lambda **kw: call_log.append("graphify") or True
        kb._run_compile  = lambda **kw: call_log.append("compile")  or True
        kb.add(str(source_pdf))
        assert call_log == ["ingest", "graphify", "compile"]

    def test_compile_first_runs_ingest_then_compile_then_graphify(self, minimal_kb, source_md):
        """Stage order: ingest → compile → graphify for compile-first."""
        call_log = []
        kb = KnowledgeBase(str(minimal_kb))
        kb._run_ingest   = lambda **kw: call_log.append("ingest")   or True
        kb._run_graphify = lambda **kw: call_log.append("graphify") or True
        kb._run_compile  = lambda **kw: call_log.append("compile")  or True
        kb.add(str(source_md))
        assert call_log == ["ingest", "compile", "graphify"]

    def test_failed_stage_recorded_in_registry(self, minimal_kb, source_pdf):
        kb = KnowledgeBase(str(minimal_kb))
        with patch.object(kb, "_run_ingest",   return_value=False), \
             patch.object(kb, "_run_graphify", return_value=True), \
             patch.object(kb, "_run_compile",  return_value=True):
            result = kb.add(str(source_pdf))
        assert result["stages"]["ingest"] == "failed"

    def test_add_duplicate_filename_gets_counter_suffix(self, minimal_kb, source_pdf):
        kb = KnowledgeBase(str(minimal_kb))
        with patch.object(kb, "_run_ingest",   return_value=True), \
             patch.object(kb, "_run_graphify", return_value=True), \
             patch.object(kb, "_run_compile",  return_value=True):
            r1 = kb.add(str(source_pdf))
            r2 = kb.add(str(source_pdf))
        assert r1["rel_path"] != r2["rel_path"]
        assert "_1" in r2["rel_path"]


class TestKnowledgeBaseStatus:

    def test_status_returns_kb_name(self, minimal_kb):
        kb = KnowledgeBase(str(minimal_kb))
        s = kb.status()
        assert s["name"] == "Test KB"

    def test_status_includes_registry_stats(self, minimal_kb):
        kb = KnowledgeBase(str(minimal_kb))
        s = kb.status()
        assert "registry" in s
        assert "total" in s["registry"]

    def test_status_shows_zero_before_any_add(self, minimal_kb):
        kb = KnowledgeBase(str(minimal_kb))
        s = kb.status()
        assert s["registry"]["total"] == 0


class TestKnowledgeBaseInit:

    def test_raises_for_nonexistent_path(self):
        with pytest.raises(FileNotFoundError):
            KnowledgeBase("/no/such/path")

    def test_loads_config_name(self, tmp_path):
        (tmp_path / ".kbaconfig").write_text("name: My Research\n")
        kb = KnowledgeBase(str(tmp_path))
        assert kb.config["name"] == "My Research"

    def test_defaults_name_to_directory_name_when_no_config(self, tmp_path):
        # No .kbaconfig — name defaults to directory name
        kb = KnowledgeBase(str(tmp_path))
        assert kb.config["name"] == tmp_path.name
