"""explore 回應組裝的測試。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from business_graph_mcp.banners import Notices
from business_graph_mcp.blast import BlastEntry
from business_graph_mcp.db.queries import NodeRow
from business_graph_mcp.edges import EDGE_DEPENDS_ON, EDGE_OVERRIDES
from business_graph_mcp.explore import ExploreResult, Hit, Neighbor, PathStep
from business_graph_mcp.formatting import format_explore
from business_graph_mcp.sync import SyncResult

空同步 = SyncResult(0, 0, 0, 0, (), ())
空提醒 = Notices((), ())


def _page(slug: str, body: str = "Body text.") -> NodeRow:
    """組出測試用的 NodeRow。"""
    return NodeRow(
        id=1, slug=slug, title=slug, node_type="rule", aliases=(), body=body,
        path=f"nodes/{slug}.md", content_hash="a3f1c8d20e4b7f91",
        status="confirmed", updated_at="2026-08-14", parse_error=None,
    )


@pytest.fixture
def 基本結果() -> ExploreResult:
    """一個只有單一命中、其餘區塊皆空的 explore 結果。"""
    return ExploreResult(
        hits=(Hit(_page("Discount Calculation"), 5.0),),
        neighbors_full=(), neighbors_brief=(), top_score=5.0,
        paths={}, blast=(), anchors={},
    )


def test_關係路徑區塊列出邊型別與深度縮排(基本結果):
    result = replace(基本結果, paths={
        "Discount Calculation": (
            PathStep("VIP Discount Tiers", EDGE_OVERRIDES, 1),
            PathStep("Customer Tier", EDGE_DEPENDS_ON, 2),
        ),
    })
    text = format_explore(result, 空提醒, 空同步, {})
    assert "--overrides--> VIP Discount Tiers" in text
    assert "--depends_on--> Customer Tier" in text
    第一跳 = next(line for line in text.splitlines() if "VIP Discount Tiers" in line)
    第二跳 = next(line for line in text.splitlines() if "Customer Tier" in line)
    assert len(第二跳) - len(第二跳.lstrip()) > len(第一跳) - len(第一跳.lstrip())


def test_影響半徑列出節點數與經由路徑(基本結果):
    result = replace(基本結果, blast=(
        BlastEntry("Order Total", EDGE_DEPENDS_ON, ()),
        BlastEntry("Invoice Rounding", EDGE_DEPENDS_ON, ("Order Total",)),
        BlastEntry("Invoice Export", EDGE_DEPENDS_ON, ("Order Total", "Invoice Rounding")),
    ))
    text = format_explore(result, 空提醒, 空同步, {})
    assert "影響半徑：3 個節點" in text
    assert "Order Total (depends_on)" in text
    assert "Invoice Rounding (depends_on，經 Order Total)" in text
    assert "Invoice Export (depends_on，經 Order Total → Invoice Rounding)" in text


def test_空區塊不輸出標題或空節點(基本結果):
    result = replace(基本結果, paths={"Empty Path": ()}, anchors={"Empty Anchor": ()})
    text = format_explore(result, 空提醒, 空同步, {})
    for omitted in ("關係路徑", "影響半徑", "程式碼錨點", "Empty Path", "Empty Anchor"):
        assert omitted not in text


def test_錨點已解析時顯示位置_未解析時標示未解析(基本結果):
    result = replace(基本結果, anchors={
        "Discount Calculation": ("OrderService.Calculate", "DiscountPolicy"),
    })
    text = format_explore(
        result, 空提醒, 空同步,
        {"OrderService.Calculate": "src/Services/OrderService.cs:118-164"},
    )
    assert "OrderService.Calculate — src/Services/OrderService.cs:118-164" in text
    assert "DiscountPolicy — 未解析" in text


def test_新增區塊排在鄰居之後待辦之前_依節點排序且略過空項(基本結果):
    result = replace(
        基本結果,
        neighbors_full=(Neighbor(_page("Neighbor", "鄰居全文。"), "references"),),
        paths={
            "Z Root": (PathStep("Z Target", EDGE_OVERRIDES, 1),),
            "Empty Path": (),
            "A Root": (PathStep("A Target", EDGE_DEPENDS_ON, 1),),
        },
        blast=(BlastEntry("Blast Target", EDGE_DEPENDS_ON, ()),),
        anchors={"Z Root": ("Z.Symbol",), "Empty Anchor": (), "A Root": ("A.Symbol",)},
    )
    text = format_explore(result, Notices(("前綴警告",), ("後綴待辦",)), 空同步, {})
    sections = ("前綴警告", "Body text.", "鄰居全文。", "## 關係路徑", "影響半徑", "## 程式碼錨點", "後綴待辦")
    assert [text.index(section) for section in sections] == sorted(text.index(section) for section in sections)
    assert text.index("A Target") < text.index("Z Target")
    assert text.index("A.Symbol") < text.index("Z.Symbol")
    assert "Empty Path" not in text
    assert "Empty Anchor" not in text


def test_前綴警告排在命中內容之前():
    # Spec 第 4.3 節:前綴影響「怎麼讀」,必須先於內容出現。
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(Hit(_page("A"), 5.0),), neighbors_full=(),
        neighbors_brief=(), top_score=5.0,
    )
    notices = Notices(prefix=("⚠ 這是警告",), todos=())

    text = format_explore(result, notices, 空同步, {})

    assert text.index("這是警告") < text.index("Body text.")


def test_後綴待辦排在命中內容之後():
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(Hit(_page("A"), 5.0),), neighbors_full=(),
        neighbors_brief=(), top_score=5.0,
    )
    notices = Notices(prefix=(), todos=("用 business_write 補上。",))

    text = format_explore(result, notices, 空同步, {})

    assert text.index("Body text.") < text.index("business_write")


def test_命中頁輸出全文與_base_hash():
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(Hit(_page("A", "完整內文在此。"), 5.0),),
        neighbors_full=(), neighbors_brief=(), top_score=5.0,
    )

    text = format_explore(result, Notices((), ()), 空同步, {})

    assert "完整內文在此。" in text
    # base_hash 必須附上,agent 要更新時才不需再查一次。
    assert "a3f1c8d20e4b7f91" in text


def test_可寫頁標題顯示真實_title_並保留_slug():
    page = replace(_page("ADR-3- Pricing Model"), title="ADR-3: Pricing Model")
    result = ExploreResult(
        hits=(Hit(page, 5.0),), neighbors_full=(Neighbor(page, "references"),),
        neighbors_brief=(), top_score=5.0, paths={}, blast=(), anchors={},
    )
    text = format_explore(result, 空提醒, 空同步, {})
    assert text.count("### ADR-3: Pricing Model · slug: ADR-3- Pricing Model") == 2


def test_路徑顯示實際父節點與平行邊(基本結果):
    result = replace(基本結果, paths={"Discount Calculation": (
        PathStep("General", "depends_on", 1, "Discount Calculation"),
        PathStep("General", "overrides", 1, "Discount Calculation"),
        PathStep("Tier", "depends_on", 2, "General"),
    )})
    text = format_explore(result, 空提醒, 空同步, {})
    assert "Discount Calculation --depends_on--> General" in text
    assert "Discount Calculation --overrides--> General" in text
    assert "General --depends_on--> Tier" in text


def test_全文鄰居輸出內容而簡要鄰居只有首段():
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(Hit(_page("A"), 5.0),),
        neighbors_full=(Neighbor(_page("B", "鄰居全文。"), "references"),),
        neighbors_brief=(Neighbor(_page("C", "首段。\n\n第二段不該出現。"), "referenced_by"),),
        top_score=5.0,
    )

    text = format_explore(result, Notices((), ()), 空同步, {})

    assert "鄰居全文。" in text
    assert "首段。" in text
    assert "第二段不該出現。" not in text


def test_簡要鄰居不附_base_hash_且提示先完整查詢():
    # 簡要鄰居只顯示首段,agent 尚未讀過全文;若附上 base_hash,
    # agent 可能拿著只讀過首段的認知直接覆寫整頁,那正是樂觀鎖要防止的失敗。
    # 這條測試釘住「刻意不附」這個設計決定,避免日後被當成漏寫而「修好」。
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(Hit(_page("A"), 5.0),),
        neighbors_full=(),
        neighbors_brief=(Neighbor(_page("C", "首段。"), "referenced_by"),),
        top_score=5.0,
    )

    text = format_explore(result, Notices((), ()), 空同步, {})

    # 命中頁本身仍附「base_hash: <值>」,簡要鄰居的提示句雖然提到
    # base_hash 這個詞（要求 agent 去查),但不會附上實際的雜湊值——
    # 用「base_hash:」(含冒號,即 _page_heading 附值的格式)確認只來自命中頁,
    # 而非誤植進簡要鄰居區塊。
    assert text.count("base_hash:") == 1
    assert "先呼叫 business_explore" in text


def test_零命中時輸出明確訊息而非空白():
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(), neighbors_full=(), neighbors_brief=(), top_score=0.0,
    )

    text = format_explore(result, Notices((), ("補上來。",)), 空同步, {})

    assert "查無" in text
    assert "補上來。" in text


def test_同步忽略的檔案會被列出而非靜默略過():
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(), neighbors_full=(), neighbors_brief=(), top_score=0.0,
    )
    同步 = SyncResult(0, 0, 0, 0, (), ("archive/Old Rule.md",))

    text = format_explore(result, Notices((), ()), 同步, {})

    assert "archive/Old Rule.md" in text


def test_同步失敗的頁會被列出():
    result = ExploreResult(
        paths={}, blast=(), anchors={},
        hits=(), neighbors_full=(), neighbors_brief=(), top_score=0.0,
    )
    同步 = SyncResult(0, 0, 0, 0, ("Broken",), ())

    text = format_explore(result, Notices((), ()), 同步, {})

    assert "Broken" in text
