"""Chapter-by-chapter compilation for large books (>300K words).

Extracted from cli.py.  Splits books at chapter boundaries, compiles
each chapter individually via LLM, then merges summaries into a final
wiki article.

Used by _compile_document when a book exceeds the chaptered threshold.
"""

import json, os, re
from datetime import datetime
from typing import Optional

from core.llm import chat
from core.prompts import _PROMPT_CHAPTER, _PROMPT_CHAPTER_MERGE
from utils.file_utils import read_text

_CHAPTER_SPLIT_PATTERNS = [
    re.compile(r'\n=== .{3,100} ===\n'),
    re.compile(r'\n(?:CHAPTER|Chapter)\s+[\w]+[:\s]*[^\n]{0,60}\n'),
    re.compile(r'\n(?:PART|Part)\s+[\w]+[:\s]*[^\n]{0,60}\n'),
    re.compile(r'\n#{2,3} .{3,80}\n'),
]


# ── Chapter-by-chapter compilation (large books) ─────────────────────

def _split_into_chapters(text: str) -> list:
    """Split book text into (title, body) pairs using chapter/part boundaries.

    Returns [(title, text), ...]. Returns [] if fewer than 3 chapters found.
    Uses the same patterns as _chunk_document but returns full text, not summaries.
    """
    SPLIT_PATTERNS = _CHAPTER_SPLIT_PATTERNS

    # Chapter titles that are metadata, not real content
    _SKIP_TITLES = {
        'advance praise', 'praise for', 'contents', 'acknowledgments',
        'acknowledgements', 'preface', 'foreword', 'index', 'notes',
        'bibliography', 'references', 'about the author', 'appendix',
        'copyright', 'title page', 'also by', 'dedication',
    }

    def _is_skip(ch_title: str) -> bool:
        lower = ch_title.lower()
        return any(skip in lower for skip in _SKIP_TITLES)

    for pattern in SPLIT_PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) < 3:
            continue

        chapters = []
        for i, m in enumerate(matches):
            title = m.group().strip()
            if _is_skip(title):
                continue
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            words = len(body.split())
            if words < 500:   # real chapters have substantial content
                continue
            chapters.append((title, body))

        if len(chapters) >= 3:
            return chapters

    return []


def _compile_document_chaptered(backend, model: str, file_info: dict,
                                 kb_path: str, max_chapters: int = 16) -> Optional[str]:
    """Compile a large book chapter-by-chapter, then merge chapter summaries.
    
    Returns None if the book has too few chapters (caller falls back to
    single-pass _compile_document).
    """
    # Deferred import — _stream_message lives in cli.py
    # Use sys.path insert to avoid "attempted relative import beyond top-level package"
    # when chaptered.py is called from compiler.py in nested import chains.
    import sys as _sys
    from pathlib import Path as _Path
    _src_dir = str(_Path(__file__).resolve().parent.parent)
    if _src_dir not in _sys.path:
        _sys.path.insert(0, _src_dir)
    import cli as _cli

    name = file_info.get("name", "Unknown")
    name_clean = re.sub(r'\.[^.]+$', '', name)
    meta = file_info.get("extracted_metadata", {})
    original_word_count = meta.get("word_count", "unknown")

    wiki_path = file_info.get("wiki_path", "")
    if not wiki_path:
        return None
    full_path = os.path.join(kb_path, wiki_path)
    raw = read_text(full_path)
    # Inline _clean_extracted_text — strip heuristic header
    marker = "\n## Structured Text\n"
    idx = raw.find(marker)
    text = raw[idx + len(marker):] if idx != -1 else raw

    chapters = _split_into_chapters(text)
    if len(chapters) < 3:
        # Not enough chapters — return None, caller falls back to single-pass
        return None

    # Cap and sample: first N-2 + last 2 for structure
    if len(chapters) > max_chapters:
        chapters = chapters[:max_chapters - 2] + chapters[-2:]

    # ── Phase 1: compile each chapter ──
    chapter_summaries = []
    for i, (ch_title, ch_text) in enumerate(chapters, 1):
        print(f"     ch {i}/{len(chapters)} {ch_title[:50]}", end="", flush=True)
        prompt = _PROMPT_CHAPTER.format(
            chapter_title=ch_title,
            book_name=name_clean,
            chapter_text=ch_text[:3000],  # cap per-chapter text
        )
        try:
            result = chat(prompt, backend=backend, model=model,
                          temperature=0.2, max_tokens=400)
            content = result["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1]
                if content.endswith("```"):
                    content = content[:-3]
            data = json.loads(content)
            summary_text = data.get("summary", "")
            chapter_summaries.append(
                f"### {ch_title}\n"
                f"Claims: {json.dumps(data.get('claims', []))}\n"
                f"Evidence: {json.dumps(data.get('evidence', []))}\n"
                f"Terms: {json.dumps(data.get('terms', []))}\n"
                f"Quotes: {json.dumps(data.get('quotes', []))}\n"
                f"Summary: {summary_text}"
            )
            print(f" ✅")
        except json.JSONDecodeError:
            raw_response = result.get("content", "") if 'result' in dir() else ""
            print(f" ⚠️ bad JSON, retrying with reformat...", end="", flush=True)
            # Second attempt: ask LLM to convert its response to valid JSON
            fix_prompt = (
                "Convert the following book chapter analysis into this exact JSON format:\n"
                '{"claims": ["...", ...], "evidence": ["...", ...], '
                '"terms": ["...", ...], "quotes": ["...", ...], '
                '"summary": "one dense sentence"}\n\n'
                f"RAW ANALYSIS:\n{raw_response[:1500]}"
            )
            try:
                result2 = chat(fix_prompt, backend=backend, model=model,
                               temperature=0.1, max_tokens=400)
                content2 = result2["content"].strip()
                if content2.startswith("```"):
                    content2 = content2.split("\n", 1)[-1]
                    if content2.endswith("```"):
                        content2 = content2[:-3]
                data = json.loads(content2)
                summary_text = data.get("summary", "")
                chapter_summaries.append(
                    f"### {ch_title}\n"
                    f"Claims: {json.dumps(data.get('claims', []))}\n"
                    f"Evidence: {json.dumps(data.get('evidence', []))}\n"
                    f"Terms: {json.dumps(data.get('terms', []))}\n"
                    f"Quotes: {json.dumps(data.get('quotes', []))}\n"
                    f"Summary: {summary_text}"
                )
                print(f" ✅ (reformatted)")
            except Exception:
                print(f" ⚠️ reformat also failed — using raw text")
                chapter_summaries.append(
                    f"### {ch_title}\nRaw: {raw_response[:300]}"
                )
        except Exception as e:
            err = str(e)
            # HTTP 400 on Chinese model = content filter → retry with DeepSeek
            if "400" in err and backend in ("zhipu", "kimi"):
                try:
                    result2 = chat(prompt, backend="deepseek", model="deepseek-chat",
                                   temperature=0.2, max_tokens=400)
                    content2 = result2["content"].strip()
                    if content2.startswith("```"):
                        content2 = content2.split("\n", 1)[-1]
                        if content2.endswith("```"):
                            content2 = content2[:-3]
                    data = json.loads(content2)
                    summary_text = data.get("summary", "")
                    chapter_summaries.append(
                        f"### {ch_title}\n"
                        f"Claims: {json.dumps(data.get('claims', []))}\n"
                        f"Evidence: {json.dumps(data.get('evidence', []))}\n"
                        f"Terms: {json.dumps(data.get('terms', []))}\n"
                        f"Quotes: {json.dumps(data.get('quotes', []))}\n"
                        f"Summary: {summary_text}"
                    )
                    print(f" ✅ (via deepseek)")
                    continue
                except Exception:
                    pass
                print(f" ⏭ (content filtered)")
            elif "400" in err:
                print(f" ⏭")
            else:
                print(f" ⚠️ {err[:60]}")
                chapter_summaries.append(
                    f"### {ch_title}\n[LLM extraction failed: {err[:80]}]"
                )

    if not chapter_summaries:
        return None

    # ── Phase 2: merge into final wiki article ──
    print(f"     merging {len(chapter_summaries)} chapter summaries...", end="", flush=True)
    stem_words = re.sub(r'[_\-. ]', ' ', name_clean).lower().split()
    tag_hint = ", ".join(w for w in stem_words[:4] if len(w) > 3)
    graph_context = _cli._build_graph_context(file_info, kb_path)
    merge_prompt = _PROMPT_CHAPTER_MERGE.format(
        name=name,
        name_clean=name_clean,
        word_count=original_word_count,
        tags=tag_hint,
        date=datetime.now().strftime("%Y-%m-%d"),
        n_chapters=len(chapter_summaries),
        chapter_summaries="\n\n".join(chapter_summaries),
        graph_context=graph_context,
    )
    result = _cli._stream_message(backend, model, merge_prompt, max_tokens=3000)
    print(f" ✅")
    return result

