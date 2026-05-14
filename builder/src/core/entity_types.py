#!/usr/bin/env python3
"""
entity_types.py — Extended wiki entity types

Borrowed from: OmegaWiki (skyllwt/OmegaWiki) — 9 entity types
Key additions for knowledge-base-suite-en:
  - methods/    — Reusable analytical frameworks, techniques, methodologies
  - topics/     — Research direction maps with SOTA tracking
  - foundations/ — Already implemented in foundations.py (terminal pages)

The existing concepts/ already maps to OmegaWiki's concepts/.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ── Method entity templates ──

METHOD_TEMPLATE = """---
type: method
created: {created}
sources: {sources}
related_methods: {related_methods}
parent_method: {parent_method}
---

# {title}

## Definition

{definition}

## How It Works

{mechanism}

## Applications

{applications}

## Source Papers

{sources_list}

## Related Methods

{related_list}

---
*Method page — reusable analytical framework. Links to source documents and related methods.*
"""


def create_method(
    wiki_path: Path,
    title: str,
    definition: str,
    mechanism: str = "",
    applications: str = "",
    sources: Optional[List[str]] = None,
    related_methods: Optional[List[str]] = None,
    parent_method: str = "",
) -> Path:
    """Create a method entity page."""
    methods_dir = wiki_path / "wiki" / "_methods"
    methods_dir.mkdir(parents=True, exist_ok=True)

    slug = title.lower().replace(" ", "-")
    for ch in "():,'\"?!/":
        slug = slug.replace(ch, "")
    filename = methods_dir / f"{slug}.md"

    sources = sources or []
    related_methods = related_methods or []

    content = METHOD_TEMPLATE.format(
        created=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        sources=json.dumps(sources),
        related_methods=json.dumps(related_methods),
        parent_method=parent_method,
        title=title,
        definition=definition,
        mechanism=mechanism or "*Mechanism not documented*",
        applications=applications or "*Applications not listed*",
        sources_list="\n".join(f"- {s}" for s in sources) or "*No sources*",
        related_list="\n".join(f"- [[{m}]]" for m in related_methods) or "*None*",
    )
    filename.write_text(content)
    return filename


# ── Topic entity templates ──

TOPIC_TEMPLATE = """---
type: topic
created: {created}
status: {status}
key_benchmarks: {key_benchmarks}
---

# {title}

## Overview

{description}

## Current State of the Art (SOTA)

{sota}

## Key Benchmarks

{benchmarks_list}

## Open Problems

{open_problems}

## Related Concepts

{related_concepts}

---
*Topic page — research direction map. Updated as new sources are ingested.*
"""


def create_topic(
    wiki_path: Path,
    title: str,
    description: str,
    sota: str = "",
    benchmarks: Optional[List[str]] = None,
    open_problems: str = "",
    related_concepts: Optional[List[str]] = None,
    status: str = "active",
) -> Path:
    """Create a topic entity page."""
    topics_dir = wiki_path / "wiki" / "_topics"
    topics_dir.mkdir(parents=True, exist_ok=True)

    slug = title.lower().replace(" ", "-")
    for ch in "():,'\"?!/":
        slug = slug.replace(ch, "")
    filename = topics_dir / f"{slug}.md"

    benchmarks = benchmarks or []
    related_concepts = related_concepts or []

    content = TOPIC_TEMPLATE.format(
        created=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        status=status,
        key_benchmarks=json.dumps(benchmarks),
        title=title,
        description=description,
        sota=sota or "*Not yet documented*",
        benchmarks_list="\n".join(f"- {b}" for b in benchmarks) or "*None*",
        open_problems=open_problems or "*Not yet identified*",
        related_concepts="\n".join(f"- [[{c}]]" for c in related_concepts) or "*None*",
    )
    filename.write_text(content)
    return filename


# ── Entity discovery ──

def list_entities(wiki_path: Path, entity_type: str) -> List[dict]:
    """List all entities of a given type."""
    type_map = {
        "methods": "_methods",
        "topics": "_topics",
        "concepts": "_concepts",
        "foundations": "foundations",
        "articles": "_articles",
    }
    dir_name = type_map.get(entity_type, f"_{entity_type}")
    entity_dir = wiki_path / "wiki" / dir_name

    if not entity_dir.exists():
        return []

    result = []
    for md in sorted(entity_dir.glob("*.md")):
        if md.stem.endswith("_extracted"):
            continue
        result.append({
            "name": md.stem,
            "path": str(md.relative_to(wiki_path)),
            "type": entity_type,
        })
    return result


def get_entity_types() -> Dict[str, str]:
    """Return all supported entity types and their directory names."""
    return {
        "articles": "_articles",
        "concepts": "_concepts",
        "methods": "_methods",
        "topics": "_topics",
        "foundations": "foundations",
    }
