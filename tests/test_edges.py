"""內文型別化邊解析的測試。"""
from __future__ import annotations

import pytest

from business_graph_mcp.edges import (
    EDGE_DEPENDS_ON,
    EDGE_EXCEPTION_TO,
    EDGE_OVERRIDES,
    EDGE_RELATES_TO,
    INLINE_EDGE_TYPES,
    parse_edge_target,
)
from business_graph_mcp.node import extract_edges


def test_合法前綴解析成對應的邊型別():
    edge = parse_edge_target("overrides:VIP Discount Tiers")
    assert edge.edge_type == EDGE_OVERRIDES
    assert edge.target == "VIP Discount Tiers"
    assert edge.error is None


def test_四種內文前綴全部可用():
    對照 = {
        "depends_on": EDGE_DEPENDS_ON,
        "overrides": EDGE_OVERRIDES,
        "exception_to": EDGE_EXCEPTION_TO,
        "relates_to": EDGE_RELATES_TO,
    }
    assert set(對照.values()) == set(INLINE_EDGE_TYPES)
    for 前綴, 型別 in 對照.items():
        assert parse_edge_target(f"{前綴}:Target").edge_type == 型別


def test_無前綴退化為_relates_to():
    edge = parse_edge_target("Shipping Rules")
    assert edge.edge_type == EDGE_RELATES_TO
    assert edge.target == "Shipping Rules"
    assert edge.error is None


def test_標題含冒號時整串當標題():
    # 前綴含大寫與連字號，不符合「全小寫字母與底線」，故不是關係前綴
    edge = parse_edge_target("ADR-3: Pricing Model")
    assert edge.edge_type == EDGE_RELATES_TO
    assert edge.target == "ADR-3: Pricing Model"
    assert edge.error is None


def test_冒號後有空白時整串當標題():
    edge = parse_edge_target("note: something")
    assert edge.edge_type == EDGE_RELATES_TO
    assert edge.target == "note: something"
    assert edge.error is None


def test_打錯字的前綴回報錯誤並列出合法值():
    edge = parse_edge_target("overide:VIP Discount Tiers")
    assert edge.error is not None
    for 合法值 in INLINE_EDGE_TYPES:
        assert 合法值 in edge.error


def test_implemented_by_寫在內文被拒並指向_code_欄位():
    edge = parse_edge_target("implemented_by:OrderService.Calculate")
    assert edge.error is not None
    assert "code" in edge.error


@pytest.mark.parametrize("raw", ["depends_on:", "overrides: ", "exception_to:\t", "relates_to:\u3000"])
def test_已知關係的空目標被拒絕(raw):
    edge = parse_edge_target(raw)
    assert edge.error is not None
    assert "目標不可為空" in edge.error


@pytest.mark.parametrize("raw", ["note:\tX", "note:\u3000X", "note:"])
def test_non_prefix_title(raw):
    edge = parse_edge_target(raw)
    assert edge.target == raw
    assert edge.edge_type == EDGE_RELATES_TO
    assert edge.error is None


@pytest.mark.parametrize("raw", ["implemented_by: OrderService.Calculate", "implemented_by:\tSymbol", "implemented_by:"])
def test_implemented_by_不因目標前空白或空目標繞過檢查(raw):
    edge = parse_edge_target(raw)
    assert edge.error is not None
    assert "code" in edge.error


def test_已知關係允許目標前空白():
    edge = parse_edge_target("depends_on: Shipping Rules")
    assert edge.edge_type == EDGE_DEPENDS_ON
    assert edge.target == "Shipping Rules"
    assert edge.error is None


@pytest.mark.parametrize(
    "body",
    [
        "[[Target]] [[typo:Target]]",
        "[[typo:Target]] [[Target]]",
    ],
)
def test_invalid_edge_is_not_hidden(body):
    edges = extract_edges(body)
    assert len(edges) == 2
    assert sum(edge.error is not None for edge in edges) == 1


def test_multiple_types_preserved_and_exact_duplicates_removed():
    edges = extract_edges("[[depends_on:X]] [[overrides:X]] [[depends_on:X]]")
    assert [(e.edge_type, e.target) for e in edges] == [
        ("depends_on", "X"),
        ("overrides", "X"),
    ]
