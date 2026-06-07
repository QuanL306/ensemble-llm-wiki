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
    
    # Determine output path
    base_name = Path(file_path).stem
    if output_dir is None:
        output_dir = os.path.dirname(file_path) or '.'
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, f"{base_name}_extracted.txt")
    
    # Build output content (no heuristic index — LLM compilation handles extraction)
    content = f"""# Document Analysis
## Basic Information
- Filename: {os.path.basename(file_path)}
- File Type: {Path(file_path).suffix.upper()}
- Total Words: {len(text.split())}

## Structured Text
{text}"""

    # Save file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    # Build metadata
    metadata = {
        "original_path": file_path,
        "file_name": os.path.basename(file_path),
        "file_type": Path(file_path).suffix.upper(),
        "word_count": len(text.split()),
        "core_claims": [],   # deprecated — LLM compile handles extraction
        "key_data": [],
        "quotes": [],
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

    # ── Encrypted / corrupt PDF detection ──
    try:
        with fitz.open(doc_path) as test_doc:
            pass
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg:
            raise ValueError(
                f"PDF is encrypted or password-protected: {doc_path}\n"
                f"Remove protection first:  qpdf --decrypt \"{doc_path}\" unprotected.pdf"
            )
        if "not a pdf" in msg or "corrupt" in msg:
            raise ValueError(f"File is not a valid PDF or is corrupted: {doc_path}")
        raise ValueError(f"Cannot open PDF: {doc_path} — {e}")

    # Detect if scanned — samples beginning, middle, and end pages
    if _is_scanned_pdf(doc_path):
        return _extract_pdf_scanned(doc_path)
    else:
        return _extract_pdf_normal(doc_path)


def _is_scanned_pdf(doc_path: str, sample_pages: int = 5) -> bool:
    """Detect if PDF is scanned — samples across beginning, middle, and end.

    Only checking the first few pages misses PDFs where the front matter
    (title, TOC) is machine-readable but the body is scanned images.
    """
    import fitz

    with fitz.open(doc_path) as doc:
        total_pages = len(doc)

        # Choose sample indices spread across the entire document
        if total_pages <= sample_pages:
            indices = list(range(total_pages))
        else:
            step = max(1, (total_pages - 1) // (sample_pages - 1))
            indices = [i * step for i in range(sample_pages)]
            # Always include the last page
            if indices[-1] != total_pages - 1:
                indices[-1] = total_pages - 1

        scanned_count = 0
        for i in indices:
            page = doc[i]
            text = page.get_text().strip()
            if len(text) < 100:
                scanned_count += 1

    return scanned_count >= (len(indices) // 2 + 1)


def _extract_pdf_normal(doc_path: str) -> str:
    """Extract normal PDF text"""
    import fitz

    MAX_EXTRACT_BYTES = 50 * 1024 * 1024  # 50 MB cap

    with fitz.open(doc_path) as doc:
        pages_text = []
        total_bytes = 0

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
            # Two-column detection: require a clear gap between left and right block clusters
            # as well as sufficient blocks on each side. This avoids false positives from
            # pages with figures, tables, or margin annotations.
            if len(left_blocks) >= 3 and len(right_blocks) >= 3:
                # Check there's a real gap between the rightmost left-column block edge
                # and the leftmost right-column block edge
                left_max_x = max(b[2] for b in left_blocks)  # right edge of left blocks
                right_min_x = min(b[0] for b in right_blocks)  # left edge of right blocks
                gap = right_min_x - left_max_x
                has_two_columns = gap > (page_rect.width * 0.05)  # gap >= 5% of page width
            else:
                has_two_columns = False

            sorted_blocks = []
            if has_two_columns:
                left_blocks.sort(key=lambda b: b[1])
                right_blocks.sort(key=lambda b: b[1])
                center_blocks.sort(key=lambda b: b[1])
                # Two-column order: full left column top-to-bottom, then full right
                # column top-to-bottom. Center blocks (titles, section headings that
                # span both columns) are interleaved by y-position within each half.
                # Re-sorting the merged list by y would incorrectly interleave left
                # and right paragraph text.
                sorted_blocks = center_blocks + left_blocks + right_blocks
            else:
                sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))

            page_text = f"\n--- Page {page_num} ---\n"
            for block in sorted_blocks:
                text = block[4].strip()
                if text:
                    page_text += text + "\n"

            total_bytes += len(page_text.encode('utf-8'))
            pages_text.append(page_text)
            if total_bytes > MAX_EXTRACT_BYTES:
                pages_text.append(
                    f"\n--- [TRUNCATED: document exceeded {MAX_EXTRACT_BYTES // 1_000_000}MB extract limit "
                    f"at page {page_num}/{len(doc)}. Remaining pages omitted.] ---\n"
                )
                break

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

            # First probe with English only (fast)
            try:
                eng_probe = pytesseract.image_to_string(img, lang='eng')
            except Exception:
                eng_probe = ""

            # Only try Chinese detection if English probe got very little text
            if len(eng_probe.strip()) < 50:
                try:
                    probe = pytesseract.image_to_string(img, lang='chi_sim+eng')
                    cn_chars = sum(1 for c in probe if '\u4e00' <= c <= '\u9fff')
                    if cn_chars > 20:
                        lang = 'chi_sim+eng'
                        print(f"  Detected Chinese text, using chi_sim+eng for OCR")
                except (pytesseract.TesseractError, UnicodeDecodeError) as e:
                    if "chi_sim" in str(e) or "Failed loading language" in str(e) or isinstance(e, UnicodeDecodeError):
                        print(f"  \u26a0\ufe0f  Chinese language pack not installed. Using English OCR.")
                        print(f"     To install: brew install tesseract-lang  (macOS)")
                        print(f"                 apt install tesseract-ocr-chi-sim  (Linux)")
                    # Fall back to eng -- lang is already 'eng'
            else:
                # English probe got enough text, no need to detect Chinese
                pass

        pages_text = []
        failed_pages = 0
        total_bytes = 0
        MAX_EXTRACT_BYTES = 50 * 1024 * 1024  # 50 MB cap

        print(f"Detected scanned PDF, {len(doc)} pages, starting OCR...")

        for page_num, page in enumerate(doc, 1):
            print(f"  OCR page {page_num}/{len(doc)}...")
            try:
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat)
                # Convert to RGB if the pixmap uses a non-RGB colorspace
                if pix.n >= 5:  # CMYK or other non-RGB/Gray
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                text = pytesseract.image_to_string(img, lang=lang)
                img.close()   # release PIL memory immediately
                del img
                pix = None    # release fitz pixmap
                text = _clean_ocr_text(text)

                page_text = f"\n--- Page {page_num} ---\n{text}\n"
                total_bytes += len(page_text.encode('utf-8'))
                pages_text.append(page_text)
                if total_bytes > MAX_EXTRACT_BYTES:
                    pages_text.append(
                        f"\n--- [TRUNCATED: document exceeded {MAX_EXTRACT_BYTES // 1_000_000}MB extract limit "
                        f"at page {page_num}/{len(doc)}. Remaining pages omitted.] ---\n"
                    )
                    break
            except Exception as e:
                failed_pages += 1
                pages_text.append(f"\n--- Page {page_num} ---\n[OCR failed: {e}]\n")
                print(f"    ⚠️ OCR page {page_num} failed: {e}")

        if failed_pages:
            print(f"  ⚠️ {failed_pages}/{len(doc)} pages failed OCR")

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
    failed_chapters = 0

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
            html_content = item.get_content().decode('utf-8', errors='replace')
        except Exception as e:
            failed_chapters += 1
            chapters_text.append(
                f"\n=== Chapter {chapter_num} [EXTRACTION FAILED: {e}] ===\n"
            )
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

    if failed_chapters > 0:
        chapters_text.append(
            f"\n\n[NOTE: {failed_chapters} chapter(s) failed to extract and are omitted above.]\n"
        )

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
