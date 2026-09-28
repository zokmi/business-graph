"""知識圖頁面的解析與序列化。

本模組為純函式，不碰資料庫與檔案系統，因此可獨立測試邊界情境。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

import yaml

from business_graph_mcp.edges import Edge, parse_edge_target

#: 業務方確認過的內容。
STATUS_CONFIRMED = "confirmed"
#: 由程式碼或推理得出、尚未經業務確認的內容。
STATUS_INFERRED = "inferred"
#: 合法的 status 值；不提供預設值，寫入時必須明確指定。
VALID_STATUSES: tuple[str, str] = (STATUS_CONFIRMED, STATUS_INFERRED)

#: 業務規則：折扣怎麼算、什麼情況不受理、額度怎麼判定。
NODE_RULE = "rule"
#: 實體或欄位：某張表、某個欄位的業務意義與合法值。
NODE_ENTITY = "entity"
#: 決策與理由：為什麼這樣設計、當時的替代方案與取捨。
NODE_DECISION = "decision"
#: 術語：業務方講的某個詞在系統裡對應什麼。
NODE_TERM = "term"
#: 合法的節點型別；與 status 同樣不提供預設值，寫入時必須明確指定——
#: 靜默吃下一個可能錯的預設值，會讓整張圖的型別語意失去可信度。
VALID_NODE_TYPES: tuple[str, str, str, str] = (
    NODE_RULE,
    NODE_ENTITY,
    NODE_DECISION,
    NODE_TERM,
)

_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_LINK = re.compile(r"\[\[([^\[\]]+?)\]\]")
#: 檔名中不可出現的字元；一律換成連字號，避免路徑穿越與跨平台檔名問題。
_UNSAFE_IN_FILENAME = re.compile(r"[/\\:*?\"<>|]+")


@dataclass(frozen=True)
class NodeMeta:
    """節點的 front matter 中繼資料。

    欄位:
        title: 節點標題，同時是 [[連結]] 的目標。
        node_type: 節點型別，見 VALID_NODE_TYPES。
        aliases: 別名；業務術語常有多種叫法，缺少別名會大幅降低命中率。
        code: 對應的程式碼符號名；產生 implemented_by 邊，由 codegraph
            索引解析成檔案與行號。選填。
        status: confirmed 或 inferred，見 VALID_STATUSES。
        updated: 最後更新日期，staleness 判定的依據，由 server 維護。
    """

    title: str
    node_type: str
    aliases: tuple[str, ...]
    code: tuple[str, ...] = field(default=(), kw_only=True)
    status: str
    updated: date


@dataclass(frozen=True)
class ParsedNode:
    """一頁 markdown 的解析結果。

    欄位:
        meta: front matter 解析成功時的中繼資料，失敗時為 None。
        body: front matter 以外的內文；解析失敗時仍保留，以便進入索引。
        parse_error: 解析失敗的原因，成功時為 None。
        edges: 內文中的型別化邊，已去重並保留首次出現順序。
    """

    meta: NodeMeta | None
    body: str
    parse_error: str | None
    edges: tuple[Edge, ...]


def extract_edges(body: str) -> tuple[Edge, ...]:
    """抽出內文中的 [[關係:目標]] 連結並解析成邊。

    去重但保留首次出現順序：待辦與懸空連結報告的可讀性依賴穩定順序。
    去重的鍵是「關係 + 目標」而非只有目標——同兩個節點之間可以同時存在
    兩種關係（例如既覆寫又依賴），那是兩條不同的邊。

    參數:
        body: 頁面內文。
    """
    seen: dict[tuple[str, str, str | None], Edge] = {}
    for match in _LINK.finditer(body):
        edge = parse_edge_target(match.group(1).strip())
        seen.setdefault((edge.edge_type, edge.target, edge.error), edge)
    return tuple(seen.values())


def slugify(title: str) -> str:
    """把標題轉成可當檔名的 slug。

    保留中文（業務術語幾乎全是中文，轉拼音只會讓檔案無法辨識），
    僅置換檔名不允許的字元並去除前後空白。

    參數:
        title: 頁面標題。
    """
    return _UNSAFE_IN_FILENAME.sub("-", title).strip()


def content_hash(text: str) -> str:
    """計算內容雜湊，用於增量同步與寫入的樂觀鎖。

    取 sha256 前 16 個十六進位字元：碰撞機率對本用途足夠低，
    且長度短到可以直接寫進待辦文字而不佔版面。

    參數:
        text: 要計算雜湊的完整文字。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parse_meta(raw: str) -> NodeMeta:
    """把 front matter 的 YAML 文字轉成 NodeMeta。

    參數:
        raw: 兩條 --- 之間的 YAML 文字。

    例外:
        ValueError: 欄位缺漏、型別錯誤或 status 不合法。訊息一律指出
            是哪個欄位有問題，讓呼叫端能直接寫進 lint 報告。
    """
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("front matter 不是鍵值對")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("front matter 缺少 title")

    node_type = data.get("type")
    if node_type not in VALID_NODE_TYPES:
        valid = "、".join(VALID_NODE_TYPES)
        raise ValueError(
            f"front matter 的 type 只接受 {valid}；收到的是 {node_type!r}"
        )

    status = data.get("status")
    if status not in VALID_STATUSES:
        valid = "、".join(VALID_STATUSES)
        raise ValueError(f"front matter 的 status 只接受 {valid}；收到的是 {status!r}")

    updated = data.get("updated")
    if isinstance(updated, date):
        updated_date = updated
    elif isinstance(updated, str):
        updated_date = date.fromisoformat(updated)
    else:
        raise ValueError("front matter 缺少 updated 或格式不是 YYYY-MM-DD")

    raw_aliases = data.get("aliases") or []
    if not isinstance(raw_aliases, list):
        raise ValueError("front matter 的 aliases 必須是陣列")
    aliases = tuple(str(a).strip() for a in raw_aliases if str(a).strip())

    raw_code = data.get("code", [])
    if not isinstance(raw_code, list):
        raise ValueError("front matter 的 code 必須是陣列")
    code = tuple(str(c).strip() for c in raw_code if str(c).strip())

    return NodeMeta(
        title=title.strip(),
        node_type=node_type,
        aliases=aliases,
        code=code,
        status=status,
        updated=updated_date,
    )


def parse_legacy_meta(text: str) -> NodeMeta | None:
    """以舊格式解析 front matter，補上 rule 型別與空 code。

    僅供遷移使用；正式節點仍須明確提供 type。解析失敗時回傳 None，
    讓遷移略過壞掉的頁面並繼續處理其餘頁面。
    """
    match = _FRONT_MATTER.match(text)
    if match is None:
        return None
    try:
        data = yaml.safe_load(match.group(1))
        if not isinstance(data, dict):
            return None
        title = data.get("title")
        status = data.get("status")
        updated = data.get("updated")
        if not isinstance(title, str) or not title.strip():
            return None
        if status not in VALID_STATUSES:
            return None
        if isinstance(updated, date):
            updated_date = updated
        elif isinstance(updated, str):
            updated_date = date.fromisoformat(updated)
        else:
            return None
        raw_aliases = data.get("aliases", [])
        if not isinstance(raw_aliases, list):
            return None
        aliases = tuple(str(a).strip() for a in raw_aliases if str(a).strip())
    except (ValueError, yaml.YAMLError):
        return None
    return NodeMeta(
        title=title.strip(),
        node_type=NODE_RULE,
        aliases=aliases,
        code=(),
        status=status,
        updated=updated_date,
    )


def parse_node(text: str) -> ParsedNode:
    """解析一頁 markdown。

    解析失敗時不拋例外：Spec §9 原則 2 要求一頁壞掉不得拖垮 server，
    body 仍須進入索引，錯誤原因交由 business_lint 列出。

    參數:
        text: 整份 markdown 檔案內容。
    """
    match = _FRONT_MATTER.match(text)
    if match is None:
        body = text.strip()
        return ParsedNode(
            meta=None,
            body=body,
            parse_error="缺少 front matter（檔案開頭必須是 --- 包住的 YAML 區塊）",
            edges=extract_edges(body),
        )

    body = text[match.end() :].strip()
    try:
        meta = _parse_meta(match.group(1))
    except (ValueError, yaml.YAMLError) as exc:
        return ParsedNode(meta=None, body=body, parse_error=str(exc), edges=extract_edges(body))

    return ParsedNode(meta=meta, body=body, parse_error=None, edges=extract_edges(body))


def render_node(meta: NodeMeta, body: str) -> str:
    """把中繼資料與內文組回完整的 markdown 檔案內容。

    手寫 front matter 而不用 yaml.dump：需要固定欄位順序、中文不轉義，
    且 aliases 要輸出成單行陣列以維持人類可讀。

    參數:
        meta: 頁面中繼資料。
        body: 頁面內文。
    """
    # title 可包含 `: `、`#` 等 YAML 有語意的字元；交給 serializer 決定
    # 是否加引號，避免 business_write 產出自己解析不回來的 front matter。
    title_line = yaml.safe_dump(
        {"title": meta.title}, allow_unicode=True, sort_keys=False
    ).rstrip()
    lines = ["---", title_line, f"type: {meta.node_type}"]
    if meta.aliases:
        lines.append(_yaml_flow_list("aliases", meta.aliases))
    if meta.code:
        lines.append(_yaml_flow_list("code", meta.code))
    lines.append(f"status: {meta.status}")
    lines.append(f"updated: {meta.updated.isoformat()}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    return "\n".join(lines)


def _yaml_flow_list(key: str, values: tuple[str, ...]) -> str:
    """Serialize every list item as a YAML scalar, including punctuation and booleans."""
    rendered = yaml.safe_dump(
        list(values), allow_unicode=True, default_flow_style=True, width=1_000_000
    )
    return f"{key}: {rendered.rstrip()}"
