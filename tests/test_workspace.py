"""知識庫目錄定位的測試。"""
from __future__ import annotations

from pathlib import Path

from business_graph_mcp.workspace import ensure_workspace, find_workspace


def test_在起始目錄直接找到(tmp_path):
    (tmp_path / ".bgraph" / "nodes").mkdir(parents=True)

    ws = find_workspace(tmp_path)

    assert ws is not None
    assert ws.root == tmp_path / ".bgraph"
    assert ws.nodes_dir == tmp_path / ".bgraph" / "nodes"
    assert ws.db_path == tmp_path / ".bgraph" / "index.db"
    assert ws.config_path == tmp_path / ".bgraph" / "bgraph.toml"


def test_從深層子目錄向上找到(tmp_path):
    # 在起始點建立 .git 作為 repo 邊界，模擬「深層子目錄在某個 repo 內」的情境
    (tmp_path / ".git").mkdir()
    (tmp_path / ".bgraph" / "nodes").mkdir(parents=True)
    深層 = tmp_path / "src" / "app" / "services"
    深層.mkdir(parents=True)

    ws = find_workspace(深層)

    assert ws is not None
    assert ws.root == tmp_path / ".bgraph"


def test_找不到時回傳_none_而不拋例外(tmp_path):
    # Spec 第九節原則 3：.bgraph/ 不存在不是錯誤，recall 要能回明確訊息。
    assert find_workspace(tmp_path) is None


def test_遇到檔案而非目錄的_bgraph_不視為知識庫(tmp_path):
    (tmp_path / ".bgraph").write_text("我是檔案不是目錄", encoding="utf-8")

    assert find_workspace(tmp_path) is None


def test_起始路徑是檔案時從其所在目錄開始找(tmp_path):
    (tmp_path / ".bgraph" / "nodes").mkdir(parents=True)
    檔案 = tmp_path / "README.md"
    檔案.write_text("x", encoding="utf-8")

    ws = find_workspace(檔案)

    assert ws is not None
    assert ws.root == tmp_path / ".bgraph"


def test_ensure_在有_git_的祖先目錄建立知識庫(tmp_path):
    # 建在專案根而非當下目錄：業務知識屬於整個 repo，
    # 建在 src/app/ 之類的子目錄會讓同一 repo 出現多個互不相通的知識庫。
    (tmp_path / ".git").mkdir()
    深層 = tmp_path / "src" / "app"
    深層.mkdir(parents=True)

    ws = ensure_workspace(深層)

    assert ws.root == tmp_path / ".bgraph"
    assert ws.nodes_dir.is_dir()


def test_ensure_沒有_git_時建在起始目錄(tmp_path):
    深層 = tmp_path / "src"
    深層.mkdir()

    ws = ensure_workspace(深層)

    assert ws.root == 深層 / ".bgraph"
    assert ws.nodes_dir.is_dir()


def test_ensure_已存在時直接沿用不重建(tmp_path):
    (tmp_path / ".bgraph" / "nodes").mkdir(parents=True)
    既有頁 = tmp_path / ".bgraph" / "nodes" / "X.md"
    既有頁.write_text("內容", encoding="utf-8")

    ws = ensure_workspace(tmp_path)

    assert ws.root == tmp_path / ".bgraph"
    assert 既有頁.read_text(encoding="utf-8") == "內容"


# ===== 密封測試（用 monkeypatch 控制 Path.home） =====


def test_起始目錄就是家目錄時仍會被檢查(tmp_path, monkeypatch):
    """驗證 Rule 1：起始目錄本身一律檢查，即使它恰好是家目錄。"""
    # 把家目錄指到 tmp_path，模擬「工作在家目錄」的情境
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    (tmp_path / ".bgraph").mkdir()
    ws = find_workspace(tmp_path)

    assert ws is not None
    assert ws.root == tmp_path / ".bgraph"


def test_家目錄及其祖先不得被採用(tmp_path, monkeypatch):
    """驗證 Rule 2：家目錄及其祖先不得被向上搜尋採用。

    此測試會因移除邊界檢查而失敗——這正是回歸保護的用意。
    """
    # 把家目錄指到 tmp_path，結構為：tmp_path/.bgraph（家目錄）+ tmp_path/sub/deep（工作目錄）
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    (tmp_path / ".bgraph").mkdir()
    深層 = tmp_path / "sub" / "deep"
    深層.mkdir(parents=True)

    # 從深層向上找，不應採用家目錄的 .bgraph
    ws = find_workspace(深層)

    assert ws is None


def test_git邊界之外的bgraph不得被找到(tmp_path, monkeypatch):
    """驗證 Rule 3：.git 邊界之外的 .bgraph 不得被找到。

    結構：tmp_path/outer/.bgraph + tmp_path/outer/inner/.git，
    起點 tmp_path/outer/inner/deep → 應回傳 None（不得採用 outer/.bgraph）。
    """
    # 把家目錄指到 tmp_path 外的某處，使得邊界只由 .git 決定
    monkeypatch.setattr(Path, "home", lambda: tmp_path.parent.parent)

    (tmp_path / "outer" / ".bgraph").mkdir(parents=True)
    (tmp_path / "outer" / "inner" / ".git").mkdir(parents=True)
    深層 = tmp_path / "outer" / "inner" / "deep"
    深層.mkdir(parents=True)

    ws = find_workspace(深層)

    assert ws is None
