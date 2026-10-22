"""遠端設定必須明確綁定工作區，且不損害既有檔案。"""
from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from business_graph_mcp.errors import GraphError
from business_graph_mcp.remote_config import RemoteSettings, validate_remote_workspace


@pytest.fixture
def settings_args(tmp_path):
    workspace = tmp_path / ".bgraph"
    (workspace / "nodes").mkdir(parents=True)
    credentials = tmp_path / "credentials.toml"
    digest = hashlib.sha256(b"test-only-token-with-at-least-256-bits-of-fixture-data").hexdigest()
    credentials.write_text(
        f'[[principals]]\nsubject = "reader"\nrole = "read"\ntoken_sha256 = "{digest}"\n',
        encoding="utf-8",
    )
    return dict(workspace=workspace, credentials_file=credentials,
                audit_file=tmp_path / "audit.jsonl", public_url="https://knowledge.example:8443/mcp")


def test_existing_graph_without_config_uses_defaults_and_pins_paths(settings_args):
    settings = RemoteSettings(**settings_args)
    assert settings.workspace == settings_args["workspace"].resolve()
    assert settings.allowed_hosts == ("knowledge.example:8443",)
    assert settings.allowed_origins == ("https://knowledge.example:8443",)
    assert not (settings.workspace / "bgraph.toml").exists()
    with pytest.raises(FrozenInstanceError):
        settings.port = 9999


@pytest.mark.parametrize("field", ["workspace", "credentials_file", "audit_file", "public_url"])
def test_required_remote_settings_cannot_be_omitted(settings_args, field):
    settings_args.pop(field)
    with pytest.raises(TypeError):
        RemoteSettings(**settings_args)


@pytest.mark.parametrize("field", ["workspace", "credentials_file", "audit_file", "codegraph_db"])
def test_paths_must_be_explicitly_absolute(settings_args, field):
    settings_args[field] = Path("relative")
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


def test_workspace_does_not_search_parent_or_initialize(settings_args):
    root = settings_args["workspace"]
    (root / "child").mkdir()
    settings_args["workspace"] = root / "child"
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)
    assert list((root / "child").iterdir()) == []


@pytest.mark.parametrize("kind", ["malformed", "unicode", "directory", "unreadable"])
def test_existing_invalid_config_fails_startup(settings_args, monkeypatch, kind):
    config = settings_args["workspace"] / "bgraph.toml"
    if kind == "directory":
        config.mkdir()
    elif kind == "unicode":
        config.write_bytes(b"\xff")
    else:
        config.write_text("stale_days = = 1", encoding="utf-8")
    if kind == "unreadable":
        original = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == config:
                raise PermissionError("無法讀取測試設定")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


@pytest.mark.parametrize("url", ["", "knowledge.example", "http://knowledge.example/mcp", "ftp://knowledge.example", "https://u:p@knowledge.example", "https://knowledge.example/mcp?q=1", "https://knowledge.example/#fragment", "https://knowledge.example:bad", "https://knowledge.example:0", "https://knowledge.example\n/mcp", "https://[::1]unexpected/mcp", "https://knowledge.example/\x00"])
def test_invalid_public_url_fails_startup(settings_args, url):
    settings_args["public_url"] = url
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


@pytest.mark.parametrize("url,host,origin", [("http://127.0.0.1:8000/mcp", "127.0.0.1:8000", "http://127.0.0.1:8000"), ("http://[::1]:8000/mcp", "[::1]:8000", "http://[::1]:8000"), ("http://localhost:8000/mcp", "localhost:8000", "http://localhost:8000")])
def test_http_loopback_retains_explicit_port(settings_args, url, host, origin):
    settings_args["public_url"] = url
    settings = RemoteSettings(**settings_args)
    assert settings.allowed_hosts == (host,)
    assert settings.allowed_origins == (origin,)


@pytest.mark.parametrize("overrides", [{"port": 0}, {"port": 65536}, {"port": True}, {"max_request_bytes": 0}, {"max_request_bytes": True}, {"host": ""}, {"host": "https://example.com"}, {"allowed_hosts": ("*",)}, {"allowed_hosts": ("*.example.com",)}, {"allowed_hosts": ("https://example.com",)}, {"allowed_origins": ("*",)}, {"allowed_origins": ("https://example.com/path",)}, {"allowed_hosts": ["example.com"]}])
def test_invalid_listener_and_allowlists_are_rejected(settings_args, overrides):
    with pytest.raises(GraphError):
        RemoteSettings(**(settings_args | overrides))


@pytest.mark.parametrize("target", ["nodes/events.md", "nodes", "bgraph.toml", "index.db", "index.db-wal", "index.db-shm", "index.db-journal", "workspace.lock"])
@pytest.mark.parametrize("field", ["audit_file", "credentials_file"])
def test_operational_files_cannot_overwrite_knowledge_or_index(settings_args, target, field):
    settings_args[field] = settings_args["workspace"] / target
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


def test_operational_files_cannot_collide_with_each_other(settings_args):
    settings_args["audit_file"] = settings_args["credentials_file"]
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


def test_codegraph_requires_explicit_existing_noncolliding_file(settings_args, tmp_path):
    settings_args["codegraph_db"] = tmp_path / "missing.db"
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)
    settings_args["codegraph_db"] = settings_args["credentials_file"]
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)
    database = tmp_path / "codegraph.db"
    database.touch()
    settings = RemoteSettings(**(settings_args | {"codegraph_db": database}))
    assert settings.codegraph_db == database.resolve()


@pytest.mark.parametrize("existing", [False, True])
def test_explicit_initialization_only_creates_requested_empty_workspace(tmp_path, existing):
    root = tmp_path / "new-graph"
    if existing:
        root.mkdir()
    workspace = validate_remote_workspace(root, initialize=True)
    assert workspace.root == root.resolve()
    assert workspace.nodes_dir.is_dir()
    assert list(root.iterdir()) == [workspace.nodes_dir]
    assert validate_remote_workspace(root, initialize=True) == workspace


def test_initialization_refuses_unknown_nonempty_directory_without_changes(tmp_path):
    preserved = tmp_path / "keep.txt"
    preserved.write_text("preserve me", encoding="utf-8")
    with pytest.raises(GraphError):
        validate_remote_workspace(tmp_path, initialize=True)
    assert preserved.read_text(encoding="utf-8") == "preserve me"
    assert list(tmp_path.iterdir()) == [preserved]


def test_missing_workspace_is_not_created_without_initialization(tmp_path):
    root = tmp_path / "missing"
    with pytest.raises(GraphError):
        validate_remote_workspace(root)
    assert not root.exists()


def test_admin_symlink_is_pinned_to_resolved_workspace(settings_args, tmp_path):
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(settings_args["workspace"], target_is_directory=True)
    except OSError:
        pytest.skip("測試主機未授予建立符號連結權限")
    settings = RemoteSettings(**(settings_args | {"workspace": alias}))
    assert settings.workspace == settings_args["workspace"].resolve()


@pytest.mark.parametrize("target", ["nodes", "bgraph.toml", "index.db"])
def test_symlinked_graph_paths_are_protected_from_audit_collisions(settings_args, tmp_path, target):
    root = settings_args["workspace"]
    external = tmp_path / "external"
    link = root / target
    if target == "nodes":
        link.rmdir()
        external.mkdir()
    else:
        external.write_text("", encoding="utf-8")
    try:
        link.symlink_to(external, target_is_directory=target == "nodes")
    except OSError:
        pytest.skip("測試主機未授予建立符號連結權限")
    settings_args["audit_file"] = external / "audit.jsonl" if target == "nodes" else external
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


def test_hardlinked_audit_cannot_modify_credentials(settings_args, tmp_path):
    audit = tmp_path / "audit-alias.jsonl"
    audit.hardlink_to(settings_args["credentials_file"])
    settings_args["audit_file"] = audit
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


@pytest.mark.parametrize("field", ["credentials_file", "codegraph_db"])
def test_audit_lock_sidecar_cannot_modify_other_operational_files(settings_args, field):
    lock = settings_args["audit_file"].with_name(settings_args["audit_file"].name + ".lock")
    lock.write_bytes(settings_args["credentials_file"].read_bytes())
    settings_args[field] = lock
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)


def test_audit_lock_sidecar_hardlink_cannot_modify_credentials(settings_args):
    lock = settings_args["audit_file"].with_name(settings_args["audit_file"].name + ".lock")
    lock.hardlink_to(settings_args["credentials_file"])
    with pytest.raises(GraphError):
        RemoteSettings(**settings_args)
