#!/usr/bin/env python3
"""
ingest_and_graph.py — Headless Graphify Wrapper

For interactive workflows, use `graphify --mcp` directly.
This module wraps `python -m graphify` via subprocess for headless
cron / session_start.py auto-sync. Adds two post-processing steps
not provided by Graphify natively: JSON-LD export + dual edge separation.

Graphify: safishamsi/graphify (https://github.com/safishamsi/graphify)
         any input (code, docs, papers, images) → knowledge graph → clustered communities → HTML + JSON + audit report
Skill Seekers: yusufkaraaslan/Skill_Seekers (https://github.com/yusufkaraaslan/Skill_Seekers)
               fetch docs/repos/video/PDF from 17 source types

Usage:
  python3 ingest_and_graph.py --input ./skill-seekers-output/ --project my-project
  python3 ingest_and_graph.py --input ./docs-scrape/ --project api-docs --mode deep
  python3 ingest_and_graph.py --watch ./skill-seekers-output/ --project live-kb
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def find_graphify_python() -> str:
    """Find the Python interpreter with graphify installed"""
    for cmd in ["python3", "python"]:
        try:
            result = subprocess.run(
                [cmd, "-c", "import graphify; print('ok')"],
                capture_output=True, text=True, timeout=10
            )
            if "ok" in result.stdout:
                return cmd
        except Exception:
            continue
    print("❌ graphify not found. Install: pip install graphifyy", file=sys.stderr)
    sys.exit(1)


def run_graphify(input_dir: Path, mode: str = "standard", python: str = "python3") -> bool:
    """Run graphify full pipeline on input directory"""
    flags = ["--mode", "deep"] if mode == "deep" else []
    
    cmd = [python, "-m", "graphify", str(input_dir)] + flags
    print(f"🔍 Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"⚠️ graphify returned non-zero: {result.returncode}", file=sys.stderr)
            print(result.stderr[-500:], file=sys.stderr)
            return False
        print(result.stdout[-1000:])
        return True
    except subprocess.TimeoutExpired:
        print("❌ graphify timed out (>10min)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ graphify failed: {e}", file=sys.stderr)
        return False


def generate_jsonld(graph_file: Path, output_file: Path, project_name: str):
    """Generate Schema.org JSON-LD from graphify's graph.json"""
    if not graph_file.exists():
        print(f"⚠️ {graph_file} not found, skipping JSON-LD generation", file=sys.stderr)
        return
    
    with open(graph_file) as f:
        data = json.load(f)
    
    nodes = data.get("nodes", [])
    links = data.get("links", [])
    
    # Build node map (safe: skip nodes without id)
    node_map = {}
    for n in nodes:
        nid = n.get("id")
        if nid:
            node_map[nid] = n
    
    # Schema.org context
    jsonld = {
        "@context": {
            "schema": "http://schema.org/",
            "graphify": "https://graphify.dev/",
            "label": "schema:name",
            "file_type": "graphify:fileType",
            "source_file": "graphify:sourceFile",
            "community": "graphify:community",
            "degree": "graphify:degree",
            "relation": "graphify:relation",
            "confidence": "graphify:confidence",
        },
        "@type": "schema:Dataset",
        "schema:name": f"Knowledge Graph: {project_name}",
        "schema:description": f"Auto-generated from Graphify — {len(nodes)} nodes, {len(links)} edges",
        "schema:dateCreated": datetime.now(timezone.utc).isoformat(),
        "schema:about": [],
    }
    
    # Nodes as schema:Thing
    for nid, node in node_map.items():
        entity = {
            "@id": f"#node/{nid}",
            "@type": "schema:Thing",
            "schema:name": node.get("label", nid),
            "graphify:fileType": node.get("file_type", ""),
            "graphify:sourceFile": node.get("source_file", ""),
            "graphify:community": node.get("community", -1),
        }
        jsonld["schema:about"].append(entity)
    
    # Edges (safe: skip edges missing source or target)
    skipped_edges = 0
    for link in links:
        src = link.get("source")
        tgt = link.get("target")
        if not src or not tgt:
            skipped_edges += 1
            continue
        edge = {
            "@id": f"#edge/{src}-{tgt}",
            "@type": "graphify:Relationship",
            "schema:subject": {"@id": f"#node/{src}"},
            "schema:object": {"@id": f"#node/{tgt}"},
            "graphify:relation": link.get("relation", ""),
            "graphify:confidence": link.get("confidence", ""),
        }
        jsonld["schema:about"].append(edge)
    
    with open(output_file, "w") as f:
        json.dump(jsonld, f, indent=2, ensure_ascii=False)
    
    entities = len(jsonld["schema:about"])
    msg = f"✅ JSON-LD: {output_file} ({entities} entities)"
    if skipped_edges:
        msg += f" [{skipped_edges} edges skipped]"
    print(msg)


def split_edges(graph_file: Path, output_dir: Path):
    """
    Split Graphify edges into dual edge system (from OmegaWiki):
      - citations.jsonl — pure citation relationships (cites, references)
      - edges.jsonl    — semantic relationships (builds_on, complements, etc.)
    
    This prevents citation noise from diluting the semantic graph.
    """
    if not graph_file.exists():
        print(f"⚠️ {graph_file} not found, skipping edge split", file=sys.stderr)
        return

    with open(graph_file) as f:
        data = json.load(f)

    links = data.get("links", [])
    nodes = data.get("nodes", [])
    node_map = {n["id"]: n for n in nodes if "id" in n}

    citation_relations = {"cites", "references", "bibliographic_reference"}
    citations = []
    semantic = []

    for link in links:
        src = link.get("source")
        tgt = link.get("target")
        if not src or not tgt:
            continue

        relation = link.get("relation", "")
        edge = {
            "source": src,
            "source_label": node_map[src]["label"] if src in node_map else src,
            "target": tgt,
            "target_label": node_map[tgt]["label"] if tgt in node_map else tgt,
            "relation": relation,
            "confidence": link.get("confidence", ""),
            "confidence_score": link.get("confidence_score", 0),
            "source_file": link.get("source_file", ""),
        }

        if relation in citation_relations:
            citations.append(edge)
        else:
            semantic.append(edge)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Write citations.jsonl
    citations_file = output_dir / "citations.jsonl"
    with open(citations_file, "w") as f:
        for edge in citations:
            f.write(json.dumps(edge, ensure_ascii=False) + "\n")

    # Write edges.jsonl (semantic relationships)
    edges_file = output_dir / "edges.jsonl"
    with open(edges_file, "w") as f:
        for edge in semantic:
            f.write(json.dumps(edge, ensure_ascii=False) + "\n")

    print(
        f"✅ Dual edges: {len(citations)} citation + {len(semantic)} semantic "
        f"({len(citations) + len(semantic)} total)"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Skill Seekers → Graphify automated pipeline"
    )
    parser.add_argument("--input", "-i", required=True, help="Input directory (Skill Seekers output)")
    parser.add_argument("--project", "-p", required=True, help="Project name")
    parser.add_argument("--mode", choices=["standard", "deep"], default="standard")
    parser.add_argument("--watch", action="store_true", help="Watch mode (rebuild on file change)")
    parser.add_argument("--jsonld", action="store_true", default=False, help="Generate JSON-LD export")
    parser.add_argument("--no-jsonld", dest="jsonld", action="store_false", help="Skip JSON-LD export")
    
    args = parser.parse_args()
    input_dir = Path(args.input).resolve()
    
    if not input_dir.exists():
        print(f"❌ Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)
    
    print(f"╔═══════════════════════════════════╗")
    print(f"║  📦 Skill Seekers → Graphify     ║")
    print(f"║  Project: {args.project:<22}║")
    print(f"║  Input:   {str(input_dir)[-35:]:<35}║")
    print(f"╚═══════════════════════════════════╝")
    
    python = find_graphify_python()
    
    if args.watch:
        print(f"👀 Watching {input_dir} for changes...")
        print(f"   (excluding: graphify-out/, __pycache__/, .git/)")
        last_mtime = {}
        debounce_counter = 0
        
        # Exclusion list
        exclude_dirs = {"graphify-out", "__pycache__", ".git", "node_modules", ".venv"}
        
        try:
            while True:
                changed = False
                for f in input_dir.rglob("*"):
                    if f.is_file():
                        # Skip files in excluded directories
                        if any(p.name in exclude_dirs for p in f.parents):
                            continue
                        try:
                            mtime = f.stat().st_mtime
                        except OSError:
                            continue
                        fkey = str(f)
                        if fkey not in last_mtime or last_mtime[fkey] != mtime:
                            changed = True
                            last_mtime[fkey] = mtime
                
                if changed:
                    debounce_counter += 1
                    if debounce_counter >= 2:  # Require two consecutive change detections
                        print(f"\n📝 Changes detected at {datetime.now().strftime('%H:%M:%S')}")
                        run_graphify(input_dir, args.mode, python)
                        if args.jsonld:
                            generate_jsonld(
                                input_dir / "graphify-out" / "graph.json",
                                input_dir / "graphify-out" / "graph.jsonld",
                                args.project,
                            )
                            split_edges(
                                input_dir / "graphify-out" / "graph.json",
                                input_dir / "graphify-out",
                            )
                        debounce_counter = 0
                else:
                    debounce_counter = 0
                
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n👋 Watch mode stopped")
    else:
        success = run_graphify(input_dir, args.mode, python)
        
        if success and args.jsonld:
            graph_json = input_dir / "graphify-out" / "graph.json"
            jsonld_file = input_dir / "graphify-out" / "graph.jsonld"
            generate_jsonld(graph_json, jsonld_file, args.project)
            split_edges(graph_json, input_dir / "graphify-out")
        
        if success:
            print(f"\n✅ Pipeline complete!")
            print(f"   Graph: {input_dir}/graphify-out/graph.html")
            print(f"   JSON:  {input_dir}/graphify-out/graph.json")
            if args.jsonld:
                print(f"   LD:    {input_dir}/graphify-out/graph.jsonld")
        else:
            print(f"\n❌ Pipeline failed", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
