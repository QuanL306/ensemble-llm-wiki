#!/usr/bin/env python3
"""
zotero_add.py — Batch-import book metadata to Zotero for ensemble-llm-wiki.

Extracts ISBN/DOI from PDFs, queries OpenLibrary/CrossRef, creates Zotero entries.
Designed for 872-book scale. No PDF attachments (Zotero cloud limit).

Usage:
    export ZOTERO_API_KEY=your_api_key
    export ZOTERO_USER_ID=your_user_id
    export ZOTERO_COLLECTION_KEY=your_collection_key   # optional
    python3 zotero_add.py --kb /path/to/kb [--batch 50] [--dry-run]

Obtain credentials at https://www.zotero.org/settings/keys
"""

import os
import sys
import json
import time
import re
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import requests
from urllib.parse import quote

# --- Zotero Configuration ---
# Set these three environment variables before running.
# Find them at https://www.zotero.org/settings/keys (API key + user ID)
# and in your Zotero library URL (collection key, e.g. /collections/XXXXXXXX).
ZOTERO_API_KEY  = os.environ.get("ZOTERO_API_KEY",  "")
ZOTERO_USER_ID  = os.environ.get("ZOTERO_USER_ID",  "")
COLLECTION_KEY  = os.environ.get("ZOTERO_COLLECTION_KEY", "")

if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
    print("Error: set ZOTERO_API_KEY and ZOTERO_USER_ID before running.", file=sys.stderr)
    sys.exit(1)

ZOTERO_BASE = f"https://api.zotero.org/users/{ZOTERO_USER_ID}"
HEADERS = {"Zotero-API-Key": ZOTERO_API_KEY, "Content-Type": "application/json"}
REQUEST_DELAY = 1.0  # seconds between API calls to avoid rate limiting

# CJK Unicode ranges: Chinese + Japanese + Korean
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')


def is_cjk_filename(filename: str) -> bool:
    """Return True if the filename contains Chinese/Japanese/Korean characters."""
    return bool(_CJK_RE.search(filename))


def extract_isbn_from_text(text: str) -> Optional[str]:
    """Extract ISBN-13 or ISBN-10 from text, preferring ISBN-13.
    Validates length to reject false positives (years like 1984, 1776, etc.).
    """
    # ISBN-13 with or without hyphens
    isbn13 = re.search(r'97[89][-\s]?[0-9]{1,5}[-\s]?[0-9]{1,7}[-\s]?[0-9]{1,6}[-\s]?[0-9X]', text)
    if isbn13:
        raw = isbn13.group(0).replace('-', '').replace(' ', '')
        if len(raw) == 13:  # must be exactly 13 chars
            return raw
    # ISBN-10
    isbn10 = re.search(r'[0-9]{1,5}[-\s]?[0-9]{1,7}[-\s]?[0-9]{1,6}[-\s]?[0-9X]', text)
    if isbn10:
        raw = isbn10.group(0).replace('-', '').replace(' ', '')
        if len(raw) == 10:  # must be exactly 10 chars
            return raw
    return None


def extract_isbn_from_pdf(filepath: str, max_chars: int = 30000) -> Optional[str]:
    """Search first max_chars of a PDF for ISBN."""
    try:
        import PyPDF2
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for i, page in enumerate(reader.pages):
                if len(text) > max_chars:
                    break
                text += page.extract_text() or ""
            return extract_isbn_from_text(text[:max_chars])
    except ImportError:
        pass
    
    try:
        import pdfminer.high_level
        text = pdfminer.high_level.extract_text(filepath, maxpages=5)
        return extract_isbn_from_text(text[:max_chars])
    except ImportError:
        pass
    
    try:
        import pypdf
        with open(filepath, 'rb') as f:
            reader = pypdf.PdfReader(f)
            text = ""
            for page in reader.pages[:20]:
                if len(text) > max_chars:
                    break
                text += page.extract_text() or ""
            return extract_isbn_from_text(text[:max_chars])
    except ImportError:
        print("  ⚠ No PDF reader available (install pypdf or pdfminer.six)", file=sys.stderr)
        return None


def lookup_openlibrary(isbn: str) -> Optional[Dict[str, Any]]:
    """Query OpenLibrary by ISBN."""
    try:
        url = f"https://openlibrary.org/isbn/{isbn}.json"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # Try full details
            if 'works' in data and len(data['works']) > 0:
                work_url = f"https://openlibrary.org{data['works'][0]['key']}.json"
                work_resp = requests.get(work_url, timeout=15)
                if work_resp.status_code == 200:
                    work = work_resp.json()
                    return {
                        'title': work.get('title', ''),
                        'author': ', '.join(a.get('name', '') for a in work.get('authors', [])),
                        'publisher': (data.get('publishers') or [''])[0],
                        'date': (data.get('publish_date') or ''),
                        'isbn': isbn,
                    }
            # Direct book data
            return {
                'title': data.get('title', ''),
                'author': ', '.join(a.get('name', '') for a in data.get('authors', [])),
                'publisher': (data.get('publishers') or [''])[0],
                'date': (data.get('publish_date') or ''),
                'isbn': isbn,
            }
        return None
    except Exception:
        return None


def lookup_crossref(title_hint: str = '', author_hint: str = '') -> Optional[Dict[str, Any]]:
    """Query CrossRef by title."""
    try:
        if not title_hint:
            return None
        query = title_hint.strip()[:200]
        url = f"https://api.crossref.org/works?query={quote(query)}&rows=3"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get('message', {}).get('items', [])
            if items:
                item = items[0]
                title = (item.get('title') or [''])[0]
                authors = item.get('author', [])
                author = ', '.join(
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in authors
                )
                publisher = item.get('publisher', '')
                date_parts = item.get('published-print', {}).get('date-parts', 
                              item.get('published-online', {}).get('date-parts', []))
                year = str(date_parts[0][0]) if date_parts and date_parts[0] else ''
                return {
                    'title': title,
                    'author': author,
                    'publisher': publisher,
                    'date': year,
                    'isbn': '',
                }
        return None
    except Exception:
        return None


def parse_filename_for_metadata(filename: str) -> Dict[str, str]:
    """Try to extract author/title/year from filename patterns."""
    # Remove extension
    name = re.sub(r'\.(pdf|epub)$', '', filename, flags=re.IGNORECASE)
    
    # Pattern: Author - Title (Year).ext  or  Author - Title.ext
    m = re.match(r'^(.+?)\s*[-–—]\s*(.+?)(?:\s*\((\d{4})\))?$', name)
    if m:
        author = m.group(1).strip()
        title = m.group(2).strip()
        year = m.group(3) or ''
        return {'title': title, 'author': author, 'year': year}
    
    # Pattern: (Series) Author - Title.ext
    m = re.match(r'^\(.+?\)\s*(.+?)\s*[-–—]\s*(.+?)(?:\s*\((\d{4})\))?$', name)
    if m:
        author = m.group(1).strip()
        title = m.group(2).strip()
        year = m.group(3) or ''
        return {'title': title, 'author': author, 'year': year}
    
    # Fallback: use the filename as title
    return {'title': name, 'author': '', 'year': ''}


def create_zotero_item(metadata: Dict[str, Any], filename: str) -> Optional[str]:
    """Create a book entry in Zotero and add to collection. Returns item key."""
    try:
        # Build the Zotero item
        item = {
            "itemType": "book",
            "title": metadata.get('title', filename),
            "creators": [],
            "publisher": metadata.get('publisher', ''),
            "date": metadata.get('date', metadata.get('year', '')),
            "ISBN": metadata.get('isbn', ''),
        }
        
        # Parse author
        author_str = metadata.get('author', '')
        if author_str:
            # Split multiple authors by semicolons or commas
            for a in re.split(r'[;,]', author_str):
                a = a.strip()
                if not a:
                    continue
                parts = a.rsplit(' ', 1)
                if len(parts) == 2:
                    item["creators"].append({
                        "creatorType": "author",
                        "lastName": parts[1],
                        "firstName": parts[0],
                    })
                else:
                    item["creators"].append({
                        "creatorType": "author",
                        "lastName": a,
                        "firstName": "",
                    })
        
        # Post to Zotero API
        resp = requests.post(
            f"{ZOTERO_BASE}/items",
            headers=HEADERS,
            json=[item],
            timeout=30,
        )
        
        if resp.status_code in (200, 201):
            result = resp.json()
            if result.get('successful') and result['successful']:
                key = list(result['successful'].values())[0].get('key', '')
                if key:
                    # Add to collection
                    requests.post(
                        f"{ZOTERO_BASE}/collections/{COLLECTION_KEY}/items/{key}",
                        headers=HEADERS,
                        timeout=15,
                    )
                    return key
        
        # If item already exists, try to find its key
        if resp.status_code == 400:
            # Might be a duplicate - try search
            title = metadata.get('title', '')
            if title:
                search_resp = requests.get(
                    f"{ZOTERO_BASE}/items?q={quote(title[:50])}&limit=5",
                    headers=HEADERS,
                    timeout=15,
                )
                if search_resp.status_code == 200:
                    items = search_resp.json()
                    for existing in items:
                        if existing.get('data', {}).get('title', '').lower() == title.lower():
                            key = existing.get('data', {}).get('key', '')
                            if key:
                                # Try to add to collection
                                requests.post(
                                    f"{ZOTERO_BASE}/collections/{COLLECTION_KEY}/items/{key}",
                                    headers=HEADERS,
                                    timeout=15,
                                )
                                return key
        
        return None
    except Exception as e:
        print(f"  ⚠ create_zotero_item error: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Batch-import book metadata to Zotero")
    parser.add_argument('--kb', required=True, help='Knowledge base path')
    parser.add_argument('--batch', type=int, default=50, help='Books per batch')
    parser.add_argument('--dry-run', action='store_true', help="Don't actually create items")
    parser.add_argument('--resume', type=int, default=0, help='Resume from index N')
    parser.add_argument('--retry-failed', action='store_true',
                        help='Remove previously failed books from progress and retry')
    
    args = parser.parse_args()
    
    kb_path = Path(args.kb)
    raw_dir = kb_path / 'raw' / 'books'
    
    if not raw_dir.exists():
        print(f'❌ raw/books not found in {kb_path}')
        sys.exit(1)
    
    # Get all PDF and EPUB files sorted by name
    books = sorted([
        f.name for f in raw_dir.iterdir()
        if f.is_file() and f.suffix.lower() in ('.pdf', '.epub')
    ])
    
    print(f'📚 {len(books)} books in {raw_dir}')
    
    progress_file = kb_path / 'outputs' / 'zotero_import.json'
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    
    if progress_file.exists():
        with open(progress_file) as f:
            imported = json.load(f)
    else:
        imported = {}
    
    errors_file = kb_path / 'outputs' / 'zotero_errors.md'
    errors = []
    
    # Handle --retry-failed
    if args.retry_failed and errors_file.exists():
        retry_count = 0
        with open(errors_file) as f:
            for line in f:
                m = re.match(r'^- (.+?): Zotero API creation failed$', line.strip())
                if m:
                    book = m.group(1)
                    if book in imported:
                        del imported[book]
                        retry_count += 1
        print(f'🔄 Retrying {retry_count} previously failed books')
        # Clear errors file
        open(errors_file, 'w').close()
    
    ok = 0
    skipped = 0
    failed = 0
    
    for i, book in enumerate(books):
        if args.resume and i < args.resume:
            continue
        
        if book in imported:
            skipped += 1
            continue
        
        filepath = raw_dir / book
        print(f'\n[{i+1}/{len(books)}] {book[:70]}...')
        
        # Extract ISBN from PDF
        isbn = None
        if book.lower().endswith('.pdf'):
            try:
                isbn = extract_isbn_from_pdf(str(filepath))
            except Exception:
                pass
        
        metadata = None
        
        # Try ISBN lookup first
        if isbn:
            print(f'  ISBN: {isbn}')
            metadata = lookup_openlibrary(isbn)
        
        # Fallback to CrossRef (skip for CJK filenames — CrossRef
        # fuzzy-matches Chinese/Japanese/Korean titles to unrelated
        # English books, producing garbage metadata)
        if not metadata:
            parsed = parse_filename_for_metadata(book)
            title_hint = parsed.get('title', '')[:200]
            if is_cjk_filename(book):
                print(f'  ↪ Skipping CrossRef (CJK filename): {title_hint[:60]}...')
            else:
                print(f'  CrossRef: {title_hint[:60]}...')
                metadata = lookup_crossref(title_hint, parsed.get('author', ''))
        
        # Final fallback: filename metadata only
        if not metadata:
            parsed = parse_filename_for_metadata(book)
            metadata = {
                'title': parsed.get('title', ''),
                'author': parsed.get('author', ''),
                'year': parsed.get('year', ''),
                'publisher': '',
                'isbn': isbn or '',
            }
            print(f'  Fallback: filename metadata only')
        
        # Dry run or create
        if args.dry_run:
            print(f'  [DRY RUN] would create: {metadata.get("title", "")[:60]}')
            imported[book] = {'title': metadata.get('title', ''), 'key': 'dry-run'}
        else:
            item_key = create_zotero_item(metadata, book)
            if item_key:
                print(f'  ✅ {item_key}')
                imported[book] = {
                    'title': metadata.get('title', ''),
                    'key': item_key,
                    'isbn': isbn,
                }
                ok += 1
            else:
                print(f'  ❌ Failed to create Zotero item')
                failed += 1
                errors.append(f'- {book}: Zotero API creation failed')
        
        # Save progress every 10 items
        if (ok + failed) % 10 == 0:
            with open(progress_file, 'w') as f:
                json.dump(imported, f, indent=2, ensure_ascii=False)
            if errors:
                with open(errors_file, 'w') as f:
                    f.write('\n'.join(errors) + '\n')
        
        # Batch limit check
        if args.batch and (ok + failed) >= args.batch:
            print(f'\n⏸ Batch limit ({args.batch}) reached')
            break
        
        # Rate limiting delay
        time.sleep(REQUEST_DELAY)
    
    # Final save
    with open(progress_file, 'w') as f:
        json.dump(imported, f, indent=2, ensure_ascii=False)
    if errors:
        with open(errors_file, 'w') as f:
            f.write('\n'.join(errors) + '\n')
    
    # Summary
    print(f'\n── Summary ──')
    print(f'  ✅ Created: {ok}')
    print(f'  ⏭ Skipped: {skipped}')
    print(f'  ❌ Failed: {failed}')
    print(f'  💾 Progress: {progress_file}')


if __name__ == '__main__':
    main()
