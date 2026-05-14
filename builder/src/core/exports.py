#!/usr/bin/env python3
"""
exports.py — AI-consumable knowledge base exports
Generates: llms.txt, llms-full.txt, and living overview.md

Borrowed from: Pratiyush/llm-wiki (llms.txt pattern)
              SamurAIGPT/llm-wiki-agent (living overview pattern)
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def generate_llms_txt(wiki_path: Path, kb_name: str = "Knowledge Base") -> str:
    """
    Generate llms.txt — short index per llmstxt.org spec.
    Lists all wiki articles with one-line descriptions, suitable for
    AI agents to discover what's in the knowledge base.
    """
    index_file = wiki_path / "wiki" / "_meta" / "file_index.json"
    articles_dir = wiki_path / "wiki" / "_articles"
    concepts_dir = wiki_path / "wiki" / "_concepts"

    lines = [
        f"# {kb_name}",
        f"> Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "## Articles",
        "",
    ]

    # Articles with metadata
    if index_file.exists():
        try:
            idx = json.loads(index_file.read_text())
            for file_id, info in idx.get("files", {}).items():
                if info.get("status") != "completed":
                    continue
                name = info.get("name", file_id)
                llm = info.get("llm_metadata") or {}
                summary = (llm.get("summary") or info.get("wiki_path", ""))[:120]
                lines.append(f"- [{name}](wiki/_articles/{file_id}.md): {summary}")
        except (json.JSONDecodeError, OSError):
            pass
    elif articles_dir.exists():
        for md in sorted(articles_dir.glob("*.md")):
            if md.stem.endswith("_extracted"):
                continue
            lines.append(f"- [{md.stem}](wiki/_articles/{md.name})")

    # Concepts
    lines.append("")
    lines.append("## Concepts")
    lines.append("")
    if concepts_dir.exists():
        for md in sorted(concepts_dir.glob("*.md")):
            lines.append(f"- [{md.stem}](wiki/_concepts/{md.name})")

    return "\n".join(lines)


def generate_llms_full(wiki_path: Path, max_size_mb: float = 5.0) -> str:
    """
    Generate llms-full.txt — flattened plain-text dump of all wiki articles.
    Capped at max_size_mb to avoid overwhelming LLM context windows.
    """
    articles_dir = wiki_path / "wiki" / "_articles"
    if not articles_dir.exists():
        return "# No articles found"

    max_bytes = int(max_size_mb * 1024 * 1024)
    total = 0
    chunks = []

    for md in sorted(articles_dir.glob("*.md")):
        if md.stem.endswith("_extracted"):
            continue
        try:
            content = md.read_text(encoding="utf-8")
        except Exception:
            continue

        header = f"\n\n---\n## {md.stem}\n---\n\n"
        block = header + content
        if total + len(block.encode("utf-8")) > max_bytes:
            chunks.append(f"\n\n---\n[TRUNCATED at {max_size_mb}MB cap]\n")
            break

        chunks.append(block)
        total += len(block.encode("utf-8"))

    return "".join(chunks)


def generate_overview(wiki_path: Path, template: Optional[str] = None) -> str:
    """
    Generate living overview.md — a synthesis of all knowledge in the wiki.
    Updates on every compile-llm run.
    
    Borrowed from: SamurAIGPT/llm-wiki-agent (overview.md pattern)
    """
    index_file = wiki_path / "wiki" / "_meta" / "file_index.json"
    concepts_file = wiki_path / "wiki" / "_meta" / "concepts.json"

    lines = [
        "# Knowledge Base Overview",
        f"*Auto-generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "## Summary",
        "",
    ]

    # Stats
    total_docs = 0
    total_concepts = 0
    topics = set()

    if index_file.exists():
        try:
            idx = json.loads(index_file.read_text())
            for info in idx.get("files", {}).values():
                if info.get("status") == "completed":
                    total_docs += 1
                    llm = info.get("llm_metadata") or {}
                    t = llm.get("topics", [])
                    if isinstance(t, list):
                        topics.update(t)
        except (json.JSONDecodeError, OSError):
            pass

    if concepts_file.exists():
        try:
            concepts = json.loads(concepts_file.read_text())
            total_concepts = len(concepts.get("concepts", {}))
        except (json.JSONDecodeError, OSError):
            pass

    lines.append(f"- **Documents**: {total_docs} ingested")
    lines.append(f"- **Concepts**: {total_concepts} cross-referenced")
    if topics:
        lines.append(f"- **Topics**: {', '.join(sorted(topics)[:20])}")
    lines.append("")

    # Document listing
    lines.append("## Document Index")
    lines.append("")
    if index_file.exists():
        try:
            idx = json.loads(index_file.read_text())
            for file_id, info in sorted(idx.get("files", {}).items()):
                if info.get("status") != "completed":
                    continue
                name = info.get("name", file_id)
                llm = info.get("llm_metadata") or {}
                summary = (llm.get("summary") or "")[:200]
                lines.append(f"### {name}")
                if summary:
                    lines.append(f"{summary}")
                lines.append("")
        except Exception:
            pass

    # Concepts
    lines.append("## Concept Map")
    lines.append("")
    if concepts_file.exists():
        try:
            concepts = json.loads(concepts_file.read_text())
            for cname, cinfo in sorted(concepts.get("concepts", {}).items()):
                sources = cinfo.get("sources", [])
                lines.append(f"- **{cname}** — referenced in {len(sources)} document(s)")
        except Exception:
            pass

    return "\n".join(lines)


def generate_all_exports(wiki_path: Path, kb_name: str = "Knowledge Base") -> dict:
    """Generate all AI-consumable exports. Returns {filename: content}."""
    wiki_path = Path(wiki_path)
    exports = {}

    # llms.txt
    llms = generate_llms_txt(wiki_path, kb_name)
    (wiki_path / "wiki" / "llms.txt").write_text(llms)
    exports["wiki/llms.txt"] = len(llms)

    # llms-full.txt
    full = generate_llms_full(wiki_path)
    (wiki_path / "wiki" / "llms-full.txt").write_text(full)
    exports["wiki/llms-full.txt"] = len(full)

    # overview.md
    overview = generate_overview(wiki_path)
    (wiki_path / "wiki" / "overview.md").write_text(overview)
    exports["wiki/overview.md"] = len(overview)

    return exports
