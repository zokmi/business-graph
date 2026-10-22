"""獨立 HTTP client 的競爭、故障復原與安全操作紀錄。"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from business_graph_mcp.node import content_hash
from business_graph_mcp.remote import create_remote_app
from tests.test_remote_http import sdk, write_args
from tests.test_remote_http import settings as settings


def events(settings):
    return [json.loads(line) for line in settings.audit_file.read_text(encoding="utf-8").splitlines()]


@asynccontextmanager
async def tcp_server(settings):
    """同一真實服務接受兩條獨立 TCP 連線，使用 OS 分配的測試連接埠。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        configured = replace(settings, public_url=url, allowed_hosts=(f"127.0.0.1:{port}",), allowed_origins=(url,))
        server = uvicorn.Server(uvicorn.Config(create_remote_app(configured), access_log=False, log_config=None))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield url
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


@asynccontextmanager
async def tcp_sdk(url):
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer test-writer"}, trust_env=False) as http:
        async with streamable_http_client(f"{url}/mcp", http_client=http) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                yield client


@pytest.mark.parametrize("mode", ["create", "update", "different"])
async def test_real_tcp_clients_race_and_preserve_markdown(settings, mode):
    async with tcp_server(settings) as url, tcp_sdk(url) as first, tcp_sdk(url) as second:
        base = None
        if mode == "update":
            initial = await first.call_tool("business_write", write_args())
            base = initial.structured_content["base_hash"]
        results = await asyncio.gather(
            first.call_tool("business_write", write_args(content="tcp one", base_hash=base)),
            second.call_tool("business_write", write_args(title="Other Policy" if mode == "different" else "Refund Policy", content="tcp two", base_hash=base)),
        )
        assert sum(not r.is_error for r in results) == (2 if mode == "different" else 1)
    pages = list((settings.workspace / "nodes").glob("*.md"))
    assert len(pages) == (2 if mode == "different" else 1)
    contents = "\n".join(p.read_text(encoding="utf-8") for p in pages)
    assert ("tcp one" in contents) + ("tcp two" in contents) == (2 if mode == "different" else 1)


@pytest.mark.parametrize("mode", ["create", "update", "different"])
async def test_independent_http_clients_race_without_lost_writes(settings, monkeypatch, mode):
    import business_graph_mcp.writer as writer

    app = create_remote_app(settings)
    original = writer._atomic_write

    def slow_write(path, text):
        time.sleep(0.08)
        original(path, text)

    monkeypatch.setattr(writer, "_atomic_write", slow_write)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (first, _), sdk(app, "test-writer") as (second, _):
            base = None
            if mode == "update":
                initial = await first.call_tool("business_write", write_args())
                base = initial.structured_content["base_hash"]
            responses = await asyncio.gather(
                first.call_tool("business_write", write_args(content="winner one", base_hash=base)),
                second.call_tool("business_write", write_args(
                    title="Other Policy" if mode == "different" else "Refund Policy",
                    content="winner two", base_hash=base,
                )),
            )
            assert sum(not r.is_error for r in responses) == (2 if mode == "different" else 1)
    pages = list((settings.workspace / "nodes").glob("*.md"))
    assert len(pages) == (2 if mode == "different" else 1)
    saved = "\n".join(p.read_text(encoding="utf-8") for p in pages)
    assert ("winner one" in saved) + ("winner two" in saved) == (2 if mode == "different" else 1)
    writes = events(settings)
    assert sum(e["outcome"] == "success" for e in writes) == (2 if mode in ("update", "different") else 1)
    for event in writes:
        if event["outcome"] != "success":
            assert event["after_hash"] is None


async def test_conflict_denial_and_invalid_arguments_are_audited_without_payload(settings, caplog):
    caplog.set_level(logging.INFO)
    app = create_remote_app(settings)
    secret = "private-knowledge-body"
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (writer, _):
            initial = await writer.call_tool("business_write", write_args(content=secret))
            base = initial.structured_content["base_hash"]
            update = await writer.call_tool("business_write", write_args(content=secret + " changed", base_hash=base))
            assert not update.is_error
            conflict = await writer.call_tool("business_write", write_args(content="replacement", base_hash=base))
            assert conflict.is_error and secret + " changed" in conflict.content[0].text
            assert (await writer.call_tool("business_explore", {"query": "sensitive-query"})).is_error is False
            assert (await writer.call_tool("business_lint", {"project_path": "sensitive-path"})).is_error
        async with sdk(app, "test-reader") as (reader, _):
            assert (await reader.call_tool("business_write", write_args(content="sensitive-content"))).is_error
    records = events(settings)
    assert {e["outcome"] for e in records} >= {"success", "conflict", "denied", "invalid_arguments"}
    success = [e for e in records if e["tool"] == "business_write" and e["outcome"] == "success"]
    assert success[0]["before_hash"] is None
    assert success[1]["before_hash"] == base
    assert success[1]["after_hash"] == update.structured_content["base_hash"]
    assert all(e["subject"] in ("reader", "writer") for e in records)
    for e in records:
        if e["outcome"] != "success":
            assert e["after_hash"] is None
    for private in (secret, "test-writer", "test-reader", "sensitive-query", "sensitive-content", "sensitive-path"):
        assert private not in settings.audit_file.read_text(encoding="utf-8")
        assert private not in caplog.text


async def test_audit_failure_after_save_returns_saved_hash_without_retry(settings, monkeypatch, caplog):
    from business_graph_mcp.audit import AuditWriter

    def disk_full(self, event):
        raise OSError("secret-disk-error")

    monkeypatch.setattr(AuditWriter, "append", disk_full)
    app = create_remote_app(settings)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (writer, _):
            response = await writer.call_tool("business_write", write_args(content="saved-once"))
            assert not response.is_error
            result = response.structured_content
            assert result["audit_warning"]
            saved = (settings.workspace / "nodes" / "Refund Policy.md").read_text(encoding="utf-8")
            assert result["base_hash"] == content_hash(saved)
            assert result["created"] is True
    assert "secret-disk-error" not in caplog.text


async def test_index_failure_saved_markdown_and_reconnect_identifies_current_hash(settings, monkeypatch, caplog):
    import business_graph_mcp.writer as writer

    def fail_index(*args, **kwargs):
        raise RuntimeError("private-index-error")

    app = create_remote_app(settings)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (client, _):
            initial = await client.call_tool("business_write", write_args())
            old_hash = initial.structured_content["base_hash"]
            with monkeypatch.context() as patch:
                patch.setattr(writer, "sync_index", fail_index)
                saved = await client.call_tool("business_write", write_args(content="persisted despite index failure", base_hash=old_hash))
            assert not saved.is_error
            assert saved.structured_content["indexed"] is False
            assert saved.structured_content["warning"]
            assert "private-index-error" not in saved.structured_content["warning"]
        # 完成後關閉第一個 client；新的獨立連線重新取得權威現況。
        async with sdk(app, "test-writer") as (reconnected, _):
            current = await reconnected.call_tool("business_explore", {"query": "Refund Policy"})
            assert saved.structured_content["base_hash"] in current.structured_content["result"]
            assert "persisted despite index failure" in current.structured_content["result"]
            stale = await reconnected.call_tool("business_write", write_args(content="unsafe replay", base_hash=old_hash))
            assert stale.is_error
    markdown = (settings.workspace / "nodes" / "Refund Policy.md").read_text(encoding="utf-8")
    assert "unsafe replay" not in markdown
    assert content_hash(markdown) == saved.structured_content["base_hash"]
    assert "private-index-error" not in caplog.text


async def test_unexpected_error_and_lock_timeout_return_safe_errors_and_audit(settings, monkeypatch, caplog):
    from business_graph_mcp.errors import GraphLockTimeoutError
    from business_graph_mcp.service import GraphService

    app = create_remote_app(settings)
    async with app.router.lifespan_context(app):
        async with sdk(app, "test-writer") as (client, _):
            for error, outcome in ((RuntimeError("secret-runtime-body"), "error"), (GraphLockTimeoutError("secret-lock-path"), "timeout")):
                def fail(self, failure=error, **kwargs):
                    raise failure
                monkeypatch.setattr(GraphService, "write", fail)
                result = await client.call_tool("business_write", write_args())
                assert result.is_error
                assert "secret-" not in result.content[0].text
                assert events(settings)[-1]["outcome"] == outcome
                assert events(settings)[-1]["after_hash"] is None
    assert "secret-" not in caplog.text
    assert not list((settings.workspace / "nodes").glob("*.md"))


async def test_http_real_lock_timeout_is_explicit_and_preserves_data(settings, monkeypatch):
    from contextlib import contextmanager

    import business_graph_mcp.engine as engine
    from business_graph_mcp.locking import workspace_lock

    @contextmanager
    def short_lock(root):
        with workspace_lock(root, timeout=0.1):
            yield

    monkeypatch.setattr(engine, "workspace_lock", short_lock)
    app = create_remote_app(settings)
    async with app.router.lifespan_context(app), sdk(app, "test-writer") as (client, _):
        with workspace_lock(settings.workspace):
            response = await client.call_tool("business_write", write_args())
        assert response.is_error
        assert "逾時" in response.content[0].text
        assert events(settings)[-1]["outcome"] == "timeout"
        assert events(settings)[-1]["after_hash"] is None
        retried = await client.call_tool("business_write", write_args())
        assert not retried.is_error
        assert retried.structured_content["created"] is True


async def test_sdk_schema_validation_does_not_log_raw_input(settings, caplog):
    caplog.set_level(logging.INFO)
    app = create_remote_app(settings)
    async with app.router.lifespan_context(app), sdk(app, "test-writer") as (client, _):
        invalid_write = await client.call_tool("business_write", write_args(content={"private": "secret-body-test-writer"}))
        invalid_query = await client.call_tool("business_explore", {"query": {"private": "secret-query-test-writer"}})
        assert invalid_write.is_error and invalid_query.is_error
    for private in ("secret-body", "secret-query", "test-writer"):
        assert private not in caplog.text
    assert not list((settings.workspace / "nodes").glob("*.md"))
