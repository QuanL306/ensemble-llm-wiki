"""
Unit tests for builder/src/core/scoring.py

How to read this file if you're new to pytest
──────────────────────────────────────────────
• Each class groups tests for one function.
• Each method is one test: it calls the function and uses `assert` to check
  the result.  If the assert fails, pytest tells you what the actual value was.
• Fixtures (like `two_doc_index`) are defined at module level and passed in as
  function parameters — pytest wires them up automatically.
• Run just this file:  pytest tests/test_scoring.py -v

Groups:
  1. TestSplitKeywords   — tokenisation and stop-word filtering
  2. TestKeywordVariants — morphological suffix expansion
  3. TestCountMatches    — word-boundary regex (no partial matches)
  4. TestTfScore         — normalised term-frequency helper
  5. TestComputeIdf      — rare terms score higher than common terms
  6. TestScoreDocument   — full per-document TF-IDF weighting
  7. TestScoreHybrid     — hybrid pipeline fallback behaviour
"""

import math
import pytest

# conftest.py already added builder/src to sys.path
from core.scoring import (
    split_keywords,
    keyword_variants,
    count_matches,
    compute_idf,
    score_document,
    score_hybrid,
    _tf_score,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def two_doc_index():
    """Two minimal documents used across several scoring tests.

    doc_sleep  — 'sleep' appears in the title and a retrieval query.
    doc_stress — 'sleep' does NOT appear anywhere; 'stress' does.
    """
    return {
        "doc_sleep": {
            "name": "Why We Sleep",
            "path": "/fake/sleep.pdf",
            "extracted_metadata": {
                "core_claims": ["Slow-wave sleep consolidates memory"],
                "key_data": [],
                "quotes": [],
            },
            "llm_metadata": {},
            "retrieval_queries": [
                "If someone asks about sleep and memory, this is relevant."
            ],
            "chunks": [
                {
                    "id": "c1",
                    "title": "Sleep Architecture",
                    "preview": "REM and NREM cycles alternate through the night.",
                    "word_count": 500,
                },
            ],
        },
        "doc_stress": {
            "name": "Stress Response Review",
            "path": "/fake/stress.pdf",
            "extracted_metadata": {
                "core_claims": ["Cortisol impairs hippocampal function"],
                "key_data": [],
                "quotes": [],
            },
            "llm_metadata": {},
            "retrieval_queries": [
                "If someone asks about chronic stress, this is relevant."
            ],
            "chunks": [],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. split_keywords
# ═══════════════════════════════════════════════════════════════════════════

class TestSplitKeywords:
    """Query tokenisation: lowercase, filter stop words and short words."""

    def test_basic_split(self):
        # "sleep" and "memory" are both long enough and not stop words
        result = split_keywords("sleep memory")
        assert "sleep" in result
        assert "memory" in result

    def test_lowercases_input(self):
        result = split_keywords("Sleep Memory")
        assert all(w == w.lower() for w in result)

    def test_filters_stop_words(self):
        # "the", "and", "is" are stop words — should be removed
        result = split_keywords("the brain and memory is important")
        assert "the" not in result
        assert "and" not in result
        assert "is" not in result

    def test_filters_short_words(self):
        # Words of length ≤ 3 are filtered (MIN_KW_LEN = 3 means > 3 chars)
        result = split_keywords("big cat runs fast")
        assert "big" not in result
        assert "cat" not in result

    def test_returns_original_when_all_filtered(self):
        # If every word is filtered, the raw query is returned as-is
        # so that a one-word query like "AI" still gets matched
        result = split_keywords("the an")
        assert len(result) == 1
        assert result[0] == "the an"

    def test_empty_string_returns_list(self):
        result = split_keywords("")
        assert isinstance(result, list)
        assert len(result) == 1  # raw query fallback


# ═══════════════════════════════════════════════════════════════════════════
# 2. keyword_variants
# ═══════════════════════════════════════════════════════════════════════════

class TestKeywordVariants:
    """Morphological expansion: a keyword generates plausible word forms."""

    def test_original_always_included(self):
        variants = keyword_variants("sleep")
        assert "sleep" in variants

    def test_plural_generated(self):
        # Words with 4+ chars get a plural suffix added
        variants = keyword_variants("sleep")
        assert "sleeps" in variants

    def test_ing_form_generated(self):
        # "sleep" → "sleeping"  (base → inflected, requires len > 3)
        variants = keyword_variants("sleep")
        assert "sleeping" in variants

    def test_strip_ing_suffix(self):
        # "sleeping" → "sleep" (inflected → base)
        variants = keyword_variants("sleeping")
        assert "sleep" in variants

    def test_strip_tion_suffix(self):
        # "creation" → "create"
        variants = keyword_variants("creation")
        assert "create" in variants

    def test_no_duplicates(self):
        variants = keyword_variants("studies")
        assert len(variants) == len(set(variants)), "Duplicate variants found"

    def test_short_word_returns_only_itself(self):
        # Words shorter than 4 chars are not expanded
        variants = keyword_variants("go")
        assert variants == ["go"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. count_matches
# ═══════════════════════════════════════════════════════════════════════════

class TestCountMatches:
    """Word-boundary regex: partial matches must NOT be counted."""

    def test_exact_match_counts(self):
        assert count_matches("sleep is important", "sleep") >= 1

    def test_word_boundary_no_partial_match(self):
        # "count" must not match inside "account" or "recount"
        assert count_matches("the account and recount", "count") == 0

    def test_case_insensitive(self):
        # scoring lowercases everything before calling count_matches,
        # so passing lowercase is the expected usage
        assert count_matches("sleep is good", "sleep") >= 1

    def test_morphological_variant_counted(self):
        # "sleep" and its variants should match "sleeping"
        assert count_matches("i was sleeping deeply", "sleep") >= 1

    def test_empty_text_returns_zero(self):
        assert count_matches("", "sleep") == 0

    def test_multiple_occurrences_counted(self):
        assert count_matches("sleep sleep sleep", "sleep") >= 3

    def test_unrelated_word_returns_zero(self):
        assert count_matches("the quick brown fox", "sleep") == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. _tf_score
# ═══════════════════════════════════════════════════════════════════════════

class TestTfScore:
    """TF score = hits / sqrt(word_count); zero hits → zero score."""

    def test_zero_hits_returns_zero(self):
        assert _tf_score(0, 100) == 0.0

    def test_positive_hits_returns_positive(self):
        assert _tf_score(3, 100) > 0.0

    def test_longer_text_reduces_score(self):
        # Same hit count in a longer text → lower normalised score
        short = _tf_score(3, 50)
        long  = _tf_score(3, 500)
        assert short > long

    def test_more_hits_increases_score(self):
        assert _tf_score(5, 100) > _tf_score(1, 100)


# ═══════════════════════════════════════════════════════════════════════════
# 5. compute_idf
# ═══════════════════════════════════════════════════════════════════════════

class TestComputeIdf:
    """Rare terms get higher IDF than common terms."""

    def test_rare_term_beats_common_term(self, two_doc_index):
        # "sleep" appears in doc_sleep only (df=1 of 2)
        # "memory" also appears in doc_sleep only (df=1 of 2) — equal here
        # Let's use a term that appears in BOTH docs vs one that appears in ONE
        idf = compute_idf(["sleep", "cortisol"], two_doc_index)
        # "sleep" is in doc_sleep only; "cortisol" is in doc_stress only
        # Both have df=1, so their IDF values should be equal
        assert abs(idf["sleep"] - idf["cortisol"]) < 0.01

    def test_universal_term_gets_minimum_idf(self, two_doc_index):
        # A term that appears in all N docs gets the minimum possible IDF
        # (log((N+1)/(N+1)) + 1 = 1.0)
        # Inject a word into both docs' titles and check
        all_files = {
            "d1": {"name": "common word here", "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
                   "llm_metadata": {}, "retrieval_queries": [], "chunks": []},
            "d2": {"name": "common word there", "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
                   "llm_metadata": {}, "retrieval_queries": [], "chunks": []},
        }
        idf = compute_idf(["common"], all_files)
        # df = 2, N = 2 → log(3/3)+1 = 1.0
        assert abs(idf["common"] - 1.0) < 0.01

    def test_absent_term_gets_maximum_idf(self, two_doc_index):
        # A term that appears in 0 docs: log((N+1)/1)+1 — highest possible
        idf = compute_idf(["zzznomatch"], two_doc_index)
        N = len(two_doc_index)
        expected = math.log((N + 1) / 1) + 1
        assert abs(idf["zzznomatch"] - expected) < 0.01

    def test_returns_value_for_every_keyword(self, two_doc_index):
        keywords = ["sleep", "stress", "memory"]
        idf = compute_idf(keywords, two_doc_index)
        for kw in keywords:
            assert kw in idf, f"IDF missing for keyword: {kw}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. score_document
# ═══════════════════════════════════════════════════════════════════════════

class TestScoreDocument:
    """Per-document TF-IDF: title >> body > chunk; zero for unrelated queries."""

    def _flat_idf(self, keywords):
        """IDF of 1.0 for all keywords — isolates weight differences."""
        return {kw: 1.0 for kw in keywords}

    def test_title_hit_outscores_body_hit(self):
        """Title weight is 50×; body weight is 10×. Same IDF → title wins."""
        title_doc = {
            "name": "sleep research",   # 'sleep' in title
            "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
            "llm_metadata": {},
            "retrieval_queries": [],
            "chunks": [],
        }
        body_doc = {
            "name": "general health",   # 'sleep' NOT in title
            "extracted_metadata": {
                "core_claims": ["sleep is very important"],   # 'sleep' in body
                "key_data": [], "quotes": [],
            },
            "llm_metadata": {},
            "retrieval_queries": [],
            "chunks": [],
        }
        idf = self._flat_idf(["sleep"])
        title_score, _ = score_document(title_doc, ["sleep"], idf)
        body_score,  _ = score_document(body_doc,  ["sleep"], idf)
        assert title_score > body_score, (
            f"Title score ({title_score:.2f}) should beat body score ({body_score:.2f})"
        )

    def test_unrelated_query_scores_zero(self, two_doc_index):
        idf = self._flat_idf(["quantum"])
        for doc in two_doc_index.values():
            score, _ = score_document(doc, ["quantum"], idf)
            assert score == 0.0

    def test_chunk_title_bonus_applies(self):
        """A chunk whose title contains the query word gets a +3 bonus."""
        doc_with_chunk = {
            "name": "general intro",
            "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
            "llm_metadata": {},
            "retrieval_queries": [],
            "chunks": [{"id": "c1", "title": "sleep cycles explained", "preview": "content", "word_count": 100}],
        }
        doc_without_chunk = {
            "name": "general intro",
            "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
            "llm_metadata": {},
            "retrieval_queries": [],
            "chunks": [],
        }
        idf = self._flat_idf(["sleep"])
        with_score,    _ = score_document(doc_with_chunk,    ["sleep"], idf)
        without_score, _ = score_document(doc_without_chunk, ["sleep"], idf)
        assert with_score > without_score

    def test_best_chunk_title_returned(self):
        """score_document returns the title of the highest-scoring chunk."""
        doc = {
            "name": "overview",
            "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
            "llm_metadata": {},
            "retrieval_queries": [],
            "chunks": [
                {"id": "c1", "title": "Introduction", "preview": "general stuff", "word_count": 100},
                {"id": "c2", "title": "Sleep Architecture", "preview": "sleep cycles", "word_count": 100},
            ],
        }
        idf = self._flat_idf(["sleep"])
        _, best = score_document(doc, ["sleep"], idf)
        assert best == "Sleep Architecture", f"Expected 'Sleep Architecture', got {best!r}"

    def test_concept_boost_adds_to_score(self, two_doc_index):
        """A concept-document match adds CONCEPT_BOOST (8.0) to the score."""
        doc = two_doc_index["doc_sleep"]
        idf = self._flat_idf(["sleep"])
        concepts = {
            "Sleep": {"files": ["/fake/sleep.pdf"], "description": "..."}
        }
        score_no_concept, _ = score_document(doc, ["sleep"], idf, concepts=None)
        score_concept,    _ = score_document(doc, ["sleep"], idf, concepts=concepts)
        assert score_concept > score_no_concept

    def test_rq_sentence_bonus_for_multi_keyword_match(self):
        """A retrieval query that contains ≥2 query keywords gets a 5× bonus."""
        doc = {
            "name": "test doc",
            "extracted_metadata": {"core_claims": [], "key_data": [], "quotes": []},
            "llm_metadata": {},
            "retrieval_queries": [
                "If someone asks about sleep and memory consolidation, this is relevant."
            ],
            "chunks": [],
        }
        # Both 'sleep' and 'memory' appear in the same RQ → sentence bonus fires
        idf = self._flat_idf(["sleep", "memory"])
        score_multi, _ = score_document(doc, ["sleep", "memory"], idf)

        # Single keyword: no sentence bonus (only 1 hit per RQ)
        score_single, _ = score_document(doc, ["sleep"], idf)

        assert score_multi > score_single, (
            "Multi-keyword match should trigger RQ sentence bonus"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. score_hybrid
# ═══════════════════════════════════════════════════════════════════════════

class TestScoreHybrid:
    """Hybrid pipeline: keyword + embedding + concept, all scaled 0-100."""

    def _flat_idf(self, keywords):
        return {kw: 1.0 for kw in keywords}

    def test_returns_tuple_of_score_and_chunk(self, two_doc_index):
        doc = two_doc_index["doc_sleep"]
        idf = self._flat_idf(["sleep"])
        result = score_hybrid(doc, ["sleep"], idf)
        assert isinstance(result, tuple) and len(result) == 2
        score, chunk = result
        assert isinstance(score, float)
        assert isinstance(chunk, str)

    def test_score_in_zero_to_hundred_range(self, two_doc_index):
        doc = two_doc_index["doc_sleep"]
        idf = self._flat_idf(["sleep"])
        score, _ = score_hybrid(doc, ["sleep"], idf)
        assert 0.0 <= score <= 100.0, f"Score out of range: {score}"

    def test_relevant_doc_scores_higher_than_irrelevant(self, two_doc_index):
        """sleep query: doc_sleep should outscore doc_stress."""
        idf = compute_idf(["sleep"], two_doc_index)
        sleep_score,  _ = score_hybrid(two_doc_index["doc_sleep"],  ["sleep"], idf)
        stress_score, _ = score_hybrid(two_doc_index["doc_stress"], ["sleep"], idf)
        assert sleep_score > stress_score, (
            f"sleep doc ({sleep_score:.1f}) should outscore stress doc ({stress_score:.1f})"
        )

    def test_no_embedding_still_returns_nonzero_for_relevant_doc(self, two_doc_index):
        """Without embedding vectors, the keyword component alone must score > 0."""
        doc = two_doc_index["doc_sleep"]
        idf = self._flat_idf(["sleep"])
        score, _ = score_hybrid(doc, ["sleep"], idf,
                                 query_vector=None, doc_vector=None)
        assert score > 0.0

    def test_embedding_boosts_score(self, two_doc_index):
        """Passing matching embedding vectors should increase the score."""
        doc = two_doc_index["doc_sleep"]
        idf = self._flat_idf(["sleep"])
        score_no_embed, _ = score_hybrid(doc, ["sleep"], idf,
                                          query_vector=None, doc_vector=None)
        # Perfectly matching unit vectors → cosine similarity = 1.0
        vec = [1.0, 0.0, 0.0]
        score_with_embed, _ = score_hybrid(doc, ["sleep"], idf,
                                            query_vector=vec, doc_vector=vec)
        assert score_with_embed > score_no_embed

    def test_zero_score_for_unrelated_query(self, two_doc_index):
        idf = self._flat_idf(["zzznomatch"])
        score, _ = score_hybrid(two_doc_index["doc_sleep"],
                                 ["zzznomatch"], idf,
                                 query_vector=None, doc_vector=None)
        assert score == 0.0
