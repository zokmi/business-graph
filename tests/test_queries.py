"""索引 schema 與頁面存取的測試。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from business_graph_mcp.db.queries import (
    SCHEMA_VERSION,
    all_content_hashes,
    ambiguous_dangling_titles,
    delete_node,
    get_node,
    open_index,
    record_miss,
    resolve_edges,
    upsert_node,
)
from business_graph_mcp.edges import Edge

現在 = datetime(2026, 8, 14, 10, 0, 0)


def _寫入一頁(conn, slug="Discount Calculation", **覆寫):
    """寫入一頁測試資料，未指定的欄位採用可辨識的預設值。"""
    參數 = {
        "slug": slug,
        "title": slug,
        "node_type": "rule",
        "code": (),
        "aliases": ("折扣計算規則",),
        "body": "Tier and campaign discounts do not stack.",
        "path": f"nodes/{slug}.md",
        "content_hash": "hash0000",
        "status": "confirmed",
        "updated_at": "2026-08-14",
        "parse_error": None,
        "edges": ("Shipping Rules",),
        "indexed_at": 現在,
    }
    參數.update(覆寫)
    參數["edges"] = tuple(Edge("relates_to", target, None) for target in 參數["edges"])
    upsert_node(conn, **參數)


def test_開新庫時建立_schema_並記錄版本(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        版本 = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(版本["value"]) == SCHEMA_VERSION == 1
    finally:
        conn.close()


def test_schema_版本不符時整庫重建(tmp_path):
    db = tmp_path / "index.db"
    conn = open_index(db)
    _寫入一頁(conn)
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    conn = open_index(db)
    try:
        # 索引是衍生物，版本不符直接重建；資料清空是預期行為而非資料遺失。
        assert all_content_hashes(conn) == {}
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"
    finally:
        conn.close()


def test_寫入後可取回完整欄位(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn)
        page = get_node(conn, "Discount Calculation")

        assert page is not None
        assert page.title == "Discount Calculation"
        assert page.node_type == "rule"
        assert page.aliases == ("折扣計算規則",)
        assert page.status == "confirmed"
        assert page.updated_at == "2026-08-14"
        assert page.parse_error is None
    finally:
        conn.close()


def test_同一_slug_重複寫入為更新而非新增(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn)
        _寫入一頁(conn, body="Updated body.", content_hash="hash1111")

        assert conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"] == 1
        # FTS 也必須跟著更新，否則會留下查得到舊內容的幽靈記錄。
        assert conn.execute("SELECT COUNT(*) AS c FROM nodes_fts").fetchone()["c"] == 1
        page = get_node(conn, "Discount Calculation")
        assert page is not None
        assert page.content_hash == "hash1111"
    finally:
        conn.close()


def test_更新時舊的別名與連結被清掉(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn)
        _寫入一頁(conn, aliases=("新別名",), edges=("Refund Policy",))

        別名 = [r["alias"] for r in conn.execute("SELECT alias FROM aliases")]
        連結 = [r["target_raw"] for r in conn.execute("SELECT target_raw FROM edges")]

        assert 別名 == ["新別名"]
        assert 連結 == ["Refund Policy"]
    finally:
        conn.close()


def test_後來才出現的目標頁要靠_resolve_edges_才會被解析(tmp_path):
    # upsert_node 只解析自己的連結；「新頁讓別人的懸空連結變有效」這件事
    # 由 sync_index 在一輪結束時呼叫 resolve_edges() 統一處理，
    # 避免每寫一頁就掃一次全表。
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn)  # 引用了尚不存在的 Shipping Rules
        _寫入一頁(conn, slug="Shipping Rules", edges=())

        取連結 = lambda: conn.execute(  # noqa: E731
            "SELECT target_raw, target_slug FROM edges WHERE source_slug = ?",
            ("Discount Calculation",),
        ).fetchone()

        assert 取連結()["target_slug"] is None

        resolve_edges(conn)

        列 = 取連結()
        assert 列["target_raw"] == "Shipping Rules"
        assert 列["target_slug"] == "Shipping Rules"
    finally:
        conn.close()


def test_目標頁已存在時寫入當下即解析(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Shipping Rules", edges=())
        _寫入一頁(conn)  # 此時 Shipping Rules 已存在

        列 = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?",
            ("Discount Calculation",),
        ).fetchone()
        assert 列["target_slug"] == "Shipping Rules"
    finally:
        conn.close()


def test_目標文字可依唯一_title_解析為經檔名清理的_slug(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(
            conn,
            slug="ADR-3- Pricing Model",
            title="ADR-3: Pricing Model",
            aliases=(),
            edges=(),
        )
        _寫入一頁(conn, slug="Pricing Rule", aliases=(), edges=("ADR-3: Pricing Model",))

        row = conn.execute(
            "SELECT target_raw, target_slug FROM edges WHERE source_slug = ?",
            ("Pricing Rule",),
        ).fetchone()
        assert tuple(row) == ("ADR-3: Pricing Model", "ADR-3- Pricing Model")
    finally:
        conn.close()


def test_後來出現的唯一_title_由_resolve_edges_解析(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Pricing Rule", aliases=(), edges=("ADR-3: Pricing Model",))
        _寫入一頁(
            conn,
            slug="ADR-3- Pricing Model",
            title="ADR-3: Pricing Model",
            aliases=(),
            edges=(),
        )

        before = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?", ("Pricing Rule",)
        ).fetchone()
        assert before["target_slug"] is None

        resolve_edges(conn)

        after = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?", ("Pricing Rule",)
        ).fetchone()
        assert after["target_slug"] == "ADR-3- Pricing Model"
    finally:
        conn.close()


def test_解析優先序為_slug_再_alias_再唯一_title(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Shared Target", aliases=(), edges=())
        _寫入一頁(conn, slug="Alias Winner", aliases=("Shared Target",), edges=())
        _寫入一頁(
            conn, slug="Title Winner", title="Shared Target", aliases=(), edges=()
        )
        _寫入一頁(conn, slug="Source", aliases=(), edges=("Shared Target",))

        row = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?", ("Source",)
        ).fetchone()
        assert row["target_slug"] == "Shared Target"

        delete_node(conn, "Shared Target")
        resolve_edges(conn)
        row = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?", ("Source",)
        ).fetchone()
        assert row["target_slug"] == "Alias Winner"
    finally:
        conn.close()


def test_重複_title_保持懸空而非任意選一個(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Title A", title="Shared Title", aliases=(), edges=())
        _寫入一頁(conn, slug="Title B", title="Shared Title", aliases=(), edges=())
        _寫入一頁(conn, slug="Source", aliases=(), edges=("Shared Title",))

        row = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?", ("Source",)
        ).fetchone()
        assert row["target_slug"] is None
    finally:
        conn.close()


def test_重複_title_懸空診斷列出所有候選且忽略大小寫(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Title B", title="Shared Title", aliases=(), edges=())
        _寫入一頁(conn, slug="Title A", title="shared title", aliases=(), edges=())
        _寫入一頁(conn, slug="Source", aliases=(), edges=("SHARED TITLE",))

        assert ambiguous_dangling_titles(conn) == {
            "SHARED TITLE": ("Title A", "Title B")
        }
    finally:
        conn.close()


def test_重複_alias_依_slug_排序決定命中(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Zulu", aliases=("Shared Alias",), edges=())
        _寫入一頁(conn, slug="Alpha", aliases=("Shared Alias",), edges=())
        _寫入一頁(conn, slug="Source", aliases=(), edges=("Shared Alias",))

        row = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = ?", ("Source",)
        ).fetchone()
        assert row["target_slug"] == "Alpha"
    finally:
        conn.close()


def test_懸空連結的_target_slug_為_null(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, edges=("不存在的頁",))

        列 = conn.execute("SELECT target_slug FROM edges").fetchone()
        assert 列["target_slug"] is None
    finally:
        conn.close()


def test_刪除頁面時一併清掉別名連結與_fts(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn)
        delete_node(conn, "Discount Calculation")

        assert get_node(conn, "Discount Calculation") is None
        assert conn.execute("SELECT COUNT(*) AS c FROM aliases").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM nodes_fts").fetchone()["c"] == 0
    finally:
        conn.close()


def test_刪除不存在的頁不報錯(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        delete_node(conn, "從未存在")
    finally:
        conn.close()


def test_取回全部內容雜湊供增量同步比對(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn)
        _寫入一頁(conn, slug="Shipping Rules", content_hash="hash2222")

        assert all_content_hashes(conn) == {
            "Discount Calculation": "hash0000",
            "Shipping Rules": "hash2222",
        }
    finally:
        conn.close()


def test_解析失敗的頁仍入庫且_body_可檢索(tmp_path):
    # Spec 第九節原則 2：一頁壞掉不得拖垮 server，body 仍須進索引。
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(
            conn,
            slug="Broken Page",
            status=None,
            updated_at=None,
            parse_error="缺少 front matter",
        )
        page = get_node(conn, "Broken Page")

        assert page is not None
        assert page.status is None
        assert page.parse_error == "缺少 front matter"
        assert conn.execute("SELECT COUNT(*) AS c FROM nodes_fts").fetchone()["c"] == 1
    finally:
        conn.close()


def test_未命中累計次數逐次遞增(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        assert record_miss(conn, "退貨折扣還原", 現在) == 1
        assert record_miss(conn, "退貨折扣還原", 現在) == 2
        assert record_miss(conn, "其他問題", 現在) == 1
    finally:
        conn.close()


def test_連結指向別名時可解析(tmp_path):
    # Spec 第 3.2 節：標題預設英文，中文術語一律寫進 aliases；
    # 用中文寫的 [[折扣計算規則]] 必須能解析到 Discount Calculation，
    # 否則會被 business_lint 誤判為懸空連結，進而誘導 agent 建立重複頁。
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, aliases=("折扣計算規則",), edges=())
        _寫入一頁(
            conn,
            slug="Shipping Rules",
            aliases=(),
            edges=("折扣計算規則",),
        )

        列 = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = 'Shipping Rules'"
        ).fetchone()
        assert 列["target_slug"] == "Discount Calculation"
    finally:
        conn.close()


def test_連結指向別名但目標頁後來才出現時靠_resolve_edges_解析(tmp_path):
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(
            conn,
            slug="Shipping Rules",
            aliases=(),
            edges=("折扣計算規則",),
        )
        _寫入一頁(conn, aliases=("折扣計算規則",), edges=())

        列 = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = 'Shipping Rules'"
        ).fetchone()
        assert 列["target_slug"] is None

        resolve_edges(conn)

        列 = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = 'Shipping Rules'"
        ).fetchone()
        assert 列["target_slug"] == "Discount Calculation"
    finally:
        conn.close()


def test_連結大小寫不同仍可解析(tmp_path):
    # SQLite 的 = 對 TEXT 預設是大小寫敏感的 BINARY collation；
    # spec 第 10 節明列大小寫差異是連結解析的測試重點之一。
    conn = open_index(tmp_path / "index.db")
    try:
        _寫入一頁(conn, slug="Shipping Rules", aliases=(), edges=())
        _寫入一頁(conn, aliases=(), edges=("shipping rules",))

        列 = conn.execute(
            "SELECT target_slug FROM edges WHERE source_slug = 'Discount Calculation'"
        ).fetchone()
        assert 列["target_slug"] == "Shipping Rules"
    finally:
        conn.close()


def test_open_index_遇到非數字的_schema_version_時整庫重建(tmp_path):
    # meta.schema_version 若被外部工具寫入、部分寫入或手動編輯成非數字，
    # int() 會拋 ValueError；索引是可拋棄的衍生物，這裡必須整庫重建
    # 而非讓例外穿出 open_session，導致三個工具全部無法使用。
    db = tmp_path / "index.db"
    conn = open_index(db)
    _寫入一頁(conn)
    conn.execute("UPDATE meta SET value = '不是數字' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    conn = open_index(db)
    try:
        assert all_content_hashes(conn) == {}
        版本 = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(版本["value"]) == SCHEMA_VERSION == 1
    finally:
        conn.close()


def test_open_index_遇到損毀的資料庫檔案時整庫重建(tmp_path):
    # 寫入垃圾 bytes（非 SQLite 格式）模擬檔案損毀；open_index 應重建
    # 而非拋出 sqlite3.DatabaseError。
    db = tmp_path / "index.db"
    db.write_bytes(b"\x00\x01\x02\x03not a sqlite database")

    conn = open_index(db)
    try:
        assert all_content_hashes(conn) == {}
        版本 = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(版本["value"]) == SCHEMA_VERSION == 1
    finally:
        conn.close()


# 型別資訊遺失、錯誤邊入庫及錨點殘留都必須由真實資料庫行為抓出。
def _寫節點(conn, slug, *, edges=(), code=()):
    from business_graph_mcp.db import queries

    queries.upsert_node(
        conn, slug=slug, title=slug, node_type="rule", aliases=(),
        body=f"{slug} 的內文。", path=f"nodes/{slug}.md", content_hash=slug,
        status="confirmed", updated_at="2026-09-18", parse_error=None,
        edges=edges, code=code, indexed_at=現在,
    )


def test_同兩節點間可並存兩種關係(tmp_path):
    from business_graph_mcp.db import queries
    from business_graph_mcp.edges import Edge

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "B")
        _寫節點(conn, "A", edges=(Edge("overrides", "B", None), Edge("depends_on", "B", None)))
        queries.resolve_edges(conn)
        assert set(queries.impact_edges(conn)) == {("A", "overrides", "B"), ("A", "depends_on", "B")}
    finally:
        conn.close()


def test_影響邊排除相關懸空及錯誤邊(tmp_path):
    from business_graph_mcp.db import queries
    from business_graph_mcp.edges import Edge

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "B")
        _寫節點(conn, "A", edges=(
            Edge("relates_to", "B", None), Edge("depends_on", "Missing", None),
            Edge("overrides", "B", "無效關係"), Edge("exception_to", "B", None),
        ))
        queries.resolve_edges(conn)
        assert queries.impact_edges(conn) == [("A", "exception_to", "B")]
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 3
    finally:
        conn.close()


def test_code_錨點排序讀回且空清單無結果(tmp_path):
    from business_graph_mcp.db import queries

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "B", code=("Z.Symbol",))
        _寫節點(conn, "A", code=("OrderService.Calculate", "DiscountPolicy", "DiscountPolicy"))
        assert queries.code_anchors_for(conn, ["A", "Missing"]) == {"A": ("DiscountPolicy", "OrderService.Calculate")}
        assert queries.code_anchors_for(conn, []) == {}
        assert queries.all_code_anchors(conn) == [("A", "DiscountPolicy"), ("A", "OrderService.Calculate"), ("B", "Z.Symbol")]
    finally:
        conn.close()


def test_重寫及刪除節點會清掉舊錨點與邊(tmp_path):
    from business_graph_mcp.db import queries
    from business_graph_mcp.edges import Edge

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "B")
        _寫節點(conn, "A", edges=(Edge("depends_on", "B", None),), code=("Old.Symbol",))
        _寫節點(conn, "A", code=("New.Symbol",))
        assert queries.code_anchors_for(conn, ["A"]) == {"A": ("New.Symbol",)}
        assert queries.impact_edges(conn) == []
        queries.delete_node(conn, "A")
        assert queries.all_code_anchors(conn) == []
    finally:
        conn.close()


def test_寫入失敗時節點別名邊錨點與全文索引一起復原(tmp_path):
    import sqlite3

    import pytest

    from business_graph_mcp.db import queries
    from business_graph_mcp.edges import Edge

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "B")
        _寫節點(conn, "A", edges=(Edge("depends_on", "B", None),), code=("Old.Symbol",))
        conn.execute("CREATE TRIGGER reject_anchor BEFORE INSERT ON code_anchors BEGIN SELECT RAISE(ABORT, '拒絕錨點'); END")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                queries.upsert_node(
                    conn, slug="A", title="Changed", node_type="flow", aliases=("Changed",),
                    body="Changed body", path="nodes/A.md", content_hash="new",
                    status="inferred", updated_at=None, parse_error=None,
                    edges=(), code=("New.Symbol",), indexed_at=現在,
                )
        node = queries.get_node(conn, "A")
        assert node.title == "A"
        assert node.node_type == "rule"
        assert node.aliases == ()
        assert queries.impact_edges(conn) == [("A", "depends_on", "B")]
        assert queries.all_code_anchors(conn) == [("A", "Old.Symbol")]
        assert queries.search_nodes(conn, "Changed", 10) == []
    finally:
        conn.close()


def test_指定節點的多種懸空關係只回傳一次目標(tmp_path):
    from business_graph_mcp.db.queries import dangling_targets

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "A", edges=(
            Edge("depends_on", "Missing", None),
            Edge("overrides", "Missing", None),
        ))
        assert dangling_targets(conn, ["A"]) == {"A": ("Missing",)}
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 2
    finally:
        conn.close()


def test_全部懸空目標按來源去重且保留不同來源(tmp_path):
    from business_graph_mcp.db.queries import all_dangling

    conn = open_index(tmp_path / "index.db")
    try:
        for slug in ("B", "A"):
            _寫節點(conn, slug, edges=(
                Edge("depends_on", "Missing", None),
                Edge("overrides", "Missing", None),
            ))
        assert all_dangling(conn) == [("A", "Missing"), ("B", "Missing")]
    finally:
        conn.close()


def test_已解析關係包含所有邊型別並排除懸空及錯誤邊(tmp_path):
    from business_graph_mcp.db.queries import resolve_edges, resolved_edges

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "B")
        _寫節點(conn, "A", edges=(
            Edge("relates_to", "B", None), Edge("depends_on", "B", None),
            Edge("exception_to", "B", None), Edge("overrides", "B", None),
            Edge("depends_on", "Missing", None), Edge("overrides", "B", "無效關係"),
        ))
        resolve_edges(conn)
        assert set(resolved_edges(conn)) == {
            ("A", "relates_to", "B"), ("A", "depends_on", "B"),
            ("A", "exception_to", "B"), ("A", "overrides", "B"),
        }
    finally:
        conn.close()


def test_無錨點規則依_slug_排序且回傳完整節點(tmp_path):
    from business_graph_mcp.db import queries

    conn = open_index(tmp_path / "index.db")
    try:
        _寫節點(conn, "Zebra")
        _寫節點(conn, "Anchored", code=("Some.Symbol",))
        _寫節點(conn, "Alpha")
        rows = queries.rule_nodes_without_code(conn)
        assert [row.slug for row in rows] == ["Alpha", "Zebra"]
        assert rows[0].content_hash == "Alpha"
        assert rows[0].node_type == "rule"
        assert rows[0].body == "Alpha 的內文。"
    finally:
        conn.close()


@pytest.mark.parametrize('code', [
    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_READONLY, sqlite3.SQLITE_FULL,
])
def test_暫時性索引錯誤不可刪除既有資料庫(tmp_path, monkeypatch, code):
    import importlib
    from pathlib import Path

    from business_graph_mcp.errors import GraphIndexError

    db = tmp_path / 'index.db'
    conn = open_index(db)
    conn.close()
    original_bytes = db.read_bytes()
    module = importlib.import_module(open_index.__module__)
    error = sqlite3.OperationalError('simulated unavailable index')
    error.sqlite_errorcode = code

    def fail_connect(_path):
        raise error

    real_unlink = Path.unlink
    def forbid_unlink(path, *args, **kwargs):
        if path == db:
            raise AssertionError('暫時性錯誤不得刪除索引')
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(module, 'connect', fail_connect)
        patcher.setattr(Path, 'unlink', forbid_unlink)
        with pytest.raises(GraphIndexError):
            open_index(db)
    assert db.read_bytes() == original_bytes


def test_新索引建表失敗時關閉連線(tmp_path, monkeypatch):
    import importlib

    from business_graph_mcp.errors import GraphIndexError

    module = importlib.import_module(open_index.__module__)
    real_connect = module.connect
    opened = []

    def tracked_connect(path):
        conn = real_connect(path)
        opened.append(conn)
        return conn

    with monkeypatch.context() as patcher:
        patcher.setattr(module, 'connect', tracked_connect)
        patcher.setattr(module, '_SCHEMA_SQL', 'INVALID SQL;')
        with pytest.raises(GraphIndexError):
            open_index(tmp_path / 'index.db')
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')


def test_索引缺少版本資料時重建(tmp_path):
    db = tmp_path / 'index.db'
    conn = open_index(db)
    conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    reopened = open_index(db)
    try:
        assert reopened.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '1'
    finally:
        reopened.close()


def test_meta_缺少必要欄位時重建索引(tmp_path):
    db = tmp_path / 'index.db'
    conn = sqlite3.connect(db)
    try:
        conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, wrong_value TEXT)')
        conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        conn.commit()
    finally:
        conn.close()
    reopened = open_index(db)
    try:
        assert reopened.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '1'
        assert reopened.execute('SELECT COUNT(*) FROM nodes').fetchone()[0] == 0
    finally:
        reopened.close()
