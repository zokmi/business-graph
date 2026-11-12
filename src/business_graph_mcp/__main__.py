"""business-graph MCP server 的進入點。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from business_graph_mcp.db.connection import ensure_fts5_available
from business_graph_mcp.errors import GraphError, GraphIndexError, GraphMigrationError
from business_graph_mcp.migrate import migrate_workspace
from business_graph_mcp.remote import run_remote
from business_graph_mcp.remote_config import RemoteSettings, validate_remote_workspace
from business_graph_mcp.server import create_server


def _serve(arguments: list[str]) -> None:
    """解析遠端服務的明確設定；初始化先於完整設定驗證。"""
    parser = argparse.ArgumentParser(description="啟動 Streamable HTTP MCP 服務。")
    parser.add_argument("--workspace", type=Path, help="預設知識庫的絕對路徑")
    parser.add_argument(
        "--local-project-root", type=Path, action="append", default=[],
        help="允許自動探索的本機專案根目錄，可重複",
    )
    parser.add_argument(
        "--project-workspace", type=Path, action="append", default=[],
        help="可用 project_path 選擇的 .bgraph 絕對路徑，可重複",
    )
    parser.add_argument(
        "--project-binding", type=Path, nargs=2, action="append", default=[],
        metavar=("PROJECT_ROOT", "WORKSPACE"), help="專案目錄及已登記知識庫的絕對路徑，可重複",
    )
    parser.add_argument("--credentials-file", type=Path, required=True, help="憑證摘要設定檔")
    parser.add_argument("--audit-file", type=Path, required=True, help="操作紀錄檔案")
    parser.add_argument("--public-url", required=True, help="明確的外部服務網址")
    parser.add_argument("--host", default="127.0.0.1", help="監聽主機")
    parser.add_argument("--port", type=int, default=8000, help="監聽連接埠")
    parser.add_argument("--init", action="store_true", help="初始化空白知識庫")
    parser.add_argument("--codegraph-db", type=Path, help="明確指定程式碼索引")
    parser.add_argument("--allowed-host", action="append", default=[], help="允許的 Host，可重複")
    parser.add_argument(
        "--allowed-origin", action="append", default=[], help="允許的 Origin，可重複"
    )
    parser.add_argument("--max-request-bytes", type=int, default=1048576, help="請求本文上限")
    args = parser.parse_args(arguments)
    try:
        if args.init:
            if args.workspace is None:
                raise GraphError("--init 必須搭配 --workspace。")
            validate_remote_workspace(args.workspace, initialize=True)
        settings = RemoteSettings(
            workspace=args.workspace,
            local_project_roots=tuple(args.local_project_root),
            project_workspaces=tuple(args.project_workspace),
            project_bindings=tuple((project, target) for project, target in args.project_binding),
            credentials_file=args.credentials_file,
            audit_file=args.audit_file,
            public_url=args.public_url,
            host=args.host,
            port=args.port,
            codegraph_db=args.codegraph_db,
            allowed_hosts=tuple(args.allowed_host),
            allowed_origins=tuple(args.allowed_origin),
            max_request_bytes=args.max_request_bytes,
        )
        ensure_fts5_available()
        run_remote(settings)
    except GraphError as exc:
        print(f"遠端服務啟動失敗：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> None:
    """無參數執行 stdio；serve 啟動遠端服務；migrate 執行一次性遷移。

    所有訊息一律輸出到 stderr；stdout 是 MCP 協定通道，寫入會破壞協定。
    """
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        _serve(sys.argv[2:])
        return

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
