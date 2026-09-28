"""SQLite 連線與 FTS5 可用性檢查的測試。"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from business_graph_mcp.db.connection import connect, ensure_fts5_available
from business_graph_mcp.errors import GraphIndexError


def test_本機環境的_sqlite_有啟用_fts5():
    # 這條是環境檢查而非邏輯檢查：FTS5 未編入時整個專案無法運作，
    # 要在第一天就發現，而不是等到查詢回空結果。
    ensure_fts5_available()


def test_缺少_fts5_時拋出可診斷的錯誤():
    # sqlite3 是全域單一模組物件，patch 會直接改掉 sqlite3.connect 本身；
    # 若替身內部仍呼叫 sqlite3.connect，會呼叫到自己而無限遞迴，因此先留一份
    # 原始 connect 供替身內部使用。
    真正的連線 = sqlite3.connect

    class _模擬無fts5的連線(sqlite3.Connection):
        """sqlite3.Connection 為 C 擴充型別，實例不可任意賦值屬性（如
        `conn.execute = ...`），因此改以子類別覆寫 execute 來模擬 FTS5 未編入。
        """

        def execute(self, sql: str, *parameters: object) -> sqlite3.Cursor:  # type: ignore[override]
            if "fts5" in sql.lower():
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, *parameters)

    def 假的無_fts5連線(*args: object, **kwargs: object) -> sqlite3.Connection:
        return 真正的連線(":memory:", factory=_模擬無fts5的連線)

    with patch("business_graph_mcp.db.connection.sqlite3.connect", 假的無_fts5連線):
        with pytest.raises(GraphIndexError) as exc:
            ensure_fts5_available()

    # 訊息必須指出解法，否則使用者只看到「不支援」無從處置。
    assert "FTS5" in str(exc.value)
    assert "Python" in str(exc.value)


def test_connect_開啟外鍵並回傳可查詢的連線(tmp_path):
    conn = connect(tmp_path / "index.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # row_factory 設為 sqlite3.Row，後續查詢才能用欄位名取值。
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        assert conn.execute("SELECT a FROM t").fetchone()["a"] == 1
    finally:
        conn.close()


def test_索引建立節點全文表且保留三個檢索欄位(tmp_path):
    from business_graph_mcp.db.queries import open_index

    conn = open_index(tmp_path / "index.db")
    try:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(nodes_fts)")]
        assert columns == ["title", "aliases_text", "body"]
        assert conn.execute("SELECT COUNT(*) FROM nodes_fts").fetchone()[0] == 0
    finally:
        conn.close()
