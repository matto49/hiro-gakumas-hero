"""日常结果里给人看的名字：一律中文（界面和游戏内容都用中文，作者 11-26）。

商店里的东西（ノート、ピース、再挑戦チケット…）不在资料库里，按名字的固定写法翻：
- 「ロジックノート(ボーカル)」→「理性笔记（歌唱）」
- 「[Wonder Scale]倉本千奈のピース」→ 偶像卡名按资料库的中文（「【卡名】偶像名」）+「的碎片」
- 几个固定的（再挑戦チケット 等）；对不上的原样返回。
"""

from __future__ import annotations

import re
from functools import cache

PLANS = {"センス": "感性", "ロジック": "理性", "アノマリー": "非凡"}
PARAMS = {"ボーカル": "歌唱", "ダンス": "舞蹈", "ビジュアル": "视觉"}
FIXED = {
    "再挑戦チケット": "再挑战券",
    "記録の鍵": "记录之钥",
    "メモリー再生成チケット": "回忆再生成券",
    "入手サポート強化Ptアップ": "支援强化点数获取提升",
    "入手ノート数アップ": "笔记获取数提升",
}


@cache
def _idols() -> list[tuple[str, str, str]]:
    """[(日文卡名, 日文偶像名, 中文「【卡名】偶像名」)]。资料库没下载时空。"""
    try:
        from hiro.cli.catalog import load

        ja, zh = load("ja").get("idols") or {}, load("zh").get("idols") or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for iid, e in ja.items():
        m = re.fullmatch(r"【(.+?)】(.+)", (e or {}).get("name") or "")
        z = (zh.get(iid) or {}).get("name")
        if m and z:
            out.append((m.group(1), m.group(2).replace(" ", ""), z))
    return out


def _fold(s: str) -> str:
    from hiro.vision.textnorm import fold

    return re.sub(r"[\s\[\]【】()（）|]", "", fold(s).replace("一", "ー"))


def item_name(name: str) -> str:
    """商店商品名 → 中文；认不出原样返回。"""
    if not name:
        return name
    n = _fold(name)
    for ja, zh in FIXED.items():
        if _fold(ja) in n:
            return zh
    if m := re.search(r"(センス|ロジック|アノマリー)ノー?ト.*?(ボーカル|ダンス|ビジュアル)", n):
        return f"{PLANS[m.group(1)]}笔记（{PARAMS[m.group(2)]}）"
    if "ピース" in n or "ピス" in n:
        best = None
        for title, chara, zh in _idols():
            if chara in n and _fold(title) in n and (best is None or len(title) > len(best[0])):
                best = (title, zh)
        if best is not None:
            return best[1] + "的碎片"
        for _, chara, zh in _idols():
            if chara in n:
                return zh.split("】", 1)[-1] + "的碎片"
    return name
