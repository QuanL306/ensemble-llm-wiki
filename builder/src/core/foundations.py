#!/usr/bin/env python3
"""
foundations.py — Foundation terminal pages

Borrowed from: OmegaWiki (skyllwt/OmegaWiki)
Concept: Foundation pages are background knowledge that only receive
         incoming links — they never emit outgoing links. This prevents
         basic concepts from becoming gravity wells in the knowledge graph.

Usage:
  from core.foundations import create_foundation, list_foundations
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

FOUNDATION_TEMPLATE = """---
type: foundation
created: {created}
sources: {sources}
---

# {title}

{description}

## Key Facts

{key_facts}

## Sources

{sources_list}

---
*This is a foundation page. It provides background context and only receives 
incoming links — it does not emit links to other pages in the wiki.*
"""


def create_foundation(
    wiki_path: Path,
    title: str,
    description: str,
    key_facts: List[str],
    sources: List[str],
) -> Path:
    """
    Create a foundation (terminal) page.
    
    Foundation pages:
    - Only receive [[wikilinks]] from other pages
    - Never emit [[wikilinks]] to other pages
    - Provide background context for domain-specific concepts
    """
    foundations_dir = wiki_path / "wiki" / "foundations"
    foundations_dir.mkdir(parents=True, exist_ok=True)

    # Safe filename
    slug = title.lower().replace(" ", "-").replace("/", "-")
    for ch in "():,'\"?!":
        slug = slug.replace(ch, "")
    filename = foundations_dir / f"{slug}.md"

    sources_list = "\n".join(f"- {s}" for s in sources)
    facts_text = "\n".join(f"- {f}" for f in key_facts) if key_facts else ""

    content = FOUNDATION_TEMPLATE.format(
        created=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        sources=json.dumps(sources),
        title=title,
        description=description,
        key_facts=facts_text or "*No key facts provided*",
        sources_list=sources_list or "*No sources listed*",
    )

    filename.write_text(content)
    return filename


def list_foundations(wiki_path: Path) -> List[dict]:
    """List all foundation pages with metadata."""
    foundations_dir = wiki_path / "wiki" / "foundations"
    if not foundations_dir.exists():
        return []

    result = []
    for md in sorted(foundations_dir.glob("*.md")):
        try:
            content = md.read_text(encoding="utf-8")
        except Exception:
            continue

        # Extract frontmatter
        info = {"file": md.name, "title": md.stem.replace("-", " ").title()}
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    fm = {}
                    for line in parts[1].strip().split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            fm[k.strip()] = v.strip()
                    info.update(fm)
                except Exception:
                    pass
        result.append(info)

    return result


def get_foundation_content(wiki_path: Path, title: str) -> Optional[str]:
    """Get the content of a specific foundation page."""
    slug = title.lower().replace(" ", "-")
    for ch in "():,'\"?!":
        slug = slug.replace(ch, "")
    filepath = wiki_path / "wiki" / "foundations" / f"{slug}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None
