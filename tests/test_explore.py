"""檢索、鄰居擴展與預算配置的測試。"""
from __future__ import annotations

from datetime import datetime

import pytest

from business_graph_mcp.config import GraphConfig
from business_graph_mcp.db.queries import NodeRow, open_index, resolve_edges, upsert_node
from business_graph_mcp.edges import EDGE_DEPENDS_ON, EDGE_OVERRIDES, Edge
from business_graph_mcp.explore import (
    Neighbor,
    allocate_neighbors,
    estimate_tokens,
    lead_paragraph,
    run_explore,
)

現在 = datetime(2026, 8, 14, 10, 0, 0)


def test_路徑展開到指定深度並記錄邊型別():
    from business_graph_mcp.explore import relation_paths

    edges = [
        ("Discount", EDGE_OVERRIDES, "VIP"),
        ("VIP", EDGE_DEPENDS_ON, "Tier"),
        ("Tier", EDGE_DEPENDS_ON, "Customer"),
    ]
    paths = relation_paths(edges, ["Discount"], depth=2)
    assert [(s.target, s.edge_type, s.depth) for s in paths["Discount"]] == [
        ("VIP", "overrides", 1), ("Tier", "depends_on", 2),
    ]


def test_路徑走訪排除自身與循環且各起點獨立():
    from business_graph_mcp.explore import relation_paths

    edges = [("A", "depends_on", "A"), ("A", "depends_on", "B"),
             ("B", "depends_on", "A")]
    paths = relation_paths(edges, ["A", "B", "Isolated"], depth=5)
    assert {root: [s.target for s in steps] for root, steps in paths.items()} == {
        "A": ["B"], "B": ["A"], "Isolated": [],
    }


def test_路徑保留最短距離且不受邊順序及重複影響():
    from business_graph_mcp.explore import relation_paths

    edges = [("A", "overrides", "C"), ("A", "depends_on", "B"),
             ("B", "depends_on", "C"), ("C", "relates_to", "D"),
             ("A", "relates_to", "B")]
    paths = relation_paths(edges, ["A"], depth=3)
    assert [(s.target, s.edge_type, s.depth) for s in paths["A"]] == [
        ("B", "depends_on", 1), ("B", "relates_to", 1),
        ("C", "overrides", 1), ("D", "relates_to", 2),
    ]
    assert [(s.source, s.target) for s in paths["A"]] == [
        ("A", "B"), ("A", "B"), ("A", "C"), ("C", "D"),
    ]
    assert relation_paths(list(reversed(edges)) + edges, ["A"], depth=3) == paths


@pytest.mark.parametrize("depth", [0, -1])
def test_非正深度不展開(depth):
    from business_graph_mcp.explore import relation_paths

    assert relation_paths([("A", "depends_on", "B")], ["A"], depth) == {"A": ()}


def test_無起點回空路徑():
    from business_graph_mcp.explore import relation_paths

    assert relation_paths([("A", "depends_on", "B")], [], 2) == {}


def _page(slug: str, body: str = "", aliases: tuple[str, ...] = ()) -> NodeRow:
    """組出測試用的 NodeRow，不經資料庫。"""
    return NodeRow(
        id=1,
        slug=slug,
        title=slug, node_type="rule",
        aliases=aliases,
        body=body,
        path=f"nodes/{slug}.md",
        content_hash="h",
        status="confirmed",
        updated_at="2026-08-14",
        parse_error=None,
    )


@pytest.fixture
def conn(tmp_path):
    """開啟索引連線並寫入一組互相引用的頁面。"""
    connection = open_index(tmp_path / "index.db")

    def 寫(slug, body, aliases=(), links=()):
        upsert_node(
            connection,
            slug=slug,
            title=slug, node_type="rule",
            aliases=aliases,
            body=body,
            path=f"nodes/{slug}.md",
            content_hash=slug,
            status="confirmed",
            updated_at="2026-08-14",
            parse_error=None,
            edges=tuple(Edge("relates_to", target, None) for target in links),
            code=(),
            indexed_at=現在,
        )

    寫(
        "Discount Calculation",
        "Tier and campaign discounts do not stack. Shipping is excluded (see [[Shipping Rules]]).",
        aliases=("折扣計算規則",),
        links=("Shipping Rules",),
    )
    寫("Shipping Rules", "Flat rate of 60 per order.", aliases=("運費規則",))
    寫("Campaign Periods", "Campaigns run monthly. See [[Discount Calculation]].",
       links=("Discount Calculation",))
    寫("Unrelated Topic", "Nothing to do with pricing.")
    # Discount Calculation 寫入時 Shipping Rules 尚不存在，該連結的 target_slug
    # 會是 NULL；upsert_node 只解析自己的連結，全表重解要靠 resolve_edges()。
    resolve_edges(connection)
    yield connection
    connection.close()


def test_英文關鍵字命中對應頁(conn):
    result = run_explore(conn, "discounts", GraphConfig())

    assert next(h.page.slug for h in result.hits) == "Discount Calculation"


def test_中文術語經別名命中英文標題的頁(conn):
    # 這是 aliases 存在的理由，也是 CJK 分詞必須生效的證明。
    result = run_explore(conn, "運費規則", GraphConfig())

    assert "Shipping Rules" in [h.page.slug for h in result.hits]


def test_查無結果時_hits_為空且_top_score_為零(conn):
    result = run_explore(conn, "quantum entanglement", GraphConfig())

    assert result.hits == ()
    assert result.top_score == 0.0
    assert result.paths == {}
    assert result.blast == ()
    assert result.anchors == {}


def test_空查詢不送出_match_直接回空結果(conn):
    # 空的 MATCH 運算式是 SQL 語法錯誤，必須在送出前攔下。
    result = run_explore(conn, "   ", GraphConfig())

    assert result.hits == ()


def test_鄰居涵蓋雙向且標註關係(conn):
    result = run_explore(conn, "discounts", GraphConfig())
    鄰居 = {n.page.slug: n.relation for n in (*result.neighbors_full, *result.neighbors_brief)}

    assert 鄰居["Shipping Rules"] == "references"
    assert 鄰居["Campaign Periods"] == "referenced_by"
    # 與查詢無關且未被引用的頁不該混進來。
    assert "Unrelated Topic" not in 鄰居


def test_已經命中的頁不重複出現在鄰居中(conn):
    result = run_explore(conn, "discounts shipping", GraphConfig())
    命中 = {h.page.slug for h in result.hits}
    鄰居 = {n.page.slug for n in (*result.neighbors_full, *result.neighbors_brief)}

    assert 命中 & 鄰居 == set()


def test_預算充足時鄰居全數展開全文():
    hits = [_page("A", "short")]
    candidates = [Neighbor(_page("B", "short body"), "references")]

    full, brief = allocate_neighbors(hits, candidates, budget=8000)

    assert [n.page.slug for n in full] == ["B"]
    assert brief == []


def test_預算耗盡時鄰居退為簡要():
    hits = [_page("A", "x" * 4000)]
    candidates = [Neighbor(_page("B", "y" * 4000), "references")]

    full, brief = allocate_neighbors(hits, candidates, budget=1000)

    assert full == []
    assert [n.page.slug for n in brief] == ["B"]


def test_命中頁即使超出預算仍全文展開():
    # Spec 第 4.1 節第 3 點：命中頁一律全文，充分性優先於預算。
    hits = [_page("A", "x" * 100000)]

    full, brief = allocate_neighbors(hits, [], budget=100)

    assert full == []
    assert brief == []


def test_鄰居依剩餘預算逐一配置而非全有全無():
    hits = [_page("A", "x" * 100)]
    candidates = [
        Neighbor(_page("B", "y" * 400), "references"),
        Neighbor(_page("C", "z" * 4000), "references"),
    ]

    full, brief = allocate_neighbors(hits, candidates, budget=300)

    assert [n.page.slug for n in full] == ["B"]
    assert [n.page.slug for n in brief] == ["C"]


def test_中文字元的_token_估算高於同長度英文():
    # 中文一字約一 token，英文約四字元一 token；估算若不分語言，
    # 中文內容會嚴重低估而衝爆實際 context。
    assert estimate_tokens("折扣計算規則") == 6
    assert estimate_tokens("abcdefgh") == 2


def test_首段抽取只取第一個段落():
    body = "First paragraph line one.\nStill first.\n\nSecond paragraph."

    assert lead_paragraph(body) == "First paragraph line one.\nStill first."


def test_首段抽取在無空行時回傳全文():
    assert lead_paragraph("Only one line.") == "Only one line."


@pytest.mark.parametrize("depth", [1, 2])
def test_查詢整合已解析正向路徑反向影響及命中錨點(tmp_path, depth):
    connection = open_index(tmp_path / "graph.db")
    try:
        for slug, edges, code in [
            ("Needle", (Edge("relates_to", "Forward", None),
                        Edge("depends_on", "Missing", None)), ("Z.Symbol", "A.Symbol")),
            ("Forward", (Edge("overrides", "Farther", None),), ("Other.Symbol",)),
            ("Farther", (), ()),
            ("Affected", (Edge("depends_on", "Needle", None),), ()),
            ("Indirect", (Edge("exception_to", "Affected", None),), ()),
            ("Unrelated", (Edge("relates_to", "Needle", None),), ()),
        ]:
            upsert_node(
                connection, slug=slug, title=slug, node_type="rule", aliases=(),
                body="", path=f"nodes/{slug}.md", content_hash=slug, status="confirmed",
                updated_at="2026-08-14", parse_error=None, edges=edges, code=code,
                indexed_at=現在,
            )
        resolve_edges(connection)
        result = run_explore(connection, "Needle", GraphConfig(explore_depth=depth))
        assert [h.page.slug for h in result.hits] == ["Needle"]
        steps = [(s.target, s.edge_type, s.depth) for s in result.paths["Needle"]]
        assert steps == ([
            ("Forward", "relates_to", 1), ("Farther", "overrides", 2),
        ] if depth == 2 else [("Forward", "relates_to", 1)])
        blast = [(e.slug, e.edge_type, e.via) for e in result.blast]
        assert blast == ([
            ("Affected", "depends_on", ()), ("Indirect", "exception_to", ("Affected",)),
        ] if depth == 2 else [("Affected", "depends_on", ())])
        assert result.anchors == {
            "Needle": ("A.Symbol", "Z.Symbol"), "Forward": ("Other.Symbol",),
        }
    finally:
        connection.close()
