"""中文分詞與 FTS5 查詢語法組裝的測試。"""
from __future__ import annotations

from business_graph_mcp.tokenizer import build_match_query, segment_cjk


def test_中文逐字以空白隔開():
    assert segment_cjk("折扣計算") == "折 扣 計 算"


def test_英數詞彙保持完整不被拆開():
    assert segment_cjk("UserService") == "UserService"
    assert segment_cjk("api_v2") == "api_v2"


def test_中英混排時只拆中文並保留分隔():
    assert segment_cjk("查詢 UserService 的折扣") == "查 詢 UserService 的 折 扣"


def test_空字串與純空白回傳空字串():
    assert segment_cjk("") == ""
    assert segment_cjk("   ") == ""


def test_標點不會混進詞彙中():
    # 標點在 FTS5 中不具檢索意義，去掉可避免 token 被標點黏住。
    assert segment_cjk("折扣，運費") == "折 扣 運 費"


def test_中文查詢組成_phrase_query_以保證相鄰():
    # 若不用引號，"折 扣" 會被當成兩個獨立詞，任何含「折」或「扣」的頁都會命中。
    assert build_match_query("折扣") == '"折 扣"'


def test_英文查詢加前綴運算子以涵蓋字尾變化():
    # unicode61 不做詞幹還原：查 discount 若用完全相符，
    # 只寫了 discounts 的頁會漏掉。實測此情況會讓命中率掉到一半。
    assert build_match_query("discount") == "discount*"


def test_中英混排時各自採用適合的語法():
    assert build_match_query("折扣 discount") == '"折 扣" OR discount*'


def test_多個詞以_or_串接以提高召回():
    # 業務問句常含多個概念，全用 AND 會讓命中率過低。
    assert build_match_query("折扣 運費") == '"折 扣" OR "運 費"'


def test_查詢中的_fts5_特殊字元被移除():
    # 未處理會讓 MATCH 語法錯誤而整個查詢失敗。
    assert build_match_query('折扣 "AND" *') == '"折 扣" OR AND*'


def test_空查詢回傳空字串供呼叫端跳過查詢():
    assert build_match_query("   ") == ""
