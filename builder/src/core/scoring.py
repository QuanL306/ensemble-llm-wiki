#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared scoring module for knowledge base retrieval.

Provides TF-IDF scoring with word-boundary matching, morphological
variant expansion, and concept-aware boosting.  Consumed by both the
local MCP server and the cloud HTTP server.
"""

import math
import re
import functools
from typing import Dict, List, Tuple, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_WORDS: set = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "it", "its", "this", "that",
    "these", "those", "i", "you", "he", "she", "we", "they", "what",
    "which", "who", "how", "when", "where", "why", "not", "no", "can",
    "if", "then", "than", "so", "as", "also", "into", "about", "up",
    "out", "just", "more", "some", "any", "all", "each", "very", "own",
}

# Additive bonus per concept-document match
CONCEPT_BOOST: float = 8.0

# Sentence-level retrieval-query bonus multiplier
RQ_SENTENCE_BONUS: float = 5.0

# Minimum keyword length after stop-word removal
MIN_KW_LEN: int = 3

# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------


def split_keywords(raw_query: str) -> List[str]:
    """Split a raw query into scored keywords (lowercased, stop-filtered)."""
    words = raw_query.lower().split()
    keywords = [w for w in words if len(w) > MIN_KW_LEN and w not in STOP_WORDS]
    if not keywords:
        keywords = [raw_query.lower()]
    return keywords


def keyword_variants(kw: str) -> List[str]:
    """Generate common English morphological variants of *kw*.

    No external libraries — purely suffix-based.  Returns a list
    containing the original keyword plus 2-8 plausible variants.
    """
    variants = [kw]
    w = kw
    if len(w) < 4:
        return variants

    # --- forward transforms (inflected → base) ---
    if w.endswith("ies") and len(w) > 4:
        variants.append(w[:-3] + "y")          # studies → study
    if w.endswith("ied") and len(w) > 4:
        variants.append(w[:-3] + "y")          # carried → carry
    if w.endswith("es") and len(w) > 4:
        variants.append(w[:-2])                # boxes → box
        variants.append(w[:-1])                # closes → close
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        variants.append(w[:-1])                # runs → run

    if w.endswith("ing") and len(w) > 5:
        base = w[:-3]
        variants.append(base)                  # running → runn
        variants.append(base + "e")            # creating → create
        if len(base) > 1 and base[-1] == base[-2]:
            variants.append(base[:-1])         # running → run

    if w.endswith("ed") and len(w) > 4:
        variants.append(w[:-2])                # walked → walk
        variants.append(w[:-1])                # created → create
        base = w[:-2]
        if len(base) > 1 and base[-1] == base[-2]:
            variants.append(base[:-1])         # stopped → stop

    if w.endswith("tion") and len(w) > 6:
        variants.append(w[:-4] + "te")         # creation → create
    if w.endswith("sion") and len(w) > 6:
        variants.append(w[:-4] + "d")          # decision → decide
        variants.append(w[:-4] + "de")
    if w.endswith("ment") and len(w) > 6:
        variants.append(w[:-4])                # development → develop
    if w.endswith("ness") and len(w) > 6:
        variants.append(w[:-4])                # fitness → fit
        variants.append(w[:-4] + "y")          # happiness → happy
    if w.endswith("able") and len(w) > 6:
        variants.append(w[:-4])                # readable → read
    if w.endswith("ible") and len(w) > 6:
        variants.append(w[:-4])
    if w.endswith("ful") and len(w) > 5:
        variants.append(w[:-3])                # helpful → help
    if w.endswith("less") and len(w) > 6:
        variants.append(w[:-4])                # careless → care
    if w.endswith("ous") and len(w) > 5:
        variants.append(w[:-3])                # famous → fame
    if w.endswith("ive") and len(w) > 5:
        variants.append(w[:-3])                # active → act
    if w.endswith("ly") and len(w) > 4:
        variants.append(w[:-2])                # quickly → quick
    if w.endswith("al") and len(w) > 4:
        variants.append(w[:-2])                # cultural → cultur
    if w.endswith("er") and len(w) > 4:
        variants.append(w[:-2])                # builder → build
        variants.append(w[:-1])                # larger → large
    if w.endswith("est") and len(w) > 5:
        variants.append(w[:-2])                # fastest → fast
        variants.append(w[:-3])                # largest → large

    # --- reverse transforms (base → inflected) ---
    # Only add suffix forms if the keyword doesn't already end with them
    base = kw
    if len(base) > 3 and not any(base.endswith(s) for s in
            ("ing", "ed", "es", "tion", "sion", "ment", "ness", "ous", "ive", "ful", "less", "able", "ible", "ly")):
        variants.append(base + "s")
        if base.endswith("e"):
            variants.append(base + "d")            # create → created
            variants.append(base[:-1] + "tion")    # create → creation
            variants.append(base[:-1] + "ment")    # name → namment (harmless)
        else:
            variants.append(base + "ing")
            variants.append(base + "ed")
            variants.append(base + "tion")
            variants.append(base + "ment")

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for v in variants:
        if v not in seen and len(v) >= 2:
            seen.add(v)
            unique.append(v)
    return unique


@functools.lru_cache(maxsize=512)
def _compiled_patterns(kw: str) -> tuple:
    """Return compiled regex patterns for *kw* and its morphological variants.

    Results are cached so patterns are built once per unique keyword.
    """
    patterns = []
    for v in keyword_variants(kw):
        if len(v) < 2:
            continue
        try:
            patterns.append(re.compile(r'\b' + re.escape(v) + r'\b'))
        except re.error:
            continue
    return tuple(patterns)


def count_matches(text: str, kw: str) -> int:
    """Count word-boundary occurrences of *kw* (and variants) in *text*.

    Uses ``\\b`` regex boundaries so that "count" does **not** match
    "account".
    """
    total = 0
    for pat in _compiled_patterns(kw):
        total += len(pat.findall(text))
    return total


# ---------------------------------------------------------------------------
# IDF computation
# ---------------------------------------------------------------------------


def _build_searchable_text(file_info: dict) -> str:
    """Concatenate all searchable fields for a single document."""
    parts = []

    # Title
    parts.append(file_info.get("name", ""))

    # Heuristic metadata
    meta = file_info.get("extracted_metadata", {})
    parts.extend(meta.get("core_claims", []))
    parts.extend(meta.get("key_data", []))
    parts.extend(meta.get("quotes", []))

    # LLM metadata
    llm = file_info.get("llm_metadata", {})
    if llm:
        parts.append(llm.get("summary", ""))
        parts.append(llm.get("core_arguments", ""))
        parts.append(" ".join(llm.get("evidence", [])))

    # Retrieval queries
    parts.extend(file_info.get("retrieval_queries", []))

    # Chunks (title + preview)
    for chunk in file_info.get("chunks", []):
        parts.append(chunk.get("title", ""))
        parts.append(chunk.get("preview", ""))

    return " ".join(parts).lower()


def compute_idf(keywords: List[str], all_files: dict) -> Dict[str, float]:
    """Compute smoothed IDF for each keyword across all documents.

    Returns ``{keyword: idf_value}`` where
    ``idf = log((N+1) / (df+1)) + 1``.

    Builds searchable text once per document (not once per keyword × document).
    """
    N = len(all_files)
    searchable_texts = [_build_searchable_text(finfo) for finfo in all_files.values()]
    idf: Dict[str, float] = {}
    for kw in keywords:
        df = sum(1 for text in searchable_texts if count_matches(text, kw) > 0)
        idf[kw] = math.log((N + 1) / (df + 1)) + 1
    return idf


# ---------------------------------------------------------------------------
# Per-document scoring
# ---------------------------------------------------------------------------


def _tf_score(hits: int, word_count: int) -> float:
    """Term-frequency score with sqrt-normalisation."""
    if hits == 0:
        return 0.0
    return hits / (word_count ** 0.5)


def score_document(
    file_info: dict,
    keywords: List[str],
    idf: Dict[str, float],
    concepts: Optional[dict] = None,
) -> Tuple[float, str]:
    """Score a single document against *keywords* using TF-IDF.

    Returns ``(score, best_chunk_title)``.

    **Fields and weights:**

    | Field                         | Weight | Notes                            |
    |-------------------------------|--------|----------------------------------|
    | title                         | 50     | highest signal                   |
    | body (claims+data+quotes)     | 10     | heuristic metadata               |
    | llm_body (summary+args+evid)  | 10     | LLM-parsed sections              |
    | retrieval queries             | 20     | word-level                       |
    | rq sentence bonus             | 5/hit  | per sentence with ≥ 2 hits       |
    | chunks                        | 0.8    | best chunk (title+preview)       |
    | chunk title bonus             | +3/kw  | keyword in chunk title           |
    | concept boost                 | +8     | per concept-document match       |
    """
    title = file_info.get("name", "").lower()
    meta = file_info.get("extracted_metadata", {})
    llm = file_info.get("llm_metadata", {})

    # --- Build searchable field texts ---
    body_parts = meta.get("core_claims", []) + meta.get("key_data", []) + meta.get("quotes", [])
    body = " ".join(body_parts).lower()

    llm_body = llm.get("llm_body_search", "").lower() if llm else ""

    rq_list = file_info.get("retrieval_queries", [])
    rq_text = " ".join(rq_list).lower()

    # Pre-compute word counts once per field (not once per keyword × field)
    title_wc = max(len(title.split()), 1)
    body_wc = max(len(body.split()), 1)
    llm_body_wc = max(len(llm_body.split()), 1) if llm_body else 1
    rq_wc = max(len(rq_text.split()), 1)

    score = 0.0

    for kw in keywords:
        idf_val = idf.get(kw, 1.0)

        # Title (×50)
        s = _tf_score(count_matches(title, kw), title_wc)
        if s > 0:
            score += s * idf_val * 50

        # Heuristic body (×10)
        s = _tf_score(count_matches(body, kw), body_wc)
        if s > 0:
            score += s * idf_val * 10

        # LLM body (×10)
        if llm_body:
            s = _tf_score(count_matches(llm_body, kw), llm_body_wc)
            if s > 0:
                score += s * idf_val * 10

        # Retrieval queries word-level (×20)
        s = _tf_score(count_matches(rq_text, kw), rq_wc)
        if s > 0:
            score += s * idf_val * 20

    # --- Sentence-level RQ bonus (applied once, not per-kw) ---
    for rq in rq_list:
        rq_lower = rq.lower()
        hits = sum(1 for kw in keywords if count_matches(rq_lower, kw) > 0)
        if hits >= 2:
            score += hits * RQ_SENTENCE_BONUS

    # --- Chunk scoring (best chunk) ---
    chunks = file_info.get("chunks", [])
    best_chunk_score = 0.0
    best_chunk_title = ""
    for chunk in chunks:
        c_title = chunk.get("title", "").lower()
        c_preview = chunk.get("preview", "").lower()
        cs = 0.0
        for kw in keywords:
            title_hits = count_matches(c_title, kw)
            cs += title_hits + count_matches(c_preview, kw)
            if title_hits > 0:
                cs += 3
        if cs > best_chunk_score:
            best_chunk_score = cs
            best_chunk_title = chunk.get("title", "")
    score += best_chunk_score * 0.8

    # --- Concept boost ---
    if concepts:
        concept_links = set(
            llm.get("terminology", []) + llm.get("connections", [])
        ) if llm else set()
        doc_path = file_info.get("path", "")
        for kw in keywords:
            kw_lower = kw.lower()
            for cname, cdata in concepts.items():
                cname_lower = cname.lower()
                if kw_lower in cname_lower or cname_lower in kw_lower:
                    if doc_path in cdata.get("files", []) or cname in concept_links:
                        score += CONCEPT_BOOST

    return score, best_chunk_title


# ---------------------------------------------------------------------------
# Embedding-based scoring
# ---------------------------------------------------------------------------


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_query(query: str, model) -> List[float]:
    """Embed a query string using a fastembed model.

    *model* should be a lazy-loaded ``TextEmbedding`` instance.
    Returns a 384-dim float list.
    """
    embeddings = list(model.embed([query]))
    return embeddings[0].tolist() if embeddings else []


EMBED_TEXT_VERSION = 2   # bump when build_doc_embed_text format changes


def build_doc_embed_text(file_info: dict) -> str:
    """Build the text to embed for a single document (Contextual Retrieval).

    Concatenates title + summary + core arguments + terminology +
    connections + top retrieval queries.  Enriching the embedding
    text with structured metadata improves semantic retrieval by
    capturing not just what the document says, but its conceptual
    relationships within the knowledge base.
    """
    parts = [file_info.get("name", "")]
    llm = file_info.get("llm_metadata", {})
    if llm and isinstance(llm, dict):
        if llm.get("summary"):
            parts.append(llm["summary"])
        if llm.get("core_arguments"):
            parts.append(llm["core_arguments"][:500])
        if llm.get("terminology"):
            parts.append("Key terms: " + ", ".join(llm["terminology"][:15]))
        if llm.get("connections"):
            parts.append("Related: " + ", ".join(llm["connections"][:10]))
    rq = file_info.get("retrieval_queries", [])[:10]
    parts.extend(rq)
    return " ".join(parts)


def score_hybrid(
    file_info: dict,
    keywords: List[str],
    idf: Dict[str, float],
    concepts: Optional[dict] = None,
    query_vector: Optional[List[float]] = None,
    doc_vector: Optional[List[float]] = None,
) -> Tuple[float, str]:
    """Hybrid scoring: keyword TF-IDF + embedding cosine + concept boost.

    Combines three independent signals:
        final = keyword_norm * 0.4 + embed_score * 0.4 + concept_norm * 0.2

    Keyword score is computed *without* concept boost (to avoid double-counting)
    and normalised using a saturating curve ``score / (score + K)``.

    If embedding vectors are not available, returns the normalised keyword
    score + concept on the same 0-100 scale so results are always comparable.
    Returns ``(score, best_chunk_title)``.
    """
    # Keyword score WITHOUT concept boost (concepts handled as separate layer)
    kw_score, best_chunk = score_document(file_info, keywords, idf, concepts=None)

    embed_score = cosine_similarity(query_vector, doc_vector) if (
        query_vector is not None and doc_vector is not None) else 0.0

    # Concept component (computed once, not inside score_document)
    concept_raw = 0.0
    if concepts:
        llm = file_info.get("llm_metadata") or {}
        concept_links = set(
            llm.get("terminology", []) + llm.get("connections", [])
        ) if isinstance(llm, dict) else set()
        doc_path = file_info.get("path", "")
        for kw in keywords:
            kw_lower = kw.lower()
            for cname, cdata in concepts.items():
                cname_lower = cname.lower()
                if kw_lower in cname_lower or cname_lower in kw_lower:
                    if doc_path in cdata.get("files", []) or cname in concept_links:
                        concept_raw += CONCEPT_BOOST

    # Normalize each component to [0, 1] using saturating curve
    # K = 50 means: score=50 → norm=0.5, score=100 → norm=0.67, score=200 → 0.8
    KW_K = 50.0
    CONCEPT_K = CONCEPT_BOOST * 3   # 24 — 1 match→0.25, 3+→saturated
    kw_norm = kw_score / (kw_score + KW_K)
    concept_norm = concept_raw / (concept_raw + CONCEPT_K) if concept_raw > 0 else 0.0

    # Weighted combination
    W_KW = 0.4
    W_EMBED = 0.4
    W_CONCEPT = 0.2
    final = kw_norm * W_KW + embed_score * W_EMBED + concept_norm * W_CONCEPT

    # Scale to 0-100 range
    return final * 100.0, best_chunk


def multi_hop_expand(
    stage1_results: List[dict],
    all_files: dict,
    keywords: List[str],
    idf: Dict[str, float],
    concepts: Optional[dict] = None,
    max_additional: int = 3,
) -> List[dict]:
    """Stage 2 of multi-hop retrieval.

    Extracts key terms from Stage 1 results' ``llm_metadata.terminology``
    and ``connections``, then searches again with those terms. Returns
    additional results not already in Stage 1 (deduped by file_id).
    """
    stage1_ids = {r.get("id", r.get("file_id", "")) for r in stage1_results}

    # Collect expansion terms
    expansion_terms: List[str] = []
    for r in stage1_results:
        llm = r.get("llm_metadata") or {}
        if isinstance(llm, dict):
            expansion_terms.extend(llm.get("terminology", []))
            expansion_terms.extend(llm.get("connections", []))

    if not expansion_terms:
        return []

    # Deduplicate and cap at 10 terms to avoid O(N*terms) blowup
    seen = set(keywords)  # skip terms already in the original query
    hop_keywords: List[str] = []
    for term in expansion_terms:
        t = term.lower().strip()
        if t and t not in seen and len(t) > 3:
            seen.add(t)
            hop_keywords.append(t)
            if len(hop_keywords) >= 10:
                break

    if not hop_keywords:
        return []

    # Reuse existing IDF where possible; compute only for new terms
    hop_idf = {kw: idf[kw] for kw in hop_keywords if kw in idf}
    new_terms = [kw for kw in hop_keywords if kw not in hop_idf]
    if new_terms:
        hop_idf.update(compute_idf(new_terms, all_files))
    scored: List[dict] = []
    for fid, finfo in all_files.items():
        if fid in stage1_ids:
            continue
        score, best_chunk = score_document(finfo, hop_keywords, hop_idf, concepts)
        if score > 0:
            meta = finfo.get("extracted_metadata", {})
            llm = finfo.get("llm_metadata", {})
            scored.append({
                "id": fid,
                "name": finfo.get("name", ""),
                "score": score,
                "core_claims": meta.get("core_claims", [])[:2],
                "best_chunk": best_chunk,
                "wiki_path": finfo.get("wiki_path", ""),
                "llm_metadata": llm,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_additional]
