"""固定知識庫服務的隔離與本機相容性測試。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from business_graph_mcp.engine import open_workspace_session
from business_graph_mcp.server import create_server
from business_graph_mcp.service import GraphService
from business_graph_mcp.workspace import Workspace
from tests.conftest import call_tool


def _workspace(project: Path, marker: str) -> Workspace:
    """建立不含選用設定檔的獨立知識庫。"""
    ws = Workspace(project / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)
    (ws.nodes_dir / "Refund Policy.md").write_text(
        "---\ntitle: Refund Policy\ntype: rule\nstatus: confirmed\n"
        "code: [Policy.Refund, Missing.Symbol]\nupdated: 2026-10-22\n---\n\n"
        f"{marker}. See [[{marker} Target]].\n",
        encoding="utf-8",
    )
    return ws


@pytest.mark.parametrize("cwd_name", ["B", "B/nested", "unrelated"])
def test_bound_service_reads_writes_and_lints_only_its_workspace(tmp_path, monkeypatch, cwd_name):
    ws_a = _workspace(tmp_path / "A", "Alpha")
    ws_b = _workspace(tmp_path / "B", "Beta")
    original_b = (ws_b.nodes_dir / "Refund Policy.md").read_bytes()
    service = GraphService(ws_a, None)
    cwd = tmp_path / cwd_name
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)

    text = service.explore("Refund Policy")["result"]
    assert "Alpha." in text
    assert "Beta." not in text
    lint = service.lint()["result"]
    assert "[[Alpha Target]]" in lint
    assert "Beta Target" not in lint

    outcome = service.write(
        title="Credit Limit", node_type="entity", content="Alpha credit limit.",
        status="inferred", aliases=("額度",), code=("Credit.Limit",),
    )
    assert outcome["created"] is True
    assert outcome["indexed"] is True
    assert "Alpha credit limit." in service.explore("額度")["result"]
    updated = service.write(
        title="Credit Limit", node_type="entity", content="Updated Alpha credit limit.",
        status="confirmed", base_hash=outcome["base_hash"],
    )
    assert updated["created"] is False
    assert updated["base_hash"] != outcome["base_hash"]
    assert (ws_b.nodes_dir / "Refund Policy.md").read_bytes() == original_b
    assert not (ws_b.nodes_dir / "Credit Limit.md").exists()
    if cwd != ws_b.root.parent:
        assert not (cwd / ".bgraph").exists()


def _codegraph(repo: Path) -> Path:
    """建立祖先目錄內可被本機工具找到的真實索引。"""
    (repo / ".git").mkdir()
    db = repo / ".codegraph" / "codegraph.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE nodes (name TEXT, qualified_name TEXT, file_path TEXT, "
            "start_line INTEGER, end_line INTEGER)"
        )
        conn.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?)",
            ("Refund", "Policy.Refund", "src/Policy.cs", 10, 20),
        )
    return db


async def test_none_disables_ancestor_codegraph_for_explore_and_lint_but_local_keeps_lookup(tmp_path):
    ws = _workspace(tmp_path / "project", "Alpha")
    _codegraph(tmp_path)
    service = GraphService(ws, None)

    text = service.explore("Refund Policy")["result"]
    assert "Policy.Refund — 未解析" in text
    assert "src/Policy.cs" not in text
    assert "未解析的程式碼錨點" not in service.lint()["result"]

    mcp = create_server()
    local_text = (await call_tool(mcp, "business_explore", {
        "query": "Refund Policy", "project_path": str(ws.root.parent),
    }))["result"]
    assert "Policy.Refund — src/Policy.cs:10-20" in local_text
    local_lint = (await call_tool(mcp, "business_lint", {
        "project_path": str(ws.root.parent),
    }))["result"]
    assert "Missing.Symbol 在 codegraph 索引中查無" in local_lint
    assert "Policy.Refund 在 codegraph 索引中查無" not in local_lint


async def test_explicit_codegraph_and_local_results_remain_compatible(tmp_path):
    ws = _workspace(tmp_path / "project", "Alpha")
    db = _codegraph(tmp_path)
    service = GraphService(ws, db)
    mcp = create_server()
    args = {"project_path": str(ws.root.parent)}

    assert service.explore("Refund Policy") == await call_tool(
        mcp, "business_explore", {**args, "query": "Refund Policy"},
    )
    assert service.lint() == await call_tool(mcp, "business_lint", args)
    first = service.write(title="Credit Limit", node_type="entity", content="Limit.", status="confirmed")
    second = await call_tool(mcp, "business_write", {
        **args, "title": "Credit Limit", "type": "entity", "content": "Limit.",
        "status": "confirmed", "base_hash": first["base_hash"],
    })
    assert first == {**second, "created": True}


def test_workspace_session_syncs_without_config_and_closes_on_error(tmp_path):
    ws = _workspace(tmp_path, "Alpha")
    with pytest.raises(RuntimeError, match="模擬失敗"):
        with open_workspace_session(ws) as session:
            assert session.conn.execute("SELECT count(*) FROM nodes").fetchone()[0] == 1
            raise RuntimeError("模擬失敗")
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")
    with open_workspace_session(ws) as session:
        assert session.ws == ws
