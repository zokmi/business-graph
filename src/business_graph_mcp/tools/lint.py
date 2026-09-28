"""business_lint 工具：知識庫的一致性稽核。"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from business_graph_mcp.engine import open_session
from business_graph_mcp.errors import GraphNotFoundError
from business_graph_mcp.lint import run_lint

READ_ONLY = ToolAnnotations(read_only_hint=True)

_PROJECT_PATH_DESCRIPTION = (
    "專案中的任一路徑；會從此處向上尋找最近的 .bgraph/ 目錄。"
    "省略時使用 server 行程的工作目錄。"
)


def register(mcp: Any) -> None:
    """在 MCP server 上註冊稽核工具。

    參數:
        mcp: MCP server 實例。
    """

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "稽核知識圖並回傳可直接執行的修正清單："
            "過期或未確認節點、懸空關係、孤兒節點、反覆查不到的知識缺口、"
            "共現但未連結的節點、未解析的程式碼錨點、未連到實作的 rule，"
            "以及尚未提交的變更。沒有可用的 codegraph 索引時，"
            "未解析錨點檢查會略過，不影響其餘稽核。"
            "過期頁、未確認頁、未解析的程式碼錨點與未連到實作的 rule，"
            "會附 business_write 與該節點的 base_hash；其他區段依問題提供"
            "建立節點、人工判讀、版本控制或檔案配置指引。"
        ),
    )
    async def business_lint(
        project_path: Annotated[
            str | None, Field(description=_PROJECT_PATH_DESCRIPTION)
        ] = None,
    ) -> dict[str, str]:
        """稽核知識庫的一致性。

        參數:
            project_path: 專案中的任一路徑，用於定位 .bgraph/。
        """
        def _run() -> dict[str, str]:
            try:
                with open_session(project_path) as session:
                    return {"result": run_lint(session, date.today())}
            except GraphNotFoundError as exc:
                return {"result": str(exc)}

        return await asyncio.to_thread(_run)
