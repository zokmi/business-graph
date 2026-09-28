"""衍生 SQLite 索引的建立、版本檢查與安全重建。"""
from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

from business_graph_mcp.db.connection import connect
from business_graph_mcp.errors import GraphIndexError

SCHEMA_VERSION = 1
_SCHEMA_SQL = (files("business_graph_mcp.db") / "schema.sql").read_text(encoding="utf-8")
_REBUILDABLE_CODES = {sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT}


def _can_rebuild(exc: sqlite3.Error) -> bool:
    """只有明確的檔案損毀或缺少 meta 表能視為可重建。"""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in _REBUILDABLE_CODES:
        return True
    return isinstance(exc, sqlite3.OperationalError) and str(exc).lower() == "no such table: meta"


def _create_index(db_path: Path) -> sqlite3.Connection:
    try:
        conn = connect(db_path)
    except (sqlite3.Error, OSError) as exc:
        raise GraphIndexError(f"無法建立索引 {db_path}：{exc}") from exc
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        return conn
    except (sqlite3.Error, OSError) as exc:
        conn.close()
        raise GraphIndexError(f"無法建立索引 schema {db_path}：{exc}") from exc


def open_index(db_path: Path) -> sqlite3.Connection:
    """開啟索引；只有可辨識的損毀或版本不符會刪除重建。

    產品入口應先取得 workspace 鎖，因為檢查與重建須由同一行程獨占。
    """
    try:
        exists = db_path.is_file()
    except OSError as exc:
        raise GraphIndexError(f"無法檢查索引 {db_path}：{exc}") from exc

    if exists:
        conn: sqlite3.Connection | None = None
        rebuild = False
        try:
            conn = connect(db_path)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(meta)")}
            if {"key", "value"}.issubset(columns):
                row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                try:
                    version = int(row["value"]) if row is not None else None
                except (ValueError, TypeError):
                    version = None
                if version == SCHEMA_VERSION:
                    ready = conn
                    conn = None
                    return ready
            rebuild = True
        except sqlite3.Error as exc:
            if _can_rebuild(exc):
                rebuild = True
            else:
                raise GraphIndexError(f"索引暫時無法開啟，保留原檔 {db_path}：{exc}") from exc
        except OSError as exc:
            raise GraphIndexError(f"索引無法開啟，保留原檔 {db_path}：{exc}") from exc
        finally:
            if conn is not None:
                conn.close()

        if rebuild:
            try:
                db_path.unlink()
                for suffix in ("-wal", "-shm"):
                    db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
            except OSError as exc:
                raise GraphIndexError(f"無法重建索引 {db_path}：{exc}") from exc

    return _create_index(db_path)
