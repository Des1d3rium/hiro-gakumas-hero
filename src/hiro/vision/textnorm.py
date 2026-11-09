"""比较 OCR 文字时用的字形归一：把字形相同、OCR 会互相读错的字归到同一个写法。

只用于「含不含某个词」的判断（危险词拦截、按钮定位），不改识别结果本身。
例：「タイトルへ」的平假名「へ」和片假名「ヘ」字形相同，RapidOCR 会读成后者（2026-11-06 Windows 实测）；
Apple Vision 偶尔把「エラー」读成汉字「工ラー」。危险词拦截靠子串匹配，不归一就会漏拦。
"""

_FOLD = str.maketrans({"ヘ": "へ", "ベ": "べ", "ペ": "ぺ", "工": "エ"})


def fold(text: str) -> str:
    """去空格，字形相同的字统一写法。"""
    return text.replace(" ", "").replace("　", "").translate(_FOLD)


def contains(text: str, word: str) -> bool:
    """text 里有没有 word（两边都先 fold）。"""
    return fold(word) in fold(text)


def contains_any(text: str, words) -> bool:
    return any(contains(text, w) for w in words)
