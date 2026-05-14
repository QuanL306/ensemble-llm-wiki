#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Document Reader Module - Built-in Full Functionality
No external skill required, ready to use out of the box

Supported formats:
- PDF (text + scanned OCR)
- EPUB ebooks
- Markdown / TXT
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


def extract_document(file_path: str, output_dir: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Extract document content (built-in full implementation)
    
    Args:
        file_path: Path to document
        output_dir: Output directory (optional)
    
    Returns:
        (Extracted text content, metadata dict)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Extract document
    text = _extract(file_path)
    
    # Generate index
    index = _generate_index(text)
    
    # Determine output path
    base_name = Path(file_path).stem
    if output_dir is None:
        output_dir = os.path.dirname(file_path) or '.'
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, f"{base_name}_extracted.txt")
    
    # Build output content
    content = f"""# Document Analysis
## Basic Information
- Filename: {os.path.basename(file_path)}
- File Type: {Path(file_path).suffix.upper()}
- Total Words: {len(text.split())}

## Content Index
### Core Claims (Top 20)
"""
    for item in index['core_claims']:
        content += f"- {item}\n"
    
    content += "\n### Key Data (Top 20)\n"
    for item in index['data_points']:
        content += f"- {item}\n"
    
    content += "\n### Notable Quotes (Top 20)\n"
    for item in index['quotes']:
        content += f"- {item}\n"
    
    content += f"\n## Structured Text\n{text}"
    
    # Save file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Build metadata
    metadata = {
        "original_path": file_path,
        "file_name": os.path.basename(file_path),
        "file_type": Path(file_path).suffix.upper(),
        "word_count": len(text.split()),
        "core_claims": index['core_claims'],
        "key_data": index['data_points'],
        "quotes": index['quotes'],
        "structured_text": text
    }
    
    return content, metadata


def _extract(file_path: str) -> str:
    """Select extraction method based on file type"""
    ext = Path(file_path).suffix.lower()
    
    if ext == '.pdf':
        return _extract_pdf(file_path)
    elif ext == '.epub':
        return _extract_epub(file_path)
    elif ext in ['.txt', '.md', '.markdown']:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _extract_pdf(doc_path: str) -> str:
    """Extract PDF text (auto-detect scanned)"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("Please install PyMuPDF: pip install PyMuPDF")
    
    # Detect if scanned
    if _is_scanned_pdf(doc_path):
        return _extract_pdf_scanned(doc_path)
    else:
        return _extract_pdf_normal(doc_path)


def _is_scanned_pdf(doc_path: str, sample_pages: int = 3) -> bool:
    """Detect if PDF is scanned"""
    import fitz

    with fitz.open(doc_path) as doc:
        total_pages = len(doc)
        check_pages = min(sample_pages, total_pages)

        scanned_count = 0
        for i in range(check_pages):
            page = doc[i]
            text = page.get_text().strip()
            if len(text) < 100:
                scanned_count += 1

    return scanned_count >= (check_pages // 2 + 1)


def _extract_pdf_normal(doc_path: str) -> str:
    """Extract normal PDF text"""
    import fitz

    with fitz.open(doc_path) as doc:
        pages_text = []

        for page_num, page in enumerate(doc, 1):
            blocks = page.get_text("blocks")
            if not blocks:
                pages_text.append(f"\n--- Page {page_num} ---\n[No text content]\n")
                continue

            # Multi-column detection
            page_rect = page.rect
            mid_x = page_rect.width / 2
            left_blocks = [b for b in blocks if b[2] < mid_x]
            right_blocks = [b for b in blocks if b[0] > mid_x]
            center_blocks = [b for b in blocks if b not in left_blocks and b not in right_blocks]
            has_two_columns = len(left_blocks) > 3 and len(right_blocks) > 3

            sorted_blocks = []
            if has_two_columns:
                left_blocks.sort(key=lambda b: b[1])
                right_blocks.sort(key=lambda b: b[1])
                center_blocks.sort(key=lambda b: b[1])
                # Place center blocks (headings, tables) before the column they precede
                sorted_blocks = left_blocks + right_blocks + center_blocks
                sorted_blocks.sort(key=lambda b: b[1])
            else:
                sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))

            page_text = f"\n--- Page {page_num} ---\n"
            for block in sorted_blocks:
                text = block[4].strip()
                if text:
                    page_text += text + "\n"

            pages_text.append(page_text)

    return "\n".join(pages_text)


def _extract_pdf_scanned(doc_path: str, lang: str = None) -> str:
    """OCR extract scanned PDF — auto-detects Chinese if lang not specified"""
    import fitz

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        raise ImportError("Please install OCR dependencies: pip install pytesseract pillow")

    with fitz.open(doc_path) as doc:
        # Auto-detect language: sample a middle page and check for CJK characters
        if lang is None:
            lang = 'eng'
            sample_page = doc[min(len(doc) // 2, len(doc) - 1)]
            mat = fitz.Matrix(2.0, 2.0)
            pix = sample_page.get_pixmap(matrix=mat)
            if pix.n >= 5:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            # Quick OCR with eng to check if result has any text
            probe = pytesseract.image_to_string(img, lang='chi_sim+eng')
            cn_chars = sum(1 for c in probe if '\u4e00' <= c <= '\u9fff')
            if cn_chars > 20:
                lang = 'chi_sim+eng'
                print(f"  Detected Chinese text, using chi_sim+eng for OCR")

        pages_text = []

        print(f"Detected scanned PDF, {len(doc)} pages, starting OCR...")

        for page_num, page in enumerate(doc, 1):
            print(f"  OCR page {page_num}/{len(doc)}...")
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            # Convert to RGB if the pixmap uses a non-RGB colorspace
            if pix.n >= 5:  # CMYK or other non-RGB/Gray
                pix = fitz.Pixmap(fitz.csRGB, pix)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            text = pytesseract.image_to_string(img, lang=lang)
            text = _clean_ocr_text(text)

            pages_text.append(f"\n--- Page {page_num} ---\n{text}\n")

    return "\n".join(pages_text)


def _clean_ocr_text(text: str) -> str:
    """Clean OCR text"""
    lines = text.split('\n')
    cleaned_lines = []
    buffer = ""
    
    for line in lines:
        line = line.strip()
        if not line:
            if buffer:
                cleaned_lines.append(buffer)
                buffer = ""
            continue
        
        if buffer:
            if not line[0].isupper() and not line[0].isdigit() and line[0] not in '""':
                buffer += line
            else:
                cleaned_lines.append(buffer)
                buffer = line
        else:
            buffer = line
    
    if buffer:
        cleaned_lines.append(buffer)
    
    return '\n'.join(cleaned_lines)


def _extract_epub(epub_path: str) -> str:
    """Extract EPUB text"""
    try:
        from ebooklib import epub
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("Please install EPUB dependencies: pip install EbookLib beautifulsoup4")
    
    try:
        book = epub.read_epub(epub_path)
    except Exception as e:
        # Some malformed EPUBs fail with nsmap errors in ebooklib
        # Try with ignore_ncx=True
        try:
            book = epub.read_epub(epub_path, options={"ignore_ncx": True})
        except Exception:
            raise ValueError(f"Cannot parse EPUB: {e}")

    chapters_text = []
    chapter_num = 0

    for item in book.get_items():
        if not hasattr(item, 'get_content'):
            continue
        try:
            if not isinstance(item, epub.EpubHtml):
                continue
        except Exception:
            continue
        chapter_num += 1
        try:
            html_content = item.get_content().decode('utf-8', errors='ignore')
        except Exception:
            continue
        soup = BeautifulSoup(html_content, 'html.parser')

        title = ""
        for tag in ['h1', 'h2', 'h3', 'title']:
            title_tag = soup.find(tag)
            if title_tag:
                title = title_tag.get_text().strip()
                break

        text = soup.get_text(separator='\n')
        text = _clean_text(text)

        chapter_header = f"\n=== Chapter {chapter_num}"
        if title:
            chapter_header += f": {title}"
        chapter_header += " ===\n"

        chapters_text.append(chapter_header + text + "\n")

    return "\n".join(chapters_text)


def _clean_text(text: str) -> str:
    """General text cleaning"""
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [line.strip() for line in text.split('\n')]
    
    filtered = []
    for line in lines:
        if re.match(r'^\d+$', line):  # Page number
            continue
        if re.match(r'^\d+\s*/\s*\d+$', line):  # "1 / 10"
            continue
        if not line or len(line) < 3:
            continue
        filtered.append(line)
    
    return '\n'.join(filtered)


def _generate_index(text: str) -> Dict[str, list]:
    """Generate content index"""
    lines = text.split('\n')
    index = {
        'core_claims': [],
        'data_points': [],
        'quotes': []
    }
    
    for line in lines:
        line = line.strip()
        if len(line) < 10 or len(line) > 300:
            continue

        # Core claims — word-bounded to avoid substring matches
        if re.search(r'\b(?:is|are|means|reflects|indicates|proves|determines|leads\s+to|essence|key)\b', line, re.IGNORECASE):
            if line not in index['core_claims']:
                index['core_claims'].append(line)

        # Key data — anchor year-like patterns with surrounding context
        if re.search(r'\d+\.?\d*\s*%|\$\d+|\b\d{4}\b|\d+\s+billion|\d+\s+million|\d+\.\d+', line):
            if line not in index['data_points']:
                index['data_points'].append(line)

        # Quotes — require actual quoted text (paired double quotes), not apostrophes
        if re.search(r'"[^"]{5,}"', line):
            if line not in index['quotes']:
                index['quotes'].append(line)

    # Truncate once after processing all lines
    for key in index:
        if len(index[key]) > 20:
            index[key] = index[key][:20]

    return index


def is_supported_format(file_path: str) -> bool:
    """Check if file format is supported"""
    supported = ['.pdf', '.epub', '.txt', '.md', '.markdown']
    ext = Path(file_path).suffix.lower()
    return ext in supported


def get_document_summary(metadata: Dict[str, Any], max_length: int = 500) -> str:
    """Generate document summary"""
    summary_parts = [
        f"File: {metadata.get('file_name', 'Unknown')}",
        f"Type: {metadata.get('file_type', 'Unknown')}",
        f"Words: {metadata.get('word_count', 0)}"
    ]
    
    core_claims = metadata.get('core_claims', [])
    if core_claims:
        summary_parts.append("\nCore Claims:")
        for claim in core_claims[:5]:
            summary_parts.append(f"  - {claim}")
    
    key_data = metadata.get('key_data', [])
    if key_data:
        summary_parts.append("\nKey Data:")
        for data in key_data[:3]:
            summary_parts.append(f"  - {data}")
    
    summary = '\n'.join(summary_parts)
    if len(summary) > max_length:
        summary = summary[:max_length] + "..."
    
    return summary
