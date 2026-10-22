"""MCP server 組裝。"""
from __future__ import annotations

from mcp.server import MCPServer

from business_graph_mcp import __version__
from business_graph_mcp.tools import explore, lint, write

SERVER_NAME = "business-graph"
#: 對 MCP client 宣告的版本；與套件版本一致，不另行維護。
SERVER_VERSION = __version__

#: Server 指示詞。這段文字是「agent 自主新增或修正知識」機制的第一個作用點：
#: server 不呼叫 LLM，由 agent 判斷收錄範圍並寫回可重用的業務知識。
#: 三條義務缺一不可，第 1 條尤其關鍵——它讓 business_explore 成為業務問題的必經
#: 步驟，而 explore 的後綴待辦是驅動寫入的引擎；沒有第 1 條，第 2、3 條就
#: 失去觸發點。修改本字串前請先讀 Spec 第 4.4 節。
INSTRUCTIONS = (
    "提供本專案業務邏輯知識的累積與查詢，內容組織成型別化的知識圖。\n"
    "只收錄可跨任務重用的業務知識：業務規則、流程與例外、實體與欄位的業務意義、"
    "業務決策及其理由、業務術語。寫入前先判斷：移除單號與本次工作背景後，"
    "內容是否仍能回答一個具體的業務問題？不能就不要寫入。\n"
    "Issue／Redmine 單與程式碼可以作為理解業務的來源，但不得把 issue 資訊存成節點或內文；"
    "不收錄單號、標題、狀態、負責人、處理進度、修復經過、commit／PR、測試結果、"
    "部署紀錄、待辦或純技術實作細節。沒有新增或修正業務知識時，不必寫入。"
    "例如單據要求『已出貨訂單不可取消』，應寫成 Order Cancellation 規則，"
    "說明適用條件、限制與例外，不要建立 issue 摘要或修復紀錄。\n"
    "\n"
    "使用本 server 即承諾三件事，缺一不可：\n"
    "\n"
    "1. 遇到業務規則相關的問題，先呼叫 business_explore 查有無記錄，"
    "不要憑既有印象作答。\n"
    "\n"
    "2. 若查無記錄或記錄不足，而你在本次工作中釐清了符合上述收錄範圍的業務知識，"
    "必須在該次工作結束前呼叫 business_write 寫回。這是義務，不是建議。\n"
    "\n"
    "3. 若你發現既有節點與現況不符（依據可以是程式碼、業務方說法或 "
    "Redmine 單），必須呼叫 business_write 更正，不可只在對話中指出而放著不改。\n"
    "\n"
    "節點型別必填，四選一：rule 是業務規則（折扣怎麼算、什麼情況不受理）；"
    "entity 是實體或欄位（某張表、某個欄位的業務意義與合法值）；"
    "decision 是業務決策與理由（為什麼採用某項業務政策、業務上的替代方案與取捨）；"
    "term 是術語（業務方講的某個詞在系統裡對應什麼）。\n"
    "\n"
    "節點之間的關係寫在內文，用 [[關係:目標]]：depends_on（本節點依賴目標）、"
    "overrides（本節點覆寫目標）、exception_to（本節點是目標的例外）、"
    "relates_to（相關，無方向）。只寫 [[目標]] 等同 relates_to。"
    "關係要寫在說明該關係的那句話旁邊，讓後面的人看得出它為何存在。"
    "前三種關係會用來計算影響半徑——改一條規則會波及誰，靠的就是它們，"
    "全部寫成 relates_to 等於讓影響半徑失效。\n"
    "\n"
    "寫 rule 時盡量補上 code 錨點（程式碼符號名，例如 "
    "OrderService.CalculateDiscount），它讓「這條規則改了會動到哪段程式」可查。"
    "填符號名即可，位置由 codegraph 索引即時解析；要看程式碼本身請另呼 "
    "codegraph 的工具。\n"
    "\n"
    "節點內容預設以英文撰寫。僅在術語翻譯後會失真、需引用原文、或該概念在"
    "公司內部只有中文說法時使用中文，並把中文術語一併寫進 aliases，"
    "讓兩種語言都查得到。\n"
    "\n"
    "status 必須誠實填寫：業務方確認過才是 confirmed，從程式碼或推理得出的"
    "一律 inferred。把推測標成 confirmed 會誤導後續所有引用者。\n"
    "\n"
    "規則若被新規則取代，舊規則本身往往仍是知識（處理歷史資料時需要）。"
    "更正時要判斷該直接覆蓋，還是在節點內保留「2026-08 前為 X，之後改為 Y」。\n"
)


def create_server() -> MCPServer:
    """建立並回傳已註冊所有工具的 MCP server。"""
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=SERVER_VERSION)
    explore.register(mcp)
    write.register(mcp)
    lint.register(mcp)
    return mcp
