"""個別憑證必須可撤銷，且所有錯誤情形均拒絕授權。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from mcp.server.auth.provider import TokenVerifier

from business_graph_mcp.errors import GraphError
from business_graph_mcp.remote_auth import FileTokenVerifier, Principal, require_tool_access

TOKEN = "test-fixture-only-256-bit-placeholder-bearer-token"
OTHER_TOKEN = "another-test-fixture-only-256-bit-placeholder-token"
DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()
OTHER_DIGEST = hashlib.sha256(OTHER_TOKEN.encode()).hexdigest()


def credentials_text(subject="alice", role="read", digest=DIGEST):
    return f'[[principals]]\nsubject = "{subject}"\nrole = "{role}"\ntoken_sha256 = "{digest}"\n'


@pytest.fixture
def credentials_file(tmp_path):
    path = tmp_path / "credentials.toml"
    path.write_text(credentials_text(), encoding="utf-8")
    return path


@pytest.mark.parametrize("role,tool,allowed", [("read", "business_explore", True), ("read", "business_lint", True), ("read", "business_write", False), ("write", "business_explore", True), ("write", "business_lint", True), ("write", "business_write", True), ("write", "unknown", False), ("read", "unknown", False)])
def test_tool_permissions_are_checked_for_each_role(role, tool, allowed):
    principal = Principal("alice", role)
    if allowed:
        require_tool_access(principal, tool)
    else:
        with pytest.raises(GraphError):
            require_tool_access(principal, tool)


@pytest.mark.parametrize("subject,role", [("", "read"), (" ", "read"), ("alice", "admin"), ("alice", ""), (1, "read"), ("alice\nadmin", "write")])
def test_invalid_principals_cannot_be_constructed(subject, role):
    with pytest.raises(GraphError):
        Principal(subject, role)


@pytest.mark.parametrize("role,scopes", [("read", ["read"]), ("write", ["read", "write"])])
async def test_verifier_maps_subject_and_granted_scopes(credentials_file, role, scopes):
    credentials_file.write_text(credentials_text(role=role), encoding="utf-8")
    verifier: TokenVerifier = FileTokenVerifier(credentials_file)
    access = await verifier.verify_token(TOKEN)
    assert access is not None
    assert access.client_id == "alice"
    assert access.scopes == scopes


@pytest.mark.parametrize("token", ["", "wrong", TOKEN + " ", "\ud800"])
async def test_missing_and_incorrect_tokens_fail_closed(credentials_file, token):
    assert await FileTokenVerifier(credentials_file).verify_token(token) is None


async def test_removal_or_replacement_takes_effect_on_next_request(credentials_file):
    verifier = FileTokenVerifier(credentials_file)
    assert await verifier.verify_token(TOKEN) is not None
    credentials_file.write_text(credentials_text("bob", "write", OTHER_DIGEST), encoding="utf-8")
    assert await verifier.verify_token(TOKEN) is None
    replacement = await verifier.verify_token(OTHER_TOKEN)
    assert replacement is not None and replacement.client_id == "bob"
    credentials_file.unlink()
    assert await verifier.verify_token(OTHER_TOKEN) is None


async def test_role_change_takes_effect_on_next_request(credentials_file):
    verifier = FileTokenVerifier(credentials_file)
    credentials_file.write_text(credentials_text(role="write"), encoding="utf-8")
    access = await verifier.verify_token(TOKEN)
    assert access is not None and access.scopes == ["read", "write"]
    credentials_file.write_text(credentials_text(role="read"), encoding="utf-8")
    access = await verifier.verify_token(TOKEN)
    assert access is not None and access.scopes == ["read"]


@pytest.mark.parametrize("content", ["", "principals = []", "[principals]\nsubject = 'alice'", credentials_text(role="admin"), credentials_text(subject=""), credentials_text(digest="invalid"), credentials_text(digest="a" * 63), credentials_text(digest="g" * 64), credentials_text() + credentials_text(digest=OTHER_DIGEST), credentials_text() + credentials_text(subject="bob"), credentials_text() + 'token = "secret"', credentials_text() + '\nextra = 1', 'unexpected = true\n' + credentials_text(), '[[principals]]\nsubject = "alice"\nrole = "read"\n'])
def test_invalid_or_duplicate_credentials_prevent_startup(credentials_file, content):
    credentials_file.write_text(content, encoding="utf-8")
    with pytest.raises(GraphError):
        FileTokenVerifier(credentials_file)


@pytest.mark.parametrize("kind", ["toml", "unicode", "permission", "directory", "duplicate"])
async def test_unreadable_or_invalid_reload_never_reuses_previous_authority(credentials_file, monkeypatch, kind):
    verifier = FileTokenVerifier(credentials_file)
    assert await verifier.verify_token(TOKEN) is not None
    if kind == "toml":
        credentials_file.write_text(f'[[principals]]\nsubject = "{TOKEN}"\nrole = =', encoding="utf-8")
    elif kind == "unicode":
        credentials_file.write_bytes(b"\xff")
    elif kind == "directory":
        credentials_file.unlink()
        credentials_file.mkdir()
    elif kind == "duplicate":
        credentials_file.write_text(credentials_text() + credentials_text(subject="bob"), encoding="utf-8")
    else:
        original = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == credentials_file:
                raise PermissionError(TOKEN)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
    assert await verifier.verify_token(TOKEN) is None


def test_startup_error_does_not_disclose_credential_content(credentials_file):
    credentials_file.write_text(f'token = "{TOKEN}"\nbroken = =', encoding="utf-8")
    with pytest.raises(GraphError) as error:
        FileTokenVerifier(credentials_file)
    assert TOKEN not in str(error.value)


def test_missing_credential_file_prevents_startup(tmp_path):
    with pytest.raises(GraphError):
        FileTokenVerifier(tmp_path / "missing.toml")
