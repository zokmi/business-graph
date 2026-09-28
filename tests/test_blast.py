"""反向影響半徑、最短路徑與穩定排序的測試。"""

from itertools import permutations

import pytest

from business_graph_mcp.blast import BlastEntry, blast_radius
from business_graph_mcp.edges import EDGE_DEPENDS_ON, EDGE_EXCEPTION_TO, EDGE_OVERRIDES


def test_直接依賴者被列出():
    # A depends_on B：改 B 會影響 A。
    entries = blast_radius([("A", EDGE_DEPENDS_ON, "B")], ["B"], depth=2)
    assert [(e.slug, e.edge_type, e.via) for e in entries] == [
        ("A", EDGE_DEPENDS_ON, ())
    ]


def test_跨邊型別可傳遞並記錄中繼節點():
    impact = [
        ("VIP", EDGE_OVERRIDES, "General"),
        ("General", EDGE_DEPENDS_ON, "Tier"),
    ]
    entries = blast_radius(impact, ["Tier"], depth=2)
    對照 = {e.slug: e for e in entries}
    assert 對照["General"].via == ()
    assert 對照["VIP"].via == ("General",)
    assert 對照["VIP"].edge_type == EDGE_OVERRIDES


def test_深度上限截斷更遠的節點():
    impact = [
        ("C", EDGE_DEPENDS_ON, "B"),
        ("B", EDGE_DEPENDS_ON, "A"),
        ("D", EDGE_DEPENDS_ON, "C"),
    ]
    slugs = {e.slug for e in blast_radius(impact, ["A"], depth=2)}
    assert slugs == {"B", "C"}


def test_環狀依賴不無限遞迴():
    impact = [
        ("A", EDGE_DEPENDS_ON, "B"),
        ("B", EDGE_DEPENDS_ON, "A"),
    ]
    entries = blast_radius(impact, ["A"], depth=5)
    assert [e.slug for e in entries] == ["B"]


def test_種子節點自己不列入():
    impact = [("A", EDGE_DEPENDS_ON, "B"), ("B", EDGE_EXCEPTION_TO, "A")]
    slugs = {e.slug for e in blast_radius(impact, ["A", "B"], depth=3)}
    assert slugs == set()


def test_同一節點只出現一次且取最短路徑():
    impact = [
        ("X", EDGE_DEPENDS_ON, "A"),
        ("Y", EDGE_DEPENDS_ON, "A"),
        ("X", EDGE_OVERRIDES, "Y"),
    ]
    entries = [e for e in blast_radius(impact, ["A"], depth=3) if e.slug == "X"]
    assert len(entries) == 1
    assert entries[0].via == ()


def test_沒有影響邊時回傳空():
    assert blast_radius([], ["A"], depth=2) == ()


@pytest.mark.parametrize("depth", [0, -1])
def test_非正深度不展開(depth):
    assert blast_radius([("B", EDGE_DEPENDS_ON, "A")], ["A"], depth) == ()


def test_沒有種子時回傳空():
    assert blast_radius([("B", EDGE_DEPENDS_ON, "A")], [], depth=2) == ()


def test_不沿正向依賴走訪():
    assert blast_radius([("A", EDGE_DEPENDS_ON, "B")], ["A"], depth=2) == ()


def test_三種邊接續且中繼路徑由近而遠():
    impact = [
        ("Z", EDGE_DEPENDS_ON, "Seed"),
        ("Y", EDGE_OVERRIDES, "Z"),
        ("X", EDGE_EXCEPTION_TO, "Y"),
    ]
    assert blast_radius(impact, ["Seed"], depth=3) == (
        BlastEntry("X", EDGE_EXCEPTION_TO, ("Z", "Y")),
        BlastEntry("Y", EDGE_OVERRIDES, ("Z",)),
        BlastEntry("Z", EDGE_DEPENDS_ON, ()),
    )


def test_多個種子仍取全域最短路徑():
    impact = [
        ("X", EDGE_DEPENDS_ON, "A"),
        ("Target", EDGE_OVERRIDES, "X"),
        ("Target", EDGE_EXCEPTION_TO, "Z"),
    ]
    assert blast_radius(impact, ["A", "Z", "A"], depth=3) == (
        BlastEntry("Target", EDGE_EXCEPTION_TO, ()),
        BlastEntry("X", EDGE_DEPENDS_ON, ()),
    )


def test_同長路徑與重複邊的結果不受輸入順序影響():
    impact = [
        ("X", EDGE_DEPENDS_ON, "A"),
        ("Y", EDGE_DEPENDS_ON, "Z"),
        ("Target", EDGE_OVERRIDES, "Y"),
        ("Target", EDGE_EXCEPTION_TO, "X"),
    ]
    for edges in permutations(impact):
        for seeds in [("A", "Z"), ("Z", "A")]:
            assert blast_radius((*edges, edges[0]), seeds, depth=3) == (
                BlastEntry("Target", EDGE_EXCEPTION_TO, ("X",)),
                BlastEntry("X", EDGE_DEPENDS_ON, ()),
                BlastEntry("Y", EDGE_DEPENDS_ON, ()),
            )


def test_同種子的分支與平行邊以固定順序選路():
    impact = [
        ("Y", EDGE_DEPENDS_ON, "Seed"),
        ("X", EDGE_DEPENDS_ON, "Seed"),
        ("Target", EDGE_OVERRIDES, "Y"),
        ("Target", EDGE_OVERRIDES, "X"),
        ("Target", EDGE_EXCEPTION_TO, "X"),
    ]
    for edges in [impact, list(reversed(impact))]:
        assert blast_radius(edges, ["Seed"], depth=2) == (
            BlastEntry("Target", EDGE_EXCEPTION_TO, ("X",)),
            BlastEntry("X", EDGE_DEPENDS_ON, ()),
            BlastEntry("Y", EDGE_DEPENDS_ON, ()),
        )
