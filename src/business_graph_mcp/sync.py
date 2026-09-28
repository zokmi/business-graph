"""markdown 頁面與索引之間的增量同步。

每次工具呼叫前執行一次。不使用 file watcher 或常駐 daemon——
.bgraph/ 只有數百個小檔，掃描與雜湊比對在毫秒等級，
背景程序帶來的生命週期成本遠高於它省下的時間（見 Spec 第 5.2 節）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from business_graph_mcp.db.queries import (
    all_content_hashes,
    delete_node,
    resolve_edges,
    upsert_node,
)
from business_graph_mcp.node import content_hash, parse_node
from business_graph_mcp.workspace import Workspace


@dataclass(frozen=True)
class SyncResult:
    """一次同步的結果統計。

    欄位:
        added: 新加入索引的頁數。
        updated: 內容有變而重建的頁數。
        removed: 檔案已刪除而從索引移除的頁數。
        unchanged: 內容未變、直接跳過的頁數。
        failed: front matter 解析失敗或讀檔失敗的頁面 slug；
            這些頁仍會盡可能進入索引，只是失去中繼資料。
        ignored: 被略過而未進索引的檔案（相對於 nodes/ 的路徑）。
            目前只有子目錄中的 .md——nodes/ 依設計為平鋪結構。
    """

    added: int
    updated: int
    removed: int
    unchanged: int
    failed: tuple[str, ...]
    ignored: tuple[str, ...]


def sync_index(conn: sqlite3.Connection, ws: Workspace, now: datetime) -> SyncResult:
    """把 nodes/ 的現況同步進索引。

    比對順序為「先移除、後寫入」：改名會呈現為一刪一增，且連結會在
    寫入階段重新解析，因此結束後 edges 表必定反映最新狀態。

    參數:
        conn: 索引連線。
        ws: 知識庫。
        now: 本次同步時間，寫進 nodes.indexed_at。
    """
    # 只有來源確實不存在時才視為空集合；權限或其他讀取錯誤必須中止，
    # 不可把「無法列出檔案」誤判成全部刪除。
    try:
        entries = list(ws.nodes_dir.iterdir())
    except FileNotFoundError:
        entries = []

    existing = all_content_hashes(conn)

    ignored: list[str] = []
    for path in sorted(ws.nodes_dir.rglob("*.md")):
        if path.parent != ws.nodes_dir:
            ignored.append(path.relative_to(ws.nodes_dir).as_posix())

    on_disk = sorted(p for p in entries if p.match("*.md") and p.is_file())
    disk_slugs = {p.stem for p in on_disk}

    removed = 0
    for slug in sorted(set(existing) - disk_slugs):
        delete_node(conn, slug)
        removed += 1

    added = updated = unchanged = 0
    failed: list[str] = []

    for path in on_disk:
        slug = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            # 讀不到的檔不能中斷整體同步，但必須被看見。若它先前已在索引中，
            # 保留舊資料——舊的內容仍比沒有內容有用。
            failed.append(slug)
            if slug not in existing:
                upsert_node(
                    conn,
                    slug=slug,
                    title=slug,
                    node_type=None,
                    aliases=(),
                    body="",
                    path=path.relative_to(ws.root).as_posix(),
                    content_hash="",
                    status=None,
                    updated_at=None,
                    parse_error=f"無法讀取檔案：{exc}",
                    edges=(),
                    code=(),
                    indexed_at=now,
                )
            continue

        digest = content_hash(text)
        if existing.get(slug) == digest:
            unchanged += 1
            continue

        parsed = parse_node(text)
        if parsed.parse_error is not None:
            failed.append(slug)

        meta = parsed.meta
        upsert_node(
            conn,
            slug=slug,
            title=meta.title if meta else slug,
            node_type=meta.node_type if meta else None,
            aliases=meta.aliases if meta else (),
            body=parsed.body,
            path=path.relative_to(ws.root).as_posix(),
            content_hash=digest,
            status=meta.status if meta else None,
            updated_at=meta.updated.isoformat() if meta else None,
            parse_error=parsed.parse_error,
            edges=parsed.edges if meta else (),
            code=meta.code if meta else (),
            indexed_at=now,
        )
        if slug in existing:
            updated += 1
        else:
            added += 1

    # 更新 title 或 aliases 也會改變其他頁面既有連結的解析結果。
    if added or updated or removed:
        resolve_edges(conn)

    return SyncResult(
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        failed=tuple(sorted(failed)),
        ignored=tuple(sorted(ignored)),
    )
