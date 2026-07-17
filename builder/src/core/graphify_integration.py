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
  python3 ingest_and_graph.py --input ./skill-seekers-output/ --project live-kb --watch
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for utils import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.file_utils import write_text, save_json


def find_graphify_python() -> str:
    """Find the Python interpreter with graphify installed"""
    for cmd in [sys.executable, "python3", "python"]:
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


def run_graphify(input_dir: Path, mode: str = "standard", python: str = "",
                 output_dir: Path | None = None) -> bool:
    """Run graphify full pipeline on input directory.

    Runs two stages:
      1. graphify extract  — builds graph.json from source files
      2. graphify cluster-only — clusters communities and generates graph.html
         (required by the dashboard; silently missing if only extract is run)

    output_dir: where to write graphify-out/. Defaults to input_dir/graphify-out.
                Pass wiki/ root so output always lands in wiki/graphify-out/
                regardless of whether input_dir is _articles/ or wiki/.
    """
    if not python:
        python = find_graphify_python()

    # Stage 1: extract
    cmd = [python, "-m", "graphify", "extract", str(input_dir)]
    if output_dir:
        cmd += ["--out", str(output_dir)]
    print(f"🔍 Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"⚠️ graphify returned non-zero: {result.returncode}", file=sys.stderr)
            print(result.stderr[-500:], file=sys.stderr)
            return False
        print(result.stdout[-1000:])
    except subprocess.TimeoutExpired:
        print("❌ graphify extract timed out (>10min)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ graphify extract failed: {e}", file=sys.stderr)
        return False

    # Stage 2: cluster-only — generates graph.html for the dashboard
    # Point at the actual output dir so it reads the graph.json we just wrote.
    cluster_target = output_dir if output_dir else input_dir
    cmd2 = [python, "-m", "graphify", "cluster-only", str(cluster_target)]
    print(f"🔍 Running: {' '.join(cmd2)}")
    try:
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)
        if result2.returncode != 0:
            print(f"⚠️ graphify cluster-only returned non-zero: {result2.returncode}", file=sys.stderr)
            print(result2.stderr[-300:], file=sys.stderr)
            # Non-fatal: extract succeeded, graph.json is valid; just no graph.html
        else:
            print(result2.stdout[-500:])
    except subprocess.TimeoutExpired:
        print("⚠️ graphify cluster-only timed out — graph.html not generated", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ graphify cluster-only failed: {e}", file=sys.stderr)

    return True


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
    write_text(str(citations_file), "\n".join(json.dumps(e, ensure_ascii=False) for e in citations) + "\n" if citations else "")

    # Write edges.jsonl (semantic relationships)
    edges_file = output_dir / "edges.jsonl"
    write_text(str(edges_file), "\n".join(json.dumps(e, ensure_ascii=False) for e in semantic) + "\n" if semantic else "")

    print(
        f"✅ Dual edges: {len(citations)} citation + {len(semantic)} semantic "
        f"({len(citations) + len(semantic)} total)"
    )


def tag_edge_provenance(graph_file: Path, articles_dir: Path) -> dict:
    """
    Classify each edge in graph.json as EXTRACTED, INFERRED, or AMBIGUOUS.

    EXTRACTED  — a [[wikilink]] in a source article explicitly names the target
    AMBIGUOUS  — edge weight below threshold (0.3) suggesting low LLM confidence
    INFERRED   — everything else (LLM-generated semantic relationship)

    Writes graph_provenance.json alongside graph.json and returns counts.
    """
    import re

    counts = {"extracted": 0, "inferred": 0, "ambiguous": 0, "total": 0}

    if not graph_file.exists():
        print(f"⚠️ {graph_file} not found, skipping provenance tagging", file=sys.stderr)
        return counts

    try:
        with open(graph_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Could not read {graph_file}: {e}", file=sys.stderr)
        return counts

    nodes = data.get("nodes", [])
    links = data.get("links", [])

    def _normalize(name: str) -> str:
        return name.lower().strip().replace("_", " ")

    # Build normalized node label → node id lookup
    norm_to_id: dict[str, str] = {}
    for node in nodes:
        nid = node.get("id", "")
        label = node.get("label", nid)
        norm_to_id[_normalize(label)] = nid

    # Scan markdown files to build extracted pairs set
    extracted_pairs: set[tuple[str, str]] = set()
    wikilink_re = re.compile(r'\[\[([^\]]+)\]\]')

    if articles_dir.exists():
        for md_file in articles_dir.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Find all wikilinks in this file
            wikilinks = {_normalize(m) for m in wikilink_re.findall(text)}
            if not wikilinks:
                continue
            # Determine which node this file represents (match by filename stem)
            file_norm = _normalize(md_file.stem)
            src_id = norm_to_id.get(file_norm)
            if src_id is None:
                continue
            # For each wikilink that matches a known node, record the pair
            for wl_norm in wikilinks:
                tgt_id = norm_to_id.get(wl_norm)
                if tgt_id is not None:
                    extracted_pairs.add((_normalize(src_id), _normalize(tgt_id)))
                    extracted_pairs.add((file_norm, wl_norm))

    # Tag each link
    tagged_links = []
    for link in links:
        # Handle both from/to and source/target conventions
        src = link.get("source") or link.get("from", "")
        tgt = link.get("target") or link.get("to", "")
        # Handle both weight and value fields
        raw_weight = link.get("weight") or link.get("value")

        src_norm = _normalize(str(src))
        tgt_norm = _normalize(str(tgt))

        tagged = dict(link)
        if (src_norm, tgt_norm) in extracted_pairs:
            tagged["provenance"] = "EXTRACTED"
            counts["extracted"] += 1
        elif raw_weight is not None:
            try:
                if float(raw_weight) < 0.3:
                    tagged["provenance"] = "AMBIGUOUS"
                    counts["ambiguous"] += 1
                else:
                    tagged["provenance"] = "INFERRED"
                    counts["inferred"] += 1
            except (ValueError, TypeError):
                tagged["provenance"] = "INFERRED"
                counts["inferred"] += 1
        else:
            tagged["provenance"] = "INFERRED"
            counts["inferred"] += 1

        tagged_links.append(tagged)

    counts["total"] = len(tagged_links)

    # Write graph_provenance.json
    provenance_data = dict(data)
    provenance_data["links"] = tagged_links
    provenance_file = graph_file.parent / "graph_provenance.json"
    save_json(str(provenance_file), provenance_data)

    # Write edges_tagged.jsonl (tagged edges.jsonl counterpart)
    edges_tagged_file = graph_file.parent / "edges_tagged.jsonl"
    node_map = {n["id"]: n for n in nodes if "id" in n}
    rows = []
    for link in tagged_links:
        src = link.get("source") or link.get("from", "")
        tgt = link.get("target") or link.get("to", "")
        row = {
            "source": src,
            "source_label": node_map[src]["label"] if src in node_map else src,
            "target": tgt,
            "target_label": node_map[tgt]["label"] if tgt in node_map else tgt,
            "relation": link.get("relation", ""),
            "confidence": link.get("confidence", ""),
            "confidence_score": link.get("confidence_score", 0),
            "source_file": link.get("source_file", ""),
            "provenance": link["provenance"],
        }
        rows.append(row)
    write_text(str(edges_tagged_file), "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n" if rows else "")

    print(f"✅ Provenance: {provenance_file.name} + {edges_tagged_file.name}")
    return counts


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
    parser.add_argument("--no-provenance", dest="provenance", action="store_false",
                        default=True, help="Skip edge provenance tagging")
    
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
        last_mtime: dict = {}
        pending_changes = False

        # Exclusion list
        exclude_dirs = {"graphify-out", "__pycache__", ".git", "node_modules", ".venv"}

        def _scan() -> bool:
            """Scan input_dir, update last_mtime, return True if anything changed."""
            changed = False
            for f in input_dir.rglob("*"):
                if not f.is_file():
                    continue
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
            return changed

        # Populate baseline on startup without triggering a build
        _scan()

        try:
            while True:
                time.sleep(5)
                if not _scan():
                    pending_changes = False
                    continue

                if not pending_changes:
                    # First poll that sees changes — wait one more cycle to let writes settle
                    pending_changes = True
                    continue

                # Two consecutive polls both saw changes → files have settled
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
                if args.provenance:
                    articles_dir = input_dir / "wiki" / "_articles"
                    if articles_dir.exists():
                        counts = tag_edge_provenance(
                            input_dir / "graphify-out" / "graph.json",
                            articles_dir,
                        )
                        print(f"   Provenance: {counts['extracted']} extracted · {counts['inferred']} inferred · {counts['ambiguous']} ambiguous")
                pending_changes = False
        except KeyboardInterrupt:
            print("\n👋 Watch mode stopped")
    else:
        success = run_graphify(input_dir, args.mode, python)
        
        if success and args.jsonld:
            graph_json = input_dir / "graphify-out" / "graph.json"
            jsonld_file = input_dir / "graphify-out" / "graph.jsonld"
            generate_jsonld(graph_json, jsonld_file, args.project)
            split_edges(graph_json, input_dir / "graphify-out")

        if success and args.provenance:
            articles_dir = input_dir / "wiki" / "_articles"
            if articles_dir.exists():
                counts = tag_edge_provenance(
                    input_dir / "graphify-out" / "graph.json",
                    articles_dir,
                )
                print(f"   Provenance: {counts['extracted']} extracted · {counts['inferred']} inferred · {counts['ambiguous']} ambiguous")

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
