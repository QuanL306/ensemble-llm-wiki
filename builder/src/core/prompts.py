"""Prompt templates for LLM-driven KB compilation.

Extracted from cli.py to keep the module manageable.  These are
raw format strings consumed by _compile_document, _compile_index,
_compile_concepts, and _compile_document_chaptered.
"""

_PROMPT_DOCUMENT = """\
You are a knowledge curator building a personal research wiki in Obsidian.

PRIMARY READERS:
1. An AI assistant that will later search this wiki to answer research \
questions — it reads the Summary to decide relevance, then digs deeper.
2. The human researcher who built this KB and wants to reference and \
build on ideas across documents.

PRIMARY RULE: Be specific. Never say "this work explores X" without \
immediately stating what it specifically concludes about X.

Document: {name}  (~{word_count} words)

{graph_context}

DOCUMENT TEXT:
{text}

---

Write the wiki article now using this exact structure:

```yaml
---
title: "{name_clean}"
type: book|paper|article|report|other
tags: [{tags}]
word_count: {word_count}
compiled: {date}
---
```

## Summary

One paragraph. Answer three things:
(1) What specific question or problem does this address?
(2) What is the author's specific answer — the actual claim, not a \
paraphrase of the topic?
(3) What makes this work's perspective distinctive compared to conventional \
wisdom on this topic?
If the source text was cut off or unclear in places, note that honestly here.

## Core Arguments

Number each major argument. For each one:
- **Claim**: what exactly is being asserted (be precise, not generic)
- **Evidence type**: experiment / case study / historical analysis / \
statistical data / logical argument / personal observation
- **Key caveat**: any limitation or counter-argument the author acknowledges

## Author's Terminology

Terms this author introduces or uses in a distinctive, non-standard way.
Each one wrapped in [[double brackets]] — these become navigable links.
Format: **[[term]]** — how the author defines or uses it specifically here.

## Evidence & Data

Concrete specifics only. Named experiments, exact statistics with their \
sources, specific dates, named people or organizations, particular case \
studies. Vague references ("studies show...") do not belong here.

## Key Quotes

2–4 verbatim quotes. Select lines that are maximally insight-dense — \
the ones where the author's thinking is sharpest.
Format: > "exact quote" — [chapter / page / section if available]

## Connections

How these ideas interact with other fields, debates, or works.
Be specific: not "this relates to psychology" but "this challenges \
[[cognitive bias]] research by arguing that..."
Use [[double brackets]] for every concept worth linking.

## What This Doesn't Cover

Honest gaps this article must flag:
- What the author explicitly sets out of scope
- Acknowledged weaknesses in the evidence or argument
- What a thoughtful critic would say is missing
- What follow-up work the author themselves calls for

## For Future Queries

Write exactly 15 retrieval hints — one sentence each. Every sentence must \
be specific to this document; never write a placeholder like "[topic]".
Use the author's actual vocabulary so keyword matching works well.

These hints will be stored in the search index and used verbatim to match \
incoming research queries, so precision matters.

Write 5 hints from each of these three angles:

**Conceptual** (what ideas / arguments / findings this work addresses):
"If someone asks about [specific concept], this document is relevant because [specific reason]."

**Methodological / evidence** (how the work argues, what data or cases it uses):
"If someone researching [specific method or evidence type], this document is relevant because [specific reason]."

**Application / implication** (what the work means for practice or related problems):
"If someone wants to [specific action or application], this document is relevant because [specific reason]."

Number the 15 hints 1–15. No category headers in the output — just the 15 numbered sentences.
"""


_PROMPT_CHAPTER = """\
Extract the key intellectual content from this book chapter for a research wiki.

Chapter: {chapter_title}
Book: {book_name}

CHAPTER TEXT:
{chapter_text}

Return ONLY this JSON (no markdown, no explanation):
{{
  "claims": ["claim 1", "claim 2", ...],
  "evidence": ["specific data point or case study", ...],
  "terms": ["distinctive term or concept", ...],
  "quotes": ["verbatim quote", ...],
  "summary": "one dense sentence capturing this chapter's contribution"
}}
"""


_PROMPT_CHAPTER_MERGE = """\
You are a knowledge curator building a personal research wiki in Obsidian.

You have chapter-level summaries for the book "{name}". Synthesize them into
a single wiki article following the structure below.

BOOK METADATA:
- Title: {name_clean}
- Word count: ~{word_count}
- Chapters summarized: {n_chapters}

{graph_context}

CHAPTER SUMMARIES:
{chapter_summaries}

Write the wiki article now using this exact structure:

```yaml
---
title: "{name_clean}"
type: book
tags: [{tags}]
word_count: {word_count}
compiled: {date}
compiled_via: chapter-synthesis
---
```

## Summary
One paragraph synthesizing the book's core argument from the chapters.

## Core Arguments
Number the major arguments that emerge across chapters.

## Author's Terminology
[[terms]] that appear across multiple chapters.

## Evidence & Data
Concrete specifics from the chapter summaries.

## Key Quotes
2–4 most important verbatim quotes from the collection.

## Chapter Map
A concise table: chapter → one-sentence contribution.

## Connections
How these ideas interact with other fields or works.

## For Future Queries
Write exactly 15 retrieval hints, 5 from each of three angles:
- Conceptual (what ideas/arguments/findings)
- Methodological/evidence (how the work argues)
- Application/implication (what it means)

Number the 15 hints 1–15. No category headers.
"""


_PROMPT_INDEX = """\
You are writing the master navigation file for a personal research knowledge \
base. An AI assistant reads this index FIRST whenever it needs to answer a \
research question — it must be able to determine in one scan which documents \
are relevant and where to look.

Current articles (frontmatter + summary + retrieval hints):

{summaries}

Write _index.md with this structure:

## About This Knowledge Base
2-3 sentences: what territory does this KB cover? What are its primary \
questions? What would be a poor use of this KB?

## Topic Map
Group documents into 3–7 thematic clusters. For each cluster:
### [Theme Name]
- [[Document Title]] — one sentence: what specific claim or data does it add?

## Core Concepts
The [[concepts]] that appear across multiple documents. These are the \
intellectual backbone — entry points for navigating the KB.
List each with a one-line role: what role does this concept play in the KB?

## Research Gaps
What key questions is this KB NOT yet equipped to answer? Be honest — \
this helps the researcher know what they need to supplement.

## Document Listing
Alphabetical listing of all documents with one-line descriptions.
Group by first letter (A, B, C...).
"""


_PROMPT_CONCEPTS = """\
You are synthesizing concept articles for a personal research wiki.

Below are concepts extracted from multiple documents, with excerpts showing \
how each is used.

{links_data}

Select the top {n} concepts that are genuinely central \
(skip generic terms like "research" or "analysis"). For each, write a \
concept article that synthesises how the term is actually used across \
these specific documents — grounded in the excerpts, not in \
generic definitions.

Separate each concept with ---CONCEPT_BREAK--- on its own line.
"""
