"""知識庫的一致性稽核。

產出的是可直接處理的修正清單，不是只有問題摘要：過期、未確認、程式碼錨點
等特定區段會附上 base_hash 與 business_write 指引；建立缺少節點、人工判讀、
版本控制或檔案配置問題則提供各自的下一步。server 不判斷語意上該怎麼修，
只把該判斷的項目推到 agent 眼前。
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

from business_graph_mcp.codegraph import find_codegraph_db, resolve_symbols_checked
from business_graph_mcp.db.queries import (
    all_code_anchors,
    all_content_hashes,
    all_dangling,
    ambiguous_dangling_titles,
    frequent_misses,
    inferred_nodes,
    node_count,
    orphan_nodes,
    rule_nodes_without_code,
    stale_nodes,
    unlinked_mentions,
)
from business_graph_mcp.engine import Session
from business_graph_mcp.workspace import Workspace, find_git_boundary

#: 共現線索比對的詞（標題或別名）最短長度。2–3 字的詞太泛用（如 "API"、
#: "Rules"），會在任意頁面內文中命中大量不相干位置，產生的線索幾乎沒有
#: 判讀價值，是雜訊的主要來源；中文與英文用同一個門檻，不為兩者分開處理，
#: 複雜度不划算。
MIN_MENTION_TERM_LENGTH = 4

#: 共現線索最多回報幾組。全表自連接＋子字串掃描的結果量不設限會讓單次
#: 稽核輸出被灌爆；超出上限時必須在輸出中明說「另有未列出」，不得靜默截斷。
MAX_MENTIONS_REPORTED = 50

#: 共現線索比對的頁數上限。比對是 O(頁數²) 的全表自連接，頁數一旦超過此
#: 門檻，成本已不合理，直接跳過整個檢查；跳過本身也要在輸出中明說，
#: 避免使用者把「沒有共現線索」誤解成「已經檢查過、沒問題」。
MAX_PAGES_FOR_MENTION_SCAN = 2000


def uncommitted_pages(ws: Workspace) -> tuple[str, ...] | None:
    """列出 nodes/ 中尚未提交的變更。

    git 不存在、或此知識庫不屬於任何自己的 repository 時回傳 None，
    呼叫端應據此靜默跳過此項——git 是選用相依，缺少它不該影響其餘稽核結果。

    邊界判定沿用 workspace.find_git_boundary（與 find_workspace／
    ensure_workspace 同一套規則），而不是自行判斷「有沒有 .git」：
    若只看 subprocess 的回傳碼，當這個知識庫位於某個無關的祖先 repo
    底下（例如使用者家目錄的 dotfiles repo）時，會誤把那個無關 repo
    的狀態當成本知識庫的狀態回報，那不是使用者想知道的事。

    參數:
        ws: 知識庫。

    回傳:
        未提交的檔案狀態列；無法檢查時為 None。
    """
    if shutil.which("git") is None:
        return None
    boundary = find_git_boundary(ws.root, Path.home())
    if boundary is None:
        return None
    try:
        completed = subprocess.run(
            # --untracked-files=all：git 預設會把「整個未追蹤的目錄」收合成
            # 一行（如 .bgraph/nodes/），本函式需要的是逐檔清單，故明確展開。
            ["git", "status", "--porcelain", "--untracked-files=all", "--", str(ws.nodes_dir)],
            cwd=boundary,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def run_lint(session: Session, today: date) -> str:
    """執行全部稽核項目並組成可執行的修正清單。

    參數:
        session: 已開啟的工具執行環境。
        today: 判定基準日。
    """
    conn = session.conn
    sections: list[str] = []

    cutoff = (today - timedelta(days=session.cfg.stale_days)).isoformat()

    stale = stale_nodes(conn, cutoff)
    if stale:
        lines = [
            f"- 〈{p.slug}〉最後更新於 {p.updated_at}。若已能確認現況，"
            f"用 business_write 更新（base_hash: {p.content_hash}）。"
            for p in stale
        ]
        sections.append(
            f"## 過期頁（超過 {session.cfg.stale_days} 天未更新）\n" + "\n".join(lines)
        )

    inferred = inferred_nodes(conn)
    if inferred:
        lines = [
            f"- 〈{p.slug}〉為 inferred。向業務確認後用 business_write 改為 confirmed"
            f"（base_hash: {p.content_hash}）。"
            for p in inferred
        ]
        sections.append("## 未確認頁\n" + "\n".join(lines))

    dangling = all_dangling(conn)
    if dangling:
        ambiguous = ambiguous_dangling_titles(conn)
        lines = []
        for source, target in dangling:
            candidates = ambiguous.get(target)
            if candidates:
                joined = "、".join(candidates)
                lines.append(
                    f"- 〈{source}〉引用的 [[{target}]] 同時符合多個頁面標題"
                    f"（{joined}），無法判定目標。請將重複 title 改名，"
                    "或把引用改成明確的 slug 或 alias。"
                )
            else:
                lines.append(
                    f"- 〈{source}〉引用了不存在的 [[{target}]]。"
                    "用 business_write 建立該頁，或更正引用。"
                )
        sections.append("## 懸空連結\n" + "\n".join(lines))

    orphans = orphan_nodes(conn)
    if orphans:
        lines = [
            f"- 〈{p.slug}〉沒有任何頁面引用，需人工判讀：若它該被某頁引用，"
            f"用 business_write 補上該頁的連結；若內容已可併入其他頁，"
            f"用 business_write 合併後再考慮是否移除本頁（base_hash: {p.content_hash}）。"
            for p in orphans
        ]
        sections.append("## 孤兒頁（需人工判讀）\n" + "\n".join(lines))

    misses = frequent_misses(conn, session.cfg.miss_threshold)
    if misses:
        lines = [
            f"- 「{query}」已查無結果 {count} 次。這是高價值知識缺口，"
            "弄清楚後用 business_write 補上。"
            for query, count in misses
        ]
        sections.append("## 知識缺口\n" + "\n".join(lines))

    total_pages = node_count(conn)
    if total_pages > MAX_PAGES_FOR_MENTION_SCAN:
        # 頁數上限：全表自連接的成本已不合理，直接跳過整個檢查，但必須
        # 明說已跳過——靜默跳過會讓人誤以為「沒有共現線索」等於「已檢查過」。
        sections.append(
            f"## 共現線索（需人工判讀）\n頁數 {total_pages} 超過上限 "
            f"{MAX_PAGES_FOR_MENTION_SCAN}，本次略過共現線索檢查。"
        )
    else:
        # 多取一筆（limit + 1）以判斷結果是否被截斷；不精確計算超出筆數，
        # 避免為了算出確切數字而多跑一次同樣代價的全表掃描。
        raw_mentions = unlinked_mentions(
            conn, MIN_MENTION_TERM_LENGTH, MAX_MENTIONS_REPORTED + 1
        )
        if raw_mentions:
            truncated = len(raw_mentions) > MAX_MENTIONS_REPORTED
            shown = raw_mentions[:MAX_MENTIONS_REPORTED]
            lines = [
                f"- 〈{mentioner}〉的內文提及〈{mentioned}〉但未以 [[ ]] 連結。"
                "讀過兩頁，判斷該補連結還是兩者互相矛盾。"
                for mentioned, mentioner in shown
            ]
            if truncated:
                lines.append(
                    f"- 已達回報上限 {MAX_MENTIONS_REPORTED} 組，另有更多組未列出。"
                )
            sections.append("## 共現線索（需人工判讀）\n" + "\n".join(lines))

    uncommitted = uncommitted_pages(session.ws)
    if uncommitted:
        lines = [f"- {item}" for item in uncommitted]
        sections.append(
            "## 未提交的變更\n"
            "知識寫了但沒 commit，等於團隊沒拿到，且下次覆蓋就永久遺失歷史。\n"
            + "\n".join(lines)
        )

    if session.sync.ignored:
        # 不在 brief 的介面清單內，是刻意保留的項目：nodes/ 子目錄裡的 .md
        # 會被同步邏輯靜默忽略而不進索引，這正是使用者最難自行察覺的失敗，
        # lint 就是該讓這件事被看見的地方，故意露出而非只有 sync 內部知道。
        清單 = "、".join(session.sync.ignored)
        sections.append(
            f"## 未進索引的檔案\n- 下列檔案位於 nodes/ 的子目錄中：{清單}。"
            "nodes/ 依設計為平鋪結構，請移到第一層。"
        )

    # 索引必須能完成全部查詢，才能把缺少的結果判為查無符號。
    # 沒有安裝 codegraph 或索引損壞都不算知識庫缺陷。
    db_path = find_codegraph_db(session.ws.root)
    if db_path is not None:
        anchors = all_code_anchors(conn)
        resolved, complete = resolve_symbols_checked(
            db_path, sorted({symbol for _, symbol in anchors})
        )
        hashes = all_content_hashes(conn)
        unresolved = [
            (slug, symbol, hashes[slug])
            for slug, symbol in anchors if complete and symbol not in resolved
        ]
        if unresolved:
            lines = [
                f"- 〈{slug}〉的錨點 {symbol} 在 codegraph 索引中查無。"
                "確認該符號是否已改名或刪除，並用 business_write 更新 code 欄位"
                f"（base_hash: {content_hash}）。"
                for slug, symbol, content_hash in unresolved
            ]
            sections.append("## 未解析的程式碼錨點\n" + "\n".join(lines))

    # 只檢查 rule：其他型別不一定有對應實作；此檢查不依賴 codegraph。
    without_code = rule_nodes_without_code(conn)
    if without_code:
        lines = [
            f"- 〈{p.slug}〉是業務規則但沒有對應的程式碼錨點。"
            f"若已有實作，用 business_write 補上 code（base_hash: {p.content_hash}）。"
            for p in without_code
        ]
        sections.append("## 規則未連到實作\n" + "\n".join(lines))

    if not sections:
        return "稽核完成，沒有發現需要處理的項目。\n"
    return "\n\n".join(sections) + "\n"
