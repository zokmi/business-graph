"""增量同步的測試。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from business_graph_mcp.db.queries import (
    all_content_hashes,
    all_dangling,
    get_node,
    open_index,
    resolved_edges,
)
from business_graph_mcp.sync import sync_index
from business_graph_mcp.workspace import Workspace

現在 = datetime(2026, 8, 14, 10, 0, 0)


@pytest.fixture
def ws(tmp_path) -> Workspace:
    """建立空的知識庫目錄。"""
    workspace = Workspace(root=tmp_path / ".bgraph")
    workspace.nodes_dir.mkdir(parents=True)
    return workspace


@pytest.fixture
def conn(ws):
    """開啟該知識庫的索引連線，測試結束自動關閉。"""
    connection = open_index(ws.db_path)
    yield connection
    connection.close()


def 寫頁(
    ws: Workspace,
    標題: str,
    內文: str = "Body text.",
    status: str = "confirmed",
    *,
    title: str | None = None,
    aliases: tuple[str, ...] = (),
) -> Path:
    """在 nodes/ 寫一個格式正確的頁面檔並回傳其路徑。"""
    path = ws.nodes_dir / f"{標題}.md"
    alias_line = f"aliases: [{', '.join(aliases)}]\n" if aliases else ""
    path.write_text(
        f"---\ntitle: {title or 標題}\ntype: rule\n{alias_line}"
        f"status: {status}\nupdated: 2026-08-14\n---\n\n{內文}\n",
        encoding="utf-8",
    )
    return path


def test_首次同步把全部頁面加進索引(ws, conn):
    寫頁(ws, "Discount Calculation")
    寫頁(ws, "Shipping Rules")

    result = sync_index(conn, ws, 現在)

    assert (result.added, result.updated, result.removed) == (2, 0, 0)
    assert set(all_content_hashes(conn)) == {"Discount Calculation", "Shipping Rules"}


def test_nodes_目錄刪除後清除既有索引(ws, conn):
    from business_graph_mcp.config import GraphConfig
    from business_graph_mcp.explore import run_explore

    page = 寫頁(ws, "Discount", 內文="See [[Discount]].")
    sync_index(conn, ws, 現在)
    page.unlink()
    ws.nodes_dir.rmdir()

    result = sync_index(conn, ws, 現在)

    assert result.removed == 1
    assert not all_content_hashes(conn)
    assert not resolved_edges(conn)
    assert not run_explore(conn, "Discount", GraphConfig()).hits


def test_nodes_讀取權限錯誤不得清除既有索引(ws, conn, monkeypatch):
    寫頁(ws, "Discount")
    sync_index(conn, ws, 現在)
    original = Path.iterdir

    def denied(path):
        if path == ws.nodes_dir:
            raise PermissionError("無法讀取 nodes")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError):
        sync_index(conn, ws, 現在)
    assert get_node(conn, "Discount") is not None


def test_內容未變時第二次同步全部跳過(ws, conn):
    寫頁(ws, "Discount Calculation")
    sync_index(conn, ws, 現在)

    result = sync_index(conn, ws, 現在)

    assert (result.added, result.updated, result.unchanged) == (0, 0, 1)


def test_內容變更時重新索引(ws, conn):
    寫頁(ws, "Discount Calculation", 內文="Old body.")
    sync_index(conn, ws, 現在)
    寫頁(ws, "Discount Calculation", 內文="New body.")

    result = sync_index(conn, ws, 現在)

    assert result.updated == 1
    page = get_node(conn, "Discount Calculation")
    assert page is not None
    assert page.body == "New body."


def test_只改_front_matter_也視為變更(ws, conn):
    # hash 計算的是整份檔案而非只有內文，否則 status 從 inferred 改成
    # confirmed 這種關鍵變更會被當成沒變。
    寫頁(ws, "Shipping Rules", status="inferred")
    sync_index(conn, ws, 現在)
    寫頁(ws, "Shipping Rules", status="confirmed")

    result = sync_index(conn, ws, 現在)

    assert result.updated == 1
    page = get_node(conn, "Shipping Rules")
    assert page is not None
    assert page.status == "confirmed"


def test_只更新_alias_也會重新解析既有入站連結(ws, conn):
    寫頁(ws, "Source", 內文="See [[Tier Alias]].")
    寫頁(ws, "Target")
    sync_index(conn, ws, 現在)
    assert all_dangling(conn) == [("Source", "Tier Alias")]

    寫頁(ws, "Target", aliases=("Tier Alias",))
    added = sync_index(conn, ws, 現在)
    assert added.updated == 1
    assert resolved_edges(conn) == [("Source", "relates_to", "Target")]

    寫頁(ws, "Target")
    removed = sync_index(conn, ws, 現在)
    assert removed.updated == 1
    assert all_dangling(conn) == [("Source", "Tier Alias")]


def test_只更新_title_也會重新解析既有入站連結(ws, conn):
    寫頁(ws, "Source", 內文="See [[Public Title]].")
    寫頁(ws, "Target", title="Internal Title")
    sync_index(conn, ws, 現在)
    assert all_dangling(conn) == [("Source", "Public Title")]

    寫頁(ws, "Target", title="Public Title")
    sync_index(conn, ws, 現在)
    assert resolved_edges(conn) == [("Source", "relates_to", "Target")]

    寫頁(ws, "Target", title="Renamed Title")
    sync_index(conn, ws, 現在)
    assert all_dangling(conn) == [("Source", "Public Title")]


def test_檔案被刪除時同步移除索引(ws, conn):
    path = 寫頁(ws, "Discount Calculation")
    sync_index(conn, ws, 現在)
    path.unlink()

    result = sync_index(conn, ws, 現在)

    assert result.removed == 1
    assert get_node(conn, "Discount Calculation") is None


def test_改名視為刪除加新增且連結重新解析(ws, conn):
    寫頁(ws, "Discount Calculation", 內文="See [[Shipping Rules]].")
    寫頁(ws, "Shipping Rules")
    sync_index(conn, ws, 現在)

    # 節點身分由檔名 slug 與 front matter title 共同表達；只改檔名、保留舊
    # title 時，舊標題仍是有效連結目標。真正改名須兩者一起改。
    (ws.nodes_dir / "Shipping Rules.md").unlink()
    寫頁(ws, "Delivery Rules")
    result = sync_index(conn, ws, 現在)

    assert (result.added, result.removed) == (1, 1)
    # 原本有效的連結變成懸空，必須反映在 edges 表，business_lint 才報得出來。
    列 = conn.execute("SELECT target_slug FROM edges WHERE target_raw = 'Shipping Rules'").fetchone()
    assert 列["target_slug"] is None


def test_front_matter_壞掉的頁仍入索引並列入_failed(ws, conn):
    # Spec 第九節原則 2：一頁壞掉不得拖垮整體，body 仍須可查。
    (ws.nodes_dir / "Broken.md").write_text("沒有 front matter 的內容", encoding="utf-8")
    寫頁(ws, "Discount Calculation")

    result = sync_index(conn, ws, 現在)

    assert result.added == 2
    assert result.failed == ("Broken",)
    page = get_node(conn, "Broken")
    assert page is not None
    assert page.parse_error is not None
    assert page.body == "沒有 front matter 的內容"


def test_空檔不會讓同步中斷(ws, conn):
    (ws.nodes_dir / "Empty.md").write_text("", encoding="utf-8")

    result = sync_index(conn, ws, 現在)

    assert result.added == 1
    assert result.failed == ("Empty",)


def test_無法以_utf8_讀取的檔列入_failed_但不中斷(ws, conn):
    (ws.nodes_dir / "Bad Encoding.md").write_bytes(b"\xff\xfe\x00invalid")
    寫頁(ws, "Discount Calculation")

    result = sync_index(conn, ws, 現在)

    assert "Bad Encoding" in result.failed
    assert get_node(conn, "Discount Calculation") is not None


def test_子目錄中的_md_被忽略但會被列出(ws, conn):
    子目錄 = ws.nodes_dir / "archive"
    子目錄.mkdir()
    (子目錄 / "Old Rule.md").write_text("---\ntitle: Old Rule\n---\n", encoding="utf-8")
    寫頁(ws, "Discount Calculation")

    result = sync_index(conn, ws, 現在)

    assert result.added == 1
    # 靜默忽略會讓使用者以為寫進去了卻查不到，必須被看見。
    assert result.ignored == ("archive/Old Rule.md",)


def test_非_md_檔案完全不參與同步(ws, conn):
    (ws.nodes_dir / "notes.txt").write_text("不是 markdown", encoding="utf-8")
    寫頁(ws, "Discount Calculation")

    result = sync_index(conn, ws, 現在)

    assert result.added == 1
    assert result.ignored == ()


def test_nodes_目錄不存在時同步不報錯(tmp_path):
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.root.mkdir(parents=True)
    conn = open_index(ws.db_path)
    try:
        result = sync_index(conn, ws, 現在)
        assert (result.added, result.removed) == (0, 0)
    finally:
        conn.close()


def test_typed_edge_projection(tmp_path):
    root = tmp_path / ".bgraph"
    nodes = root / "nodes"
    nodes.mkdir(parents=True)
    for title, body in {
        "Source": "[[depends_on:Target]] [[overrides:Target]] [[typo:Missing]]",
        "Target": "目標內容",
    }.items():
        (nodes / f"{title}.md").write_text(
            f"---\ntitle: {title}\ntype: rule\nstatus: confirmed\n"
            f"updated: 2026-09-21\n---\n{body}\n",
            encoding="utf-8",
        )
    conn = open_index(root / "index.db")
    try:
        result = sync_index(conn, Workspace(root), datetime(2026, 9, 21))
        assert result.added == 2
        rows = conn.execute(
            "SELECT edge_type, target_raw FROM edges WHERE source_slug = 'Source' ORDER BY edge_type"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("depends_on", "Target"), ("overrides", "Target")]
    finally:
        conn.close()


def test_v2_舊索引以相同內容重新開啟時重建型別邊投影(tmp_path):
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)
    寫頁(
        ws,
        "Source",
        內文="[[depends_on:Target]] [[overrides:Target]] [[typo:Missing]]",
    )
    寫頁(ws, "Target")
    conn = open_index(ws.db_path)
    sync_index(conn, ws, 現在)

    # 模擬型別化解析上線前的 v2 索引：content_hash 已相同，但 links 仍是
    # 把 [[...]] 內容整串寫入的舊投影。同目標不同型別也各占一列。
    conn.execute("ALTER TABLE nodes RENAME TO pages")
    conn.execute("DROP TABLE edges")
    conn.execute("CREATE TABLE links (source_slug TEXT, target_raw TEXT)")
    conn.executemany(
        "INSERT INTO links (source_slug, target_raw) VALUES ('Source', ?)",
        [("depends_on:Target",), ("overrides:Target",), ("typo:Missing",)],
    )
    conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    conn = open_index(ws.db_path)
    try:
        result = sync_index(conn, ws, 現在)

        assert result.added == 2
        rows = conn.execute(
            "SELECT edge_type, target_raw FROM edges WHERE source_slug = 'Source' ORDER BY edge_type"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("depends_on", "Target"), ("overrides", "Target")]
    finally:
        conn.close()


def test_同步保存型別與錨點且解析失敗清除關係(ws, conn):
    from business_graph_mcp.db import queries

    path = ws.nodes_dir / "A.md"
    path.write_text("---\ntitle: A\ntype: rule\nstatus: confirmed\nupdated: 2026-09-18\ncode: [Order.Calculate]\n---\n[[depends_on:B]]", encoding="utf-8")
    寫頁(ws, "B")
    sync_index(conn, ws, 現在)
    assert queries.get_node(conn, "A").node_type == "rule"
    assert queries.code_anchors_for(conn, ["A"]) == {"A": ("Order.Calculate",)}
    assert queries.impact_edges(conn) == [("A", "depends_on", "B")]

    path.write_text("沒有中繼資料 [[depends_on:B]]", encoding="utf-8")
    result = sync_index(conn, ws, 現在)
    assert result.failed == ("A",)
    assert queries.get_node(conn, "A").node_type is None
    assert queries.code_anchors_for(conn, ["A"]) == {}
    assert queries.impact_edges(conn) == []
