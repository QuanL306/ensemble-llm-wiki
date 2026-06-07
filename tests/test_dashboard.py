"""
Tests for dashboard/app.py — pure analytics functions and argument parsing.

The dashboard is a read-only, localhost-only FastAPI app.  Tests here cover:
  1. Graph analytics (compute_god_nodes, compute_community_summary)
  2. Graph cache (load_graph_with_cache)
  3. KB discovery (discover_knowledge_bases)
  4. Argument parser (parse_args)
  5. Route-level smoke tests via TestClient
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# ── Path setup ───────────────────────────────────────────────────────────────
_REPO      = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

# ── Import dashboard module ───────────────────────────────────────────────────
try:
    import app as dashboard
    _IMPORT_OK = True
except Exception as exc:
    dashboard = None
    _IMPORT_OK = False
    _IMPORT_ERR = str(exc)

_dashboard_available = pytest.mark.skipif(
    not _IMPORT_OK,
    reason=f"dashboard import failed ({_IMPORT_ERR if not _IMPORT_OK else ''})",
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Graph analytics — pure functions
# ═══════════════════════════════════════════════════════════════════════════

@_dashboard_available
class TestComputeGodNodes:

    def _graph(self, links):
        """Build a minimal graph dict from a list of (source, target) tuples."""
        node_ids = {n for pair in links for n in pair}
        return {
            "nodes": [{"id": nid, "label": nid, "community": 0} for nid in node_ids],
            "links": [{"source": s, "target": t} for s, t in links],
        }

    def test_most_connected_node_is_first(self):
        graph = self._graph([("A", "B"), ("A", "C"), ("A", "D"), ("B", "C")])
        result = dashboard.compute_god_nodes(graph, top_n=10)
        assert result[0]["id"] == "A"

    def test_degree_counted_correctly(self):
        graph = self._graph([("A", "B"), ("A", "C")])
        result = dashboard.compute_god_nodes(graph, top_n=10)
        a = next(r for r in result if r["id"] == "A")
        assert a["degree"] == 2   # A appears as source in 2 links

    def test_top_n_limits_results(self):
        graph = self._graph([(str(i), str(i + 1)) for i in range(20)])
        result = dashboard.compute_god_nodes(graph, top_n=3)
        assert len(result) <= 3

    def test_empty_graph_returns_empty_list(self):
        result = dashboard.compute_god_nodes({"nodes": [], "links": []}, top_n=10)
        assert result == []

    def test_missing_source_or_target_skipped(self):
        graph = {
            "nodes": [{"id": "A", "label": "A"}],
            "links": [{"source": "A"}, {"target": "B"}, {}],  # incomplete links
        }
        result = dashboard.compute_god_nodes(graph, top_n=10)
        assert isinstance(result, list)

    def test_includes_label_from_nodes(self):
        graph = {
            "nodes": [{"id": "n1", "label": "Node One"}],
            "links": [{"source": "n1", "target": "n2"}],
        }
        result = dashboard.compute_god_nodes(graph, top_n=10)
        n1 = next((r for r in result if r["id"] == "n1"), None)
        assert n1 is not None
        assert n1["label"] == "Node One"


@_dashboard_available
class TestComputeCommunitySummary:

    def test_small_communities_filtered(self):
        """Communities with <= 2 nodes should not appear."""
        graph = {
            "nodes": [
                {"id": "a", "label": "A", "community": 0},
                {"id": "b", "label": "B", "community": 0},  # only 2 → filtered
                {"id": "c", "label": "C", "community": 1},
                {"id": "d", "label": "D", "community": 1},
                {"id": "e", "label": "E", "community": 1},  # 3 → included
            ],
            "links": [],
        }
        result = dashboard.compute_community_summary(graph)
        ids = [r["id"] for r in result]
        assert 1 in ids
        assert 0 not in ids

    def test_sorted_by_node_count_descending(self):
        graph = {
            "nodes": [
                *[{"id": f"x{i}", "label": f"X{i}", "community": 0} for i in range(5)],
                *[{"id": f"y{i}", "label": f"Y{i}", "community": 1} for i in range(3)],
            ],
            "links": [],
        }
        result = dashboard.compute_community_summary(graph)
        assert result[0]["node_count"] >= result[-1]["node_count"]

    def test_empty_graph_returns_empty(self):
        result = dashboard.compute_community_summary({"nodes": [], "links": []})
        assert result == []

    def test_sample_labels_capped_at_three(self):
        graph = {
            "nodes": [
                {"id": str(i), "label": f"Label{i}", "community": 0}
                for i in range(10)
            ],
            "links": [],
        }
        result = dashboard.compute_community_summary(graph)
        assert len(result[0]["sample_labels"]) <= 3


# ═══════════════════════════════════════════════════════════════════════════
# 2. Graph cache
# ═══════════════════════════════════════════════════════════════════════════

@_dashboard_available
class TestLoadGraphWithCache:

    def _make_project(self, tmp_path, project_key, data):
        gfo = tmp_path / project_key
        gfo.mkdir(parents=True)
        (gfo / "graph.json").write_text(json.dumps(data))
        return str(gfo)

    def test_returns_none_for_unknown_project(self):
        with patch.object(dashboard, "get_all_graphify_projects", return_value={}):
            result = dashboard.load_graph_data("no_such_project")
        assert result is None

    def test_returns_data_for_existing_project(self, tmp_path):
        data = {"nodes": [{"id": "a"}], "links": []}
        path = self._make_project(tmp_path, "test_proj", data)
        projects = {"test_proj": {"path": path}}
        with patch.object(dashboard, "get_all_graphify_projects", return_value=projects):
            dashboard._graph_cache.clear()
            result = dashboard.load_graph_data("test_proj")
        assert result is not None
        assert result["nodes"][0]["id"] == "a"

    def test_missing_graph_json_returns_none(self, tmp_path):
        path = str(tmp_path / "empty_proj")
        os.makedirs(path, exist_ok=True)
        projects = {"empty_proj": {"path": path}}
        with patch.object(dashboard, "get_all_graphify_projects", return_value=projects):
            dashboard._graph_cache.clear()
            result = dashboard.load_graph_data("empty_proj")
        assert result is None

    def test_cache_returns_same_object_on_second_call(self, tmp_path):
        data = {"nodes": [], "links": []}
        path = self._make_project(tmp_path, "cached_proj", data)
        projects = {"cached_proj": {"path": path}}
        with patch.object(dashboard, "get_all_graphify_projects", return_value=projects):
            dashboard._graph_cache.clear()
            r1 = dashboard.load_graph_data("cached_proj")
            r2 = dashboard.load_graph_data("cached_proj")
        assert r1 is r2    # same object from cache, not re-loaded


# ═══════════════════════════════════════════════════════════════════════════
# 3. KB discovery
# ═══════════════════════════════════════════════════════════════════════════

@_dashboard_available
class TestDiscoverKnowledgeBases:

    def test_finds_directories_with_kbaconfig(self, tmp_path):
        (tmp_path / "research").mkdir()
        (tmp_path / "research" / ".kbaconfig").write_text("name: research\n")
        (tmp_path / "notes").mkdir()   # no .kbaconfig — should not appear
        with patch.object(dashboard, "KB_ROOT", str(tmp_path)):
            result = dashboard.discover_knowledge_bases()
        assert "research" in result
        assert "notes" not in result

    def test_empty_root_returns_empty_dict(self, tmp_path):
        with patch.object(dashboard, "KB_ROOT", str(tmp_path)):
            result = dashboard.discover_knowledge_bases()
        assert result == {}

    def test_nonexistent_root_returns_empty_dict(self, tmp_path):
        with patch.object(dashboard, "KB_ROOT", str(tmp_path / "no_such_dir")):
            result = dashboard.discover_knowledge_bases()
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Argument parser
# ═══════════════════════════════════════════════════════════════════════════

@_dashboard_available
class TestParseArgs:

    def test_default_port_is_8765(self):
        args = dashboard.parse_args([])
        assert args.port == 8765

    def test_custom_port_accepted(self):
        args = dashboard.parse_args(["9000"])
        assert args.port == 9000

    def test_default_host_is_localhost(self):
        args = dashboard.parse_args([])
        assert args.host == "127.0.0.1"

    def test_host_flag_accepted(self):
        args = dashboard.parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_kb_root_flag_accepted(self):
        args = dashboard.parse_args(["--kb-root", "/tmp/my-kbs"])
        assert args.kb_root == "/tmp/my-kbs"

    def test_kb_root_defaults_to_none(self):
        args = dashboard.parse_args([])
        assert args.kb_root is None

    def test_out_of_range_port_raises_system_exit(self):
        with pytest.raises(SystemExit):
            dashboard.parse_args(["80"])   # below 1024

    def test_apply_args_sets_kb_root(self, tmp_path):
        (tmp_path / "my-kbs").mkdir()
        args = dashboard.parse_args(["--kb-root", str(tmp_path / "my-kbs")])
        dashboard._apply_args(args)
        assert dashboard.KB_ROOT == str((tmp_path / "my-kbs").resolve())


# ═══════════════════════════════════════════════════════════════════════════
# 5. Route smoke tests
# ═══════════════════════════════════════════════════════════════════════════

@_dashboard_available
class TestRoutes:

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(dashboard.app)

    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_stats_returns_200(self, client):
        with patch.object(dashboard, "load_kb_stats",
                          return_value={"available": False, "kbs": {}, "total_docs": 0}), \
             patch.object(dashboard, "get_all_graphify_projects", return_value={}):
            resp = client.get("/api/stats")
        assert resp.status_code == 200

    def test_graph_unknown_project_returns_404(self, client):
        with patch.object(dashboard, "get_all_graphify_projects", return_value={}):
            resp = client.get("/api/graph/no_such_project")
        assert resp.status_code == 404

    def test_god_nodes_unknown_project_returns_404(self, client):
        with patch.object(dashboard, "get_all_graphify_projects", return_value={}):
            resp = client.get("/api/graph/no_such_project/god-nodes")
        assert resp.status_code == 404

    def test_graph_html_unknown_project_returns_404(self, client):
        with patch.object(dashboard, "get_all_graphify_projects", return_value={}):
            resp = client.get("/api/graph/no_such_project/html")
        assert resp.status_code == 404
