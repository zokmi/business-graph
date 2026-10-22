"""遠端 HTTP 協定、授權與請求邊界的整合測試。"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from business_graph_mcp.remote import create_remote_app
from business_graph_mcp.remote_config import RemoteSettings


def credentials(path, *, writer_role="write", revoke=False):
    rows = [("reader", "read", "test-reader"), ("writer", writer_role, "test-writer")]
    path.write_text(
        "\n".join(
            f'[[principals]]\nsubject="{subject}"\nrole="{role}"\ntoken_sha256="{hashlib.sha256(token.encode()).hexdigest()}"\n'
            for subject, role, token in rows
            if not (revoke and subject == "writer")
        ),
        encoding="utf-8",
    )


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "knowledge"
    (root / "nodes").mkdir(parents=True)
    auth = tmp_path / "credentials.toml"
    credentials(auth)
    return RemoteSettings(
        workspace=root,
        credentials_file=auth,
        audit_file=tmp_path / "audit.jsonl",
        public_url="http://localhost",
    )


@asynccontextmanager
async def sdk(app, token, *, base_url="http://localhost"):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), headers={"Authorization": f"Bearer {token}"}
    ) as client:
        async with streamable_http_client(f"{base_url}/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session, client


def write_args(**kwargs):
    return {
        "title": "Refund Policy",
        "type": "rule",
        "content": "Refunds require approval.",
        "status": "inferred",
        **kwargs,
    }


def node_hash(settings):
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted((settings.workspace / "nodes").glob("*.md")))
    ).hexdigest()


async def test_independent_sdk_clients_fixed_workspace_read_write(settings):
    app = create_remote_app(settings)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (writer, _):
            listing = await writer.list_tools()
            assert {t.name for t in listing.tools} == {
                "business_explore",
                "business_write",
                "business_lint",
            }
            assert all(
                "project_path" not in t.input_schema.get("properties", {}) for t in listing.tools
            )
            outcome = await writer.call_tool("business_write", write_args())
            assert not outcome.is_error
            assert outcome.structured_content["created"] is True
        async with sdk(app, "test-reader") as (reader, _):
            result = await reader.call_tool("business_explore", {"query": "refund"})
            assert "Refund Policy" in result.structured_content["result"]
            assert not (await reader.call_tool("business_lint", {})).is_error
    assert (settings.workspace / "nodes" / "Refund Policy.md").is_file()


async def test_reader_cannot_directly_call_write_and_paths_are_rejected(settings):
    app = create_remote_app(settings)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-reader") as (reader, _):
            before = node_hash(settings)
            denied = await reader.call_tool("business_write", write_args())
            assert denied.is_error
            assert node_hash(settings) == before
        async with sdk(app, "test-writer") as (writer, _):
            for name, args in [
                ("business_explore", {"query": "refund"}),
                ("business_lint", {}),
                ("business_write", write_args()),
            ]:
                result = await writer.call_tool(
                    name, {**args, "project_path": str(settings.workspace.parent)}
                )
                assert result.is_error
            assert node_hash(settings) == before
            assert not (settings.workspace.parent / ".bgraph").exists()


async def test_each_request_rechecks_token_role_and_no_session_auth_bypass(settings):
    app = create_remote_app(settings)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (session, client):
            assert not (await session.call_tool("business_write", write_args())).is_error
            credentials(settings.credentials_file, writer_role="read")
            before = node_hash(settings)
            assert (
                await session.call_tool("business_write", write_args(title="Other Policy"))
            ).is_error
            assert node_hash(settings) == before
            assert not (await session.call_tool("business_explore", {"query": "refund"})).is_error
            credentials(settings.credentials_file, revoke=True)
            response = await client.post(
                "http://localhost/mcp",
                json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                headers={"mcp-session-id": "arbitrary-old-session"},
            )
            assert response.status_code == 401
            client.headers.pop("Authorization")
            response = await client.post(
                "http://localhost/mcp", json={}, headers={"mcp-session-id": "arbitrary-old-session"}
            )
            assert response.status_code == 401


async def test_health_and_missing_invalid_credentials(settings):
    app = create_remote_app(settings)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = await client.post("/mcp", json={}, headers=headers)
            assert response.status_code == 401
            assert "Bearer" in response.headers["www-authenticate"]


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Host": "evil.example"}, 421),
        ({"Origin": "https://evil.example"}, 403),
        ({"Host": "evil.example", "X-Forwarded-Host": "localhost"}, 421),
        ({"Origin": "null"}, 403),
    ],
)
async def test_host_origin_boundary_before_any_engine(settings, headers, status):
    app = create_remote_app(settings)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.post("/mcp", json={}, headers=headers)).status_code == status
        assert (await client.get("/healthz", headers=headers)).status_code == status
    assert not (settings.workspace / "index.db").exists()


@pytest.mark.parametrize("chunked", [False, True])
async def test_oversized_body_rejected_before_engine(settings, chunked):
    from dataclasses import replace

    settings = replace(settings, max_request_bytes=128)
    app = create_remote_app(settings)

    async def chunks():
        yield b"a" * 80
        yield b"b" * 80

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/mcp",
            content=chunks() if chunked else b"x" * 129,
            headers={"Authorization": "Bearer test-writer", "Content-Type": "application/json"},
        )
        assert response.status_code == 413
    assert not (settings.workspace / "index.db").exists()


async def test_run_remote_cli_over_real_loopback_tcp(settings):
    """真實程序、uvicorn 與 TCP 連線驗證 CLI 到 MCP 工具的完整路徑。"""
    import asyncio
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "business_graph_mcp",
            "serve",
            "--workspace",
            str(settings.workspace),
            "--credentials-file",
            str(settings.credentials_file),
            "--audit-file",
            str(settings.audit_file),
            "--public-url",
            url,
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        async with httpx2.AsyncClient(timeout=1.0, trust_env=False) as probe:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    pytest.fail(
                        f"服務提前結束：{stdout.decode(errors='replace')} {stderr.decode(errors='replace')}"
                    )
                try:
                    response = await probe.get(f"{url}/healthz")
                    if response.status_code == 200:
                        break
                except httpx2.TransportError:
                    pass
                assert time.monotonic() < deadline, "服務未在期限內啟動。"
                await asyncio.sleep(0.05)
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer test-writer"}, trust_env=False
        ) as client:
            async with streamable_http_client(f"{url}/mcp", http_client=client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    assert len((await session.list_tools()).tools) == 3
                    assert not (await session.call_tool("business_write", write_args())).is_error
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer test-reader"}, trust_env=False
        ) as client:
            async with streamable_http_client(f"{url}/mcp", http_client=client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    result = await session.call_tool("business_explore", {"query": "refund"})
                    assert "Refund Policy" in result.structured_content["result"]
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


async def test_static_bearer_has_no_oauth_server_or_discovery(settings):
    app = create_remote_app(settings)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server",
            "/authorize",
            "/token",
            "/register",
        ):
            assert (await client.get(path)).status_code == 404
        response = await client.post("/mcp", json={})
        assert response.status_code == 401
        assert "resource_metadata" not in response.headers["www-authenticate"]


@pytest.mark.parametrize("length", ["0", "9" * 5000], ids=["underdeclared", "huge-integer"])
async def test_body_limit_counts_received_bytes_even_with_bad_declared_size(settings, length):
    from dataclasses import replace

    app = create_remote_app(replace(settings, max_request_bytes=128))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.post("/mcp", content=b"x" * 129, headers={"Content-Length": length})
        assert response.status_code == 413
    assert not (settings.workspace / "index.db").exists()
