#!/usr/bin/env python3
"""
lifecycle.py — 5-state lifecycle machine for knowledge base documents

Borrowed from: Pratiyush/llm-wiki (5-state lifecycle pattern)

States:  draft → reviewed → verified → stale → archived
         ↑        ↑          ↑         ↑        ↑
       default  human ok   second eye 90d no    removed
                                        touch     from active

Transitions:
  draft     → reviewed   (human marks as reviewed)
  reviewed  → verified   (second reviewer confirms)
  reviewed  → draft      (reviewer finds issues)
  *         → stale      (auto: no update in 90 days)
  stale     → reviewed   (human updates content)
  stale     → archived   (auto: stale for another 90 days, or manual)
  archived  → draft      (manual restore)
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ── State definitions ──
VALID_STATES = {"draft", "reviewed", "verified", "stale", "archived"}

# Auto-stale threshold: 90 days without update
AUTO_STALE_DAYS = 90
# Auto-archive threshold: another 90 days in stale state
AUTO_ARCHIVE_DAYS = 180  # 90 stale + 90 more

# Allowed transitions for each state
ALLOWED_TRANSITIONS = {
    "draft":     {"reviewed"},
    "reviewed":  {"verified", "draft", "stale"},
    "verified":  {"reviewed", "stale"},
    "stale":     {"reviewed", "draft", "archived"},
    "archived":  {"draft"},
}


def get_lifecycle(doc_info: dict) -> dict:
    """Get the current lifecycle state of a document."""
    lc = doc_info.get("lifecycle") or {}
    return {
        "state": lc.get("state", "draft"),
        "entered_at": lc.get("entered_at", ""),
        "updated_at": lc.get("updated_at", ""),
        "updated_by": lc.get("updated_by", ""),
        "history": lc.get("history", []),
    }


def transition(
    doc_info: dict,
    new_state: str,
    updated_by: str = "system",
    reason: str = "",
) -> bool:
    """
    Transition a document to a new lifecycle state.
    Returns True if the transition is valid and was applied.
    """
    if new_state not in VALID_STATES:
        return False

    lc = doc_info.get("lifecycle") or {}
    current = lc.get("state", "draft")

    if new_state not in ALLOWED_TRANSITIONS.get(current, set()):
        return False

    now = datetime.now(timezone.utc).isoformat()

    # Record history
    history = lc.get("history", [])
    history.append({
        "from": current,
        "to": new_state,
        "at": now,
        "by": updated_by,
        "reason": reason,
    })

    # Keep only last 20 history entries
    if len(history) > 20:
        history = history[-20:]

    lc["state"] = new_state
    lc["entered_at"] = lc.get("entered_at") or now
    lc["updated_at"] = now
    lc["updated_by"] = updated_by
    lc["history"] = history

    doc_info["lifecycle"] = lc
    return True


def auto_advance_stale(doc_info: dict) -> Optional[str]:
    """
    Auto-advance lifecycle based on time elapsed since last update.
    - draft/reviewed/verified → stale if no update in 90 days
    - stale → archived if no update in 180 days (90+90)
    
    Returns the new state if changed, None otherwise.
    """
    lc = doc_info.get("lifecycle") or {}
    current = lc.get("state", "draft")
    last_update = lc.get("updated_at") or lc.get("entered_at") or ""

    if not last_update:
        return None

    try:
        if "T" in last_update:
            updated = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
        else:
            updated = datetime.strptime(last_update[:10], "%Y-%m-%d")
        updated = updated.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    now = datetime.now(timezone.utc)
    days_since = (now - updated).days

    if current in {"draft", "reviewed", "verified"} and days_since >= AUTO_STALE_DAYS:
        transition(doc_info, "stale", "system", f"Auto-stale after {days_since} days")
        return "stale"

    if current == "stale" and days_since >= AUTO_ARCHIVE_DAYS:
        transition(doc_info, "archived", "system", f"Auto-archive after {days_since} days")
        return "archived"

    return None


def apply_lifecycle_to_index(index_path: Path) -> Dict[str, str]:
    """
    Apply auto-stale/archive logic to all documents in file_index.json.
    Writes changes back to the file.
    
    Returns {file_id: new_state} for changed documents.
    """
    if not index_path.exists():
        return {}

    with open(index_path) as f:
        idx = json.load(f)

    changes = {}
    for file_id, info in idx.get("files", {}).items():
        if info.get("status") != "completed":
            continue
        new_state = auto_advance_stale(info)
        if new_state:
            changes[file_id] = new_state

    if changes:
        with open(index_path, "w") as f:
            json.dump(idx, f, indent=2, ensure_ascii=False)

    return changes


# ── Lifecycle statistics ──

def lifecycle_stats(index_path: Path) -> dict:
    """Get lifecycle distribution statistics for the knowledge base."""
    if not index_path.exists():
        return {"states": {}, "total": 0}

    with open(index_path) as f:
        idx = json.load(f)

    stats = {s: 0 for s in VALID_STATES}
    total = 0

    for info in idx.get("files", {}).values():
        if info.get("status") != "completed":
            continue
        lc = info.get("lifecycle") or {}
        state = lc.get("state", "draft")
        if state in stats:
            stats[state] += 1
        total += 1

    return {"states": stats, "total": total}
