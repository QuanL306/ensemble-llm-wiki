#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wiki Compiler Module
"""

import os
import re
import sys
from typing import Dict, List, Any, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_utils import write_text, read_text, ensure_dir, sanitize_filename
from core.indexer import IndexManager, ConceptIndex


class WikiCompiler:
    """Wiki Compiler"""
    
    def __init__(self, kb_path: str, llm_client=None):
        self.kb_path = kb_path
        self.llm_client = llm_client
        
        self.indexer = IndexManager(kb_path)
        self.concept_index = ConceptIndex(kb_path)
        
        self.wiki_dir = os.path.join(kb_path, "wiki")
        self.concepts_dir = os.path.join(self.wiki_dir, "_concepts")
        self.articles_dir = os.path.join(self.wiki_dir, "_articles")
        self.topics_dir = os.path.join(self.wiki_dir, "_topics")
        
        for dir_path in [self.concepts_dir, self.articles_dir, self.topics_dir]:
            ensure_dir(dir_path)
    
    def compile_summary(self, file_id: str) -> Optional[str]:
        """Generate summary article for file"""
        file_info = self.indexer.get_file(file_id)
        if not file_info:
            return None
        
        wiki_path = file_info.get("wiki_path")
        if not wiki_path:
            return None
        
        full_wiki_path = os.path.join(self.kb_path, wiki_path)
        if not os.path.exists(full_wiki_path):
            return None
        
        extracted_content = read_text(full_wiki_path)
        metadata = file_info.get("extracted_metadata", {})
        
        summary = self._generate_summary_markdown(file_info, metadata, extracted_content)
        
        article_filename = f"{sanitize_filename(file_info['stem'])}.md"
        article_path = os.path.join(self.articles_dir, article_filename)
        
        write_text(article_path, summary)
        
        return article_path
    
    def _generate_summary_markdown(
        self, 
        file_info: Dict, 
        metadata: Dict, 
        extracted_content: str
    ) -> str:
        """Generate summary Markdown"""
        
        lines = [
            "---",
            f"title: {file_info['name']}",
            f"source: {file_info['path']}",
            f"file_type: {metadata.get('file_type', 'Unknown')}",
            f"word_count: {metadata.get('word_count', 0)}",
            f"added_at: {file_info.get('added_at', '')}",
            f"status: {file_info.get('status', '')}",
            "---",
            "",
            f"# {file_info['name']}",
            "",
            "## Basic Information",
            "",
            f"- **Filename**: {file_info['name']}",
            f"- **File Type**: {metadata.get('file_type', 'Unknown')}",
            f"- **Word Count**: {metadata.get('word_count', 0)}",
            f"- **Source Path**: `{file_info['path']}`",
            "",
            "## Core Claims",
            ""
        ]
        
        core_claims = metadata.get('core_claims', [])
        if core_claims:
            for claim in core_claims:
                lines.append(f"- {claim}")
        else:
            lines.append("_No core claims_")
        
        lines.extend(["", "## Key Data", ""])
        
        key_data = metadata.get('key_data', [])
        if key_data:
            for data in key_data:
                lines.append(f"- {data}")
        else:
            lines.append("_No key data_")
        
        lines.extend(["", "## Notable Quotes", ""])
        
        quotes = metadata.get('quotes', [])
        if quotes:
            for quote in quotes:
                lines.append(f"> {quote}")
                lines.append("")
        else:
            lines.append("_No quotes_")
        
        lines.extend(["", "## Full Content", "", "<details>", "<summary>Expand</summary>", ""])
        
        structured_text = metadata.get('structured_text', '')
        if structured_text:
            lines.append(structured_text)
        
        lines.extend(["", "</details>", "", "## Related Documents", "", "_Auto-generated..._", ""])
        
        return '\n'.join(lines)
    
    # Common English words to exclude from concept candidates
    _STOP_WORDS = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "it", "its", "this", "that",
        "these", "those", "i", "you", "he", "she", "we", "they", "what",
        "which", "who", "how", "when", "where", "why", "not", "no", "can",
        "if", "then", "than", "so", "as", "also", "one", "two", "three",
        "new", "use", "used", "using", "based", "see", "see", "each",
    }

    # Common Chinese stop words
    _CN_STOP_WORDS = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
        "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会",
        "着", "没有", "看", "好", "自己", "这", "他", "她", "它", "们",
        "那", "些", "什么", "怎么", "如何", "可以", "因为", "所以", "但是",
        "而且", "或者", "如果", "虽然", "已经", "还是", "只是", "不是",
        "这个", "那个", "这些", "那些", "我们", "你们", "他们", "它们",
        "其", "之", "等", "把", "被", "让", "给", "从", "向", "对", "与",
        "及", "以", "为", "而", "地", "得", "能", "将", "才", "则",
        "更", "最", "又", "并", "于", "中", "里", "后", "前", "时",
        "年", "月", "日", "个", "种", "样", "些", "所", "来", "出",
        "下", "过", "没", "两", "三", "四", "五", "六", "七", "八",
        "九", "十", "百", "千", "万", "亿", "多", "少", "大", "小",
        "长", "短", "高", "低", "好", "坏", "新", "老", "第", "次",
        # Common verbs/adverbs that are not useful as concepts
        "不过", "因此", "大多数", "由此", "带来", "见到", "所处",
        "起来", "出来", "出来", "下去", "上去", "过去", "回来",
        "开始", "进行", "做出", "看来", "认为", "看到", "指出",
        "通过", "以及", "成为", "之间", "关于", "对于", "根据",
        "同时", "当时", "其中", "之后", "之前", "现在", "今天",
        "可能", "应该", "必须", "需要", "得到", "实现", "建设",
        "发展", "经济", "社会", "问题", "方面", "情况", "工作",
        "国家", "企业", "市场", "城市", "地区", "时候", "地方",
        "准备", "举办", "探讨", "平衡", "长远", "上升", "全部",
        "运作", "形势", "时机", "专家", "领导人", "媒体",
        "宏观", "指导", "报告", "优势",
    }

    @staticmethod
    def _is_chinese_text(text: str) -> bool:
        """Check if text is predominantly Chinese"""
        if not text:
            return False
        cn = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return cn / max(len(text), 1) > 0.15

    def _extract_chinese_concepts(self, text: str) -> set:
        """Extract concepts from Chinese text using jieba + heuristics"""
        concepts: set = set()

        # 1. 《》 book/article titles
        for title in re.findall(r'《([^》]{2,40})》', text):
            concepts.add(title)

        # 2. 「」and 『』quoted terms
        for term in re.findall(r'[「『]([^」』]{2,30})[」』]', text):
            concepts.add(term)

        # 3. Section headers (第X章/节/篇 patterns)
        for header in re.findall(r'(?:第[一二三四五六七八九十百千\d]+[章节篇部])\s*([^\n,，。.]{2,20})', text):
            header = header.strip()
            if 2 <= len(header) <= 20 and not re.match(r'^[\d\s\-_]+$', header):
                concepts.add(header)

        # 4. jieba segmentation with POS tagging — keep only nouns
        try:
            import jieba.posseg as pseg
            word_freq: dict = {}
            for pair in pseg.cut(text):
                w = pair.word.strip()
                flag = pair.flag
                # Only keep nouns: n(名词), nr(人名), ns(地名), nt(机构名),
                # nz(其他专名), ng(名语素), vn(名动词), an(名形词)
                if flag not in ('n', 'nr', 'ns', 'nt', 'nz', 'ng', 'vn', 'an'):
                    continue
                if len(w) < 2 or len(w) > 8:
                    continue
                if not all('\u4e00' <= c <= '\u9fff' for c in w):
                    continue
                if w in self._CN_STOP_WORDS:
                    continue
                word_freq[w] = word_freq.get(w, 0) + 1
            # Keep 3+ char terms appearing 4+ times, 2-char terms appearing 8+ times
            for w, freq in word_freq.items():
                if len(w) >= 3 and freq >= 4:
                    concepts.add(w)
                elif len(w) == 2 and freq >= 8:
                    concepts.add(w)
        except ImportError:
            # Fallback: extract high-frequency 2-4 char n-grams
            cn_runs = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
            freq: dict = {}
            for phrase in cn_runs:
                freq[phrase] = freq.get(phrase, 0) + 1
            for phrase, count in freq.items():
                if count >= 5:
                    concepts.add(phrase)

        return concepts

    def extract_concepts(self, file_id: str) -> List[str]:
        """
        Extract concepts from file using multiple strategies.
        Auto-detects Chinese vs English text and uses appropriate method.
        """
        file_info = self.indexer.get_file(file_id)
        if not file_info:
            return []

        metadata = file_info.get("extracted_metadata", {})
        concepts: set = set()

        # --- Strategy 1: explicit markers in core_claims ---
        core_claims = metadata.get('core_claims', [])
        all_claim_text = " ".join(core_claims)

        # Terms in "quotes" or 'quotes'
        quoted = re.findall(r'[""\'"]([^"""\'\"]{4,60})[""\'"]', all_claim_text)
        concepts.update(quoted)

        # 《CJK book titles》
        book_titles = re.findall(r'《([^》]+)》', all_claim_text)
        concepts.update(book_titles)

        # --- Load full text from disk ---
        structured_text = ''
        wiki_path = file_info.get('wiki_path', '')
        if wiki_path:
            full_path = os.path.join(self.kb_path, wiki_path)
            if os.path.exists(full_path):
                try:
                    structured_text = read_text(full_path)
                except Exception:
                    pass

        if structured_text:
            if self._is_chinese_text(structured_text):
                # --- Chinese extraction ---
                concepts.update(self._extract_chinese_concepts(structured_text))
            else:
                # --- English extraction (original logic) ---
                multi_word = re.findall(
                    r'\b([A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,}){1,3})\b',
                    structured_text
                )
                concepts.update(multi_word)

                mid_sentence_caps = re.findall(
                    r'(?<=[a-z]\s)([A-Z][a-z]{3,})\b',
                    structured_text
                )
                word_freq: dict = {}
                for w in mid_sentence_caps:
                    if w.lower() not in self._STOP_WORDS:
                        word_freq[w] = word_freq.get(w, 0) + 1
                for word, freq in word_freq.items():
                    if freq >= 3:
                        concepts.add(word)

        # --- Noise filter ---
        _is_cn = self._is_chinese_text(structured_text) or self._is_chinese_text(all_claim_text)
        filtered = set()
        for concept in concepts:
            stripped = concept.strip(' /\\.,;:\'"-，。、；：""''（）\n\r\t')
            if len(stripped) < 2 or len(stripped) > 60:
                continue
            # Reject concepts with newlines
            if '\n' in stripped or '\r' in stripped:
                continue
            # Reject ASCII-only concepts in Chinese documents (OCR noise)
            if _is_cn and re.search(r'[a-zA-Z]', stripped):
                # Allow mixed CJK+English like "珠三角CBD", but reject pure ASCII
                if not re.search(r'[\u4e00-\u9fff]', stripped):
                    continue
            if re.match(r'^[/\\.\-_\d\s]+$', stripped):
                continue
            if stripped.lower() in self._STOP_WORDS:
                continue
            if stripped in self._CN_STOP_WORDS:
                continue
            if re.search(r'https?://|www\.|\.(com|org|io|py|js|md)$', stripped, re.I):
                continue
            filtered.add(stripped)

        # --- Build definition from surrounding sentence ---
        for concept in filtered:
            definition = f"Extracted from {file_info['name']}"
            if structured_text:
                for sentence in re.split(r'[。！？.!?\n]', structured_text):
                    if concept in sentence and 10 < len(sentence) < 300:
                        definition = sentence.strip()
                        break

            self.concept_index.add_concept(
                name=concept,
                definition=definition,
                source_file=file_info['path']
            )

        self.concept_index.save()
        return list(filtered)
    
    def generate_concept_article(self, concept_name: str) -> Optional[str]:
        """Generate article for concept"""
        concept = self.concept_index.get_concept(concept_name)
        if not concept:
            return None
        
        filename = f"{sanitize_filename(concept_name)}.md"
        filepath = os.path.join(self.concepts_dir, filename)
        
        lines = [
            "---",
            f"title: {concept_name}",
            f"created_at: {concept.get('created_at', datetime.now().isoformat())}",
            f"source_count: {len(concept.get('files', []))}",
            "---",
            "",
            f"# {concept_name}",
            "",
            "## Definition",
            "",
            concept.get('definition', '_No definition_'),
            "",
            "## Source Documents",
            ""
        ]
        
        for file_path in concept.get('files', []):
            file_name = os.path.basename(file_path)
            lines.append(f"- [[{file_name}]]")
        
        lines.extend(["", "## Related Concepts", ""])
        
        for related in concept.get('related_concepts', []):
            lines.append(f"- [[{related}]]")
        
        if not concept.get('related_concepts'):
            lines.append("_No related concepts_")
        
        lines.append("")
        
        write_text(filepath, '\n'.join(lines))
        
        return filepath
    
    def generate_all_concept_articles(self) -> List[str]:
        """Generate articles for all concepts"""
        generated = []
        
        for concept_name in self.concept_index.get_all_concepts().keys():
            filepath = self.generate_concept_article(concept_name)
            if filepath:
                generated.append(filepath)
        
        return generated
    
    def generate_main_index(self) -> str:
        """Generate main index page"""
        index_path = os.path.join(self.wiki_dir, "_index.md")
        
        stats = self.indexer.get_stats()
        concepts = self.concept_index.get_all_concepts()
        
        lines = [
            "---",
            f"title: Knowledge Base Index",
            f"last_updated: {datetime.now().isoformat()}",
            "---",
            "",
            "# Knowledge Base Index",
            "",
            "## Statistics",
            "",
            f"- **Total Documents**: {stats['total']}",
            f"- **Completed**: {stats['completed']}",
            f"- **Pending**: {stats['pending']}",
            f"- **Processing**: {stats['processing']}",
            f"- **Error**: {stats['error']}",
            f"- **Total Concepts**: {len(concepts)}",
            "",
            "## Quick Navigation",
            "",
            "### 📄 Document Summaries",
            "",
            "See [[_articles/_index|Article Index]]",
            "",
            "### 💡 Concept Dictionary",
            "",
            f"Total {len(concepts)} concepts:",
            ""
        ]
        
        for concept_name in sorted(concepts.keys())[:50]:
            filename = sanitize_filename(concept_name)
            lines.append(f"- [[{filename}|{concept_name}]]")
        
        if len(concepts) > 50:
            lines.append(f"\n... and {len(concepts) - 50} more concepts")
        
        lines.extend(["", "### 📁 Topic Categories", "", "See [[_topics/_index|Topic Index]]", ""])
        
        write_text(index_path, '\n'.join(lines))
        
        return index_path
    
    def compile_all(self) -> Dict[str, Any]:
        """Full compilation workflow"""
        results = {
            "summaries": [],
            "concepts_extracted": [],
            "concept_articles": [],
            "errors": []
        }
        
        completed_files = self.indexer.get_completed_files()
        
        for file_info in completed_files:
            try:
                file_id = self.indexer.generate_file_id(file_info['path'])
                summary_path = self.compile_summary(file_id)
                if summary_path:
                    results["summaries"].append(summary_path)
                    
                    concepts = self.extract_concepts(file_id)
                    results["concepts_extracted"].extend(concepts)
                    
            except Exception as e:
                results["errors"].append({
                    "file": file_info.get('path', 'unknown'),
                    "error": str(e)
                })
        
        concept_articles = self.generate_all_concept_articles()
        results["concept_articles"] = concept_articles
        
        main_index = self.generate_main_index()
        results["main_index"] = main_index
        
        return results
