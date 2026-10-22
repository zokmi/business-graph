"""停止寫入後的白名單備份、全新目錄還原與衍生索引重建。"""
from __future__ import annotations

import hashlib
import shutil

import pytest

from business_graph_mcp.audit import AuditWriter
from business_graph_mcp.config import load_config
from business_graph_mcp.engine import open_workspace_session
from business_graph_mcp.errors import GraphError
from business_graph_mcp.remote_config import validate_remote_workspace
from business_graph_mcp.service import GraphService


def _copy_snapshot(source, destination):
    """對應 SETUP 的停機後複製白名單；目的地必須不存在或為空。"""
    destination.mkdir(exist_ok=True)
    if any(destination.iterdir()):
        raise FileExistsError("備份或還原目的地必須為空。")
    (destination / ".bgraph").mkdir()
    shutil.copytree(source / ".bgraph" / "nodes", destination / ".bgraph" / "nodes")
    if (source / ".bgraph" / "bgraph.toml").exists():
        shutil.copy2(source / ".bgraph" / "bgraph.toml", destination / ".bgraph" / "bgraph.toml")
    if (source / "audit.jsonl").exists():
        shutil.copy2(source / "audit.jsonl", destination / "audit.jsonl")


def _bytes(directory):
    """逐檔記錄來源，確認還原未碰觸原資料。"""
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_stopped_snapshot_restores_full_nodes_hashes_edges_config_and_audit(tmp_path):
    source = tmp_path / "original"
    ws = validate_remote_workspace(source / ".bgraph", initialize=True)
    ws.config_path.write_text("explore_depth = 4\nstale_days = 123\n", encoding="utf-8")
    service = GraphService(ws, None)
    service.write(title="Customer Tier", node_type="entity", content="Gold tier.", status="confirmed")
    service.write(title="VIP Discount", node_type="rule", content="Gold pricing [[depends_on:Customer Tier]].", status="confirmed")
    AuditWriter(source / "audit.jsonl").append({"subject": "writer", "tool": "business_write", "outcome": "success"})
    with open_workspace_session(ws) as session:
        before_nodes = [tuple(row) for row in session.conn.execute("SELECT slug, body, content_hash FROM nodes ORDER BY slug")]
        before_edges = [tuple(row) for row in session.conn.execute("SELECT source_slug, edge_type, target_raw, target_slug FROM edges ORDER BY source_slug")]
        assert before_edges == [("VIP Discount", "depends_on", "Customer Tier", "Customer Tier")]
    # 至此所有 session 均已關閉，等同停掉唯一服務且沒有其他檔案寫入者。
    original = _bytes(source)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    _copy_snapshot(source, backup)
    restored.mkdir()  # 對應新掛載的空資料卷根目錄。
    _copy_snapshot(backup, restored)
    assert not (backup / ".bgraph" / "index.db").exists()
    assert not (restored / ".bgraph" / "index.db").exists()
    restored_ws = validate_remote_workspace(restored / ".bgraph")
    assert restored_ws.config_path.read_bytes() == ws.config_path.read_bytes()
    assert load_config(restored_ws.config_path).explore_depth == 4
    assert load_config(restored_ws.config_path).stale_days == 123
    assert (restored / "audit.jsonl").read_bytes() == original["audit.jsonl"]
    assert _bytes(restored_ws.nodes_dir) == _bytes(ws.nodes_dir)
    with open_workspace_session(restored_ws) as session:
        assert [tuple(row) for row in session.conn.execute("SELECT slug, body, content_hash FROM nodes ORDER BY slug")] == before_nodes
        assert [tuple(row) for row in session.conn.execute("SELECT source_slug, edge_type, target_raw, target_slug FROM edges ORDER BY source_slug")] == before_edges
        assert len(before_edges) == 1
    text = GraphService(restored_ws, None).explore("VIP Discount")["result"]
    assert "Gold pricing" in text
    assert "Customer Tier" in text
    for path in restored_ws.nodes_dir.glob("*.md"):
        assert hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:16] in {row[2] for row in before_nodes}
    assert _bytes(source) == original
    assert {p.name for p in backup.iterdir()} == {".bgraph", "audit.jsonl"}
    assert {p.name for p in (backup / ".bgraph").iterdir()} == {"nodes", "bgraph.toml"}


@pytest.mark.parametrize("existing_empty", [False, True])
def test_empty_volume_initializes_and_existing_graph_restarts_without_data_loss(tmp_path, existing_empty):
    root = tmp_path / "data" / ".bgraph"
    if existing_empty:
        root.mkdir(parents=True)
    ws = validate_remote_workspace(root, initialize=True)
    assert list(ws.nodes_dir.iterdir()) == []
    result = GraphService(ws, None).write(title="Customer Tier", node_type="entity", content="Original gold tier.", status="confirmed")
    original = _bytes(root)
    restarted = validate_remote_workspace(root, initialize=True)
    assert _bytes(root) == original
    assert result["base_hash"] in GraphService(restarted, None).explore("Customer Tier")["result"]
    assert _bytes(ws.nodes_dir) == {"Customer Tier.md": original["nodes/Customer Tier.md"]}


def test_optional_config_and_audit_are_not_required_for_empty_graph_backup(tmp_path):
    source = tmp_path / "source"
    validate_remote_workspace(source / ".bgraph", initialize=True)
    backup = tmp_path / "backup"
    _copy_snapshot(source, backup)
    assert validate_remote_workspace(backup / ".bgraph").nodes_dir.is_dir()
    assert not (backup / ".bgraph" / "bgraph.toml").exists()


def test_initialization_refuses_unknown_nonempty_workspace_and_preserves_files(tmp_path):
    root = tmp_path / ".bgraph"
    root.mkdir()
    (root / "keep.md").write_text("Existing user data.", encoding="utf-8")
    before = _bytes(root)
    with pytest.raises(GraphError):
        validate_remote_workspace(root, initialize=True)
    assert _bytes(root) == before


def test_backup_refuses_existing_destination_without_overwriting(tmp_path):
    source = tmp_path / "source"
    validate_remote_workspace(source / ".bgraph", initialize=True)
    existing = tmp_path / "backup"
    existing.mkdir()
    (existing / "keep.txt").write_text("Previous backup.", encoding="utf-8")
    before = _bytes(existing)
    with pytest.raises(FileExistsError):
        _copy_snapshot(source, existing)
    assert _bytes(existing) == before

