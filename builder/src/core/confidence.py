#!/usr/bin/env python3
"""
confidence.py — 4-factor confidence scoring for knowledge base documents

Borrowed from: Pratiyush/llm-wiki (4-factor scoring model)
Extended with: Ebbinghaus-inspired recency decay, per-content-type decay rates

Four factors:
  1. source_count     — how many independent sources reference this claim
  2. source_quality   — authority tier of sources (1-5 scale)
  3. recency          — freshness with Ebbinghaus decay (half-life by content type)
  4. cross_references — how many other wiki pages link to this one

Final score = weighted sum, normalized to 0.0-1.0 with confidence tier labels.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Source quality tiers (1-5 scale) ──
# Maps source domain/name patterns to quality scores
QUALITY_PATTERNS = [
    (5, ["academia", ".edu", "arxiv.org", "semanticscholar.org", "pubmed",
         "nber.org", "repec.org", "ssrn.com", "doi.org", "census.gov",
         "bls.gov", "bea.gov", "fred.stlouisfed.org", "imf.org",
         "worldbank.org", "oecd.org", "congress.gov", "gov.cn"]),
    (4, ["reuters.com", "bloomberg.com", "apnews.com", "ft.com",
         "economist.com", "nature.com", "science.org", "wsj.com",
         "nytimes.com", "washingtonpost.com", "theatlantic.com"]),
    (3, ["theguardian.com", "bbc.com", "cnn.com", "aljazeera.com",
         "scmp.com", "nikkei.com", "dw.com", "france24.com"]),
    (2, ["medium.com", "substack.com", "blog.", "opinion", "commentary"]),
    (1, ["twitter.com", "x.com", "reddit.com", "t.co", "forum"]),
]

# Recency half-life (days) by content type — after this many days, score halves
RECENCY_HALF_LIFE = {
    "news": 7,        # news articles decay fast
    "analysis": 30,   # analytical pieces last longer
    "report": 90,     # institutional reports
    "paper": 365,     # academic papers
    "book": 730,      # books endure
    "data": 180,      # datasets
    "reference": 365, # reference material
    "default": 60,
}

# Confidence tier labels
CONFIDENCE_TIERS = [
    (0.8, "high",      "★★★"),
    (0.6, "medium",    "★★☆"),
    (0.4, "low",       "★☆☆"),
    (0.0, "unverified", "---"),
]


def estimate_source_quality(source_name: str) -> int:
    """Estimate source quality tier from name/domain patterns."""
    name_lower = source_name.lower()
    for tier, patterns in QUALITY_PATTERNS:
        for pat in patterns:
            if pat in name_lower:
                return tier
    return 2  # default: unknown blog-ish


def compute_recency_factor(
    created_at: Optional[str],
    content_type: str = "default",
    current_time: Optional[datetime] = None,
) -> float:
    """
    Compute recency factor with Ebbinghaus-inspired exponential decay.
    score = 0.5 ^ (days_elapsed / half_life)
    """
    if not created_at:
        return 0.5  # unknown date → neutral

    try:
        # Parse ISO date
        if "T" in created_at:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created = datetime.strptime(created_at[:10], "%Y-%m-%d")
        created = created.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.5

    now = current_time or datetime.now(timezone.utc)
    days = (now - created).days
    if days < 0:
        days = 0

    half_life = RECENCY_HALF_LIFE.get(content_type, RECENCY_HALF_LIFE["default"])
    return 0.5 ** (days / half_life)


def score_document(
    doc_info: dict,
    cross_ref_count: int = 0,
    current_time: Optional[datetime] = None,
) -> dict:
    """
    Compute 4-factor confidence score for a single document.
    
    Args:
        doc_info: from file_index.json files[file_id]
        cross_ref_count: number of other wiki pages linking to this one
        current_time: for recency calculation (default: now)
    
    Returns:
        {score, tier, tier_label, factors: {source_count, source_quality, 
         recency_raw, recency_factor, cross_refs, breakdown}}
    """
    # Factor 1: Source count
    llm = doc_info.get("llm_metadata") or {}
    extracted = doc_info.get("extracted_metadata") or {}

    sources = llm.get("sources") or extracted.get("sources") or []
    source_count = max(len(sources), 1)

    # Factor 2: Source quality (average tier)
    if sources:
        quality_scores = [estimate_source_quality(s) for s in sources]
        source_quality = sum(quality_scores) / len(quality_scores)
    else:
        source_quality = 2.0  # unknown

    # Factor 3: Recency
    content_type = llm.get("content_type") or extracted.get("content_type") or "default"
    created = llm.get("created") or doc_info.get("created_at")
    recency_raw = compute_recency_factor(created, content_type, current_time)

    # Factor 4: Cross-references
    cross_refs = cross_ref_count

    # Weighted composite (equal weights, tuned for analysis content)
    normalized_count = min(source_count / 5.0, 1.0)  # caps at 5 sources
    normalized_quality = source_quality / 5.0          # normalized 1-5
    normalized_refs = min(cross_refs / 10.0, 1.0)      # caps at 10 refs

    score = (
        normalized_count * 0.20
        + normalized_quality * 0.25
        + recency_raw * 0.30
        + normalized_refs * 0.25
    )

    # Determine tier
    tier = "unverified"
    tier_label = "---"
    for threshold, t_name, t_label in CONFIDENCE_TIERS:
        if score >= threshold:
            tier = t_name
            tier_label = t_label
            break

    return {
        "score": round(score, 3),
        "tier": tier,
        "tier_label": tier_label,
        "factors": {
            "source_count": source_count,
            "source_quality": round(source_quality, 1),
            "recency_factor": round(recency_raw, 3),
            "cross_references": cross_refs,
        },
        "breakdown": {
            "count_weight": round(normalized_count * 0.20, 3),
            "quality_weight": round(normalized_quality * 0.25, 3),
            "recency_weight": round(recency_raw * 0.30, 3),
            "cross_ref_weight": round(normalized_refs * 0.25, 3),
        },
    }


def score_all_documents(
    index_path: Path,
    cross_refs: Optional[Dict[str, int]] = None,
) -> Dict[str, dict]:
    """
    Score all documents in a knowledge base.
    Writes confidence fields back to file_index.json.
    
    Args:
        index_path: path to file_index.json
        cross_refs: optional {file_id: count} cross-reference map
    
    Returns:
        {file_id: score_dict} for all scored documents
    """
    if not index_path.exists():
        return {}

    with open(index_path) as f:
        idx = json.load(f)

    if cross_refs is None:
        cross_refs = {}

    now = datetime.now(timezone.utc)
    scores = {}

    for file_id, info in idx.get("files", {}).items():
        if info.get("status") != "completed":
            continue

        ref_count = cross_refs.get(file_id, 0)
        result = score_document(info, ref_count, now)

        # Write back to document info
        info["confidence"] = {
            "score": result["score"],
            "tier": result["tier"],
            "tier_label": result["tier_label"],
            "factors": result["factors"],
            "scored_at": now.isoformat(),
        }
        scores[file_id] = result

    # Save updated index
    idx["_confidence_scored_at"] = now.isoformat()
    with open(index_path, "w") as f:
        json.dump(idx, f, indent=2, ensure_ascii=False)

    return scores


def compute_cross_references(wiki_path: Path) -> Dict[str, int]:
    """
    Count how many other wiki pages link to each document.
    Scans all .md files in _articles/ and _concepts/ for [[wikilinks]].
    """
    refs: Dict[str, int] = {}
    articles_dir = wiki_path / "wiki" / "_articles"
    concepts_dir = wiki_path / "wiki" / "_concepts"

    for d in [articles_dir, concepts_dir]:
        if not d.exists():
            continue
        for md in d.glob("*.md"):
            if md.stem.endswith("_extracted"):
                continue
            try:
                content = md.read_text(encoding="utf-8")
            except Exception:
                continue

            # Find [[wikilinks]] and count them as cross-refs
            import re
            links = re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", content)
            for link in links:
                link = link.strip()
                if link:
                    refs[link] = refs.get(link, 0) + 1

    return refs
