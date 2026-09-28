"""邊型別常數與 [[關係:目標]] 的解析。

本模組為純函式，不碰資料庫與檔案系統。

parse_edge_target 是全案唯一一處需要「推測呼叫端意圖」的邏輯：標題本身
可能含冒號（[[ADR-3: Pricing Model]] 是合理的節點名），因此不能單純以
「冒號前的字串不在合法集合就報錯」處理，否則會把正常標題誤判成打錯字的
關係前綴。判定條件見 _PREFIXED 的註解。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 本節點的成立依賴目標節點。
EDGE_DEPENDS_ON = "depends_on"
#: 本節點覆寫目標節點。
EDGE_OVERRIDES = "overrides"
#: 本節點是目標節點的例外。
EDGE_EXCEPTION_TO = "exception_to"
#: 相關，無方向語意；無前綴連結的預設型別。
EDGE_RELATES_TO = "relates_to"
#: 對應到程式碼符號。只能由 front matter 的 code 欄位產生，不可寫在內文。
EDGE_IMPLEMENTED_BY = "implemented_by"

#: 內文中可以使用的邊型別。implemented_by 不在此列——它指向的是程式碼符號
#: 而非業務節點，寫在內文會產生一條指向不存在節點的懸空邊。
INLINE_EDGE_TYPES: tuple[str, str, str, str] = (
    EDGE_DEPENDS_ON,
    EDGE_OVERRIDES,
    EDGE_EXCEPTION_TO,
    EDGE_RELATES_TO,
)

#: 計入影響半徑的邊型別；relates_to 無方向語意，不參與影響傳遞。
IMPACT_EDGE_TYPES: tuple[str, str, str] = (
    EDGE_DEPENDS_ON,
    EDGE_OVERRIDES,
    EDGE_EXCEPTION_TO,
)

#: 推測未知名稱是否為關係前綴：冒號前全為小寫字母與底線，
#: 且冒號後緊接非空白字元。已知關係另行優先辨識，不受空白或空目標影響。
#: 這兩個條件把正常標題（含大寫、數字、連字號，或冒號後有空格者）排除在外，
#: 剩下的形態幾乎只可能是有意寫成關係前綴，因此不在合法集合時報錯是安全的。
_PREFIXED = re.compile(r"\A([a-z_]+):(\S.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Edge:
    """一條從某節點指出的邊。

    欄位:
        edge_type: 邊型別；解析失敗時為 EDGE_RELATES_TO（佔位，不應被採用）。
        target: 目標節點的原始標題文字。
        error: 解析失敗的原因，成功時為 None。
    """

    edge_type: str
    target: str
    error: str | None


def parse_edge_target(raw: str) -> Edge:
    """把 [[...]] 內的原始文字解析成一條邊。

    不拋例外：一個節點的內文寫錯不得中斷整體解析，錯誤原因由呼叫端
    （business_write 拒絕寫入、business_lint 列出）呈現。

    參數:
        raw: [[ 與 ]] 之間的文字，已去除前後空白。

    回傳:
        解析結果；error 不為 None 時表示這條邊不可採用。
    """
    合法值 = "、".join(INLINE_EDGE_TYPES)
    prefix, separator, target = raw.partition(":")
    if not separator or prefix not in (*INLINE_EDGE_TYPES, EDGE_IMPLEMENTED_BY):
        match = _PREFIXED.match(raw)
        if match is None:
            if not raw:
                return Edge(EDGE_RELATES_TO, raw, "連結目標不可為空。")
            return Edge(EDGE_RELATES_TO, raw, None)
        prefix, target = match.groups()

    target = target.strip()
    if prefix == EDGE_IMPLEMENTED_BY:
        return Edge(
            EDGE_RELATES_TO,
            target,
            f"[[{raw}]]：implemented_by 不可寫在內文，它指向的是程式碼符號"
            "而非業務節點。請改寫進 front matter 的 code 欄位。",
        )
    if not target:
        return Edge(EDGE_RELATES_TO, raw, f"[[{raw}]] 的連結目標不可為空。")
    if prefix not in INLINE_EDGE_TYPES:
        return Edge(
            EDGE_RELATES_TO,
            target,
            f"[[{raw}]] 的關係 {prefix!r} 無法辨識；內文可用的關係為 {合法值}。",
        )
    return Edge(prefix, target, None)
