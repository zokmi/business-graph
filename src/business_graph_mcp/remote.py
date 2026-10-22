"""固定知識庫的 Streamable HTTP MCP 與請求邊界。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from business_graph_mcp.errors import GraphError
from business_graph_mcp.remote_auth import FileTokenVerifier, Principal, require_tool_access
from business_graph_mcp.remote_config import RemoteSettings, validate_remote_workspace
from business_graph_mcp.server import INSTRUCTIONS, SERVER_NAME, SERVER_VERSION
from business_graph_mcp.service import GraphService


class RequestBoundary:
    """先檢查明確的 Host／Origin，完整計數請求本文後才交給 SDK。"""

    def __init__(self, app: ASGIApp, settings: RemoteSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)
        hosts = headers.get(b"host", [])
        origins = headers.get(b"origin", [])
        if len(hosts) != 1 or hosts[0].decode("latin-1") not in self.settings.allowed_hosts:
            await JSONResponse({"error": "不允許的主機。"}, status_code=421)(scope, receive, send)
            return
        if origins and (
            len(origins) != 1 or origins[0].decode("latin-1") not in self.settings.allowed_origins
        ):
            await JSONResponse({"error": "不允許的來源。"}, status_code=403)(scope, receive, send)
            return
        lengths = headers.get(b"content-length", [])
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                await JSONResponse({"error": "無效的本文長度。"}, status_code=400)(
                    scope, receive, send
                )
                return
            declared = lengths[0].lstrip(b"0") or b"0"
            maximum = str(self.settings.max_request_bytes).encode("ascii")
            if len(declared) > len(maximum) or (
                len(declared) == len(maximum) and declared > maximum
            ):
                await self._too_large(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.settings.max_request_bytes:
                await self._too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        """回應大小限制，不將部分本文交給業務引擎。"""
        await JSONResponse({"error": "請求本文超過大小限制。"}, status_code=413)(
            scope, receive, send
        )


class ToolArgumentsBoundary:
    """SDK 預設忽略額外參數；在驗證前拒絕所有未宣告欄位。"""

    def __init__(self) -> None:
        self.parameters: dict[str, set[str]] = {}

    def register(self, function: Callable[..., Any]) -> None:
        """由實際工具簽章取得允許欄位，避免另行維護相同清單。"""
        self.parameters[function.__name__] = set(inspect.signature(function).parameters)

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method == "tools/call" and ctx.params is not None:
            name = ctx.params.get("name")
            arguments = ctx.params.get("arguments", {})
            if isinstance(name, str) and name in self.parameters and isinstance(arguments, dict):
                if set(arguments) - self.parameters[name]:
                    return CallToolResult(
                        content=[TextContent(type="text", text="遠端工具不接受未宣告的參數。")],
                        is_error=True,
                    )
        return await call_next(ctx)


def _authorize(tool_name: str) -> Principal:
    """在當次 SDK 請求 context 取得主體；進入執行緒前驗證工具權限。"""
    token = get_access_token()
    if token is None or "read" not in token.scopes:
        raise GraphError("此請求缺少有效的讀取授權。")
    principal = Principal(token.client_id, "write" if "write" in token.scopes else "read")
    require_tool_access(principal, tool_name)
    return principal


def create_remote_app(settings: RemoteSettings) -> Starlette:
    """建立單一知識庫、逐次驗證且不保留 session 的 HTTP MCP。"""
    service = GraphService(validate_remote_workspace(settings.workspace), settings.codegraph_db)
    arguments_boundary = ToolArgumentsBoundary()
    mcp = MCPServer(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS
        + (
            "\n遠端服務固定綁定一份知識庫，所有工具均不接受本機路徑。"
            "唯讀主體只可查詢與稽核，沒有寫入權限，前述寫回義務僅適用具寫入權限的主體。"
        ),
        token_verifier=FileTokenVerifier(settings.credentials_file),
        auth=AuthSettings(
            # SDK 要求 issuer_url，但固定 Bearer 模式不提供 OAuth 發行或探索端點。
            issuer_url=AnyHttpUrl(settings.public_url),
            resource_server_url=None,
            required_scopes=["read"],
        ),
        middleware=[arguments_boundary],
    )

    @mcp.tool()
    async def business_explore(query: str) -> dict[str, str]:
        """查詢固定知識庫的業務知識；query 是搜尋詞或業務問題。"""
        _authorize("business_explore")
        return await asyncio.to_thread(service.explore, query)

    @mcp.tool()
    async def business_write(
        title: str,
        type: str,
        content: str,
        status: str,
        aliases: list[str] | None = None,
        code: list[str] | None = None,
        base_hash: str | None = None,
    ) -> dict[str, object]:
        """寫入業務知識；type 為 rule/entity/decision/term，status 為 confirmed/inferred。

        內文使用 markdown，不含 front matter。更新既有頁必須提供 explore 的 base_hash，
        新頁省略；aliases 是別名，code 是程式碼符號名稱。須具寫入權限。
        """
        _authorize("business_write")
        return await asyncio.to_thread(
            service.write,
            title=title,
            node_type=type,
            content=content,
            status=status,
            aliases=tuple(aliases or ()),
            code=tuple(code or ()),
            base_hash=base_hash,
        )

    @mcp.tool()
    async def business_lint() -> dict[str, str]:
        """稽核固定知識庫的一致性，唯讀主體也可使用。"""
        _authorize("business_lint")
        return await asyncio.to_thread(service.lint)

    for function in (business_explore, business_write, business_lint):
        arguments_boundary.register(function)
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=settings.max_request_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=list(settings.allowed_origins),
        ),
    )

    async def health(request: Request) -> JSONResponse:
        """只回報程序存活，不揭露路徑、主體或知識內容。"""
        return JSONResponse({"status": "ok"})

    app.router.routes.append(Route("/healthz", health, methods=["GET"]))
    app.add_middleware(RequestBoundary, settings=settings)
    return app


def run_remote(settings: RemoteSettings) -> None:
    """以明確的介面與連接埠啟動；不信任轉送標頭且不記錄請求 URL。"""
    uvicorn.run(
        create_remote_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        access_log=False,
    )
