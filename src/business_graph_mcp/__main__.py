"""business-graph MCP server 的進入點。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from business_graph_mcp.db.connection import ensure_fts5_available
from business_graph_mcp.errors import GraphIndexError, GraphMigrationError
from business_graph_mcp.migrate import migrate_workspace
from business_graph_mcp.server import create_server


def main() -> None:
    """無參數時執行 server；migrate <repo> 執行一次性遷移。

    所有訊息一律輸出到 stderr；stdout 是 MCP 協定通道，寫入會破壞協定。
    """
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        if len(sys.argv) != 3:
            print("用法：python -m business_graph_mcp migrate <repo 路徑>", file=sys.stderr)
            raise SystemExit(2)
        try:
            report = migrate_workspace(Path(sys.argv[2]))
        except GraphMigrationError as exc:
            print(f"遷移失敗：{exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"已遷移 {len(report.moved)} 個節點到 .bgraph/nodes/：", file=sys.stderr)
        for slug in report.moved:
            print(f"  {slug}", file=sys.stderr)
        if report.skipped:
            print(f"跳過 {len(report.skipped)} 個（解析失敗或目標已存在）：", file=sys.stderr)
            for slug in report.skipped:
                print(f"  {slug}", file=sys.stderr)
        if report.config_moved:
            print("設定檔已遷移為 .bgraph/bgraph.toml。", file=sys.stderr)
        print(
            "所有節點的 type 一律暫定為 rule，請逐一複查並改成正確的型別"
            "（rule／entity／decision／term）。",
            file=sys.stderr,
        )
        return

    try:
        ensure_fts5_available()
    except GraphIndexError as exc:
        # 啟動時就擋下：FTS5 缺席時查詢會回空結果而非報錯，
        # 等到執行期才發現，症狀與「知識圖裡真的沒有」無法區分。
        print(f"環境檢查失敗：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    logging.info("business-graph MCP server 啟動")
    create_server().run()


if __name__ == "__main__":
    main()
