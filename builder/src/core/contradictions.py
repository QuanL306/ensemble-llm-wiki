#!/usr/bin/env python3
"""
contradictions.py — Contradiction detection at ingest time

Borrowed from: SamurAIGPT/llm-wiki-agent (contradiction flags pattern)
Key insight: Flag contradictions when a new source is ingested, not at query time.
This makes conflicts discoverable before the user asks.

Detection methods:
  1. Claim-level: exact opposite statements (e.g., "X is rising" vs "X is falling")
  2. Source-level: same source cited for opposing conclusions
  3. Temporal: newer source contradicts older source on the same topic
"""
import json
import os as _os
import re
import tempfile as _tf
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Negation pairs for basic claim contradiction ──
NEGATION_PAIRS = [
    (r"\bincreas(?:ing|ed|es)\b", r"\bdecreas(?:ing|ed|es)\b"),
    (r"\bris(?:ing|en|es)\b", r"\bfall(?:ing|en|s)\b"),
    (r"\bgrow(?:ing|th|s)\b", r"\bshrink(?:ing|s)?\b"),
    (r"\bexpand(?:ing|ed|s)\b", r"\bcontract(?:ing|ed|s)\b"),
    (r"\bimprove(?:ment|d|s)\b", r"\bworsen(?:ing|ed|s)\b|\bdeclin(?:ing|ed|es)\b"),
    (r"\bsupport(?:s|ed|ing)\b", r"\boppos(?:es|ed|ing)\b|\breject(?:s|ed|ing)\b"),
    (r"\bagree(?:s|d|ment)\b", r"\bdisagree(?:s|d|ment)\b|\bcontradict(?:s|ed|ion)\b"),
    (r"\bconfirm(?:s|ed|ation)\b", r"\bden(?:y|ies|ied|ial)\b|\brefut(?:es|ed|ation)\b"),
]

# Common topic-tracking terms for detecting same-topic contradictions
TOPIC_INDICATORS = [
    "unemployment", "inflation", "gdp", "growth", "deficit",
    "trade", "tariff", "sanction", "rate", "index", "price",
    "employment", "wage", "investment", "export", "import",
    "revenue", "profit", "debt", "supply", "demand",
]


def _extract_claims(text: str, max_claims: int = 10) -> List[str]:
    """Extract claim-like sentences from text."""
    sentences = re.split(r"[.!?]\s+", text)
    claims = []
    for s in sentences:
        s = s.strip()
        if len(s) < 20 or len(s) > 300:
            continue
        # Claims tend to have numbers or key verbs
        if re.search(r"\d+", s) or any(
            w in s.lower() for w in ["is", "are", "was", "were", "has", "have",
                                      "will", "would", "should", "must", "can"]
        ):
            claims.append(s)
        if len(claims) >= max_claims:
            break
    return claims


def _normalize_claim(claim: str) -> str:
    """Normalize a claim for comparison."""
    return re.sub(r"[^\w\s]", " ", claim.lower()).strip()


def _is_contradictory(claim_a: str, claim_b: str) -> Tuple[bool, str]:
    """
    Check if two claims contradict using negation pair patterns.
    Returns (is_contradiction, reason).
    """
    norm_a = _normalize_claim(claim_a)
    norm_b = _normalize_claim(claim_b)

    # Must share at least 3 topic words
    words_a = set(norm_a.split())
    words_b = set(norm_b.split())
    common = words_a & words_b
    if len(common) < 3:
        return False, ""

    # Check negation pairs
    for pat_a, pat_b in NEGATION_PAIRS:
        has_a_in_a = bool(re.search(pat_a, norm_a))
        has_b_in_a = bool(re.search(pat_b, norm_a))
        has_a_in_b = bool(re.search(pat_a, norm_b))
        has_b_in_b = bool(re.search(pat_b, norm_b))

        # One has A, the other has B (negation)
        if (has_a_in_a and has_b_in_b) or (has_b_in_a and has_a_in_b):
            return True, f"Negation pair found: {pat_a} vs {pat_b}"

    return False, ""


def detect_contradictions(
    new_doc_text: str,
    existing_docs: Dict[str, str],
    new_doc_name: str = "new document",
) -> List[dict]:
    """
    Detect contradictions between a new document and existing wiki content.
    
    Args:
        new_doc_text: full text of the newly ingested document
        existing_docs: {file_id: text_content} of existing documents.
            Note: keys are IndexManager file_ids (MD5 of relative path), NOT
            human-readable names. source_b in contradiction records will
            therefore be a file_id, not a filename.
        new_doc_name: name of the new document
    
    Returns:
        List of contradiction records: [{source_a, claim_a, source_b, claim_b, reason}]
    """
    new_claims = _extract_claims(new_doc_text)
    if not new_claims:
        return []

    contradictions = []

    for doc_name, text in existing_docs.items():
        existing_claims = _extract_claims(text)
        for nc in new_claims:
            for ec in existing_claims:
                is_contra, reason = _is_contradictory(nc, ec)
                if is_contra:
                    contradictions.append({
                        "flagged_at": datetime.now(timezone.utc).isoformat(),
                        "source_a": new_doc_name,
                        "claim_a": nc[:200],
                        "source_b": doc_name,
                        "claim_b": ec[:200],
                        "reason": reason,
                    })

    return contradictions


def flag_contradictions_in_index(
    index_path: Path,
    new_file_id: str,
    contradictions: List[dict],
    existing_file_ids: List[str],
):
    """
    Write contradiction flags into file_index.json.
    Updates both the new document and the contradicted existing documents.
    """
    if not index_path.exists() or not contradictions:
        return

    with open(index_path) as f:
        idx = json.load(f)

    # Flag on new document
    if new_file_id in idx.get("files", {}):
        info = idx["files"][new_file_id]
        contra_list = info.get("contradictions", [])
        contra_list.extend(contradictions)
        info["contradictions"] = contra_list
        info["_has_contradictions"] = True

    # Flag on contradicted documents
    for contra in contradictions:
        source_b = contra.get("source_b", "")
        if source_b in idx.get("files", {}):
            b_info = idx["files"][source_b]
            b_list = b_info.get("contradictions_flagged_by", [])
            b_list.append({
                "flagged_at": contra.get("flagged_at"),
                "by": new_file_id,
                "reason": contra.get("reason"),
            })
            b_info["contradictions_flagged_by"] = b_list

    fd, tmp_path = _tf.mkstemp(dir=str(Path(index_path).parent), suffix=".tmp")
    try:
        with _os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(idx, f, indent=2, ensure_ascii=False)
        _os.replace(tmp_path, str(index_path))
    except Exception:
        try:
            _os.unlink(tmp_path)
        except OSError:
            pass
        raise
