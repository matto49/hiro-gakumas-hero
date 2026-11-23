"""比较 OCR 文字时用的容错匹配（只用于「含不含某个词」「是不是这个词」的判断，不改识别结果本身）。

两层：
1. fold：去空格、全角半角统一（NFKC）、拉丁字母大写，字形相同的几个字统一写法（平假名「へ」/ 片假名「ヘ」等，见 _FOLD）。
2. 编辑距离容错（用户 2026-11-21：OCR 本来就不可靠，不要逐个列易混字）：词越长允许读错的字越多（max_errors）。
   一个字读错 = 一次替换，所以「綿成 / 編成」「レツスン / レッスン」「ズ / ス」这类都不用单独列。
   - 4 个字以内的词必须完全一样（決定、休む、次へ、ジュエル：错一个字就容易撞上别的词，「ジュエル」≈「ビジュアル」里的「ジュアル」）；
   - 5–9 个字允许错 1 个，10 个字以上允许错 2 个；其中整个字丢掉的最多占一半（5–9 字不许丢，10 字以上最多丢 1 个）：
     读错一个字很常见，丢字少见，而且丢字会把短词认成长词（「プロデュース」≈「プロデュース編成」）；
   - contains 是「近似子串」：text 里有一段和 word 只差这么多个字就算含（OCR 常把两个词拼成一个框）。
   数字、属性前缀（Vo / Da / Vi）、编成号这类决定意思的内容不要用这里判断，另用正则精确读。
例：「タイトルへ」的平假名「へ」RapidOCR 会读成片假名「ヘ」（2026-11-06）；Apple Vision 偶尔把「エラー」读成「工ラー」；
DMM 541x961 小窗口把「編成」读成「綿成 / 縞成」、「レッスン」读成「レツスン」（2026-11-21）。
"""

from __future__ import annotations

import unicodedata

_FOLD = str.maketrans({"ヘ": "へ", "ベ": "べ", "ペ": "ぺ", "工": "エ"})


def fold(text: str) -> str:
    """去空格，全角半角统一（NFKC），拉丁字母转大写（OCR 大小写乱：「NskIp」），字形相同的字统一写法。"""
    return unicodedata.normalize("NFKC", text).replace(" ", "").replace("　", "").upper().translate(_FOLD)


def max_errors(word: str) -> int:
    """word 允许读错几个字：4 个字以内 0，5–9 个字 1，10 个字以上 2。
    4 个字的词不容错：「ジュエル」错一个字就是「ビジュアル」里的「ジュアル」（11-21 测出来的误认），这种撞车在 4 字词里太容易。"""
    n = len(fold(word))
    return 0 if n <= 4 else 1 if n <= 9 else 2


def substring_distance(text: str, word: str) -> tuple[int, int]:
    """word 和 text 里最像的一段之间的 (编辑距离, 其中 word 有几个字没出现)。近似子串匹配（Sellers 算法），两边都已 fold。
    「没出现」单独数：OCR 读错一个字（替换）常见，整个字丢掉少见，而且丢字容易把短词认成长词（「プロデュース」≈「プロデュース編成」）。"""
    if not word:
        return 0, 0
    prev = [(0, 0)] * (len(text) + 1)  # 第 0 行全 0：word 可以从 text 的任意位置开始
    for i, wc in enumerate(word, 1):
        cur = [(i, i)] + [(0, 0)] * len(text)
        for j, tc in enumerate(text, 1):
            sub = (prev[j - 1][0] + (wc != tc), prev[j - 1][1])
            miss = (prev[j][0] + 1, prev[j][1] + 1)  # word 的这个字在 text 里没有
            extra = (cur[j - 1][0] + 1, cur[j - 1][1])  # text 多了一个字
            cur[j] = min(sub, miss, extra)
        prev = cur
    return min(prev)


def distance(a: str, b: str) -> int:
    """整串编辑距离（Levenshtein）。"""
    prev = list(range(len(b) + 1))
    for i, ac in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, bc in enumerate(b, 1):
            cur[j] = min(prev[j - 1] + (ac != bc), prev[j] + 1, cur[j - 1] + 1)
        prev = cur
    return prev[-1]


def contains(text: str, word: str, max_err: int | None = None) -> bool:
    """text 里有没有 word：两边 fold 后，text 里有一段和 word 只差 max_err 个字以内（缺省按 max_errors(word)）。"""
    t, w = fold(text), fold(word)
    if w in t:
        return True
    k = max_errors(w) if max_err is None else max_err
    if k <= 0 or len(t) < len(w) - k // 2:
        return False
    d, missing = substring_distance(t, w)
    return d <= k and missing <= k // 2  # 丢字最多占一半容错：5–9 个字的词不许丢字，10 个字以上最多丢 1 个


def contains_any(text: str, words) -> bool:
    return any(contains(text, w) for w in words)


def same(text: str, word: str, max_err: int | None = None) -> bool:
    """text 整个就是 word（容错同 contains，但不是子串：「プロデュース編成」≠「プロデュース編成・スキルカード」）。"""
    t, w = fold(text), fold(word)
    k = max_errors(w) if max_err is None else max_err
    return t == w or (k > 0 and len(w) - len(t) <= k // 2 and len(t) - len(w) <= k and distance(t, w) <= k)
