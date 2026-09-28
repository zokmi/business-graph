"""business_explore 工具與 server instructions 的測試。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from business_graph_mcp.server import INSTRUCTIONS, SERVER_NAME, create_server
from tests.conftest import ToolCallError, call_tool


@pytest.mark.parametrize("query", ["HiddenAlias", "UniqueRoot"])
async def test_完整節點可由查詢中繼資料寫回且保留別名與錨點(tmp_path, query):
    from business_graph_mcp.node import parse_node

    nodes = tmp_path / ".bgraph" / "nodes"
    nodes.mkdir(parents=True)
    (nodes / "Root.md").write_text(
        "---\ntitle: Root\ntype: rule\nstatus: confirmed\nupdated: 2026-09-28\n"
        "---\n\nUniqueRoot links [[HiddenAlias]].\n", encoding="utf-8",
    )
    path = nodes / "Choice.md"
    path.write_text(
        '---\ntitle: Choice\ntype: decision\naliases: [HiddenAlias, "中文,別名"]\n'
        'code: [Policy.Decide]\nstatus: confirmed\nupdated: 2026-09-28\n'
        '---\n\nOriginal decision.\n', encoding="utf-8",
    )
    mcp = create_server()
    result = await call_tool(mcp, "business_explore", {
        "query": query, "project_path": str(tmp_path),
    })
    text = result["result"]
    section = text.split("### Choice · slug: Choice", 1)[1].split("\n##", 1)[0]
    metadata_match = re.search(r"```json\n(.*?)\n```", section, re.S)
    assert metadata_match is not None, "完整節點必須提供可寫回的中繼資料"
    metadata = json.loads(metadata_match.group(1))
    assert metadata == {
        "type": "decision", "aliases": ["HiddenAlias", "中文,別名"],
        "code": ["Policy.Decide"],
    }
    digest = re.search(r"base_hash: (\w+)", section).group(1)
    await call_tool(mcp, "business_write", {
        "project_path": str(tmp_path), "title": "Choice", "status": "confirmed",
        "content": "Corrected decision.", "base_hash": digest, **metadata,
    })
    parsed = parse_node(path.read_text(encoding="utf-8"))
    assert parsed.meta.node_type == "decision"
    assert parsed.meta.aliases == ("HiddenAlias", "中文,別名")
    assert parsed.meta.code == ("Policy.Decide",)
    assert parsed.body == "Corrected decision."


async def test_伺服器名稱與規格一致():
    assert SERVER_NAME == "business-graph"


def test_instructions_載明三條義務且不可遺漏():
    # 這段文字是「agent 自主寫入」機制的第一個作用點，
    # 以快照方式固定，避免日後修改時無意間刪掉任何一條。
    assert "先呼叫 business_explore" in INSTRUCTIONS
    assert "必須在該次工作結束前呼叫 business_write 寫回" in INSTRUCTIONS
    assert "必須呼叫 business_write 更正" in INSTRUCTIONS
    assert "這是義務，不是建議" in INSTRUCTIONS


def test_instructions_載明內容語言與_status_規則():
    assert "預設以英文撰寫" in INSTRUCTIONS
    assert "aliases" in INSTRUCTIONS
    assert "confirmed" in INSTRUCTIONS and "inferred" in INSTRUCTIONS


async def test_查得到內容時回傳全文與_base_hash(graph):
    mcp = create_server()

    result = await call_tool(
        mcp, "business_explore", {"query": "discounts", "project_path": str(graph)}
    )

    assert "Discount Calculation" in result["result"]
    assert "do not stack" in result["result"]
    assert "base_hash" in result["result"]


async def test_中文術語經別名查得到英文標題的頁(graph):
    mcp = create_server()

    result = await call_tool(
        mcp, "business_explore", {"query": "運費規則", "project_path": str(graph)}
    )

    assert "Shipping Rules" in result["result"]


async def test_alias_更新後路徑與_lint_同步反映入站連結(graph):
    nodes = graph / ".bgraph" / "nodes"
    source = nodes / "Source.md"
    target = nodes / "Target.md"
    source.write_text(
        "---\ntitle: Source\ntype: rule\nstatus: confirmed\nupdated: 2026-09-27\n"
        "---\n\nSee [[depends_on:Tier Alias]].\n",
        encoding="utf-8",
    )
    target.write_text(
        "---\ntitle: Target\ntype: entity\nstatus: confirmed\nupdated: 2026-09-27\n"
        "---\n\nTarget body.\n",
        encoding="utf-8",
    )
    mcp = create_server()
    lint_args = {"project_path": str(graph)}
    assert "[[Tier Alias]]" in (await call_tool(mcp, "business_lint", lint_args))["result"]

    target.write_text(
        "---\ntitle: Target\ntype: entity\naliases: [Tier Alias]\n"
        "status: confirmed\nupdated: 2026-09-27\n---\n\nTarget body.\n",
        encoding="utf-8",
    )
    explored = await call_tool(
        mcp, "business_explore", {"query": "Source", "project_path": str(graph)}
    )
    assert "Source --depends_on--> Target" in explored["result"]

    target.write_text(
        "---\ntitle: Target\ntype: entity\nstatus: confirmed\nupdated: 2026-09-27\n"
        "---\n\nTarget body.\n",
        encoding="utf-8",
    )
    assert "[[Tier Alias]]" in (await call_tool(mcp, "business_lint", lint_args))["result"]


async def test_精確標題查詢保留被引用頁的影響半徑(graph):
    nodes = graph / ".bgraph" / "nodes"
    for title, body in (
        ("Customer Tier", "Tier root."),
        ("General Discount", "See [[depends_on:Customer Tier]]."),
        ("VIP Discount", "See [[overrides:General Discount]]."),
    ):
        (nodes / f"{title}.md").write_text(
            f"---\ntitle: {title}\ntype: rule\nstatus: confirmed\n"
            f"updated: 2026-09-27\n---\n\n{body}\n", encoding="utf-8"
        )
    result = await call_tool(
        create_server(), "business_explore",
        {"query": "Customer Tier", "project_path": str(graph)},
    )
    text = result["result"]
    assert "General Discount (depends_on)" in text
    assert "VIP Discount (overrides，經 General Discount)" in text


async def test_命中推測且過期的頁時同時出現警告與待辦(graph):
    mcp = create_server()

    result = await call_tool(
        mcp, "business_explore", {"query": "shipping", "project_path": str(graph)}
    )
    text = result["result"]

    assert "推測" in text
    assert "未更新" in text
    assert "business_write" in text


async def test_查無結果時提示寫回並累計未命中(graph):
    mcp = create_server()
    args = {"query": "quantum entanglement", "project_path": str(graph)}

    第一次 = await call_tool(mcp, "business_explore", args)
    assert "查無" in 第一次["result"]

    for _ in range(2):
        最新 = await call_tool(mcp, "business_explore", args)

    # 累計達門檻（預設 3）後升級為高價值缺口。
    assert "缺口" in 最新["result"]


async def test_知識庫不存在時回明確訊息而非例外(tmp_path):
    # Spec 第九節原則 3：.bgraph/ 不存在不是錯誤。
    mcp = create_server()

    result = await call_tool(
        mcp, "business_explore", {"query": "anything", "project_path": str(tmp_path)}
    )

    assert "尚未建立知識圖" in result["result"]


async def test_設定檔錯誤時以可診斷的訊息失敗(graph):
    (graph / ".bgraph" / "bgraph.toml").write_text("stale_dayz = 30\n", encoding="utf-8")
    mcp = create_server()

    with pytest.raises(ToolCallError) as exc:
        await call_tool(
            mcp, "business_explore", {"query": "discounts", "project_path": str(graph)}
        )

    assert "stale_dayz" in str(exc.value)


async def test_explore_顯示已解析及未解析的錨點(graph, monkeypatch):
    (graph / ".bgraph" / "nodes" / "Discount Calculation.md").write_text(
        "---\ntitle: Discount Calculation\ntype: rule\n"
        "code: [OrderService.Calculate, Missing.Symbol]\n"
        "status: confirmed\nupdated: 2026-09-18\n---\n\nTier discounts.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "business_graph_mcp.tools.explore.find_codegraph_db", lambda _: Path("x")
    )

    def resolve(db_path, symbols):
        assert db_path == Path("x")
        assert symbols == ["Missing.Symbol", "OrderService.Calculate"]
        return {"OrderService.Calculate": "src/OrderService.cs:10-20"}

    monkeypatch.setattr("business_graph_mcp.tools.explore.resolve_symbols", resolve)
    text = (await call_tool(
        create_server(), "business_explore", {"query": "discount", "project_path": str(graph)}
    ))["result"]

    assert "OrderService.Calculate — src/OrderService.cs:10-20" in text
    assert "Missing.Symbol — 未解析" in text


async def test_explore_沒有_codegraph_索引仍顯示未解析錨點及內文(graph, monkeypatch):
    (graph / ".bgraph" / "nodes" / "Discount Calculation.md").write_text(
        "---\ntitle: Discount Calculation\ntype: rule\ncode: [Missing.Symbol]\n"
        "status: confirmed\nupdated: 2026-09-18\n---\n\nTier discounts.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("business_graph_mcp.tools.explore.find_codegraph_db", lambda _: None)
    text = (await call_tool(
        create_server(), "business_explore", {"query": "discount", "project_path": str(graph)}
    ))["result"]

    assert "Missing.Symbol — 未解析" in text
    assert "Tier discounts." in text
    assert "base_hash" in text


async def test_慢速同步不阻塞事件迴圈(graph, monkeypatch):
    import asyncio
    import threading
    import time

    import business_graph_mcp.engine as engine

    entered = threading.Event()
    release = threading.Event()
    real_sync = engine.sync_index

    def slow_sync(*args, **kwargs):
        entered.set()
        assert release.wait(2), '測試同步等待逾時'
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(engine, 'sync_index', slow_sync)
    mcp = create_server()
    task = asyncio.create_task(call_tool(mcp, 'business_explore', {
        'query': 'discounts', 'project_path': str(graph),
    }))
    try:
        start = time.monotonic()
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.02)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
        await task


async def test_取消等待後工作執行緒完成前仍持有工作區鎖(graph, monkeypatch):
    import asyncio
    import threading

    import business_graph_mcp.engine as engine

    entered = threading.Event()
    release = threading.Event()
    real_sync = engine.sync_index
    calls = 0

    def slow_first_sync(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2), '測試同步等待逾時'
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(engine, 'sync_index', slow_first_sync)
    mcp = create_server()
    args = {'query': 'discounts', 'project_path': str(graph)}
    first = asyncio.create_task(call_tool(mcp, 'business_explore', args))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(call_tool(mcp, 'business_explore', args))
        await asyncio.sleep(0.1)
        assert not second.done()
    finally:
        release.set()
        await asyncio.gather(first, *( [second] if second is not None else [] ), return_exceptions=True)
    assert second is not None and second.result()['result']
