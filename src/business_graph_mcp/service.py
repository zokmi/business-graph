"""綁定單一知識庫的同步工具服務。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from business_graph_mcp.engine import open_workspace_session
from business_graph_mcp.lint import lint_session
from business_graph_mcp.tools.explore import explore_session
from business_graph_mcp.tools.write import write_session
from business_graph_mcp.workspace import Workspace


class GraphService:
    """固定操作指定的知識庫，不依工作目錄或祖先目錄選擇其他資料來源。"""

    def __init__(self, workspace: Workspace, codegraph_db: Path | None) -> None:
        self.workspace = Workspace(workspace.root.resolve())
        self.codegraph_db = codegraph_db.resolve() if codegraph_db is not None else None

    def explore(self, query: str) -> dict[str, str]:
        """查詢綁定知識庫的內容與待辦。"""
        with open_workspace_session(self.workspace) as session:
            return explore_session(session, query, self.codegraph_db)

    def write(
        self,
        *,
        title: str,
        node_type: str,
        content: str,
        status: str,
        aliases: tuple[str, ...] = (),
        code: tuple[str, ...] = (),
        base_hash: str | None = None,
    ) -> dict[str, object]:
        """建立或以 base_hash 保護更新綁定知識庫的一頁內容。"""
        with open_workspace_session(self.workspace) as session:
            return write_session(
                session,
                title=title,
                node_type=node_type,
                content=content,
                status=status,
                aliases=aliases,
                code=code,
                base_hash=base_hash,
            )

    def lint(self) -> dict[str, str]:
        """稽核綁定知識庫的一致性。"""
        with open_workspace_session(self.workspace) as session:
            return {"result": lint_session(session, date.today(), self.codegraph_db)}
