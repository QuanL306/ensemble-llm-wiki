"""
Shared pytest configuration and fixtures.

Sets up sys.path so both builder and cloud modules are importable,
and provides lightweight KB fixtures for tests that need filesystem access.
"""

import os
import sys
import json
import tempfile
import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_BUILDER_SRC = os.path.join(_REPO, "builder", "src")
if _BUILDER_SRC not in sys.path:
    sys.path.insert(0, _BUILDER_SRC)

_CLOUD_SRC = os.path.join(_REPO, "cloud_platform", "src", "server")
if _CLOUD_SRC not in sys.path:
    sys.path.insert(0, _CLOUD_SRC)

# ── KB_BASE_PATH: redirect KnowledgeBaseManager away from /data ─────────────
# Must be set before mcp_http_server is imported (module-level instantiation).

_tmp_base = tempfile.mkdtemp(prefix="kb_test_base_")
os.environ.setdefault("KB_BASE_PATH", _tmp_base)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_article():
    """A complete wiki article with all required sections and 15 queries."""
    return """\
---
title: "Why We Sleep"
type: book
tags: [sleep, memory, neuroscience]
word_count: 80000
compiled: 2026-01-01
---

## Summary

Matthew Walker argues that sleep is the single most powerful health behaviour
available to us. Specifically, he claims that 7–9 hours of sleep per night are
required for full hippocampal memory consolidation and amyloid clearance.

## Core Arguments

1. **Claim**: Slow-wave sleep replays hippocampal memories for long-term storage.
   - **Evidence type**: neuroimaging + EEG correlation studies
   - **Key caveat**: causation not fully established in humans

## Author's Terminology

**[[hippocampal replay]]** — the re-activation of daytime memory traces during
slow-wave sleep, proposed as the mechanism for declarative memory consolidation.

## Evidence & Data

- Subjects deprived of REM sleep showed 40% reduction in skill learning
- NREM slow-wave activity predicts next-day recall accuracy (r = 0.72)

## Key Quotes

> "Sleep is the single most effective thing we can do to reset our brain and
> body health each day." — Chapter 1

## Connections

Connects to [[cognitive bias]] research via the role of sleep in emotional
memory consolidation.

## What This Doesn't Cover

- Polyphasic sleep schedules
- Sleep in non-mammalian species

## For Future Queries

1. If someone asks about sleep and memory consolidation, this document is relevant because it covers hippocampal replay during slow-wave sleep.
2. If someone researching REM sleep effects, this document is relevant because it discusses procedural memory and emotional regulation.
3. If someone wants to improve learning efficiency, this document is relevant because optimal sleep timing enhances encoding and consolidation.
4. If someone asks about circadian rhythms, this document is relevant because sleep architecture is tightly coupled to the circadian clock.
5. If someone researching cognitive performance decline, this document is relevant because sleep deprivation impairs prefrontal cortex function.
6. If someone asks about neurogenesis, this document is relevant because slow-wave sleep promotes hippocampal neurogenesis via BDNF.
7. If someone researching stress hormones and brain, this document is relevant because cortisol elevation during sleep deprivation impairs hippocampal function.
8. If someone wants to understand memory forgetting, this document is relevant because sleep consolidation determines which memories are retained versus forgotten.
9. If someone asks about napping, this document is relevant because 20-minute naps restore alertness without causing sleep inertia.
10. If someone researching aging and cognition, this document is relevant because slow-wave sleep amplitude decreases with age and correlates with memory decline.
11. If someone asks about caffeine and sleep, this document is relevant because adenosine blockade by caffeine disrupts sleep pressure accumulation.
12. If someone researching dreams and REM sleep, this document is relevant because REM sleep generates vivid emotional dreams via limbic reactivation.
13. If someone wants to study more effectively, this document is relevant because spaced repetition combined with sleep intervals maximises long-term retention.
14. If someone asks about insomnia treatment, this document is relevant because sleep restriction therapy outperforms medication as first-line treatment.
15. If someone researching Alzheimer's disease prevention, this document is relevant because amyloid and tau clearance from the brain occurs primarily during deep sleep.
"""


@pytest.fixture
def sample_index():
    """Synthetic file_index.json data for two documents."""
    return {
        "files": {
            "doc001": {
                "name": "Why We Sleep.pdf",
                "path": "/fake/raw/Why We Sleep.pdf",
                "status": "completed",
                "wiki_path": "wiki/_meta/why_we_sleep_extracted.txt",
                "extracted_metadata": {
                    "word_count": 80000,
                    "core_claims": [
                        "Sleep deprivation impairs cognitive performance",
                        "REM sleep consolidates procedural memory",
                        "Slow-wave sleep clears amyloid plaques",
                    ],
                    "key_data": ["7–9 hours required for full consolidation"],
                    "quotes": [
                        "Sleep is the single most effective thing we can do"
                    ],
                },
                "retrieval_queries": [
                    "If someone asks about sleep and memory consolidation, this is relevant because hippocampal replay occurs during slow-wave sleep.",
                    "If someone researching REM sleep, this is relevant because procedural memory consolidation requires REM.",
                    "If someone wants to improve learning efficiency, this is relevant because sleep timing affects encoding.",
                ],
                "chunks": [
                    {
                        "id": "c001",
                        "title": "Chapter 1: Why Sleep Matters",
                        "preview": "Sleep is not passive it is the most powerful health behaviour available to us.",
                        "word_count": 4200,
                    },
                    {
                        "id": "c002",
                        "title": "Chapter 6: Your Mother and Shakespeare",
                        "preview": "Memory consolidation during sleep separates fact from emotion.",
                        "word_count": 3800,
                    },
                ],
            },
            "doc002": {
                "name": "Stress Response Review.pdf",
                "path": "/fake/raw/Stress Response Review.pdf",
                "status": "completed",
                "wiki_path": "wiki/_meta/stress_review_extracted.txt",
                "extracted_metadata": {
                    "word_count": 12000,
                    "core_claims": [
                        "Acute stress enhances memory encoding via norepinephrine",
                        "Chronic cortisol impairs hippocampal neurogenesis",
                    ],
                    "key_data": [],
                    "quotes": [],
                },
                "retrieval_queries": [
                    "If someone asks about stress effects on learning, this is relevant.",
                    "If someone researching cortisol and hippocampal volume, this is relevant.",
                ],
                "chunks": [],
            },
        }
    }


@pytest.fixture
def tmp_kb(tmp_path, sample_index):
    """
    Create a minimal KB directory structure on disk.

    Layout:
        tmp_path/
          alice/
            my-research/
              .kbaconfig
              wiki/
                _meta/
                  file_index.json
                _articles/
    """
    kb_root = tmp_path / "alice" / "my-research"
    meta_dir = kb_root / "wiki" / "_meta"
    meta_dir.mkdir(parents=True)
    (kb_root / "wiki" / "_articles").mkdir()

    (kb_root / ".kbaconfig").write_text(
        "name: Test KB\nversion: '1.0'\n", encoding="utf-8"
    )
    (meta_dir / "file_index.json").write_text(
        json.dumps(sample_index, indent=2), encoding="utf-8"
    )

    return {
        "root": kb_root,
        "base": tmp_path,
        "user_id": "alice",
        "kb_id": "my-research",
    }
