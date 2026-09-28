"""將舊 .wiki 知識庫複製成 .bgraph，保留原件供逐頁複查。"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from business_graph_mcp.errors import GraphMigrationError
from business_graph_mcp.node import NodeMeta, parse_legacy_meta

OLD_DIR_NAME = ".wiki"
OLD_PAGES_DIR_NAME = "pages"
OLD_CONFIG_NAME = "wiki.toml"


@dataclass(frozen=True)
class MigrationReport:
    """遷移結果；moved 為成功複製的識別字，skipped 為略過的識別字。"""

    moved: tuple[str, ...]
    skipped: tuple[str, ...]
    config_moved: bool


def _render_legacy_node(meta: NodeMeta, text: str) -> bytes:
    """安全序列化舊中繼資料，保留結束分隔線後的原始換行與內文。

    共用的 render_node 會修剪內文，且未替特殊 YAML 字串加引號，
    因此遷移使用獨立序列化，避免改變手寫頁面及其他工具的既有行為。
    呼叫前已透過 parse_legacy_meta 確認 front matter 分隔線存在。
    """
    front_matter, tail = re.split(r"\r?\n---", text, maxsplit=1)
    # 已驗證為 mapping；以原始 YAML 值為準，避免遺漏自訂欄位或改變其型別。
    metadata = yaml.safe_load(front_matter.split("\n", 1)[1])
    metadata["type"] = meta.node_type
    header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    return ("---\n" + header + "---" + tail).encode("utf-8")


def _write_new(target: Path, content: bytes) -> bool:
    """完整寫入並關檔後才獨佔建立目標；失敗時只清理本次暫存目錄。

    暫存目錄位於目標旁，硬連結不跨檔案系統，也不覆寫已存在的目標。
    寫入或關檔失敗不會留下半成品目標；不支援硬連結時明確回報遷移錯誤。
    """
    try:
        directory = TemporaryDirectory(prefix=".migrate-", dir=target.parent)
    except OSError:
        return False
    try:
        staged = Path(directory.name) / "content"
        try:
            with staged.open("xb") as stream:
                stream.write(content)
        except OSError:
            return False
        try:
            os.link(staged, target)
        except (FileExistsError, FileNotFoundError):
            return False
        except PermissionError as exc:
            raise GraphMigrationError(
                f"無法建立 {target}：權限遭拒（{exc}）。"
                "請檢查目標目錄的寫入權限與硬連結權限後重跑；原始 .wiki 不受影響。"
            ) from exc
        except OSError as exc:
            raise GraphMigrationError(
                f"無法以硬連結建立 {target}：{exc}。"
                "請確認檔案系統支援硬連結，或將專案移到支援的磁碟後重跑；"
                "已完成的檔案會保留，原始 .wiki 不受影響。"
            ) from exc
        return True
    finally:
        try:
            directory.cleanup()
        except OSError as exc:
            logging.warning("無法清理遷移暫存目錄 %s：%s", directory.name, exc)


def migrate_workspace(repo: Path) -> MigrationReport:
    """複製舊頁面與設定，保留連結並將每頁型別暫定為 rule。

    解析失敗或目標已存在時跳過；不覆寫目標，也不刪除任何舊檔。
    """
    old_root = repo / OLD_DIR_NAME
    old_pages = old_root / OLD_PAGES_DIR_NAME
    if not old_pages.is_dir():
        return MigrationReport(moved=(), skipped=(), config_moved=False)

    new_root = repo / ".bgraph"
    new_nodes = new_root / "nodes"
    new_nodes.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    skipped: list[str] = []
    for path in sorted(old_pages.glob("*.md")):
        slug = path.stem
        target = new_nodes / path.name
        try:
            if target.exists():
                skipped.append(slug)
                continue
            text = path.read_bytes().decode("utf-8")
            meta = parse_legacy_meta(text)
            if meta is None:
                skipped.append(slug)
                continue
            copied = _write_new(target, _render_legacy_node(meta, text))
        except (OSError, UnicodeDecodeError):
            # 單頁可能是目錄、已消失或無權限，不應阻止後續可讀寫的頁面。
            skipped.append(slug)
            continue
        if copied:
            moved.append(slug)
        else:
            skipped.append(slug)

    old_config = old_root / OLD_CONFIG_NAME
    new_config = new_root / "bgraph.toml"
    config_moved = False
    if old_config.is_file() and not new_config.exists():
        config_moved = _write_new(new_config, old_config.read_bytes())
    return MigrationReport(tuple(moved), tuple(skipped), config_moved)
