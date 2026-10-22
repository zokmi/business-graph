"""遠端工具的授權、錯誤回應與操作紀錄整合。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp_types import CallToolResult, TextContent

from business_graph_mcp.audit import AuditWriter
from business_graph_mcp.errors import GraphConflictError, GraphError, GraphLockTimeoutError
from business_graph_mcp.remote_auth import Principal, require_tool_access

_AUDIT_WARNING = "操作已完成，但操作紀錄未能確認保存；請聯絡管理者，不要直接重送寫入。"


def error_result(message: str) -> CallToolResult:
    """直接回覆工具錯誤，避免 SDK 將含全文的例外寫入日誌。"""
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


class RemoteOperations:
    """保留業務結果，操作紀錄失敗不得改成業務失敗或觸發重送。"""

    def __init__(self, audit: AuditWriter) -> None:
        self.audit = audit

    async def record(
        self, tool: str, outcome: str, *, slug: str | None = None,
        before_hash: str | None = None, after_hash: str | None = None,
    ) -> bool:
        """只記當次授權主體與識別資訊，不傳入本文或例外文字。"""
        token = get_access_token()
        event = {
            "subject": token.client_id if token is not None else None,
            "tool": tool, "slug": slug, "before_hash": before_hash,
            "after_hash": after_hash, "outcome": outcome,
        }
        try:
            await asyncio.to_thread(self.audit.append, event)
        except Exception:
            return False
        return True

    async def call(
        self, tool: str, operation: Callable[[], Mapping[str, object]], *,
        slug: str | None = None, base_hash: str | None = None,
    ) -> CallToolResult:
        """授權先於執行緒；成功更新的前雜湊來自同鎖內驗證通過的 base_hash。"""
        token = get_access_token()
        try:
            if token is None or "read" not in token.scopes:
                raise GraphError("此請求缺少有效的讀取授權。")
            principal = Principal(token.client_id, "write" if "write" in token.scopes else "read")
            require_tool_access(principal, tool)
        except GraphError:
            await self.record(tool, "denied", slug=slug)
            return error_result("此主體沒有使用此工具的權限。")
        try:
            result = dict(await asyncio.to_thread(operation))
        except GraphConflictError as exc:
            await self.record(tool, "conflict", slug=slug)
            return error_result(str(exc))
        except GraphLockTimeoutError:
            await self.record(tool, "timeout", slug=slug)
            return error_result("知識庫正被另一個作業使用；等待逾時，請稍後重試。")
        except GraphError as exc:
            await self.record(tool, "rejected", slug=slug)
            return error_result(str(exc))
        except Exception:
            await self.record(tool, "error", slug=slug)
            return error_result("工具執行失敗，請查驗現況後再決定是否重試。")
        saved_hash = result.get("base_hash") if tool == "business_write" else None
        if isinstance(saved_hash, str) and result.get("indexed") is False:
            result["warning"] = (
                "Markdown 已保存，但索引同步或驗證失敗；下次工具呼叫會重試索引，"
                "請保留本次新雜湊，不要直接重送寫入。"
            )
        recorded = await self.record(
            tool, "success", slug=slug,
            before_hash=base_hash if isinstance(saved_hash, str) else None,
            after_hash=saved_hash if isinstance(saved_hash, str) else None,
        )
        if not recorded:
            result["audit_warning"] = _AUDIT_WARNING
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
            structured_content=result,
        )
