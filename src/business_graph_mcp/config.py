"""bgraph.toml 的讀取與驗證。

零設定即可運作：設定檔不存在時一律採用預設值。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from business_graph_mcp.errors import GraphError


@dataclass(frozen=True)
class GraphConfig:
    """知識庫的可調參數。

    欄位:
        stale_days: 頁面幾天未更新即視為過期。預設 90——業務規則的變動
            速度慢於程式碼，門檻過短會產生大量假警報，而警報一多，
            agent 就會開始無視，那是提醒機制唯一的死法。
        recall_budget: recall 展開內容的 token 預算上限。
        explore_depth: business_explore 展開關係路徑與影響半徑的最大跳數。
            預設 2——一跳看不出關係鏈，三跳以上在小圖上會把整張圖拉進來，
            而遠親對當下問題的相關度已明顯衰減。
        miss_threshold: 同一問題累計未命中幾次後視為高價值知識缺口。
        low_confidence_score: FTS 最高分低於此值即提示命中信心不足。
            以本 monorepo 的真實業務情境（redmine-mcp 多站台設定、
            business-graph-mcp 自身的延遲同步／CJK 斷詞等設計決策）建立 16 頁
            知識圖實測：對明確指向某頁主題的查詢（中英各 6 筆，共 12 筆，
            即「應命中組」）與對該知識圖完全無關的查詢（中英各半，共 12
            筆，即「不該命中組」）分別量測 ExploreResult.top_score。
            應命中組分數落在 [3.8234, 12.4731]；不該命中組原有噪音上限
            達 4.18（由英數詞前綴運算子造成的詞根巧合，如 `stand*` 誤中
            `standard`，屬 Task 3 刻意的召回優先取捨，換掉該筆查詢後
            噪音降到 [0.0, 1.8849]）。兩組分數區間不重疊，取貼近不該
            命中組上限的 2.0：寧可漏掉部分邊緣情況的警告，也不要因門檻
            訂在應命中組下緣附近而讓真正的好命中被誤標為信心不足
            （警報一多，agent 就會開始無視所有警告）。
    """

    stale_days: int = 90
    recall_budget: int = 8000
    miss_threshold: int = 3
    low_confidence_score: float = 2.0
    explore_depth: int = 2


def _require_positive(name: str, value: object, allow_float: bool = False) -> float:
    """驗證設定值為正數並回傳。

    參數:
        name: 欄位名稱，用於錯誤訊息。
        value: 從 toml 讀出的原始值。
        allow_float: 是否接受浮點數。

    例外:
        GraphError: 型別錯誤或數值不為正。
    """
    # bool 是 int 的子型別，但設定值寫成 true/false 幾乎必然是筆誤，故先排除。
    # 直接用 isinstance 分支讓 mypy 自行縮小型別，不必再靠 cast。
    num_value: float
    if isinstance(value, bool):
        expected = "數字" if allow_float else "整數"
        raise GraphError(f"bgraph.toml 的 {name} 必須是{expected}；收到的是 {value!r}")
    elif isinstance(value, int):
        num_value = float(value)
    elif allow_float and isinstance(value, float):
        num_value = value
    else:
        expected = "數字" if allow_float else "整數"
        raise GraphError(f"bgraph.toml 的 {name} 必須是{expected}；收到的是 {value!r}")

    if num_value <= 0:
        raise GraphError(f"bgraph.toml 的 {name} 必須大於 0；收到的是 {value!r}")
    return num_value


def load_config(path: Path) -> GraphConfig:
    """讀取設定檔；不存在時回傳全預設值的設定。

    未知欄位一律報錯而非靜默忽略：欄位名打錯卻採用預設值時，
    症狀是「設定沒生效」，使用者無從察覺。

    參數:
        path: bgraph.toml 的路徑。

    例外:
        GraphError: 語法錯誤、未知欄位或數值不合理。
    """
    if not path.is_file():
        return GraphConfig()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise GraphError(f"讀取 {path} 失敗：{exc}") from exc

    known = {f.name for f in fields(GraphConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        valid = "、".join(sorted(known))
        raise GraphError(
            f"bgraph.toml 有無法辨識的欄位：{'、'.join(unknown)}。合法欄位為 {valid}"
        )

    defaults = GraphConfig()
    return GraphConfig(
        stale_days=int(
            _require_positive("stale_days", data.get("stale_days", defaults.stale_days))
        ),
        recall_budget=int(
            _require_positive("recall_budget", data.get("recall_budget", defaults.recall_budget))
        ),
        miss_threshold=int(
            _require_positive("miss_threshold", data.get("miss_threshold", defaults.miss_threshold))
        ),
        low_confidence_score=_require_positive(
            "low_confidence_score",
            data.get("low_confidence_score", defaults.low_confidence_score),
            allow_float=True,
        ),
        explore_depth=int(
            _require_positive("explore_depth", data.get("explore_depth", defaults.explore_depth))
        ),
    )
