"""知識庫目錄（.bgraph/）的定位與建立。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: 知識庫目錄名稱；跟著 repo 走，隨 git 版控。
GRAPH_DIR_NAME = ".bgraph"


@dataclass(frozen=True)
class Workspace:
    """一個知識庫的路徑集合。

    欄位:
        root: .bgraph/ 目錄本身。
    """

    root: Path

    @property
    def nodes_dir(self) -> Path:
        """節點 markdown 所在目錄；此目錄是真相來源，隨 git 版控。"""
        return self.root / "nodes"

    @property
    def db_path(self) -> Path:
        """衍生索引的路徑；不進 git，隨時可刪除重建。"""
        return self.root / "index.db"

    @property
    def config_path(self) -> Path:
        """設定檔路徑；不存在時一律採用預設值。"""
        return self.root / "bgraph.toml"


def _start_dir(start: Path) -> Path:
    """把起始路徑正規化成目錄。

    工具的 project_path 可能被指到某個檔案，此時應從它所在的目錄開始找。

    參數:
        start: 呼叫端提供的起始路徑。
    """
    resolved = start.resolve()
    return resolved if resolved.is_dir() else resolved.parent


def find_git_boundary(current: Path, home: Path) -> Path | None:
    """向上尋找最近的 repository 邊界（.git 所在層），受家目錄邊界限制。

    搜尋規則依兩層邊界：
    1. Repository 邊界（.git 層）：防止採用無關祖先 repo 的知識庫。
       每個 repo 應有獨立知識庫；靜默採用祖先 repo 知識庫是無感的嚴重錯誤。
    2. 使用者主目錄邊界：防止向上搜進家目錄及其祖先。理由同上，但針對
       工作目錄不在任何 repo 內的情況（如臨時目錄）。允許進入家目錄會導致
       任何非 repo 工作都採用 ~/.bgraph，造成交叉污染。

    本函式原為模組內部輔助，現為公開介面：`lint` 模組也依賴這個邊界，
    用來判斷一個知識圖是否屬於「自己的」repository，藉此避免誤報無關
    祖先 repo（例如使用者家目錄的 dotfiles repo）的 git 狀態。

    參數:
        current: 起始目錄（已正規化）。
        home: 使用者家目錄（由 Path.home() 取得）。

    回傳:
        最近的 .git 所在目錄，或 None 如果找不到（包括被家目錄邊界擋住）。
    """
    for directory in [current, *current.parents]:
        # 檢查是否進入家目錄（起始層本身不受限）
        if directory != current and (directory == home or directory in home.parents):
            # 進入家目錄範圍，停止向上搜
            break

        if (directory / ".git").exists():
            return directory

    return None


def find_workspace(start: Path) -> Workspace | None:
    """從指定路徑向上尋找最近的 .bgraph/ 目錄。

    找不到時回傳 None 而非拋例外：Spec 第九節原則 3 要求「尚未建立知識圖」
    是一種正常狀態，呼叫端應回覆明確訊息而不是錯誤。

    搜尋範圍受兩個邊界限制，各有明確用途：

    1. Repository 邊界（.git 層）：防止採用無關祖先 repo 的知識庫。
       每個 repo 應有自己獨立的知識庫；靜默採用祖先 repo 的知識庫比查不到
       更危險——會導致無感地使用錯誤的知識庫，症狀難以發現。

    2. 使用者主目錄邊界：防止向上搜進家目錄及其祖先。理由同上，但針對
       工作目錄不在任何 repo 內的情況（如臨時目錄）。若允許進入家目錄，
       任何非 repo 的工作都會採用 ~/.bgraph，造成交叉污染。

    搜尋規則：
    - 起始目錄本身一律檢查（即使它恰好是家目錄）
    - 向上走訪時，遇到含有 .git 的目錄就在檢查完該層後停止
    - 向上走訪時，不得進入使用者家目錄或其任何祖先
    - 若在允許範圍內都沒有 .git，視為「不在 repository 內」，只搜起始層

    參數:
        start: 起始路徑，可為目錄或檔案。
    """
    current = _start_dir(start)
    home = Path.home()

    # 第一階段：找到 repository 邊界（.git 所在層），但不進入家目錄
    git_boundary = find_git_boundary(current, home)

    # 第二階段：搜尋 .bgraph，但限制在邊界內
    if git_boundary is None:
        # 不在任何 repo 內，只搜起始層
        candidate = current / GRAPH_DIR_NAME
        return Workspace(root=candidate) if candidate.is_dir() else None

    # 在 repo 內，搜起始層到 git_boundary（含）
    for directory in [current, *current.parents]:
        candidate = directory / GRAPH_DIR_NAME
        if candidate.is_dir():
            return Workspace(root=candidate)
        if directory == git_boundary:
            break

    return None


def ensure_workspace(start: Path) -> Workspace:
    """取得知識庫，不存在則建立。

    建立位置優先選擇最近的、含有 .git 的祖先目錄——業務知識屬於整個 repo，
    建在 src/app/ 之類的子目錄會讓同一個 repo 出現多個互不相通的知識庫。
    找不到 .git 時退回起始目錄。

    向上尋找 .git 時遵循 find_workspace 的邊界限制：不進入家目錄及其祖先。

    參數:
        start: 起始路徑，可為目錄或檔案。
    """
    existing = find_workspace(start)
    if existing is not None:
        existing.nodes_dir.mkdir(parents=True, exist_ok=True)
        return existing

    current = _start_dir(start)
    home = Path.home()

    # 尋找 .git 邊界；若找到則建在邊界層，否則建在起始層
    git_boundary = find_git_boundary(current, home)
    base = git_boundary if git_boundary is not None else current

    workspace = Workspace(root=base / GRAPH_DIR_NAME)
    workspace.nodes_dir.mkdir(parents=True, exist_ok=True)
    return workspace
