"""business_write 工具：建立或更新一頁業務知識。"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import Field

from business_graph_mcp.engine import open_session
from business_graph_mcp.writer import write_node

_TITLE_DESCRIPTION = (
    "頁面標題，預設使用英文。它同時是 [[連結]] 的目標與檔名，"
    "因此要具體且穩定（例如 Discount Calculation，而非 Rules）。"
)

_TYPE_DESCRIPTION = (
    "節點型別，必填且無預設值。"
    "rule：業務規則（折扣怎麼算、什麼情況不受理、額度怎麼判定）。"
    "entity：實體或欄位（某張表、某個欄位的業務意義與合法值）。"
    "decision：決策與理由（為什麼這樣設計、當時的替代方案與取捨）。"
    "term：術語（業務方講的某個詞在系統裡對應什麼）。"
)

_CONTENT_DESCRIPTION = (
    "節點內文，markdown 格式，不要自己寫 front matter——那由 server 管理。"
    "預設以英文撰寫；術語翻譯後會失真、需引用原文、或該概念只有中文說法時"
    "才用中文。"
    "要連到其他節點用 [[關係:目標標題]]，關係可為 depends_on（依賴）、"
    "overrides（覆寫）、exception_to（例外）、relates_to（相關）；"
    "只寫 [[目標標題]] 等同 relates_to。關係要寫在說明該關係的那句話旁邊。"
    "更新既有節點時請提供完整的新內文，而非只有差異部分。"
)

_STATUS_DESCRIPTION = (
    "內容的可信程度，必填且無預設值。"
    "confirmed：業務方確認過。"
    "inferred：從程式碼、既有實作或推理得出，尚未經業務確認。"
    "把推測標成 confirmed 會誤導後續所有引用者，判斷依據是有沒有人親口確認過，"
    "不是你有多確定。"
)

_ALIASES_DESCRIPTION = (
    "別名。中文術語寫在這裡，讓中英文都查得到；同一概念的其他叫法也應列入"
    "（例如會員／客戶／用戶）。缺少別名會大幅降低命中率。"
)

_CODE_DESCRIPTION = (
    "對應的程式碼符號名，例如 OrderService.CalculateDiscount。"
    "寫 rule 時應盡量補上——它讓「這條規則改了會動到哪段程式」可查。"
    "填符號名即可，不要填檔案路徑或行號，位置由 codegraph 索引即時解析。"
)

_BASE_HASH_DESCRIPTION = (
    "更新既有頁時必填，值取自 business_explore 回應中該頁的 base_hash。"
    "新頁不可提供。與現況不符時本次寫入會被拒絕並回傳現況全文，"
    "請依現況重做。"
)

_PROJECT_PATH_DESCRIPTION = (
    "專案中的任一路徑；會從此處向上尋找最近的 .bgraph/ 目錄，"
    "找不到則自動建立。省略時使用 server 行程的工作目錄。"
)


def register(mcp: Any) -> None:
    """在 MCP server 上註冊寫入工具。

    參數:
        mcp: MCP server 實例。
    """

    @mcp.tool(
        description=(
            "建立或更新一頁業務知識。查無記錄而你弄清楚了答案時，"
            "或發現既有頁面與現況不符時，都用本工具寫回——這是義務，不是建議。"
            "寫入為整頁覆蓋：更新既有頁時請提供完整新內文並帶上 base_hash。"
        ),
    )
    async def business_write(
        title: Annotated[str, Field(description=_TITLE_DESCRIPTION)],
        type: Annotated[str, Field(description=_TYPE_DESCRIPTION)],
        content: Annotated[str, Field(description=_CONTENT_DESCRIPTION)],
        status: Annotated[str, Field(description=_STATUS_DESCRIPTION)],
        aliases: Annotated[
            list[str] | None, Field(description=_ALIASES_DESCRIPTION)
        ] = None,
        code: Annotated[list[str] | None, Field(description=_CODE_DESCRIPTION)] = None,
        base_hash: Annotated[
            str | None, Field(description=_BASE_HASH_DESCRIPTION)
        ] = None,
        project_path: Annotated[
            str | None, Field(description=_PROJECT_PATH_DESCRIPTION)
        ] = None,
    ) -> dict[str, object]:
        """寫入一頁業務知識。

        參數:
            title: 頁面標題。
            type: 節點型別，rule、entity、decision 或 term。
            content: 頁面內文，不含 front matter。
            status: confirmed 或 inferred。
            aliases: 別名清單。
            code: 對應的程式碼符號名稱清單。
            base_hash: 更新既有頁時的樂觀鎖值。
            project_path: 專案中的任一路徑，用於定位 .bgraph/。
        """
        with open_session(project_path, create=True) as session:
            outcome = write_node(
                session,
                title=title,
                node_type=type,
                content=content,
                status=status,
                aliases=tuple(aliases or ()),
                code=tuple(code or ()),
                base_hash=base_hash,
                today=date.today(),
            )
            return {
                "slug": outcome.slug,
                "created": outcome.created,
                "base_hash": outcome.base_hash,
                "dangling": list(outcome.dangling),
            }
