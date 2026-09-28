"""單次工具呼叫的執行環境。

每次呼叫都走同一條路：定位知識庫 → 開索引 → lazy sync → 交給工具邏輯。
同步放在這裡而非各工具內，確保三個工具看到的都是最新狀態。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from business_graph_mcp.config import GraphConfig, load_config
from business_graph_mcp.db.queries import open_index
from business_graph_mcp.errors import GraphNotFoundError
from business_graph_mcp.sync import SyncResult, sync_index
from business_graph_mcp.workspace import Workspace, ensure_workspace, find_workspace


@dataclass
class Session:
    """一次工具呼叫的執行環境。

    欄位:
        ws: 知識庫。
        conn: 索引連線。
        cfg: 知識庫設定。
        sync: 本次進場時的同步結果，供回應標示被忽略或失敗的檔案。
    """

    ws: Workspace
    conn: sqlite3.Connection
    cfg: GraphConfig
    sync: SyncResult


@contextmanager
def open_session(project_path: str | None, *, create: bool = False) -> Iterator[Session]:
    """開啟一次工具呼叫所需的全部資源，結束時關閉連線。

    參數:
        project_path: 起始路徑；省略時使用行程的工作目錄。
        create: 找不到 .bgraph/ 時是否建立。讀取類工具應為 False，
            寫入類工具為 True——第一次寫入即是初始化，不另設 init 指令。

    例外:
        GraphNotFoundError: create=False 且向上找不到 .bgraph/。
    """
    start = Path(project_path) if project_path else Path.cwd()

    if create:
        ws = ensure_workspace(start)
    else:
        found = find_workspace(start)
        if found is None:
            raise GraphNotFoundError(
                f"從 {start} 向上找不到 .bgraph/ 目錄，本專案尚未建立知識圖。"
                "第一次呼叫 business_write 就會自動建立。"
            )
        ws = found

    cfg = load_config(ws.config_path)
    conn = open_index(ws.db_path)
    try:
        result = sync_index(conn, ws, datetime.now())
        yield Session(ws=ws, conn=conn, cfg=cfg, sync=result)
    finally:
        conn.close()
