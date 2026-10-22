"""遠端 CLI 與既有入口的相容測試。"""

from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest

from business_graph_mcp import __main__ as cli
from tests.test_remote_http import credentials


def test_serve_builds_explicit_settings_and_initializes(tmp_path, monkeypatch):
    auth = tmp_path / "auth.toml"
    credentials(auth)
    root = tmp_path / "new-knowledge"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business-graph-mcp",
            "serve",
            "--workspace",
            str(root),
            "--credentials-file",
            str(auth),
            "--audit-file",
            str(tmp_path / "audit.jsonl"),
            "--public-url",
            "https://knowledge.example/mcp",
            "--init",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--allowed-host",
            "knowledge.example",
            "--allowed-host",
            "proxy.internal:9000",
            "--allowed-origin",
            "https://client.example",
            "--max-request-bytes",
            "2048",
        ],
    )
    runner = Mock()
    monkeypatch.setattr(cli, "run_remote", runner, raising=False)
    cli.main()
    settings = runner.call_args.args[0]
    assert settings.workspace == root
    assert (root / "nodes").is_dir()
    assert not (root / "bgraph.toml").exists()
    assert settings.codegraph_db is None
    assert settings.host == "0.0.0.0" and settings.port == 9000
    assert settings.max_request_bytes == 2048
    assert settings.allowed_hosts == ("knowledge.example", "proxy.internal:9000")
    assert settings.allowed_origins == ("https://client.example",)


def test_serve_invalid_settings_exit_without_starting(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business-graph-mcp",
            "serve",
            "--workspace",
            str(tmp_path),
            "--credentials-file",
            str(tmp_path / "absent"),
            "--audit-file",
            str(tmp_path / "audit"),
            "--public-url",
            "https://knowledge.example",
        ],
    )
    runner = Mock()
    monkeypatch.setattr(cli, "run_remote", runner, raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    runner.assert_not_called()
    assert "啟動失敗" in capsys.readouterr().err


def test_no_arguments_still_runs_stdio(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["business-graph-mcp"])
    server = Mock()
    monkeypatch.setattr(cli, "create_server", lambda: server)
    cli.main()
    server.run.assert_called_once_with()
