"""recall 回應的文字組裝。

版面順序是功能決定的，不是美觀決定的：前綴警告影響 agent 怎麼讀內容，
必須先出現；後綴待辦影響它讀完做什麼，必須緊鄰下一步動作的位置。
"""
from __future__ import annotations

import json

from business_graph_mcp.banners import Notices
from business_graph_mcp.db.queries import NodeRow
from business_graph_mcp.explore import ExploreResult, Neighbor, lead_paragraph
from business_graph_mcp.sync import SyncResult


def _relation_label(relation: str) -> str:
    """把關係代碼轉成中文說明。"""
    return "被命中頁引用" if relation == "references" else "引用了命中頁"


def _page_heading(
    title: str, slug: str, status: str | None, updated_at: str | None, digest: str
) -> str:
    """組出一頁的標題行，含狀態、更新日期與 base_hash。"""
    狀態 = status or "狀態未知"
    日期 = updated_at or "日期未知"
    return f"### {title} · slug: {slug} · {狀態} · 更新於 {日期} · base_hash: {digest}"


def _sync_notes(sync: SyncResult) -> list[str]:
    """組出同步過程中需要讓 agent 看見的事項。

    被忽略或讀取失敗的檔案若靜默略過，使用者會以為內容已寫進知識圖
    卻永遠查不到，那是最難察覺的失敗。
    """
    notes: list[str] = []
    if sync.ignored:
        清單 = "、".join(sync.ignored)
        notes.append(
            f"⚠ 下列檔案位於 nodes/ 的子目錄中而未進入索引：{清單}。"
            "nodes/ 依設計為平鋪結構，請把檔案移到 nodes/ 第一層。"
        )
    if sync.failed:
        清單 = "、".join(sync.failed)
        notes.append(
            f"⚠ 下列頁面的 front matter 解析失敗，內文仍可檢索但缺少中繼資料：{清單}。"
        )
    return notes


def _metadata_lines(page: NodeRow, code: tuple[str, ...]) -> list[str]:
    """提供完整節點寫回時必須保留的欄位，避免省略即清空的資料遺失。"""
    metadata = {"type": page.node_type, "aliases": page.aliases, "code": code}
    return ["", "寫回時請保留以下中繼資料（除非本次要修改）：", "```json",
            json.dumps(metadata, ensure_ascii=False), "```", ""]


def _neighbor_lines(
    full: tuple[Neighbor, ...], brief: tuple[Neighbor, ...],
    anchors: dict[str, tuple[str, ...]],
) -> list[str]:
    """組出鄰居頁的輸出區塊。"""
    lines: list[str] = []
    if full:
        lines.append("## 關聯頁面")
        for n in full:
            lines.append("")
            lines.append(
                _page_heading(
                    n.page.title, n.page.slug, n.page.status,
                    n.page.updated_at, n.page.content_hash,
                )
                + f"（{_relation_label(n.relation)}）"
            )
            lines.extend(_metadata_lines(n.page, anchors.get(n.page.slug, ())))
            lines.append(n.page.body)
    if brief:
        lines.append("")
        lines.append("## 關聯頁面（預算不足，僅列首段）")
        lines.append("")
        # 刻意不在這段附提示以外的內容,只給一句指示,理由見下方逐頁迴圈的註解。
        lines.append(
            "要修改下列頁面，須先呼叫 business_explore 取得其完整內容與 base_hash——"
            "只讀過首段不得直接覆寫。"
        )
        for n in brief:
            lines.append("")
            # 刻意不附 base_hash：這裡只顯示首段，agent 尚未讀過全文。
            # base_hash 樂觀鎖的用意正是逼 agent 寫入前先看過現況；若在此附上
            # hash，agent 就能拿著只讀過首段的認知覆寫整頁，那正是這個機制
            # 要防止的失敗。少一次 recall 往返，換來的是覆蓋掉沒看過的內容，
            # 這個交換不划算——因此簡要鄰居沒有 base_hash 是設計，不是漏寫。
            lines.append(f"### {n.page.title}（{_relation_label(n.relation)}）")
            lines.append("")
            lines.append(lead_paragraph(n.page.body))
    return lines


def _path_lines(result: ExploreResult) -> list[str]:
    """組出關係路徑區塊，以縮排表示各步驟距離起點的深度。"""
    有內容 = {slug: steps for slug, steps in result.paths.items() if steps}
    if not 有內容:
        return []
    lines = ["", "## 關係路徑"]
    for slug in sorted(有內容):
        lines.append("")
        lines.append(slug)
        for step in 有內容[slug]:
            縮排 = "  " * step.depth
            prefix = f"{step.source} " if step.source else ""
            lines.append(f"{縮排}{prefix}--{step.edge_type}--> {step.target}")
    return lines


def _blast_lines(result: ExploreResult) -> list[str]:
    """組出影響半徑摘要，不展開內文以保留命中節點的預算。"""
    if not result.blast:
        return []
    lines = ["", f"影響半徑：{len(result.blast)} 個節點會受本次命中的節點影響"]
    for entry in result.blast:
        經由 = f"，經 {' → '.join(entry.via)}" if entry.via else ""
        lines.append(f"  {entry.slug} ({entry.edge_type}{經由})")
    return lines


def _anchor_lines(result: ExploreResult, anchor_locations: dict[str, str]) -> list[str]:
    """組出程式碼錨點位置；原始碼由呼叫端另呼 codegraph_explore 取得。"""
    有錨點 = {slug: symbols for slug, symbols in result.anchors.items() if symbols}
    if not 有錨點:
        return []
    lines = ["", "## 程式碼錨點"]
    for slug in sorted(有錨點):
        lines.append("")
        lines.append(f"### {slug}")
        for symbol in 有錨點[slug]:
            位置 = anchor_locations.get(symbol, "未解析")
            lines.append(f"{symbol} — {位置}")
    return lines


def format_explore(
    result: ExploreResult, notices: Notices, sync: SyncResult, anchor_locations: dict[str, str]
) -> str:
    """把 explore 結果組成要回給 agent 的完整文字。

    參數:
        result: 檢索與預算配置的結果。
        notices: 提醒與待辦。
        sync: 本次進場的同步結果。
        anchor_locations: 符號名對應到已解析的位置字串；未解析者不在其中。
    """
    lines: list[str] = []

    for note in _sync_notes(sync):
        lines.append(note)
    for warning in notices.prefix:
        lines.append(f"⚠ {warning}")
    if lines:
        lines.append("")

    if result.hits:
        lines.append("## 命中")
        for hit in result.hits:
            lines.append("")
            lines.append(
                _page_heading(
                    hit.page.title, hit.page.slug, hit.page.status,
                    hit.page.updated_at, hit.page.content_hash,
                )
            )
            lines.extend(_metadata_lines(hit.page, result.anchors.get(hit.page.slug, ())))
            lines.append(hit.page.body)
        lines.extend(_neighbor_lines(
            result.neighbors_full, result.neighbors_brief, result.anchors,
        ))
        lines.extend(_path_lines(result))
        lines.extend(_blast_lines(result))
        lines.extend(_anchor_lines(result, anchor_locations))
    else:
        lines.append("## 命中")
        lines.append("")
        lines.append("查無相關記錄。")

    if notices.todos:
        lines.append("")
        lines.append("---")
        lines.append("📝 待辦（本次 recall 觸發）")
        for index, todo in enumerate(notices.todos, start=1):
            lines.append(f"{index}. {todo}")

    return "\n".join(lines).strip() + "\n"
