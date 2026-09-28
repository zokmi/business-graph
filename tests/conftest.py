"""測試共用 fixture 與輔助函式。"""
from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError


class ToolCallError(RuntimeError):
    """工具回傳 is_error 時由 call_tool 轉拋，方便用 pytest.raises 斷言。"""


async def call_tool(mcp, name: str, args: dict | None = None):
    """透過 MCP server 呼叫工具並取回結構化結果。

    MCP SDK 2.0 有兩條會走到工具錯誤的路徑，行為並不一致：
    協定層路徑（stdio/sse transport 呼叫的 _handle_call_tool）會把工具
    內的例外接住,轉成 is_error=True 的 CallToolResult；但本函式呼叫的
    MCPServer.call_tool（供測試直接呼叫，繞過協定層）不會接住，
    工具內的例外一律被 SDK 包成 ToolError 直接往外拋
    （見 mcp/server/mcpserver/tools/base.py 的 Tool.run，docstring
    明寫「Raises: ToolError: If the tool function raises during
    execution.」）。這裡把兩種情形都還原成 ToolCallError，讓測試能一致地
    用 pytest.raises 斷言錯誤訊息，不必關心呼叫的是哪條路徑。

    except 只收窄到 ToolError（SDK 執行工具失敗時實際拋出的型別），
    不用裸的 Exception：本輔助函式之後會被其他工具（business_write、
    business_lint）的測試共用，若這裡吃下所有例外，測試程式本身的
    bug（例如 fixture 寫錯、斷言前的準備碼出錯）會被誤報成「工具呼叫失敗」
    而非讓真正的例外原樣浮現。
    """
    try:
        result = await mcp.call_tool(name, args or {})
    except ToolError as exc:
        raise ToolCallError(str(exc)) from exc
    if getattr(result, "is_error", False):
        text = " ".join(
            getattr(block, "text", "") for block in (result.content or [])
        ).strip()
        raise ToolCallError(text or f"{name} 呼叫失敗")
    return result.structured_content


@pytest.fixture
def graph(tmp_path) -> Path:
    """建立一個含有數個測試節點的知識庫，回傳專案根目錄。

    內容採混語形態（英文標題 + 中文別名），與實際會進索引的樣子一致。
    """
    nodes = tmp_path / ".bgraph" / "nodes"
    nodes.mkdir(parents=True)

    def 寫(title: str, body: str, aliases: str = "", status: str = "confirmed",
          updated: str = "2026-08-14", node_type: str = "rule") -> None:
        別名行 = f"aliases: [{aliases}]\n" if aliases else ""
        (nodes / f"{title}.md").write_text(
            f"---\ntitle: {title}\ntype: {node_type}\n{別名行}"
            f"status: {status}\nupdated: {updated}\n---\n\n{body}\n",
            encoding="utf-8",
        )

    寫(
        "Discount Calculation",
        "Tier and campaign discounts do not stack — the larger one wins.\n"
        "Shipping is excluded from discounts (see [[Shipping Rules]]).",
        aliases="折扣計算規則",
    )
    寫("Shipping Rules", "Flat rate of 60 per order.", aliases="運費規則",
      status="inferred", updated="2026-01-01")
    return tmp_path
