"""recall 回應的提醒與待辦判定。

全部為確定性規則，不需要語意能力：server 只負責把「該判斷的項目」
推到 agent 眼前，判斷本身由 agent 完成（見 Spec 第 2.2 節的分工）。

措辭規則：
- 前綴警告影響「怎麼讀」，必須在內容之前出現，否則 agent 會把推測當事實。
- 後綴待辦影響「讀完做什麼」，緊鄰 agent 準備下一步動作的位置。
- 一律祈使句。「建議補上」會被忽略，「用 business_write 補上」才會被執行。
- 不得編造上下文：可以說「若本次工作已能確認」，不可以說「你正在讀某某程式碼」。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from business_graph_mcp.config import GraphConfig
from business_graph_mcp.explore import ExploreResult
from business_graph_mcp.node import STATUS_INFERRED


@dataclass(frozen=True)
class Notices:
    """一次 recall 要附帶的提醒。

    欄位:
        prefix: 放在內容之前的警告，影響 agent 怎麼讀這些內容。
        todos: 放在內容之後的待辦，影響 agent 讀完之後做什麼。
    """

    prefix: tuple[str, ...]
    todos: tuple[str, ...]


def _days_since(updated_at: str | None, today: date) -> int | None:
    """算出距今幾天；日期無法解析時回傳 None。

    參數:
        updated_at: ISO 格式日期字串，解析失敗的頁為 None。
        today: 判定基準日，由呼叫端傳入以便測試。
    """
    if not updated_at:
        return None
    try:
        return (today - date.fromisoformat(updated_at)).days
    except ValueError:
        return None


def _listed(names: list[str]) -> str:
    """把頁名清單組成可讀的字串。"""
    return "、".join(f"〈{name}〉" for name in names)


def build_notices(
    result: ExploreResult,
    cfg: GraphConfig,
    *,
    query: str,
    miss_count: int,
    dangling: dict[str, tuple[str, ...]],
    today: date,
) -> Notices:
    """依 recall 結果產生前綴警告與後綴待辦。

    同類警告合併成一則並列出所有相關頁名，不逐頁各發一則——
    警報一多，agent 就會開始無視，那是提醒機制唯一的死法。

    參數:
        result: recall 的結果。
        cfg: 知識庫設定，提供過期與信心門檻。
        query: 原始查詢字串，用於零命中時的待辦文字。
        miss_count: 本次查詢累計的未命中次數（含本次）。
        dangling: 命中頁的懸空連結，來自 db.queries.dangling_targets。
        today: 判定基準日。
    """
    prefix: list[str] = []
    todos: list[str] = []

    if not result.hits:
        todos.append(
            f"知識圖中查無「{query}」的記錄。若本次工作已弄清楚答案，"
            "用 business_write 建立新頁記下來。"
        )
        if miss_count >= cfg.miss_threshold:
            todos.append(
                f"「{query}」已是第 {miss_count} 次查無結果，屬高價值知識缺口，"
                "優先補上。"
            )
        return Notices(prefix=tuple(prefix), todos=tuple(todos))

    低信心 = result.top_score < cfg.low_confidence_score
    if 低信心:
        prefix.append(
            "本次命中的信心不高，內容可能不是你要找的。"
            "若答案不在其中，用 business_write 補上正確內容。"
        )

    broken = [h.page for h in result.hits if h.page.parse_error is not None]
    if broken:
        prefix.append(
            f"{_listed([p.slug for p in broken])} 的 front matter 解析失敗，"
            "其中繼資料（status 與更新日期）為未知，不可當作已確認內容引用。"
        )

    inferred = [
        h.page for h in result.hits if h.page.status == STATUS_INFERRED
    ]
    if inferred:
        prefix.append(
            f"{_listed([p.slug for p in inferred])} 為推測內容（status: inferred），"
            "尚未經業務確認，引用前請向業務確認。"
        )

    stale: list[tuple[str, int, str]] = []
    for hit in result.hits:
        days = _days_since(hit.page.updated_at, today)
        if days is not None and days > cfg.stale_days:
            stale.append((hit.page.slug, days, hit.page.content_hash))
    if stale:
        描述 = "、".join(f"〈{slug}〉{days} 天" for slug, days, _ in stale)
        prefix.append(f"下列頁面長期未更新，內容可能已與現況不符：{描述}。")

    if 低信心:
        # Spec 第 4.3 節：後綴措辭須同時涵蓋「零命中」與「命中但不足」，
        # 不可只在 prefix 提醒信心不足卻不附對應的待辦動作。
        todos.append(
            f"若本次命中的內容未回答「{query}」，用 business_write 補上正確內容。"
        )

    for page in inferred:
        todos.append(
            f"〈{page.slug}〉標記為 inferred。若本次工作已能確認其內容，"
            f"用 business_write 更正並改為 confirmed（base_hash: {page.content_hash}）。"
        )
    for slug, days, digest in stale:
        todos.append(
            f"〈{slug}〉已 {days} 天未更新。若本次工作已能確認現況，"
            f"用 business_write 更新（base_hash: {digest}）。"
        )
    for slug in sorted(dangling):
        targets = "、".join(f"[[{t}]]" for t in dangling[slug])
        todos.append(
            f"〈{slug}〉引用了不存在的頁面 {targets}。"
            "用 business_write 建立該頁，或更正引用。"
        )

    return Notices(prefix=tuple(prefix), todos=tuple(todos))
