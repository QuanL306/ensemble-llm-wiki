"""
Transcript Harvester — imports AI session transcripts into the KB.

Adapters:
  ClaudeCodeAdapter  — ~/.claude/projects/**/*.jsonl
  CursorAdapter      — ~/Library/Application Support/Cursor/... (macOS)

Each adapter produces a list of Session objects. Sessions are converted to
Markdown and written to raw/transcripts/<slug>.md for the normal
ingest → compile pipeline to pick up.
"""

import json
import re
import sqlite3
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Sensitive-data scrubbing
# ---------------------------------------------------------------------------

# Each entry is (compiled_pattern, replacement_string).
# Replacement strings may reference capture groups with \1, \2, etc.
_SCRUB_PATTERNS = [
    # password / secret / token = <value>
    (re.compile(
        r'(?i)((?:password|passwd|pwd|secret|token|api[-_]?key|auth[-_]?token'
        r'|access[-_]?key|private[-_]?key|client[-_]?secret)\s*[:=]\s*)\S+',
    ), r'\1[REDACTED]'),
    # OpenAI / Anthropic sk-... keys
    (re.compile(r'\bsk-[A-Za-z0-9\-_]{20,}'), r'sk-[REDACTED]'),
    # AWS IAM access keys
    (re.compile(r'\bAKIA[A-Z0-9]{16}\b'), r'AKIA[REDACTED]'),
    # GitHub personal access tokens
    (re.compile(r'\bghp_[A-Za-z0-9]{36}\b'), r'ghp_[REDACTED]'),
    # Bearer tokens in Authorization headers
    (re.compile(r'(?i)(Bearer\s+)[A-Za-z0-9\-._~+/]{20,}'), r'\1[REDACTED]'),
]


def _scrub_sensitive(text: str) -> str:
    """Replace common credential patterns with [REDACTED] placeholders."""
    for pattern, repl in _SCRUB_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Exchange:
    user: str
    assistant: str


@dataclass
class Session:
    slug: str
    source: str           # "claude-code" or "cursor"
    exchanges: List[Exchange] = field(default_factory=list)
    mtime: Optional[float] = None  # file mtime for since_days filtering


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_slug(text: str) -> str:
    """Turn an arbitrary string into a filesystem-safe slug."""
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text.strip())
    return text[:80].lower().strip('-')


def _truncate(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + " [truncated]"


def _extract_assistant_text(content) -> str:
    """Extract plain text from an assistant content block (list or string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class ClaudeCodeAdapter:
    """Reads JSONL session transcripts from ~/.claude/projects/"""

    BASE = Path.home() / ".claude" / "projects"

    def sessions(self, since_days: Optional[int] = None) -> List[Session]:
        if not self.BASE.is_dir():
            return []

        cutoff = None
        if since_days is not None:
            cutoff = datetime.now().timestamp() - since_days * 86400

        results: List[Session] = []
        for jsonl_file in self.BASE.rglob("*.jsonl"):
            try:
                mtime = jsonl_file.stat().st_mtime
            except OSError:
                continue

            if cutoff is not None and mtime < cutoff:
                continue

            exchanges = self._parse_jsonl(jsonl_file)
            if len(exchanges) < 3:
                continue

            slug = _sanitize_slug(f"{jsonl_file.parent.name}-{jsonl_file.stem}")
            results.append(Session(
                slug=slug,
                source="claude-code",
                exchanges=exchanges,
                mtime=mtime,
            ))

        return results

    def _parse_jsonl(self, path: Path) -> List[Exchange]:
        lines = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        lines.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []

        # Pair consecutive user/assistant messages into exchanges
        exchanges: List[Exchange] = []
        pending_user: Optional[str] = None

        for obj in lines:
            msg_type = obj.get("type", "")
            content = obj.get("content", "")

            if msg_type == "user":
                text = content if isinstance(content, str) else ""
                if not text and isinstance(content, list):
                    # Sometimes user content is also a list of blocks
                    text = _extract_assistant_text(content)
                if len(text) < 10:
                    continue
                pending_user = text

            elif msg_type == "assistant":
                text = _extract_assistant_text(content)
                if len(text) < 10 or pending_user is None:
                    continue
                exchanges.append(Exchange(user=pending_user, assistant=text))
                pending_user = None

        return exchanges


class CursorAdapter:
    """
    Best-effort adapter for Cursor's SQLite conversation store (macOS).
    Returns [] gracefully if anything goes wrong.
    """

    DB_PATH = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "globalStorage"
        / "state.vscdb"
    )

    def sessions(self, since_days: Optional[int] = None) -> List[Session]:
        try:
            return self._load(since_days)
        except Exception as exc:
            warnings.warn(f"CursorAdapter: skipped — {exc}")
            return []

    def _load(self, since_days: Optional[int]) -> List[Session]:
        if not self.DB_PATH.exists():
            return []

        cutoff = None
        if since_days is not None:
            cutoff = datetime.now().timestamp() - since_days * 86400

        conn = sqlite3.connect(str(self.DB_PATH))
        try:
            cur = conn.cursor()
            # Try known key patterns
            cur.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE 'aiService%'"
            )
            rows = cur.fetchall()
        except sqlite3.DatabaseError:
            return []
        finally:
            conn.close()

        results: List[Session] = []
        for key, value in rows:
            try:
                data = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(data, list):
                continue

            slug = _sanitize_slug(key)
            exchanges = self._extract_exchanges(data)
            if len(exchanges) < 3:
                continue

            results.append(Session(slug=slug, source="cursor", exchanges=exchanges))

        return results

    def _extract_exchanges(self, data: list) -> List[Exchange]:
        exchanges: List[Exchange] = []
        pending_user: Optional[str] = None

        for item in data:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "")
            content = item.get("content", "") or item.get("text", "")
            if isinstance(content, list):
                content = _extract_assistant_text(content)
            if not isinstance(content, str) or len(content) < 10:
                continue

            if role in ("user", "human"):
                pending_user = content
            elif role in ("assistant", "ai") and pending_user:
                exchanges.append(Exchange(user=pending_user, assistant=content))
                pending_user = None

        return exchanges


# ---------------------------------------------------------------------------
# Session → Markdown
# ---------------------------------------------------------------------------

def session_to_markdown(session: Session) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        "type: transcript",
        f"source: {session.source}",
        f"session_id: {session.slug}",
        f"harvested: {today}",
        "---",
        "",
        f"# Session: {session.slug}",
        "",
    ]

    for ex in session.exchanges:
        lines += [
            "## User",
            "",
            _scrub_sensitive(ex.user.strip()),
            "",
            "## Assistant",
            "",
            _truncate(_scrub_sensitive(ex.assistant.strip())),
            "",
            "---",
            "",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main harvester
# ---------------------------------------------------------------------------

class TranscriptHarvester:
    def __init__(self, kb_path: str):
        self.kb_path = Path(kb_path)
        self.transcripts_dir = self.kb_path / "raw" / "transcripts"
        self.manifest_file = self.transcripts_dir / ".harvest_manifest.json"

    # ── manifest helpers ────────────────────────────────────────────────────

    def _load_manifest(self) -> dict:
        if self.manifest_file.exists():
            try:
                return json.loads(self.manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_manifest(self, manifest: dict) -> None:
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── public API ──────────────────────────────────────────────────────────

    def harvest(self, sources=("claude-code",), since_days=None) -> dict:
        """
        Run all requested adapters and write new sessions to raw/transcripts/.
        Returns {"new": int, "skipped": int, "errors": int}
        """
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        manifest = self._load_manifest()

        adapter_map = {
            "claude-code": ClaudeCodeAdapter(),
            "cursor": CursorAdapter(),
        }

        new_count = skipped = errors = 0

        for source in sources:
            adapter = adapter_map.get(source)
            if adapter is None:
                warnings.warn(f"Unknown source '{source}' — skipped")
                continue

            try:
                sessions = adapter.sessions(since_days=since_days)
            except Exception as exc:
                warnings.warn(f"Adapter '{source}' failed: {exc}")
                errors += 1
                continue

            for session in sessions:
                if session.slug in manifest:
                    skipped += 1
                    continue

                try:
                    md = session_to_markdown(session)
                    out_path = self.transcripts_dir / f"{session.slug}.md"
                    out_path.write_text(md, encoding="utf-8")

                    manifest[session.slug] = {
                        "harvested_at": datetime.now().isoformat(),
                        "source": session.source,
                        "message_count": len(session.exchanges),
                    }
                    new_count += 1
                except Exception as exc:
                    warnings.warn(f"Failed to write session {session.slug}: {exc}")
                    errors += 1

        self._save_manifest(manifest)
        return {"new": new_count, "skipped": skipped, "errors": errors}

    def list_harvested(self) -> list:
        """Return list of harvested sessions from manifest."""
        manifest = self._load_manifest()
        return [
            {"slug": slug, **info}
            for slug, info in manifest.items()
        ]
