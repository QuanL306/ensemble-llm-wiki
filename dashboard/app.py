#!/usr/bin/env python3
"""
Knowledge Base Dashboard — unified frontend
Integrates: knowledge-base-suite-en + Graphify knowledge graph + Skill Seekers

Start:
  python3 dashboard/app.py                           # default port 8765, localhost only
  python3 dashboard/app.py 9000                      # custom port
  python3 dashboard/app.py --kb-root /path/to/kbs   # custom KB root
  python3 dashboard/app.py --host 0.0.0.0            # expose to network (requires DASHBOARD_TOKEN)

Security note: this is a read-only local dashboard.  By default it binds to
127.0.0.1 only.  Do NOT expose it on 0.0.0.0 without a reverse-proxy that
handles authentication.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import uvicorn

# Project root = dashboard parent = knowledge-base-suite-en/
BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Knowledge Base Dashboard", version="2.0.0")

# ============================================================
# Configuration
# ============================================================

# Hardcoded Graphify projects (optional — add your own here).
# Auto-discovery via .kbaconfig covers most cases; this dict is for graphs
# that live outside a standard KB directory (e.g. standalone Graphify runs).
# Example:
#   "my_project": {
#       "name": "My Project",
#       "path": "/path/to/graphify-out",
#       "description": "What this graph covers",
#   },
GRAPHIFY_PROJECTS = {}


def discover_graphify_outputs() -> dict:
    """Auto-discover graphify-out/ directories under knowledge bases."""
    discovered = {}
    # Scan KBs for graphify-out/
    kbs = discover_knowledge_bases()
    for name, path in kbs.items():
        gfo = Path(path) / "wiki" / "graphify-out"
        if gfo.exists() and (gfo / "graph.json").exists():
            discovered[name] = {
                "name": name,
                "path": str(gfo),
                "description": f"Knowledge base: {name}",
            }
    return discovered


def get_all_graphify_projects() -> dict:
    """Merge hardcoded projects with auto-discovered ones."""
    all_projects = dict(GRAPHIFY_PROJECTS)  # hardcoded ones have priority
    discovered = discover_graphify_outputs()
    for key, info in discovered.items():
        if key not in all_projects:
            all_projects[key] = info
    return all_projects

# KB root — overridable via --kb-root CLI arg or KB_ROOT env var
KB_ROOT = os.environ.get(
    "KB_ROOT",
    str(Path.home() / "Documents/Notes/knowledge_base"),
)


def discover_knowledge_bases() -> dict:
    """Discover all knowledge-base-suite-en KBs (via .kbaconfig)"""
    result = {}
    root = Path(KB_ROOT)
    if not root.is_dir():
        return result

    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / ".kbaconfig").exists():
            result[child.name] = str(child)
    return result


# ============================================================
# Graph data cache
# ============================================================
_graph_cache = {}


def _load_graph_with_cache(project: str) -> Optional[dict]:
    proj = get_all_graphify_projects().get(project)
    if not proj:
        return None
    graph_path = Path(proj["path"]) / "graph.json"
    if not graph_path.exists():
        _graph_cache.pop(project, None)
        return None
    try:
        mtime = graph_path.stat().st_mtime
    except OSError:
        _graph_cache.pop(project, None)
        return None
    cached = _graph_cache.get(project)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(graph_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        _graph_cache.pop(project, None)
        return None
    _graph_cache[project] = (mtime, data)
    return data


def load_graph_data(project: str) -> Optional[dict]:
    return _load_graph_with_cache(project)


# ============================================================
# Graph analysis
# ============================================================

def compute_god_nodes(data: dict, top_n: int = 20) -> list:
    adj = {}
    for link in data.get("links", []):
        s, t = link.get("source"), link.get("target")
        if not s or not t:
            continue
        adj[s] = adj.get(s, 0) + 1
        adj[t] = adj.get(t, 0) + 1
    ranked = sorted(adj.items(), key=lambda x: x[1], reverse=True)
    node_map = {n["id"]: n for n in data.get("nodes", []) if "id" in n}
    return [
        {
            "id": nid,
            "label": node_map[nid]["label"] if nid in node_map else nid,
            "file_type": node_map[nid].get("file_type", "") if nid in node_map else "",
            "community": node_map[nid].get("community", -1) if nid in node_map else -1,
            "degree": deg,
        }
        for nid, deg in ranked[:top_n]
    ]


def compute_community_summary(data: dict) -> list:
    communities = {}
    for node in data.get("nodes", []):
        cid = node.get("community", -1)
        if cid not in communities:
            communities[cid] = {"id": cid, "node_count": 0, "top_labels": []}
        communities[cid]["node_count"] += 1
        label = node.get("label", "")
        if label:
            communities[cid]["top_labels"].append(label)
    result = []
    for cid, info in communities.items():
        if info["node_count"] > 2:
            result.append({
                "id": cid,
                "node_count": info["node_count"],
                "sample_labels": info["top_labels"][:3],
            })
    return sorted(result, key=lambda x: x["node_count"], reverse=True)


# ============================================================
# KB stats (knowledge-base-suite-en format: file_index.json)
# ============================================================

def load_kb_stats() -> dict:
    """Read knowledge-base-suite-en KB stats via file_index.json"""
    kbs = discover_knowledge_bases()
    if not kbs:
        return {"available": False, "kbs": {}, "total_docs": 0,
                "error": "No knowledge bases found (.kbaconfig missing)"}

    result = {"available": True, "kbs": {}, "total_docs": 0}
    for name, path in kbs.items():
        index_file = Path(path) / "wiki" / "_meta" / "file_index.json"
        if not index_file.exists():
            result["kbs"][name] = {"path": path, "docs": 0, "status": "no index"}
            continue
        try:
            with open(index_file) as f:
                idx = json.load(f)
        except (json.JSONDecodeError, OSError):
            result["kbs"][name] = {"path": path, "docs": 0, "status": "corrupt"}
            continue

        files = idx.get("files", {})
        completed = sum(1 for f in files.values() if f.get("status") == "completed")
        result["kbs"][name] = {
            "path": path,
            "docs": completed,
            "total": len(files),
            "status": "ready",
        }
        result["total_docs"] += completed
    return result


# ============================================================
# API routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    template = TEMPLATES_DIR / "index.html"
    if not template.exists():
        return HTMLResponse(
            "<h2>Template not found</h2><p>index.html is missing from templates/</p>",
            status_code=500,
        )
    with open(template) as f:
        return f.read()


@app.get("/api/stats")
async def api_stats():
    """KB + graph + Skill Seekers stats"""
    kb = load_kb_stats()
    projects = {}
    for key, proj in get_all_graphify_projects().items():
        data = load_graph_data(key)
        if data:
            projects[key] = {
                "name": proj["name"],
                "description": proj["description"],
                "nodes": len(data.get("nodes", [])),
                "edges": len(data.get("links", [])),
                "communities": len(set(
                    n.get("community", -1) for n in data.get("nodes", [])
                )),
                "has_graph_html": (Path(proj["path"]) / "graph.html").exists(),
                "has_jsonld": (Path(proj["path"]) / "graph.jsonld").exists(),
                "god_nodes": compute_god_nodes(data, 5),
            }
    return {
        "knowledge_base": kb,
        "graphify_projects": projects,
    }


@app.get("/api/graph/{project}")
async def api_graph_data(project: str, top_nodes: int = Query(20, ge=5, le=100)):
    data = load_graph_data(project)
    if not data:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return {
        "project": project,
        "nodes": len(data.get("nodes", [])),
        "edges": len(data.get("links", [])),
        "god_nodes": compute_god_nodes(data, top_nodes),
        "communities": compute_community_summary(data),
    }


@app.get("/api/graph/{project}/html")
async def api_graph_html(project: str):
    proj = get_all_graphify_projects().get(project)
    if not proj:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    path = Path(proj["path"]) / "graph.html"
    if not path.exists():
        return JSONResponse({"error": "graph.html not found"}, status_code=404)
    if not str(path.resolve()).startswith(str(Path(proj["path"]).resolve())):
        return JSONResponse({"error": "Access denied"}, status_code=403)
    return FileResponse(path, media_type="text/html")


@app.get("/api/graph/{project}/god-nodes")
async def api_god_nodes(project: str, top_n: int = Query(15, ge=5, le=50)):
    data = load_graph_data(project)
    if not data:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return {"god_nodes": compute_god_nodes(data, top_n)}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================================
# Startup
# ============================================================

_LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments.  Returns a Namespace with .port, .host, .kb_root."""
    parser = argparse.ArgumentParser(
        description="Knowledge Base Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "port", nargs="?", type=int, default=8765,
        help="Port to listen on (default: 8765)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1 — localhost only)",
    )
    parser.add_argument(
        "--kb-root", dest="kb_root", default=None,
        help="Root directory to scan for knowledge bases (.kbaconfig)",
    )
    args = parser.parse_args(argv)
    if not (1024 <= args.port <= 65535):
        parser.error(f"Port {args.port} is out of range (1024–65535)")
    return args


def _apply_args(args: argparse.Namespace) -> None:
    """Apply parsed args to module-level config (KB_ROOT)."""
    global KB_ROOT
    if args.kb_root:
        KB_ROOT = str(Path(args.kb_root).resolve())


if __name__ == "__main__":
    _args = parse_args()
    _apply_args(_args)

    if _args.host not in _LOCALHOST_ADDRS:
        print(
            f"⚠️  Dashboard is binding to {_args.host} — this exposes your knowledge base"
            " to the network. Place a reverse-proxy with authentication in front.",
            file=sys.stderr,
        )

    uvicorn.run(app, host=_args.host, port=_args.port, log_level="info")
