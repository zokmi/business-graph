"""bgraph.toml 設定讀取的測試。"""
from __future__ import annotations

import pytest

from business_graph_mcp.config import GraphConfig, load_config
from business_graph_mcp.errors import GraphError


def test_explore_depth_預設為_2(tmp_path):
    assert load_config(tmp_path / "bgraph.toml").explore_depth == 2


def test_explore_depth_可覆寫(tmp_path):
    path = tmp_path / "bgraph.toml"
    path.write_text("explore_depth = 3\n", encoding="utf-8")
    assert load_config(path).explore_depth == 3


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '\"2\"'])
def test_explore_depth_必須是正整數(tmp_path, value):
    path = tmp_path / "bgraph.toml"
    path.write_text(f"explore_depth = {value}\n", encoding="utf-8")
    with pytest.raises(GraphError, match="explore_depth 必須"):
        load_config(path)


def test_設定檔不存在時全部採用預設值(tmp_path):
    cfg = load_config(tmp_path / "bgraph.toml")

    assert cfg == GraphConfig(
        stale_days=90,
        recall_budget=8000,
        miss_threshold=3,
        low_confidence_score=2.0,
    )


def test_只覆寫部分欄位其餘維持預設(tmp_path):
    path = tmp_path / "bgraph.toml"
    path.write_text("stale_days = 30\n", encoding="utf-8")

    cfg = load_config(path)

    assert cfg.stale_days == 30
    assert cfg.recall_budget == 8000


def test_可覆寫全部欄位(tmp_path):
    path = tmp_path / "bgraph.toml"
    path.write_text(
        "stale_days = 45\nrecall_budget = 12000\n"
        "miss_threshold = 5\nlow_confidence_score = 2.5\n",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg == GraphConfig(
        stale_days=45, recall_budget=12000, miss_threshold=5, low_confidence_score=2.5
    )


def test_未知欄位直接報錯而非靜默忽略(tmp_path):
    # 打錯欄位名而靜默採用預設值，症狀是「設定沒生效」且無從察覺。
    path = tmp_path / "bgraph.toml"
    path.write_text("stale_dayz = 30\n", encoding="utf-8")

    with pytest.raises(GraphError) as exc:
        load_config(path)

    assert "stale_dayz" in str(exc.value)


@pytest.mark.parametrize(
    ("內容", "關鍵字"),
    [
        ("stale_days = 0\n", "stale_days"),
        ("stale_days = -1\n", "stale_days"),
        ("stale_days = true\n", "stale_days"),
        ("recall_budget = 0\n", "recall_budget"),
        ("miss_threshold = 0\n", "miss_threshold"),
        ("low_confidence_score = -1\n", "low_confidence_score"),
    ],
)
def test_數值不合理時報錯並指出欄位(tmp_path, 內容, 關鍵字):
    path = tmp_path / "bgraph.toml"
    path.write_text(內容, encoding="utf-8")

    with pytest.raises(GraphError) as exc:
        load_config(path)

    assert 關鍵字 in str(exc.value)


def test_toml_語法錯誤時報錯並指出檔案路徑(tmp_path):
    path = tmp_path / "bgraph.toml"
    path.write_text("stale_days = = 30\n", encoding="utf-8")

    with pytest.raises(GraphError) as exc:
        load_config(path)

    assert "bgraph.toml" in str(exc.value)
