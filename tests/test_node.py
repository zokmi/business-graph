"""頁面解析與序列化的測試。"""
from __future__ import annotations

from datetime import date

import pytest

from business_graph_mcp.node import (
    NODE_DECISION,
    NODE_RULE,
    VALID_NODE_TYPES,
    NodeMeta,
    content_hash,
    extract_edges,
    parse_node,
    render_node,
    slugify,
)

# 頁面內容預設英文、術語保留中文並寫進 aliases（Spec §3.2「內容語言」），
# 測試資料刻意採用這種混語形態，因為那才是實際會進索引的樣子。
完整頁面 = """---
title: Discount Calculation
type: rule
aliases: [折扣計算規則, discount rules]
status: confirmed
updated: 2026-08-14
---

Tier discount and campaign discount do **not** stack — the larger one wins.
Shipping is excluded from discounts (see [[Shipping Rules]]).
"""


def test_解析完整頁面取得四個欄位():
    page = parse_node(完整頁面)

    assert page.parse_error is None
    assert page.meta is not None
    assert page.meta.title == "Discount Calculation"
    assert page.meta.node_type == NODE_RULE
    assert page.meta.aliases == ("折扣計算規則", "discount rules")
    assert page.meta.status == "confirmed"
    assert page.meta.updated == date(2026, 8, 14)
    assert page.body.startswith("Tier discount")
    assert tuple(edge.target for edge in page.edges) == ("Shipping Rules",)


def test_沒有_front_matter_時仍保留內文並記錄錯誤():
    # Spec §9 原則 2：一頁壞掉不得拖垮 server，body 仍須進索引。
    page = parse_node("這頁沒有 front matter，但內容仍有價值。")

    assert page.meta is None
    assert page.parse_error is not None
    assert "front matter" in page.parse_error
    assert page.body == "這頁沒有 front matter，但內容仍有價值。"


def test_front_matter_是壞掉的_yaml_時記錄錯誤而不拋例外():
    page = parse_node("---\ntitle: [未閉合\n---\n\n內文\n")

    assert page.meta is None
    assert page.parse_error is not None
    assert page.body == "內文"


def test_status_不合法時視為解析失敗():
    壞的 = "---\ntitle: X\ntype: rule\nstatus: maybe\nupdated: 2026-08-14\n---\n\n內文\n"
    page = parse_node(壞的)

    assert page.meta is None
    assert "status" in (page.parse_error or "")


def test_缺少_title_時視為解析失敗():
    page = parse_node("---\nstatus: confirmed\nupdated: 2026-08-14\n---\n\n內文\n")

    assert page.meta is None
    assert "title" in (page.parse_error or "")


def test_aliases_可省略且預設為空():
    page = parse_node(
        "---\ntitle: X\ntype: rule\nstatus: inferred\nupdated: 2026-08-14\n---\n\n內文\n"
    )

    assert page.meta is not None
    assert page.meta.aliases == ()


def test_render_後再_parse_可還原():
    meta = NodeMeta(
        title="Membership Tiers",
        node_type=NODE_RULE,
        aliases=("會員等級",),
        status="inferred",
        updated=date(2026, 1, 2),
    )
    text = render_node(meta, "Three tiers: Bronze, Silver, Gold.")
    page = parse_node(text)

    assert page.meta == meta
    assert page.body == "Three tiers: Bronze, Silver, Gold."


def test_render_標題含冒號與空白時仍可_round_trip():
    meta = NodeMeta(
        title="ADR-3: Pricing Model",
        node_type=NODE_DECISION,
        aliases=(),
        status="confirmed",
        updated=date(2026, 9, 27),
    )

    text = render_node(meta, "Decision body.")

    assert "title: 'ADR-3: Pricing Model'" in text
    assert parse_node(text).meta == meta


def test_render_在無別名時不輸出_aliases_欄位():
    meta = NodeMeta(
        title="X", node_type=NODE_RULE, aliases=(), status="confirmed", updated=date(2026, 1, 2)
    )

    assert "aliases" not in render_node(meta, "內文")


def test_抽取邊涵蓋重複與多個():
    edges = extract_edges("見 [[A]] 與 [[B]]，再看一次 [[A]]。")

    # 去重但保留首次出現順序：待辦與懸空連結報告的可讀性依賴穩定順序。
    assert tuple(edge.target for edge in edges) == ("A", "B")


def test_抽取邊忽略未閉合目標並保留空白目標的錯誤():
    edges = extract_edges("[[未閉合 與 [[]] 與 [[  ]]")

    assert len(edges) == 1
    assert edges[0].target == ""
    assert edges[0].error is not None


def test_slugify_保留英文空白與中文並去除路徑危險字元():
    # 標題預設英文且含空白，slug 必須原樣保留空白（檔名允許），
    # 中文術語標題也要能直接當檔名，不轉拼音。
    assert slugify("Discount Calculation") == "Discount Calculation"
    assert slugify("折扣計算規則") == "折扣計算規則"
    assert slugify("A/B") == "A-B"
    assert slugify("  前後空白  ") == "前後空白"
    assert slugify("a\\b:c*d?e") == "a-b-c-d-e"


def test_content_hash_對相同內容穩定且對不同內容相異():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    # 長度固定，方便附在待辦文字中而不佔版面。
    assert len(content_hash("abc")) == 16


_有效節點 = (
    "---\n"
    "title: Discount Calculation\n"
    "type: rule\n"
    "status: confirmed\n"
    "updated: 2026-09-18\n"
    "---\n"
    "\n"
    "內文。\n"
)


def test_解析出節點型別():
    parsed = parse_node(_有效節點)
    assert parsed.parse_error is None
    assert parsed.meta is not None
    assert parsed.meta.node_type == NODE_RULE


def test_缺少_type_視為解析失敗():
    text = _有效節點.replace("type: rule\n", "")
    parsed = parse_node(text)
    assert parsed.meta is None
    assert "type" in (parsed.parse_error or "")
    # 內文仍須保留，一頁壞掉不得讓它從索引中消失
    assert parsed.body == "內文。"


def test_type_不在合法值之列時錯誤訊息列出全部合法值():
    text = _有效節點.replace("type: rule", "type: policy")
    parsed = parse_node(text)
    assert parsed.meta is None
    for 合法值 in VALID_NODE_TYPES:
        assert 合法值 in (parsed.parse_error or "")


def test_render_node_輸出的_type_在_title_之後():
    meta = NodeMeta(
        title="Discount Calculation",
        node_type=NODE_RULE,
        aliases=("折扣計算",),
        status="confirmed",
        updated=date(2026, 9, 18),
    )
    text = render_node(meta, "內文。")
    lines = text.splitlines()
    assert lines[1] == "title: Discount Calculation"
    assert lines[2] == "type: rule"
    # round-trip：render 出來的東西必須解析得回去
    assert parse_node(text).meta == meta


def test_解析_code_錨點():
    text = _有效節點.replace(
        "status: confirmed\n",
        "code: [OrderService.CalculateDiscount, DiscountPolicy]\nstatus: confirmed\n",
    )
    parsed = parse_node(text)
    assert parsed.meta is not None
    assert parsed.meta.code == ("OrderService.CalculateDiscount", "DiscountPolicy")


def test_沒有_code_時為空_tuple():
    parsed = parse_node(_有效節點)
    assert parsed.meta is not None
    assert parsed.meta.code == ()


def test_code_不是陣列時解析失敗():
    text = _有效節點.replace("status:", "code: OrderService\nstatus:")
    parsed = parse_node(text)
    assert parsed.meta is None
    assert "code" in (parsed.parse_error or "")
    assert parsed.body == "內文。"


@pytest.mark.parametrize("raw_code", ["null", "false", "0", '""'])
def test_code_存在且為_falsy_非陣列時解析失敗(raw_code):
    text = _有效節點.replace("status:", f"code: {raw_code}\nstatus:")
    parsed = parse_node(text)
    assert parsed.meta is None
    assert "code" in (parsed.parse_error or "")
    assert parsed.body == "內文。"


def test_render_node_的_code_可以_round_trip():
    meta = NodeMeta(
        title="Discount Calculation",
        node_type=NODE_RULE,
        aliases=("折扣計算",),
        code=("OrderService.CalculateDiscount",),
        status="confirmed",
        updated=date(2026, 9, 18),
    )
    text = render_node(meta, "內文。")
    assert parse_node(text).meta == meta
    assert "aliases: [折扣計算]\ncode: [OrderService.CalculateDiscount]\nstatus:" in text


def test_render_特殊_yaml_清單項目可還原():
    meta = NodeMeta(
        title="Pricing", node_type=NODE_RULE,
        aliases=("tier: VIP", "A, B", "[literal]", "true", "hash # mark"),
        code=("Rule: apply", "A, B", "#symbol"),
        status="confirmed", updated=date(2026, 9, 18),
    )
    assert parse_node(render_node(meta, "Body.")).meta == meta


def test_code_陣列項目轉字串並去除空白():
    text = _有效節點.replace("status:", 'code: [" DiscountPolicy ", " ", 123]\nstatus:')
    parsed = parse_node(text)
    assert parsed.meta is not None
    assert parsed.meta.code == ("DiscountPolicy", "123")


def test_建構時省略_code_預設為空且不輸出欄位():
    meta = NodeMeta(
        title="X", node_type=NODE_RULE, aliases=(), status="confirmed", updated=date(2026, 9, 18)
    )
    assert meta.code == ()
    assert "code:" not in render_node(meta, "內文。")
