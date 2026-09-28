"""business_explore 工具：知識庫的唯一讀取入口。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from business_graph_mcp.banners import build_notices
from business_graph_mcp.codegraph import find_codegraph_db, resolve_symbols
from business_graph_mcp.db.queries import dangling_targets, record_miss
from business_graph_mcp.engine import open_session
from business_graph_mcp.errors import GraphNotFoundError
from business_graph_mcp.explore import run_explore
from business_graph_mcp.formatting import format_explore

READ_ONLY = ToolAnnotations(read_only_hint=True)

_QUERY_DESCRIPTION = (
    "要查的業務問題或關鍵字。可用自然語言問句，也可用術語；"
    "中英文皆可，兩者都會比對到（頁面標題預設英文，別名放中文術語）。"
)

_PROJECT_PATH_DESCRIPTION = (
    "專案中的任一路徑；會從此處向上尋找最近的 .bgraph/ 目錄。"
    "省略時使用 server 行程的工作目錄。要查詢 monorepo 中的其他子專案，"
    "或另一個 repo 的知識庫時，指定該處的路徑。"
)


def _normalize(query: str) -> str:
    """把查詢正規化成未命中統計的鍵。

    只做大小寫與空白的正規化：同一個問題換個問法本來就該分開計數，
    過度正規化會讓「反覆查不到」這個訊號失真。
    """
    return " ".join(query.lower().split())


def register(mcp: Any) -> None:
    """在 MCP server 上註冊讀取工具。

    參數:
        mcp: MCP server 實例。
    """

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "查詢本專案累積的業務邏輯知識。這是知識圖唯一的讀取入口，"
            "一次呼叫即回傳命中節點的完整內文、節點之間的關係路徑、"
            "影響半徑（改動命中節點會波及哪些節點）、對應的程式碼位置，"
            "以及需要你處理的待辦。"
            "遇到業務規則相關的問題時應先呼叫本工具，不要憑既有印象作答。"
            "若查無記錄或記錄不足，而你在本次工作中弄清楚了答案，"
            "必須用 business_write 寫回。"
        ),
    )
    async def business_explore(
        query: Annotated[str, Field(description=_QUERY_DESCRIPTION)],
        project_path: Annotated[
            str | None, Field(description=_PROJECT_PATH_DESCRIPTION)
        ] = None,
    ) -> dict[str, str]:
        """查詢業務知識並回傳完整內容與待辦。

        參數:
            query: 要查的業務問題或關鍵字。
            project_path: 專案中的任一路徑，用於定位 .bgraph/。
        """
        try:
            with open_session(project_path) as session:
                result = run_explore(session.conn, query, session.cfg)

                miss_count = 0
                if not result.hits:
                    miss_count = record_miss(session.conn, _normalize(query), datetime.now())

                dangling = dangling_targets(
                    session.conn, [h.page.slug for h in result.hits]
                )
                notices = build_notices(
                    result,
                    session.cfg,
                    query=query,
                    miss_count=miss_count,
                    dangling=dangling,
                    today=date.today(),
                )
                symbols = sorted({s for anchors in result.anchors.values() for s in anchors})
                anchor_locations = resolve_symbols(find_codegraph_db(session.ws.root), symbols)
                return {
                    "result": format_explore(result, notices, session.sync, anchor_locations)
                }
        except GraphNotFoundError as exc:
            # Spec 第九節原則 3：尚未建立知識圖是正常狀態，不是錯誤。
            return {"result": str(exc)}
