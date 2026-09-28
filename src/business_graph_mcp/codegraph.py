"""唯讀查詢外部 codegraph 索引，把符號名解析成檔案與行號。

codegraph（https://github.com/colbymchenry/codegraph）在專案下建立
.codegraph/codegraph.db，其中 nodes 表存放符號的 qualified_name、
file_path 與行號，且 qualified_name 與 lower(name) 皆有索引，因此解析
一個符號是單次點查詢。

刻意不走 `codegraph explore` 子行程：那是為人類與 agent 設計的文字輸出，
解析它比讀一張有索引的表脆弱，還要付起 Node 行程的延遲，而每次 explore
最多解析數十個符號。

代價是耦合到 codegraph 的內部 schema，因此本模組設三道防線：全程唯讀、
整個相依關在這一個模組後面、**任何失敗一律降級不拋錯**。最壞情況只是少
一個檔案與行號，業務知識本身完全不受影響——這正是敢於直接讀取外部工具
資料庫的前提。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from business_graph_mcp.workspace import find_git_boundary

#: codegraph 的索引目錄名稱。
CODEGRAPH_DIR_NAME = ".codegraph"
#: codegraph 的索引檔名。
CODEGRAPH_DB_NAME = "codegraph.db"


def find_codegraph_db(start: Path) -> Path | None:
    """從指定路徑向上尋找 codegraph 索引。

    搜尋邊界沿用 workspace.find_git_boundary：codegraph 索引與知識圖同屬
    一個 repo，向上搜過頭會讀到無關祖先 repo 的索引，那比查不到更危險。

    參數:
        start: 起始路徑，通常是知識庫所在目錄。

    回傳:
        codegraph.db 的路徑；找不到時為 None。
    """
    try:
        current = start.resolve()
        if not current.is_dir():
            current = current.parent
        boundary = find_git_boundary(current, Path.home())

        for directory in [current, *current.parents]:
            candidate = directory / CODEGRAPH_DIR_NAME / CODEGRAPH_DB_NAME
            if candidate.is_file():
                return candidate
            if boundary is not None and directory == boundary:
                break
            if boundary is None:
                break
    except OSError:
        return None
    return None


def resolve_symbols(db_path: Path | None, symbols: Sequence[str]) -> dict[str, str]:
    """把符號名解析成 "檔案:起行-迄行"。

    先以 qualified_name 精確比對（符號的正式全名，命中優先權最高），
    找不到再以 lower(name) 比對簡名。兩者在 codegraph 皆有索引。

    任何失敗都回傳目前已解析的部分而非拋例外：沒有索引、檔案不是 SQLite、
    表或欄位對不上、查無此符號，對呼叫端而言結果一致——該符號沒有位置，
    顯示為「未解析」。

    參數:
        db_path: codegraph.db 的路徑；None 表示找不到索引。
        symbols: 要解析的符號名。

    回傳:
        符號名對應到位置字串；未能解析的符號不會出現在結果中。
    """
    resolved, _ = resolve_symbols_checked(db_path, symbols)
    return resolved


def resolve_symbols_checked(
    db_path: Path | None, symbols: Sequence[str]
) -> tuple[dict[str, str], bool]:
    """解析符號位置，並回報本次查詢是否全部完成。

    參數:
        db_path: codegraph.db 的路徑；None 表示找不到索引。
        symbols: 要解析的符號名。

    回傳:
        已解析的位置與查詢完成旗標。找不到索引或任一查詢失敗時為 False，
        即使前面的符號已解析成功也一樣；呼叫端不可將缺少的結果判為查無。
        查詢正常完成但查無符號仍為 True；空符號清單不需要查詢。
    """
    if db_path is None:
        return {}, False
    if not symbols:
        return {}, True

    resolved: dict[str, str] = {}
    conn: sqlite3.Connection | None = None
    try:
        # 以唯讀模式開啟：本模組絕不可寫入別人的索引，而 URI 的 mode=ro
        # 讓「不小心寫入」在 SQLite 層就失敗，不必依賴自律。
        db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        for symbol in symbols:
            row = conn.execute(
                "SELECT file_path, start_line, end_line FROM nodes "
                "WHERE qualified_name = ? LIMIT 1",
                (symbol,),
            ).fetchone()
            if row is None:
                candidates = conn.execute(
                    "SELECT file_path, start_line, end_line FROM nodes "
                    "WHERE lower(name) = lower(?) LIMIT 2",
                    (symbol,),
                ).fetchall()
                row = candidates[0] if len(candidates) == 1 else None
            if row is not None:
                resolved[symbol] = (
                    f"{row['file_path']}:{row['start_line']}-{row['end_line']}"
                )
    except (sqlite3.Error, OSError):
        # 這裡吞掉例外是刻意的：codegraph 的 schema 是別人的衍生物，可能
        # 隨對方改版而變。本模組的契約是「解析不到就算了」，讓一個選用的
        # 增強功能有能力讓整個 business_explore 失敗，是不成比例的風險。
        return resolved, False
    finally:
        if conn is not None:
            try:
                conn.close()
            except (sqlite3.Error, OSError):
                pass
    return resolved, True
