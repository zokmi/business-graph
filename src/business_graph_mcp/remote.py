"""Streamable HTTP MCP 與請求邊界。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server import MCPServer
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

from business_graph_mcp.audit import AuditWriter
from business_graph_mcp.errors import GraphError
from business_graph_mcp.node import slugify
from business_graph_mcp.remote_auth import FileTokenVerifier
from business_graph_mcp.remote_config import RemoteSettings, validate_remote_workspace
from business_graph_mcp.remote_operations import RemoteOperations
from business_graph_mcp.server import INSTRUCTIONS, SERVER_NAME, SERVER_VERSION
from business_graph_mcp.service import GraphService
from business_graph_mcp.workspace import GRAPH_DIR_NAME, find_git_boundary, find_workspace


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

    def __init__(self, operations: RemoteOperations) -> None:
        self.parameters: dict[str, set[str]] = {}
        self.operations = operations

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
                    await self.operations.record(name, "invalid_arguments")
                    return CallToolResult(
                        content=[TextContent(type="text", text="遠端工具不接受未宣告的參數。")],
                        is_error=True,
                    )
        return await call_next(ctx)


def create_remote_app(settings: RemoteSettings) -> Starlette:
    """建立逐次驗證且不保留 session 的 HTTP MCP。"""
    default = (
        GraphService(validate_remote_workspace(settings.workspace), settings.codegraph_db)
        if settings.workspace is not None else None
    )
    services = {
        path: GraphService(validate_remote_workspace(path), codegraph_db=None)
        for path in settings.project_workspaces
        if path != settings.workspace
    }
    if (
        default is not None and settings.workspace is not None
        and settings.project_workspaces and settings.workspace.name == GRAPH_DIR_NAME
    ):
        services[settings.workspace] = default
    bindings = sorted(settings.project_bindings, key=lambda pair: len(pair[0].parts), reverse=True)
    local_roots = settings.local_project_roots
    project_roots = (
        *local_roots, *(project for project, _ in bindings), *(path.parent for path in services)
    )
    bound_services = dict(services)
    if settings.workspace is not None and default is not None:
        bound_services[settings.workspace] = default

    def resolve_project(project_path: str, roots: tuple[Path, ...]) -> Path:
        message = "project_path 必須指向允許的專案目錄或檔案。"
        path = Path(project_path)
        if path.is_absolute() and ".." not in path.parts:
            if any(path.is_relative_to(root) for root in roots):
                try:
                    resolved = path.resolve(strict=True)
                    if any(resolved.is_relative_to(root) for root in roots):
                        return resolved
                except (OSError, RuntimeError, ValueError):
                    raise GraphError(message) from None
        raise GraphError(message)

    def git_project(path: Path) -> Path | None:
        return find_git_boundary(path if path.is_dir() else path.parent, Path.home())

    def local_workspace(git_root: Path) -> Path:
        workspace = git_root / GRAPH_DIR_NAME
        if workspace.resolve() != workspace:
            raise GraphError("本機知識庫的 .bgraph 必須位於 Git 專案內。")
        return workspace

    def service_for(project_path: str | None) -> GraphService:
        if project_path is None:
            if default is None:
                raise GraphError("請提供目前 Git 專案的絕對 project_path。")
            return default
        resolved = resolve_project(project_path, project_roots)
        git_root = git_project(resolved)
        for project, target in bindings:
            if resolved.is_relative_to(project) and (
                git_root is None or git_root == project or not git_root.is_relative_to(project)
            ):
                return bound_services[target]
        if git_root is not None and any(git_root.is_relative_to(root) for root in local_roots):
            path = local_workspace(git_root)
            if not path.exists():
                raise GraphError("此 Git 專案沒有 .bgraph，請先向使用者確認是否新增知識庫。")
            if path in bound_services:
                return bound_services[path]
            return GraphService(validate_remote_workspace(path), codegraph_db=None)
        workspace = find_workspace(resolved)
        if workspace is not None and workspace.root in services:
            return services[workspace.root]
        raise GraphError("project_path 必須指向已登記的知識庫或允許的本機 Git 專案。")

    operations = RemoteOperations(AuditWriter(settings.audit_file))
    arguments_boundary = ToolArgumentsBoundary(operations)
    routing_instructions = (
        "\n已知目前專案時，請在工具呼叫中傳入其絕對 project_path。"
        "若專案未登記，請先向使用者確認應使用的知識庫，再由管理者保存專案對應。"
        "沒有專案上下文時可省略 project_path，使用預設知識庫。"
        if project_roots
        else "\n遠端服務固定綁定一份知識庫，所有工具均不接受本機路徑。"
    )
    if local_roots:
        routing_instructions = (
            "\n請傳入目前 Git 專案的絕對 project_path，服務會使用該專案的 .bgraph。"
            "若尚未建立，先問使用者是否新增，只有在明確同意後才呼叫 business_init。"
            "使用者拒絕或尚未回答時，請繼續處理原任務。"
        )
    mcp = MCPServer(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS
        + routing_instructions
        + (
            "\n唯讀主體只可查詢與稽核，沒有寫入權限，前述寫回義務僅適用具寫入權限的主體。"
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
    async def business_explore(query: str, project_path: str | None = None) -> CallToolResult:
        """查詢業務知識；project_path 是已登記專案的絕對目錄或檔案路徑。"""
        return await operations.call(
            "business_explore", lambda: service_for(project_path).explore(query)
        )

    @mcp.tool()
    async def business_write(
        title: str,
        type: str,
        content: str,
        status: str,
        aliases: list[str] | None = None,
        code: list[str] | None = None,
        base_hash: str | None = None,
        project_path: str | None = None,
    ) -> CallToolResult:
        """寫入業務知識；type 為 rule/entity/decision/term，status 為 confirmed/inferred。

        內文使用 markdown，不含 front matter。更新既有頁必須提供 explore 的 base_hash，
        新頁省略；aliases 是別名，code 是程式碼符號名稱。須具寫入權限。
        project_path 是已登記專案的絕對目錄或檔案路徑。
        """
        return await operations.call(
            "business_write",
            lambda: service_for(project_path).write(
                title=title, node_type=type, content=content, status=status,
                aliases=tuple(aliases or ()), code=tuple(code or ()), base_hash=base_hash,
            ),
            slug=slugify(title), base_hash=base_hash,
        )

    @mcp.tool()
    async def business_lint(project_path: str | None = None) -> CallToolResult:
        """稽核業務知識；project_path 是已登記專案的絕對目錄或檔案路徑。"""
        return await operations.call("business_lint", lambda: service_for(project_path).lint())

    for function in (business_explore, business_write, business_lint):
        arguments_boundary.register(function)
        if not project_roots:
            arguments_boundary.parameters[function.__name__].discard("project_path")

    if local_roots:
        def initialize(project_path: str) -> dict[str, str]:
            git_root = git_project(resolve_project(project_path, local_roots))
            if git_root is None or not any(git_root.is_relative_to(root) for root in local_roots):
                raise GraphError("只能初始化允許的本機 Git 專案。")
            validate_remote_workspace(local_workspace(git_root), initialize=True)
            return {"result": "Git 專案的 .bgraph 已就緒。"}

        @mcp.tool()
        async def business_init(project_path: str) -> CallToolResult:
            """取得使用者明確同意後，建立該 Git 專案的 .bgraph。須具寫入權限。"""
            return await operations.call("business_init", lambda: initialize(project_path))

        arguments_boundary.register(business_init)
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
