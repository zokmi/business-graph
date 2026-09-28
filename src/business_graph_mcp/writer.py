"""頁面寫入與樂觀鎖。

寫入採整頁覆蓋。Spec 第 5.1 節的三種修正形態（補充、更正、取代）都由
覆蓋完成，不另設工具：補充是「原文加新段落」，更正是「改寫內容」，
取代是「改寫並在頁內保留舊規則的生效期間」。哪一種適用是語意判斷，
由 agent 決定，server 只確保它寫之前看過現況。
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from business_graph_mcp.db.queries import dangling_targets, get_node
from business_graph_mcp.engine import Session
from business_graph_mcp.errors import GraphConflictError, GraphError, GraphIndexError
from business_graph_mcp.node import (
    VALID_NODE_TYPES,
    VALID_STATUSES,
    NodeMeta,
    content_hash,
    extract_edges,
    render_node,
    slugify,
)
from business_graph_mcp.sync import sync_index


@dataclass(frozen=True)
class WriteOutcome:
    """一次寫入的結果。

    欄位:
        slug: 實際寫入的頁面識別字（標題經檔名安全化後的結果）。
        created: True 表示新建，False 表示更新既有頁。
        base_hash: 寫入後的內容雜湊，供接續更新時帶回。
        dangling: 本頁引用但目前不存在的頁面。
    """

    slug: str
    created: bool
    base_hash: str
    dangling: tuple[str, ...] | None
    indexed: bool = True
    warning: str | None = None


def _atomic_write(path: Path, text: str) -> None:
    """把整份內容原子性地寫入 path。

    直接 path.write_text() 看起來更簡單，但行程若在寫入中途被中斷
    （crash、OOM、斷電、被 kill），會在磁碟上留下截斷或空白的頁面，
    而且沒有前一版可回復——對一份「業務規則的可信真相」而言，
    這個風險不可接受。

    做法是先寫到同目錄下的暫存檔，再用 os.replace() 換名。同一磁碟區內
    的 rename 在 POSIX 與 Windows 上都是原子操作，中斷只會留下暫存檔
    （不影響正式檔），或正式檔已完整換新，不存在「半份內容」的中間態。
    暫存檔必須建在同一目錄——跨磁碟區的 os.replace() 不保證原子性。

    參數:
        path: 目標檔案路徑。
        text: 要寫入的完整內容。
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
            # 斷電時 rename 的 metadata 可能先於資料落盤；flush + fsync
            # 確保暫存檔的內容在換名前已確實寫入磁碟，換名後不會出現空檔。
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # 換名失敗（或寫入階段失敗）時清掉暫存檔，避免在 nodes/ 底下
        # 留下垃圾——即使副檔名不是 .md 不會被 sync 掃到，也不該留著。
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise


def write_node(
    session: Session,
    *,
    title: str,
    node_type: str,
    content: str,
    status: str,
    aliases: tuple[str, ...],
    code: tuple[str, ...],
    base_hash: str | None,
    today: date,
) -> WriteOutcome:
    """建立或更新一頁，並即時重建索引。

    參數:
        session: 已開啟的工具執行環境。
        title: 頁面標題。
        node_type: 節點型別，rule、entity、decision 或 term。
        content: 頁面內文，不含 front matter——front matter 由 server 管理。
        status: confirmed 或 inferred，必填。
        aliases: 別名；中文術語應寫在這裡，讓中英文都查得到。
        code: 對應的程式碼符號名稱。
        base_hash: 更新既有頁時必填，須與現況相符；新頁不得提供。
        today: 寫入日期，戳進 front matter 的 updated 欄位。

    例外:
        GraphError: status 或節點型別不合法、內文關係錯誤、內容為空，
            或 base_hash 的有無與頁面存在與否不一致。
        GraphConflictError: base_hash 與現況不符；訊息帶回現況全文。
    """
    if status not in VALID_STATUSES:
        valid = "、".join(VALID_STATUSES)
        raise GraphError(
            f"status 只接受 {valid}；收到的是 {status!r}。"
            "業務方確認過才是 confirmed，從程式碼或推理得出的一律 inferred。"
        )
    if node_type not in VALID_NODE_TYPES:
        valid = "、".join(VALID_NODE_TYPES)
        raise GraphError(
            f"type 只接受 {valid}；收到的是 {node_type!r}。"
            "rule 是業務規則，entity 是實體或欄位，decision 是決策與理由，"
            "term 是術語。"
        )

    # 在覆蓋檔案前拒絕錯誤關係，避免語意錯誤的邊混進知識圖。
    邊錯誤 = [edge.error for edge in extract_edges(content) if edge.error is not None]
    if 邊錯誤:
        raise GraphError("\n".join(邊錯誤))
    if not content.strip():
        raise GraphError("內容不可為空。空頁沒有知識價值，且會污染檢索結果。")

    slug = slugify(title)
    if not slug:
        raise GraphError(f"標題 {title!r} 無法轉成合法檔名，請改用其他標題。")

    path = session.ws.nodes_dir / f"{slug}.md"
    exists = path.is_file()

    if exists:
        current = path.read_text(encoding="utf-8")
        current_hash = content_hash(current)
        if base_hash is None:
            raise GraphError(
                f"〈{slug}〉已存在，更新既有頁必須提供 base_hash。"
                "先用 business_explore 讀過現況再寫，避免覆蓋掉你沒看過的內容。"
            )
        if base_hash != current_hash:
            raise GraphConflictError(
                f"〈{slug}〉的 base_hash 與現況不符（你提供 {base_hash}，"
                f"現況為 {current_hash}），檔案在這期間被改過。"
                f"請依下列現況重做這次更新：\n\n{current}"
            )
    elif base_hash is not None:
        raise GraphError(
            f"〈{slug}〉不存在，新頁不可提供 base_hash。"
            "若你要更新的是別的頁面，請確認標題是否打錯。"
        )

    text = render_node(
        NodeMeta(
            title=title.strip(),
            node_type=node_type,
            aliases=aliases,
            code=code,
            status=status,
            updated=today,
        ),
        content,
    )
    session.ws.nodes_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, text)

    # Markdown 已原子替換，後續任何索引或回應查詢失敗都必須保留新雜湊。
    digest = content_hash(text)
    try:
        sync_result = sync_index(session.conn, session.ws, datetime.now())
        indexed = get_node(session.conn, slug)
        if (
            slug in sync_result.failed
            or indexed is None
            or indexed.content_hash != digest
            or indexed.parse_error is not None
        ):
            raise GraphIndexError(f"〈{slug}〉本次未成功進入索引。")
        dangling = dangling_targets(session.conn, [slug]).get(slug, ())
    except Exception as exc:
        return WriteOutcome(
            slug=slug,
            created=not exists,
            base_hash=digest,
            dangling=None,
            indexed=False,
            warning=f"Markdown 已保存，但索引同步或驗證失敗；下次工具呼叫會重試：{exc}",
        )

    return WriteOutcome(
        slug=slug,
        created=not exists,
        base_hash=digest,
        dangling=dangling,
    )
