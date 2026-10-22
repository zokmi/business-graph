"""個別 Bearer 憑證與業務工具的權限驗證。"""
from __future__ import annotations

import hashlib
import hmac
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mcp.server.auth.provider import AccessToken, TokenVerifier

from business_graph_mcp.errors import GraphError


@dataclass(frozen=True)
class Principal:
    """可追溯的個別主體與其工具權限。"""

    subject: str
    role: Literal["read", "write"]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject, str)
            or not self.subject
            or self.subject != self.subject.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in self.subject)
        ):
            raise GraphError("憑證主體必須是非空且不含控制字元的字串。")
        if self.role not in ("read", "write"):
            raise GraphError("憑證角色必須是 read 或 write。")


def load_principals(path: Path) -> tuple[tuple[Principal, str], ...]:
    """嚴格讀取主體與 SHA256 摘要；錯誤訊息不包含憑證內容。"""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise GraphError("無法讀取有效的遠端憑證設定。") from None

    rows = data.get("principals")
    if set(data) != {"principals"} or not isinstance(rows, list) or not rows:
        raise GraphError("憑證設定必須包含非空的 principals 陣列。")

    result: list[tuple[Principal, str]] = []
    subjects: set[str] = set()
    digests: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"subject", "role", "token_sha256"}:
            raise GraphError("每筆憑證只可包含 subject、role、token_sha256。")
        principal = Principal(row["subject"], row["role"])
        digest = row["token_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise GraphError("憑證 token_sha256 必須是 SHA256 十六進位摘要。")
        digest = digest.lower()
        if principal.subject in subjects or digest in digests:
            raise GraphError("憑證主體與 token_sha256 不得重複。")
        subjects.add(principal.subject)
        digests.add(digest)
        result.append((principal, digest))
    return tuple(result)


class FileTokenVerifier(TokenVerifier):
    """每次驗證重新讀檔，讓撤銷與權限異動於下一請求生效。"""

    def __init__(self, credentials_file: Path) -> None:
        self.credentials_file = credentials_file
        # 啟動時就拒絕不可用的憑證，不快取任何授權資料。
        load_principals(self.credentials_file)

    async def verify_token(self, token: str) -> AccessToken | None:
        """只接受摘要相符的憑證；讀檔或解析失敗一律拒絕。"""
        if not isinstance(token, str) or not token:
            return None
        try:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            principals = load_principals(self.credentials_file)
        except (GraphError, UnicodeError):
            return None

        matched: Principal | None = None
        for principal, expected in principals:
            if hmac.compare_digest(digest, expected):
                matched = principal
        if matched is None:
            return None
        return AccessToken(
            token=token,
            client_id=matched.subject,
            scopes=["read", "write"] if matched.role == "write" else ["read"],
        )


def require_tool_access(principal: Principal, tool_name: str) -> None:
    """在每次工具呼叫驗證權限；未知工具一律拒絕。"""
    if tool_name in ("business_explore", "business_lint"):
        return
    if tool_name == "business_write" and principal.role == "write":
        return
    raise GraphError("此主體沒有呼叫指定工具的權限。")
