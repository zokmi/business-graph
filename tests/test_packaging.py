"""打包完整性的測試。"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

專案根 = Path(__file__).resolve().parent.parent


def test_schema_sql_能以_importlib_resources_讀到():
    # 以 __file__ 相對路徑讀取在 wheel 與 zipapp 下會失敗；
    # 這條測試確保實作用的是 importlib.resources。
    from importlib.resources import files

    content = (files("business_graph_mcp.db") / "schema.sql").read_text(encoding="utf-8")

    assert "CREATE VIRTUAL TABLE" in content
    assert "nodes_fts" in content


def test_建出的_wheel_含有_schema_sql(tmp_path):
    # 漏打包 package data 時，套件安裝後會在第一次開索引就炸掉，
    # 而開發環境因為讀得到原始碼目錄完全察覺不到。
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        cwd=專案根,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("*.whl"))

    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()

    assert "business_graph_mcp/db/schema.sql" in names
    assert "business_graph_mcp/py.typed" in names


def test_進入點可被匯入():
    from business_graph_mcp.__main__ import main

    assert callable(main)
