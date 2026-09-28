"""中文檢索的分詞處理。

SQLite FTS5 內建的 unicode61 tokenizer 以空白與標點斷詞，一整串漢字會被
當成單一 token——查「折扣」無法比對到「折扣計算規則」。

頁面內容預設為英文，但業務術語、客戶名稱、法規名詞與原文引用仍會是中文，
且別名（aliases）明訂要放中文術語。那些正是最常被查的關鍵字，
不處理 CJK 斷詞就等於查不到。

作法是在寫入索引與組查詢時都把 CJK 字元逐字以空白隔開，讓每個漢字成為
獨立 token；查詢再包成 phrase query，確保命中的是相鄰字元而非散落各處。
效果等同 unigram 索引，純 Python 實作，不需要自訂 tokenizer 或額外相依。
"""
from __future__ import annotations

import re

#: 需要逐字拆開的字元範圍：中日韓統一表意文字、擴充區 A、相容表意文字，
#: 以及日文假名（業務文件偶有日文專有名詞）。
_CJK = re.compile(
    r"[㐀-䶿一-鿿豈-﫿぀-ヿ]"
)
#: 保留的可檢索字元：英數、底線與 CJK。其餘（標點、符號）一律視為分隔。
_TOKEN_CHARS = re.compile(
    r"[0-9A-Za-z_㐀-䶿一-鿿豈-﫿぀-ヿ]+"
)


def segment_cjk(text: str) -> str:
    """把文字轉成 FTS5 可正確斷詞的形式。

    CJK 字元逐字以空白隔開，英數詞彙保持完整，標點與符號一律去除。

    參數:
        text: 原始文字。
    """
    parts: list[str] = []
    for token in _TOKEN_CHARS.findall(text):
        buffer = ""
        for char in token:
            if _CJK.match(char):
                if buffer:
                    parts.append(buffer)
                    buffer = ""
                parts.append(char)
            else:
                buffer += char
        if buffer:
            parts.append(buffer)
    return " ".join(parts)


def build_match_query(query: str) -> str:
    """把使用者查詢轉成 FTS5 的 MATCH 運算式。

    兩種詞採不同語法，這是實測後的結果：

    - 含 CJK 的詞包成 phrase query。拆字後必須相鄰才算命中，
      否則任何含「折」或「扣」的頁都會被撈進來。
    - 純英數的詞加前綴運算子。unicode61 不做詞幹還原，用完全相符時
      查 discount 會漏掉只寫 discounts 的頁——實測命中率因此掉了一半。

    詞與詞之間以 OR 串接：業務問句常含多個概念，全用 AND 會讓命中率過低；
    排序交給 bm25，命中越多概念的頁面自然排前面。

    參數:
        query: 使用者或 agent 提供的查詢字串。

    回傳:
        可直接放進 MATCH 的字串；查詢不含任何可檢索字元時回傳空字串，
        呼叫端應據此跳過查詢而非送出空 MATCH（那會是語法錯誤）。
    """
    parts: list[str] = []
    for token in _TOKEN_CHARS.findall(query):
        if _CJK.search(token):
            parts.append(f'"{segment_cjk(token)}"')
        else:
            parts.append(f"{token}*")
    return " OR ".join(parts)
