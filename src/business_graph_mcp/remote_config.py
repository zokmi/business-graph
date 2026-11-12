"""遠端服務的明確路徑、網路設定與安全初始化。"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from business_graph_mcp.audit import audit_lock_path
from business_graph_mcp.config import load_config
from business_graph_mcp.errors import GraphError
from business_graph_mcp.remote_auth import load_principals
from business_graph_mcp.workspace import GRAPH_DIR_NAME, Workspace


def _absolute_path(path: Path, name: str) -> Path:
    """拒絕隱含工作目錄，並固定管理者指定的符號連結目標。"""
    if not isinstance(path, Path) or not path.is_absolute():
        raise GraphError(f"{name} 必須是明確的絕對路徑。")
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        raise GraphError(f"無法解析 {name} 路徑。") from None


def validate_remote_workspace(path: Path, *, initialize: bool = False) -> Workspace:
    """驗證指定知識庫本身；僅明確初始化時建立不存在或空的目錄。

    不尋找祖先；既有 nodes 目錄缺設定時沿用本機預設值。
    """
    root = _absolute_path(path, "workspace")
    try:
        if initialize:
            if root.exists() and not root.is_dir():
                raise GraphError("workspace 必須是目錄。")
            if root.is_dir() and any(root.iterdir()) and not (root / "nodes").is_dir():
                raise GraphError("非空且無法辨識的目錄不可初始化。")
            if not root.exists() or not any(root.iterdir()):
                (root / "nodes").mkdir(parents=True, exist_ok=True)

        workspace = Workspace(root)
        if not root.is_dir() or not workspace.nodes_dir.is_dir():
            raise GraphError("workspace 必須直接指向已包含 nodes 的知識庫目錄。")
        # 開啟目錄以驗證可讀性；不要求載入或修改任何節點。
        next(workspace.nodes_dir.iterdir(), None)
        try:
            workspace.config_path.lstat()
        except FileNotFoundError:
            return workspace
        if not workspace.config_path.is_file():
            raise GraphError("既有 bgraph.toml 必須是可讀的設定檔。")
        load_config(workspace.config_path)
        return workspace
    except (OSError, UnicodeError):
        raise GraphError("無法讀取遠端知識庫或其設定。") from None


def _hostname(value: str) -> bool:
    """接受具體 DNS 名稱或 IP，拒絕萬用字元與 URL。"""
    if not value or len(value) > 253:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            is not None
            for label in value.rstrip(".").split(".")
        )


def _url(value: str, name: str, *, origin_only: bool = False) -> SplitResult:
    """驗證外部 HTTPS URL；開發用 HTTP 僅允許 loopback。"""
    try:
        if not isinstance(value, str) or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in ("https", "http")
            or host is None
            or not _hostname(host)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
            or parsed.netloc.endswith(":")
            or (origin_only and parsed.path)
        ):
            raise ValueError
        if parsed.scheme == "http" and host != "localhost":
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        return parsed
    except ValueError:
        raise GraphError(f"{name} 必須是有效的 HTTPS 網址；HTTP 僅允許 loopback。") from None


@dataclass(frozen=True)
class RemoteSettings:
    """啟動時驗證並固定一份遠端知識庫的完整設定。

    空的 allowlist 從 public_url 衍生單一具體來源，不加入萬用字元。
    """

    workspace: Path | None
    credentials_file: Path
    audit_file: Path
    public_url: str
    host: str = "127.0.0.1"
    port: int = 8000
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    max_request_bytes: int = 1024 * 1024
    codegraph_db: Path | None = None
    project_workspaces: tuple[Path, ...] = ()
    project_bindings: tuple[tuple[Path, Path], ...] = ()
    local_project_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        public = _url(self.public_url, "public_url")
        if not isinstance(self.host, str) or not _hostname(self.host):
            raise GraphError("host 必須是具體的監聽主機名稱或 IP。")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise GraphError("port 必須是 1 到 65535 的整數。")
        if type(self.max_request_bytes) is not int or self.max_request_bytes <= 0:
            raise GraphError("max_request_bytes 必須是正整數。")
        if not isinstance(self.allowed_hosts, tuple) or not isinstance(self.allowed_origins, tuple):
            raise GraphError("允許的主機與來源必須是 tuple。")
        allowed_hosts = self.allowed_hosts or (public.netloc,)
        allowed_origins = self.allowed_origins or (f"{public.scheme}://{public.netloc}",)
        for host in allowed_hosts:
            if not isinstance(host, str):
                raise GraphError("allowed_hosts 必須包含具體主機名稱。")
            parsed = _url(f"https://{host}", "allowed_hosts", origin_only=True)
            if parsed.netloc != host:
                raise GraphError("allowed_hosts 必須包含具體主機名稱。")
        for origin in allowed_origins:
            _url(origin, "allowed_origins", origin_only=True)

        local_roots = tuple(_absolute_path(path, "local_project_root")
                            for path in self.local_project_roots)
        if local_roots:
            loopback_names = {"127.0.0.1", "localhost", "::1"}
            if self.host not in loopback_names or public.hostname not in loopback_names:
                raise GraphError("本機專案探索只能使用 loopback 位址。")
            if any(not path.is_dir() for path in local_roots):
                raise GraphError("local_project_root 必須是既有目錄。")
        if self.workspace is None and (not local_roots or self.codegraph_db is not None):
            raise GraphError("固定服務或使用 codegraph_db 時必須指定預設知識庫。")
        root = _absolute_path(self.workspace, "workspace") if self.workspace is not None else None
        projects = tuple(
            _absolute_path(path, "project_workspace") for path in self.project_workspaces
        )
        if any(path.name != GRAPH_DIR_NAME for path in projects):
            raise GraphError("project_workspace 必須直接指向專案的 .bgraph 目錄。")
        roots = set(projects)
        if root is not None:
            roots.add(root)
        bindings = tuple(
            (_absolute_path(project, "project_root"), _absolute_path(target, "workspace"))
            for project, target in self.project_bindings
        )
        if len(dict(bindings)) != len(bindings):
            raise GraphError("同一個專案只能登記一份知識庫。")
        if any(not project.is_dir() or target not in roots for project, target in bindings):
            raise GraphError("專案對應必須指定既有專案目錄和已登記的知識庫。")
        credentials = _absolute_path(self.credentials_file, "credentials_file")
        audit = _absolute_path(self.audit_file, "audit_file")
        audit_lock = _absolute_path(audit_lock_path(audit), "audit 鎖")
        codegraph = (
            _absolute_path(self.codegraph_db, "codegraph_db")
            if self.codegraph_db is not None else None
        )
        protected = roots | {
            _absolute_path(workspace / name, name)
            for workspace in roots
            for name in (
                "bgraph.toml", "index.db", "index.db-wal", "index.db-shm",
                "index.db-journal", "workspace.lock",
            )
        }
        nodes = [_absolute_path(workspace / "nodes", "nodes") for workspace in roots]
        operational = [credentials, audit, audit_lock] + (
            [codegraph] if codegraph is not None else []
        )
        if len(set(operational)) != len(operational) or any(
            path in protected or any(path.is_relative_to(directory) for directory in nodes)
            for path in operational
        ):
            raise GraphError("憑證、操作紀錄與程式碼索引不得互相覆蓋或佔用知識庫檔案。")
        try:
            for path in (audit, audit_lock):
                if path.exists() and not path.is_file():
                    raise GraphError("audit_file 與操作紀錄鎖必須是檔案路徑。")
                # 操作紀錄與側檔只可使用獨立檔案，避免硬連結修改其他資料。
                if path.exists() and path.stat().st_nlink > 1:
                    raise GraphError("audit_file 與操作紀錄鎖不可使用硬連結。")
            if codegraph is not None and not codegraph.is_file():
                raise GraphError("codegraph_db 必須是已存在的索引檔案。")
            for index, path in enumerate(operational):
                if path.exists() and any(
                    other.exists() and path.samefile(other)
                    for other in [*operational[index + 1:], *protected]
                ):
                    raise GraphError("設定路徑不得透過硬連結佔用同一個檔案。")
        except OSError:
            raise GraphError("無法檢查操作紀錄或程式碼索引路徑。") from None
        load_principals(credentials)
        for workspace in roots:
            validate_remote_workspace(workspace)
        for name, value in (
            ("workspace", root), ("credentials_file", credentials), ("audit_file", audit),
            ("codegraph_db", codegraph), ("allowed_hosts", allowed_hosts),
            ("allowed_origins", allowed_origins),
            ("project_workspaces", projects),
            ("project_bindings", bindings),
            ("local_project_roots", local_roots),
        ):
            object.__setattr__(self, name, value)
