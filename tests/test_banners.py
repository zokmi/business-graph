"""提醒與待辦判定的測試。"""
from __future__ import annotations

from datetime import date

from business_graph_mcp.banners import build_notices
from business_graph_mcp.config import GraphConfig
from business_graph_mcp.db.queries import NodeRow
from business_graph_mcp.explore import ExploreResult, Hit

今天 = date(2026, 8, 14)
設定 = GraphConfig()


def _page(slug="Discount Calculation", status="confirmed", updated="2026-08-14",
          parse_error=None, content_hash="a3f1c8d20e4b7f91") -> NodeRow:
    """組出測試用的 NodeRow。"""
    return NodeRow(
        id=1, slug=slug, title=slug, node_type=None if parse_error else "rule", aliases=(), body="Body.",
        path=f"nodes/{slug}.md", content_hash=content_hash,
        status=status, updated_at=updated, parse_error=parse_error,
    )


def _result(*pages_and_scores) -> ExploreResult:
    """以 (頁面, 分數) 組出 ExploreResult。"""
    hits = tuple(Hit(page=p, score=s) for p, s in pages_and_scores)
    return ExploreResult(
        paths={}, blast=(), anchors={},
        hits=hits, neighbors_full=(), neighbors_brief=(),
        top_score=hits[0].score if hits else 0.0,
    )


def test_零命中產生寫回待辦():
    notices = build_notices(
        _result(), 設定, query="退貨折扣還原", miss_count=1, dangling={}, today=今天
    )

    assert notices.prefix == ()
    assert len(notices.todos) == 1
    # 措辭必須是祈使句：建議句會被忽略。
    assert "business_write" in notices.todos[0]
    assert "建議" not in notices.todos[0]


def test_反覆未命中達門檻時額外標為高價值缺口():
    notices = build_notices(
        _result(), 設定, query="退貨折扣還原", miss_count=3, dangling={}, today=今天
    )

    合併 = " ".join(notices.todos)
    assert "3" in 合併
    assert "缺口" in 合併


def test_未達門檻時不出現高價值缺口字樣():
    notices = build_notices(
        _result(), 設定, query="退貨折扣還原", miss_count=2, dangling={}, today=今天
    )

    assert "缺口" not in " ".join(notices.todos)


def test_命中信心低於門檻時前綴警告():
    notices = build_notices(
        _result((_page(), 1.0)), 設定, query="折扣", miss_count=0, dangling={}, today=今天
    )

    assert any("信心" in text for text in notices.prefix)


def test_信心恰等於門檻時不警告():
    # 門檻採「低於才警告」，邊界值視為足夠。
    notices = build_notices(
        _result((_page(), 2.0)), 設定, query="折扣", miss_count=0, dangling={}, today=今天
    )

    assert not any("信心" in text for text in notices.prefix)


def test_命中頁超過過期門檻時前綴警告並附待辦():
    舊頁 = _page(updated="2026-01-01")

    notices = build_notices(
        _result((舊頁, 5.0)), 設定, query="折扣", miss_count=0, dangling={}, today=今天
    )

    assert any("未更新" in text for text in notices.prefix)
    # 待辦要附 base_hash，agent 才不必為了修正再查一次。
    assert any("a3f1c8d20e4b7f91" in text for text in notices.todos)


def test_過期恰等於門檻天數時不警告():
    # stale_days=90，恰好 90 天視為尚未過期。
    notices = build_notices(
        _result((_page(updated="2026-05-16"), 5.0)),
        設定, query="折扣", miss_count=0, dangling={}, today=今天,
    )

    assert not any("未更新" in text for text in notices.prefix)


def test_推測內容前綴警告並附修正待辦():
    notices = build_notices(
        _result((_page(slug="Shipping Rules", status="inferred"), 5.0)),
        設定, query="運費", miss_count=0, dangling={}, today=今天,
    )

    assert any("推測" in text and "Shipping Rules" in text for text in notices.prefix)
    assert any("a3f1c8d20e4b7f91" in text for text in notices.todos)


def test_懸空連結產生後綴待辦():
    notices = build_notices(
        _result((_page(), 5.0)), 設定, query="折扣", miss_count=0,
        dangling={"Discount Calculation": ("Refund Policy",)}, today=今天,
    )

    合併 = " ".join(notices.todos)
    assert "Refund Policy" in 合併


def test_解析失敗的命中頁前綴警告中繼資料未知():
    notices = build_notices(
        _result((_page(status=None, updated=None, parse_error="缺少 front matter"), 5.0)),
        設定, query="折扣", miss_count=0, dangling={}, today=今天,
    )

    assert any("中繼資料" in text for text in notices.prefix)


def test_多個條件同時成立時前綴不重複且順序穩定():
    頁一 = _page(slug="A", status="inferred", updated="2026-01-01")
    頁二 = _page(slug="B", status="inferred", updated="2026-01-01")

    notices = build_notices(
        _result((頁一, 0.5), (頁二, 0.4)), 設定,
        query="折扣", miss_count=0, dangling={}, today=今天,
    )

    assert len(notices.prefix) == len(set(notices.prefix))
    # 同類警告合併成一則並列出所有頁名，不要每頁各發一則淹掉版面。
    推測 = [t for t in notices.prefix if "推測" in t]
    assert len(推測) == 1
    assert "A" in 推測[0] and "B" in 推測[0]


def test_有命中且一切正常時不產生任何提醒():
    notices = build_notices(
        _result((_page(), 5.0)), 設定, query="折扣", miss_count=0, dangling={}, today=今天
    )

    assert notices.prefix == ()
    assert notices.todos == ()


def test_命中但內容可能不足的情境也涵蓋在待辦措辭中():
    # Spec 第 4.3 節：後綴措辭須同時涵蓋「零命中」與「命中但不足」，
    # 不可只在信心不高時於 prefix 提醒卻不附對應的待辦動作。
    # top_score=1.0 低於門檻（2.0），觸發的是「信心不足」而非 inferred。
    notices = build_notices(
        _result((_page(), 1.0)), 設定,
        query="折扣", miss_count=0, dangling={}, today=今天,
    )

    assert any("折扣" in text and "business_write" in text for text in notices.todos)
