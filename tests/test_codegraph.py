"""外部 codegraph 索引 adapter 的測試。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from business_graph_mcp.codegraph import find_codegraph_db, resolve_symbols


@pytest.fixture
def 假索引(tmp_path):
    """建立一個最小的假 codegraph.db。

    刻意不依賴本機是否真的安裝 codegraph：那個相依本就設計成可有可無，
    讓測試依賴它會導致測試在未安裝的機器上紅掉，而紅掉的原因與程式對錯無關。
    只建出 resolve_symbols 實際會讀的欄位。
    """
    d = tmp_path / ".codegraph"
    d.mkdir()
    db = d / "codegraph.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "qualified_name TEXT NOT NULL, file_path TEXT NOT NULL, "
        "start_line INTEGER NOT NULL, end_line INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO nodes VALUES ('1', 'CalculateDiscount', "
        "'OrderService.CalculateDiscount', 'src/Services/OrderService.cs', 118, 164)"
    )
    conn.commit()
    conn.close()
    return db


def test_以完整符號名解析出位置(假索引):
    assert resolve_symbols(假索引, ["OrderService.CalculateDiscount"]) == {
        "OrderService.CalculateDiscount": "src/Services/OrderService.cs:118-164"
    }


def test_完整名找不到時退回比對簡名且不分大小寫(假索引):
    assert resolve_symbols(假索引, ["calculatediscount"]) == {
        "calculatediscount": "src/Services/OrderService.cs:118-164"
    }


def test_簡名有多個候選時保持未解析(假索引):
    conn = sqlite3.connect(假索引)
    conn.execute(
        "INSERT INTO nodes VALUES ('2', 'CalculateDiscount', "
        "'OtherService.CalculateDiscount', 'src/other.py', 1, 2)"
    )
    conn.commit()
    conn.close()
    assert resolve_symbols(假索引, ["CalculateDiscount"]) == {}
    assert resolve_symbols(假索引, ["OrderService.CalculateDiscount"]) == {
        "OrderService.CalculateDiscount": "src/Services/OrderService.cs:118-164"
    }


def test_特殊字元路徑仍以唯讀模式解析(假索引, tmp_path, monkeypatch):
    特殊目錄 = tmp_path / "索引 # 100%"
    特殊目錄.mkdir()
    特殊索引 = 特殊目錄 / "codegraph.db"
    特殊索引.write_bytes(假索引.read_bytes())
    真實連線 = sqlite3.connect

    def 驗證唯讀連線(database, *args, **kwargs):
        conn = 真實連線(database, *args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE 不得寫入 (id INTEGER)")
        return conn

    monkeypatch.setattr(sqlite3, "connect", 驗證唯讀連線)

    assert resolve_symbols(特殊索引, ["OrderService.CalculateDiscount"]) == {
        "OrderService.CalculateDiscount": "src/Services/OrderService.cs:118-164"
    }


def test_查無符號時不出現在結果中(假索引):
    assert resolve_symbols(假索引, ["NoSuchSymbol"]) == {}


def test_沒有索引時回傳空而不拋錯():
    assert resolve_symbols(None, ["OrderService.CalculateDiscount"]) == {}


def test_檔案不是_sqlite_時回傳空而不拋錯(tmp_path):
    壞檔 = tmp_path / "codegraph.db"
    壞檔.write_text("這不是 SQLite 資料庫", encoding="utf-8")
    assert resolve_symbols(壞檔, ["OrderService.CalculateDiscount"]) == {}


def test_表或欄位對不上時回傳空而不拋錯(tmp_path):
    db = tmp_path / "codegraph.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE symbols (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    assert resolve_symbols(db, ["OrderService.CalculateDiscount"]) == {}


@pytest.mark.parametrize(
    "關閉錯誤",
    [sqlite3.OperationalError("無法關閉"), OSError("無法關閉")],
)
def test_close_失敗仍回傳已解析結果(假索引, monkeypatch, 關閉錯誤):
    真實連線 = sqlite3.connect(假索引)

    class 關閉失敗連線:
        @property
        def row_factory(self):
            return 真實連線.row_factory

        @row_factory.setter
        def row_factory(self, value):
            真實連線.row_factory = value

        def execute(self, *args, **kwargs):
            return 真實連線.execute(*args, **kwargs)

        def close(self):
            raise 關閉錯誤

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: 關閉失敗連線())

    try:
        assert resolve_symbols(假索引, ["OrderService.CalculateDiscount"]) == {
            "OrderService.CalculateDiscount": "src/Services/OrderService.cs:118-164"
        }
    finally:
        真實連線.close()


def test_找得到_repo_內的_codegraph_索引(假索引, tmp_path):
    (tmp_path / ".git").mkdir()
    子目錄 = tmp_path / "src" / "deep"
    子目錄.mkdir(parents=True)
    assert find_codegraph_db(子目錄) == 假索引


def test_沒有_codegraph_目錄時回傳_None(tmp_path):
    (tmp_path / ".git").mkdir()
    assert find_codegraph_db(tmp_path) is None


def test_尋找索引時_oserror_降級為_none(tmp_path, monkeypatch):
    def 拒絕解析(_path):
        raise OSError("無法解析路徑")

    monkeypatch.setattr(Path, "resolve", 拒絕解析)

    assert find_codegraph_db(tmp_path) is None


def test_可查詢的索引查無符號仍標示完成(假索引):
    from business_graph_mcp import codegraph

    assert codegraph.resolve_symbols_checked(假索引, ["Missing.Symbol"]) == ({}, True)
    assert codegraph.resolve_symbols_checked(None, ["Missing.Symbol"]) == ({}, False)


def test_中途查詢失敗保留已解析結果但標示未完成(假索引, monkeypatch):
    from business_graph_mcp import codegraph

    connect = sqlite3.connect

    class 中途失敗連線(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if parameters == ("Missing.Symbol",):
                raise sqlite3.OperationalError("查詢中途中斷")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *args, **kwargs: connect(*args, **kwargs, factory=中途失敗連線),
    )
    symbols = ["OrderService.CalculateDiscount", "Missing.Symbol"]
    expected = {"OrderService.CalculateDiscount": "src/Services/OrderService.cs:118-164"}

    assert codegraph.resolve_symbols_checked(假索引, symbols) == (expected, False)
    assert resolve_symbols(假索引, symbols) == expected
