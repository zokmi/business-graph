"""SQLite 連線建立與執行環境檢查。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from business_graph_mcp.errors import GraphIndexError


def ensure_fts5_available() -> None:
    """確認本機 Python 內建的 sqlite3 有編入 FTS5 模組。

    全文檢索是本服務的核心，FTS5 缺席時查詢會回空結果而非報錯，
    是最難診斷的失敗模式。因此在啟動時明確檢查並給出可行的解法。

    例外:
        GraphIndexError: FTS5 不可用。
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
    except sqlite3.OperationalError as exc:
        raise GraphIndexError(
            "本機 Python 內建的 SQLite 未啟用 FTS5 模組，business-graph 無法運作。"
            "請改用官方發行版的 Python（python.org 或 uv 安裝的版本皆已啟用 FTS5），"
            "或重新編譯 SQLite 時加上 -DSQLITE_ENABLE_FTS5。"
        ) from exc
    finally:
        conn.close()


def connect(db_path: Path) -> sqlite3.Connection:
    """開啟索引資料庫連線。

    參數:
        db_path: index.db 的完整路徑；所在目錄須已存在。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL 讓讀取不被寫入阻塞；是否重建由 db.index.open_index 判定。
        conn.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        # 檔案損毀（非 SQLite 格式）時上面的 PRAGMA 會拋例外；若不在此
        # 先關閉連線就丟出去，呼叫端拿不到 conn 也就無法 close()，
        # 在 Windows 上會讓檔案控制代碼一直鎖著，導致後續 unlink() 失敗。
        conn.close()
        raise
    return conn
