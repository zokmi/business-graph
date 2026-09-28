"""business_lint 的測試。"""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from business_graph_mcp.lint import uncommitted_pages
from business_graph_mcp.server import create_server
from business_graph_mcp.workspace import Workspace
from tests.conftest import call_tool

今天 = date(2026, 8, 14)


async def _lint(mcp, graph) -> str:
    """呼叫 business_lint 並回傳文字結果。"""
    result = await call_tool(mcp, "business_lint", {"project_path": str(graph)})
    return result["result"]


def _寫頁(graph, title, body="Body.", status="confirmed", updated="2026-08-14", aliases=""):
    """在測試知識庫中新增或覆寫一頁。"""
    別名行 = f"aliases: [{aliases}]\n" if aliases else ""
    (graph / ".bgraph" / "nodes" / f"{title}.md").write_text(
        f"---\ntitle: {title}\ntype: rule\n{別名行}status: {status}\nupdated: {updated}\n---\n\n{body}\n",
        encoding="utf-8",
    )


async def test_孤兒頁被列出(graph):
    # conftest 的 Shipping Rules 被 Discount Calculation 引用，故非孤兒；
    # 這頁沒有任何人引用。
    _寫頁(graph, "Lonely Page")
    mcp = create_server()

    assert "Lonely Page" in await _lint(mcp, graph)


async def test_懸空連結被列出且指明工具(graph):
    _寫頁(graph, "Refund Policy", body="See [[Nonexistent Rule]].")
    mcp = create_server()

    text = await _lint(mcp, graph)

    assert "Nonexistent Rule" in text
    assert "business_write" in text


async def test_重複_title_造成懸空時回報歧義而非建議建立頁面(graph):
    nodes = graph / ".bgraph" / "nodes"
    for slug, title in (("Title A", "Shared Title"), ("Title B", "shared title")):
        (nodes / f"{slug}.md").write_text(
            f"---\ntitle: {title}\ntype: entity\nstatus: confirmed\n"
            "updated: 2026-08-14\n---\n\nCandidate.\n",
            encoding="utf-8",
        )
    _寫頁(graph, "Source", body="See [[SHARED TITLE]].")

    text = await _lint(create_server(), graph)
    line = next(line for line in text.splitlines() if "[[SHARED TITLE]]" in line)

    assert "多個頁面標題" in line
    assert "Title A、Title B" in line
    assert "改名" in line
    assert "slug 或 alias" in line
    assert "不存在" not in line
    assert "建立該頁" not in line


async def test_過期頁被列出並附_base_hash(graph):
    # conftest 的 Shipping Rules 更新於 2026-01-01，超過預設 90 天門檻。
    mcp = create_server()

    text = await _lint(mcp, graph)

    assert "Shipping Rules" in text
    assert "base_hash" in text


async def test_未確認頁被列出(graph):
    mcp = create_server()

    text = await _lint(mcp, graph)

    assert "inferred" in text


async def test_反覆未命中的問題被列為知識缺口(graph):
    # 刻意避開 fixture 內文出現過的詞根（如 "rule"）：build_match_query 會替
    # 英數詞加上前綴運算子（discount* 要能命中 discounts 是刻意的召回設計），
    # 若查詢字串與 fixture 詞根重疊，即使語意上查無結果，仍可能因前綴比對
    # 命中而不會被記為 miss（例如 "rule*" 會命中 [[Shipping Rules]] 裡的
    # "Rules"）。下次在這裡加測試查詢時，務必確認它與 graph fixture 的既有
    # 內容（含連結內文字）沒有詞根交集。
    mcp = create_server()
    for _ in range(3):
        await call_tool(
            mcp,
            "business_explore",
            {"query": "warehouse robot calibration", "project_path": str(graph)},
        )

    text = await _lint(mcp, graph)

    assert "warehouse robot calibration" in text
    assert "缺口" in text


async def test_未達門檻的未命中不列入(graph):
    mcp = create_server()
    await call_tool(mcp, "business_explore", {"query": "one off question", "project_path": str(graph)})

    assert "one off question" not in await _lint(mcp, graph)


async def test_共現但未連結的頁被列為待判讀線索(graph):
    # Refund Policy 的內文提到 Shipping Rules，但沒有用 [[ ]] 連結——
    # 可能該補連結，也可能兩頁互相矛盾，交給 agent 判讀。
    _寫頁(graph, "Refund Policy", body="Refunds also affect Shipping Rules in some cases.")
    mcp = create_server()

    text = await _lint(mcp, graph)

    assert "共現" in text or "提及" in text


async def test_已連結的頁不列為共現線索(graph):
    # 已有連結就不是待判讀線索，否則整份報告會被正常引用淹沒。
    _寫頁(graph, "Refund Policy", body="See [[Shipping Rules]] for details.")
    mcp = create_server()

    text = await _lint(mcp, graph)

    共現段 = ""
    if "## 共現線索" in text:
        共現段 = text.split("## 共現線索")[1].split("\n## ")[0]
    assert "〈Refund Policy〉的內文提及〈Shipping Rules〉" not in 共現段


async def test_一切正常時明確回報無問題(tmp_path):
    nodes = tmp_path / ".bgraph" / "nodes"
    nodes.mkdir(parents=True)
    (nodes / "A.md").write_text(
        "---\ntitle: A\ntype: rule\ncode: [A.Symbol]\n"
        "status: confirmed\nupdated: 2026-08-14\n---\n\nSee [[B]].\n",
        encoding="utf-8",
    )
    (nodes / "B.md").write_text(
        "---\ntitle: B\ntype: rule\ncode: [B.Symbol]\n"
        "status: confirmed\nupdated: 2026-08-14\n---\n\nSee [[A]].\n",
        encoding="utf-8",
    )
    mcp = create_server()

    text = await _lint(mcp, tmp_path)

    # 精確比對整段輸出：舊斷言「沒有」in text or「無」in text 幾乎恆真——
    # 即使孤兒頁區塊誤報也含「沒有」二字，該斷言鎖不住任何錯誤。
    assert text == "稽核完成，沒有發現需要處理的項目。\n"


def test_無_git_時未提交檢查靜默跳過(tmp_path):
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)

    with patch("business_graph_mcp.lint.shutil.which", return_value=None):
        assert uncommitted_pages(ws) is None


def test_不在_git_版控下時靜默跳過(tmp_path):
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)

    # tmp_path 沒有 .git，且往上到使用者家目錄邊界前都找不到 .git，故
    # find_git_boundary 回傳 None，uncommitted_pages 在呼叫 subprocess.run
    # 之前就提早短路——這條測試走的是邊界判定分支，不是 git 指令本身失敗。
    assert uncommitted_pages(ws) is None


def test_有未提交變更時列出檔名(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)
    (ws.nodes_dir / "New Page.md").write_text("x", encoding="utf-8")

    結果 = uncommitted_pages(ws)

    assert 結果 is not None
    assert any("New Page.md" in item for item in 結果)


def test_git_指令拋出例外時靜默跳過(tmp_path, monkeypatch):
    # 與「找不到 git」「不在版控下」是三條各自獨立的防線：這裡模擬知識庫
    # 確實屬於某個 repo（find_git_boundary 找得到邊界），但 subprocess 執行
    # 本身拋出例外（例如 git 執行檔損毀、路徑權限問題）。
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)

    def _raise(*args, **kwargs):
        raise OSError("模擬 git 執行檔無法啟動")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert uncommitted_pages(ws) is None


def test_git_回傳非零狀態碼時靜默跳過(tmp_path, monkeypatch):
    # 同上，這裡模擬邊界找得到、指令也順利執行，但 git 本身回報失敗
    # （非零狀態碼），例如目標路徑損毀或索引鎖死。
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ws = Workspace(root=tmp_path / ".bgraph")
    ws.nodes_dir.mkdir(parents=True)

    def _fail(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="模擬失敗")

    monkeypatch.setattr(subprocess, "run", _fail)

    assert uncommitted_pages(ws) is None


async def test_知識庫不存在時回明確訊息(tmp_path):
    mcp = create_server()

    result = await call_tool(mcp, "business_lint", {"project_path": str(tmp_path)})

    assert "尚未建立知識圖" in result["result"]


async def test_同一懸空目標有多種關係時只列一條提示(graph):
    _寫頁(
        graph, "Refund Policy",
        body="[[depends_on:Nonexistent Rule]] [[overrides:Nonexistent Rule]]",
    )
    text = await _lint(create_server(), graph)

    assert text.count("[[Nonexistent Rule]]") == 1


def _寫錨點頁(graph):
    (graph / ".bgraph" / "nodes" / "Anchored.md").write_text(
        "---\ntitle: Anchored\ntype: rule\ncode: [Missing.Symbol, Existing.Symbol]\n"
        "status: confirmed\nupdated: 2026-09-18\n---\n\n內文。\n",
        encoding="utf-8",
    )


async def test_lint_只列出未解析的錨點(graph, monkeypatch):
    _寫錨點頁(graph)
    monkeypatch.setattr("business_graph_mcp.lint.find_codegraph_db", lambda _: Path("x"))

    def resolve(db_path, symbols):
        assert db_path == Path("x")
        assert symbols == ["Existing.Symbol", "Missing.Symbol"]
        return {"Existing.Symbol": "src/Existing.cs:1-5"}, True

    monkeypatch.setattr("business_graph_mcp.lint.resolve_symbols_checked", resolve)
    text = await _lint(create_server(), graph)
    section = text.split("## 未解析的程式碼錨點\n")[1].split("\n\n")[0]
    assert "〈Anchored〉的錨點 Missing.Symbol" in section
    assert "Existing.Symbol" not in section
    assert "business_write" in section
    content = (graph / ".bgraph" / "nodes" / "Anchored.md").read_text(encoding="utf-8")
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    assert f"base_hash: {expected_hash}" in section


async def test_沒有_codegraph_索引時整段略過錨點檢查(graph, monkeypatch):
    _寫錨點頁(graph)
    monkeypatch.setattr("business_graph_mcp.lint.find_codegraph_db", lambda _: None)
    text = await _lint(create_server(), graph)
    assert "Missing.Symbol" not in text
    assert "## 未解析的程式碼錨點" not in text
    assert "## 規則未連到實作" in text


async def test_查詢中途失敗即使有部分結果仍略過未解析檢查(graph, monkeypatch):
    _寫錨點頁(graph)
    monkeypatch.setattr("business_graph_mcp.lint.find_codegraph_db", lambda _: Path("x"))
    monkeypatch.setattr(
        "business_graph_mcp.lint.resolve_symbols_checked",
        lambda _p, _s: ({"Existing.Symbol": "src/Existing.cs:1-5"}, False),
    )

    text = await _lint(create_server(), graph)

    assert "## 未解析的程式碼錨點" not in text
    assert "Missing.Symbol" not in text


async def test_lint_只列出沒有錨點的_rule_並附修改雜湊(graph):
    _寫錨點頁(graph)
    text = await _lint(create_server(), graph)
    section = text.split("## 規則未連到實作\n")[1].split("\n\n")[0]
    assert "Discount Calculation" in section
    assert "Shipping Rules" in section
    assert "Anchored" not in section
    assert "沒有對應的程式碼錨點" in section
    assert "business_write" in section
    assert "base_hash:" in section


@pytest.mark.parametrize("node_type", ["term", "decision", "flow"])
async def test_非_rule_沒有錨點不列入(graph, node_type):
    (graph / ".bgraph" / "nodes" / "Jargon.md").write_text(
        f"---\ntitle: Jargon\ntype: {node_type}\nstatus: confirmed\n"
        "updated: 2026-09-18\n---\n\n內文。\n",
        encoding="utf-8",
    )
    text = await _lint(create_server(), graph)
    section = text.split("## 規則未連到實作\n")[1].split("\n\n")[0]
    assert "Jargon" not in section


@pytest.mark.parametrize("index_kind", ["broken", "wrong_schema"])
async def test_無法查詢的_codegraph_索引不誤報未解析錨點(graph, monkeypatch, index_kind):
    _寫錨點頁(graph)
    db = graph / "codegraph.db"
    if index_kind == "broken":
        db.write_text("損壞的索引", encoding="utf-8")
    else:
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE nodes (id TEXT)")
    monkeypatch.setattr("business_graph_mcp.lint.find_codegraph_db", lambda _: db)

    text = await _lint(create_server(), graph)

    assert "## 未解析的程式碼錨點" not in text
    assert "Missing.Symbol" not in text
    assert "## 規則未連到實作" in text
