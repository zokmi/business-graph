"""business_write 工具的測試。"""
from __future__ import annotations

import os

import pytest

from business_graph_mcp.db import queries
from business_graph_mcp.engine import open_session
from business_graph_mcp.server import create_server
from tests.conftest import ToolCallError, call_tool


async def _recall(mcp, graph, query: str) -> str:
    """呼叫 business_explore 並回傳文字結果。"""
    result = await call_tool(mcp, "business_explore", {"query": query, "project_path": str(graph)})
    return result["result"]


async def _write(mcp, graph, **args):
    """呼叫 business_write，自動帶上 project_path。"""
    return await call_tool(mcp, "business_write", {"project_path": str(graph), "type": "rule", **args})


async def test_建立新頁後立刻查得到(graph):
    mcp = create_server()

    outcome = await _write(
        mcp, graph,
        title="Refund Policy",
        content="Refunds restore the original tier discount.",
        status="confirmed",
    )

    assert outcome["created"] is True
    assert "Refund Policy" in await _recall(mcp, graph, "refund")


async def test_新頁的_front_matter_由_server_填寫(graph):
    mcp = create_server()

    await _write(mcp, graph, title="Refund Policy", content="Body.", status="inferred",
                 aliases=["退貨政策"])

    text = (graph / ".bgraph" / "nodes" / "Refund Policy.md").read_text(encoding="utf-8")
    assert "status: inferred" in text
    assert "aliases: [退貨政策]" in text
    # updated 由 server 自動戳記，agent 無法指定——它會填錯，
    # 而過期判定完全依賴這個欄位。
    assert "updated:" in text


async def test_更新既有頁需帶正確的_base_hash(graph):
    mcp = create_server()
    現況 = await _recall(mcp, graph, "discounts")
    base_hash = 現況.split("base_hash: ")[1].split()[0]

    outcome = await _write(
        mcp, graph, title="Discount Calculation", content="Updated rule.",
        status="confirmed", base_hash=base_hash,
    )

    assert outcome["created"] is False
    assert "Updated rule." in await _recall(mcp, graph, "updated rule")


async def test_base_hash_不符時拒絕並回傳現況全文(graph):
    mcp = create_server()

    with pytest.raises(ToolCallError) as exc:
        await _write(
            mcp, graph, title="Discount Calculation", content="Clobbered.",
            status="confirmed", base_hash="0000000000000000",
        )

    訊息 = str(exc.value)
    assert "base_hash" in 訊息
    # 必須帶回現況全文，agent 才能就地重做而不必再查一次。
    assert "do not stack" in 訊息


async def test_更新既有頁未帶_base_hash_時拒絕(graph):
    # 樂觀鎖的目的是逼 agent 先看過現況，缺 base_hash 代表它沒看過。
    mcp = create_server()

    with pytest.raises(ToolCallError) as exc:
        await _write(mcp, graph, title="Discount Calculation", content="Blind write.",
                     status="confirmed")

    assert "base_hash" in str(exc.value)


async def test_新頁誤帶_base_hash_時拒絕(graph):
    # 頁不存在卻帶了 base_hash，通常代表標題打錯，靜默建新頁會製造重複知識。
    mcp = create_server()

    with pytest.raises(ToolCallError) as exc:
        await _write(mcp, graph, title="Nonexistent Page", content="Body.",
                     status="confirmed", base_hash="a3f1c8d20e4b7f91")

    assert "不存在" in str(exc.value)


@pytest.mark.parametrize("壞的status", ["maybe", "CONFIRMED", "", "true"])
async def test_status_不合法時拒絕並列出合法值(graph, 壞的status):
    mcp = create_server()

    with pytest.raises(ToolCallError) as exc:
        await _write(mcp, graph, title="New Page", content="Body.", status=壞的status)

    訊息 = str(exc.value)
    assert "confirmed" in 訊息 and "inferred" in 訊息


async def test_內容為空時拒絕(graph):
    # 空頁會污染檢索結果，且沒有任何知識價值。
    mcp = create_server()

    with pytest.raises(ToolCallError) as exc:
        await _write(mcp, graph, title="New Page", content="   ", status="confirmed")

    assert "內容" in str(exc.value)


async def test_寫入時引用不存在的頁會立刻提示(graph):
    mcp = create_server()

    outcome = await _write(
        mcp, graph, title="Refund Policy",
        content="See [[Nonexistent Rule]] for details.", status="inferred",
    )

    assert outcome["dangling"] == ["Nonexistent Rule"]


async def test_標題含路徑字元時被安全化為檔名(graph):
    mcp = create_server()

    outcome = await _write(mcp, graph, title="A/B Testing", content="Body.", status="inferred")

    assert outcome["slug"] == "A-B Testing"
    assert (graph / ".bgraph" / "nodes" / "A-B Testing.md").is_file()


async def test_知識庫不存在時第一次寫入即自動建立(tmp_path):
    # Spec 第九節原則 3：不提供 init 指令，第一次寫入就是初始化。
    mcp = create_server()

    outcome = await call_tool(
        mcp, "business_write",
        {"project_path": str(tmp_path), "title": "First Page",
         "type": "rule", "content": "Body.", "status": "inferred"},
    )

    assert outcome["created"] is True
    assert (tmp_path / ".bgraph" / "nodes" / "First Page.md").is_file()


async def test_回傳新的_base_hash_供接續更新(graph):
    mcp = create_server()

    第一次 = await _write(mcp, graph, title="Refund Policy", content="v1", status="inferred")
    第二次 = await _write(
        mcp, graph, title="Refund Policy", content="v2", status="inferred",
        base_hash=第一次["base_hash"],
    )

    assert 第二次["base_hash"] != 第一次["base_hash"]


async def test_寫入中斷時原檔完好且不留暫存檔(graph, monkeypatch):
    # 驗證 _atomic_write 的保護真的存在：換名階段中斷不得截斷原檔，
    # 也不得在 nodes/ 底下留下暫存垃圾。若實作改回直接 write_text，
    # 這條測試必須失敗（os.replace 根本不會被呼叫，也就無從模擬中斷）。
    mcp = create_server()
    page_path = graph / ".bgraph" / "nodes" / "Discount Calculation.md"
    原始內容 = page_path.read_text(encoding="utf-8")

    現況 = await _recall(mcp, graph, "discounts")
    base_hash = 現況.split("base_hash: ")[1].split()[0]

    def 中斷(*args, **kwargs):
        raise OSError("模擬寫入中途被中斷（例如斷電或行程被 kill）")

    monkeypatch.setattr(os, "replace", 中斷)

    with pytest.raises(ToolCallError):
        await _write(
            mcp, graph, title="Discount Calculation", content="Interrupted write.",
            status="confirmed", base_hash=base_hash,
        )

    assert page_path.read_text(encoding="utf-8") == 原始內容
    殘留暫存檔 = [
        p for p in (graph / ".bgraph" / "nodes").iterdir()
        if p.name != page_path.name and p.name.startswith(".Discount Calculation.md")
    ]
    assert 殘留暫存檔 == []


async def test_寫入必須提供節點型別(graph):
    with pytest.raises(ToolCallError) as exc:
        await call_tool(create_server(), "business_write", {
            "title": "New Rule", "content": "內文", "status": "confirmed",
            "project_path": str(graph),
        })
    assert "type" in str(exc.value)
    assert not (graph / ".bgraph" / "nodes" / "New Rule.md").exists()


async def test_寫入需要合法節點型別(graph):
    with pytest.raises(ToolCallError) as exc:
        await _write(create_server(), graph, title="New Rule", type="policy",
                     content="內文", status="confirmed")
    for 合法值 in ("rule", "entity", "decision", "term"):
        assert 合法值 in str(exc.value)
    assert not (graph / ".bgraph" / "nodes" / "New Rule.md").exists()


@pytest.mark.parametrize("content, hints", [
    ("見 [[overide:Shipping Rules]]。", ("overide", "depends_on")),
    ("見 [[implemented_by:OrderService.Calculate]]。", ("implemented_by", "code")),
    ("[[depends_on:]]", ("目標", "空")),
    ("[[implemented_by: OrderService.Calculate]]", ("implemented_by", "code")),
    ("見 [[ ]]。", ("目標", "空")),
])
async def test_內文關係錯誤時拒絕且保留既有節點(graph, content, hints):
    mcp = create_server()
    created = await _write(mcp, graph, title="New Rule", content="原始內文", status="confirmed")
    path = graph / ".bgraph" / "nodes" / "New Rule.md"
    original = path.read_text(encoding="utf-8")
    with pytest.raises(ToolCallError) as exc:
        await _write(mcp, graph, title="New Rule", content=content,
                     status="confirmed", base_hash=created["base_hash"])
    for hint in hints:
        assert hint in str(exc.value)
    assert path.read_text(encoding="utf-8") == original


async def test_標題含冒號不被誤判為關係(graph):
    result = await _write(create_server(), graph, title="New Rule",
                          content="見 [[ADR-3: Pricing Model]]。", status="confirmed")
    assert "ADR-3: Pricing Model" in result["dangling"]


async def test_冒號標題關係可解析並出現在路徑與反向影響(graph):
    mcp = create_server()
    await _write(
        mcp,
        graph,
        title="ADR-3: Pricing Model",
        type="entity",
        aliases=["pricingmodelneedle"],
        content="Pricing model decision record.",
        status="confirmed",
    )
    outcome = await _write(
        mcp,
        graph,
        title="Pricing Rule",
        aliases=["pricingruleneedle"],
        content="This rule uses [[depends_on:ADR-3: Pricing Model]].",
        status="confirmed",
    )
    assert outcome["dangling"] == []

    path_text = await _recall(mcp, graph, "pricingruleneedle")
    assert "--depends_on--> ADR-3- Pricing Model" in path_text

    blast_text = await _recall(mcp, graph, "pricingmodelneedle")
    assert "Pricing Rule (depends_on)" in blast_text


@pytest.mark.parametrize("node_type", ["rule", "entity", "decision", "term"])
async def test_寫入的型別與_code_錨點會落進_front_matter_及索引(graph, node_type):
    await _write(create_server(), graph, title="New Rule", type=node_type,
                 content="見 [[depends_on:Shipping Rules]]。", status="confirmed",
                 code=["OrderService.Calculate"])
    text = (graph / ".bgraph" / "nodes" / "New Rule.md").read_text(encoding="utf-8")
    assert f"type: {node_type}" in text
    assert "code: [OrderService.Calculate]" in text
    with open_session(str(graph)) as session:
        assert queries.code_anchors_for(session.conn, ["New Rule"]) == {
            "New Rule": ("OrderService.Calculate",),
        }
        assert tuple(session.conn.execute(
            "SELECT node_type FROM nodes WHERE slug = ?", ("New Rule",),
        ).fetchone()) == (node_type,)
        assert [tuple(row) for row in session.conn.execute(
            "SELECT edge_type, target_raw FROM edges WHERE source_slug = ?", ("New Rule",),
        )] == [("depends_on", "Shipping Rules")]


async def test_頁面已保存但索引失敗時回傳新雜湊並可在下次同步恢復(graph, monkeypatch):
    import business_graph_mcp.writer as writer

    mcp = create_server()
    real_sync = writer.sync_index

    def fail_sync(*args, **kwargs):
        raise RuntimeError('模擬索引寫入失敗')

    with monkeypatch.context() as patcher:
        patcher.setattr(writer, 'sync_index', fail_sync)
        outcome = await _write(
            mcp, graph, title='Saved Rule', content='saved before indexing',
            status='inferred',
        )
    assert outcome['slug'] == 'Saved Rule'
    assert outcome['indexed'] is False
    assert outcome['dangling'] is None
    assert outcome['base_hash']
    assert '已保存' in outcome['warning']
    saved = graph / '.bgraph' / 'nodes' / 'Saved Rule.md'
    assert 'saved before indexing' in saved.read_text(encoding='utf-8')
    assert 'Saved Rule' in await _recall(mcp, graph, 'saved before indexing')
    assert writer.sync_index is real_sync


async def test_保存後讀不到本頁時不得宣稱索引完成(graph, monkeypatch):
    from pathlib import Path

    from business_graph_mcp.db.queries import get_node, open_index

    mcp = create_server()
    target = graph / '.bgraph' / 'nodes' / 'Unreadable Rule.md'
    real_read = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == target:
            raise PermissionError('模擬保存後的暫時共享衝突')
        return real_read(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, 'read_text', unreadable)
        outcome = await _write(
            mcp, graph, title='Unreadable Rule', content='real saved knowledge',
            status='inferred',
        )
    assert outcome['indexed'] is False
    assert outcome['dangling'] is None
    assert outcome['base_hash']
    assert '已保存' in outcome['warning']
    assert 'real saved knowledge' in target.read_text(encoding='utf-8')
    conn = open_index(graph / '.bgraph' / 'index.db')
    try:
        indexed = get_node(conn, 'Unreadable Rule')
        assert indexed is None or indexed.content_hash != outcome['base_hash']
    finally:
        conn.close()
    assert 'real saved knowledge' in await _recall(mcp, graph, 'real saved knowledge')


async def test_保存後關係查詢失敗仍回傳新雜湊(graph, monkeypatch):
    import sqlite3

    import business_graph_mcp.writer as writer

    def fail_dangling(*args, **kwargs):
        raise sqlite3.OperationalError('模擬關係查詢失敗')

    mcp = create_server()
    with monkeypatch.context() as patcher:
        patcher.setattr(writer, 'dangling_targets', fail_dangling)
        outcome = await _write(
            mcp, graph, title='Saved Rule With Query Failure',
            content='saved despite lookup failure', status='inferred',
        )
    assert outcome['indexed'] is False
    assert outcome['dangling'] is None
    assert outcome['base_hash']
    assert '已保存' in outcome['warning']
    assert 'saved despite lookup failure' in await _recall(mcp, graph, 'saved despite lookup failure')
