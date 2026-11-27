"""刷回忆（メモリー転写）：「初」BAD 育成，目标评价 B（用户 2026-11-21；社区方法见 docs/memory-transcription.md 第二节 ①）。

开关 HIRO_FARM=bad（缺省关）。原理：回忆编成里带一张想转写的技能卡（转写目标），这一局除了它和初始卡 / 偶像固有卡之外
什么卡都不拿，以 B 评价通关；中間試験后的抽选池里只剩目标卡，B 评价能抽到 虹- / 虹+。

这里只放决策规则，接入点：
- 选行动：deciders._action_select_hajime_nia 开头 → pick_action（按周固定日程）；
- 练习课：runtime.hajime 的练习课把出牌顾问包一层 BadLessonAdvisor（离通关线只差 margin 时不再出牌，课程以「刚好没过」结束，
  不掉技能卡）；
- 相談：deciders._hajime_nia_consult → delete_pool（删掉这局拿到的所有卡，除了初始卡 / 固有卡 / 目标卡）。
考试（中間 / 最終）照常打：B 的难点是分数够不够，不是会不会超（社区：参数合计 ≥1500、最终得分 ≥3000 才稳）。

- 收尾：runtime.finish 在回忆结果页调 regen_step：读出生成的回忆带的技能卡，不是目标、还有次数就「再生成」一次
  （确认框里有ジュエル或认不出确认按钮一律キャンセル）。

还没做：租借回忆（程序不会选，玩家自己开局后 resume 接手）、按卡片预演精确卡在通关线下。
"""

from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger(__name__)

# 13 周日程（社区 ①；第 6 / 13 周是考试，选行动页不出现）。值是这一周要做的事：
# lesson = 审查基准第 1 属性的 SP 课（没有 SP 就普通课）；oikomi = 追い込み（社区：没有 SP，可以拿第 2 属性）；
# rest = 休息；shop_or_outing = 有要删的卡、能进相談就相談，否则外出（第 7 / 8 周不能连着休息两次，第 7 周外出确定不掉卡）
SCHEDULE = {1: "lesson", 2: "rest", 3: "lesson", 4: "rest", 5: "oikomi", 7: "shop_or_outing", 8: "rest",
            9: "lesson", 10: "lesson", 11: "rest", 12: "oikomi"}  # fmt: skip
DEFAULT_MARGIN = 0.4  # 练习课离通关线还差「通关线 × 这个比例」以内就不再出牌（先保守，实机看读数再调）


def mode(env: dict | None = None) -> str | None:
    v = ((env if env is not None else os.environ).get("HIRO_FARM") or "").strip().lower()
    if v in ("", "0", "off"):
        return None
    if v != "bad":
        logger.warning("HIRO_FARM=%r 不认识（现在只有 bad），不刷回忆", v)
        return None
    return v


def margin_ratio(env: dict | None = None) -> float:
    try:
        return min(
            0.9,
            max(0.05, float((env if env is not None else os.environ).get("HIRO_FARM_BAD_MARGIN") or DEFAULT_MARGIN)),
        )
    except ValueError:
        return DEFAULT_MARGIN


def _ranked_attrs(criteria: dict | None) -> list[str]:
    """审查基准三属性按权重从高到低（Vo / Da / Vi）。"""
    if not criteria:
        return []
    norm = {str(k).capitalize(): v for k, v in criteria.items() if v is not None}
    return [a for a in sorted(norm, key=lambda a: -norm[a]) if a in ("Vo", "Da", "Vi")]


def _attr(o: dict) -> str | None:
    a = o.get("attr")
    return str(a).capitalize() if a else None


def pick_action(step: int | None, opts: list[dict], criteria: dict | None, *, has_junk: bool = False,
                shop_ok: bool = True) -> tuple[int, str] | None:  # fmt: skip
    """这一周按日程选哪个选项 → (opts 下标, 理由)；周数不知道 / 日程里没有这一周 / 要的选项不在画面上时 None（交回原来的规则）。"""
    want = SCHEDULE.get(int(step or 0))
    if want is None:
        return None
    ranked = _ranked_attrs(criteria)
    idx = dict(enumerate(opts))

    def first(pred) -> int | None:
        return next((i for i, o in idx.items() if pred(o)), None)

    if want == "lesson":
        lessons = {i: o for i, o in idx.items() if o.get("option") == "training" and not o.get("oikomi")}
        for attr in ranked[:1]:
            for sp in (True, None, False):
                i = next(
                    (i for i, o in lessons.items() if _attr(o) == attr and (sp is None or bool(o.get("sp")) == sp)),
                    None,
                )
                if i is not None:
                    return (
                        i,
                        f"刷回忆 BAD 第 {step} 周：审查基准第 1 的 {attr} {'SP ' if lessons[i].get('sp') else ''}课（故意不通关）",
                    )
        i = next((i for i, o in lessons.items() if o.get("sp")), None)
        if i is None:
            i = next(iter(lessons), None)
        return (
            (i, f"刷回忆 BAD 第 {step} 周：上课（故意不通关；审查基准没读到，按 SP 优先）") if i is not None else None
        )
    if want == "oikomi":
        oik = {i: o for i, o in idx.items() if o.get("option") == "training" and o.get("oikomi")} or {
            i: o for i, o in idx.items() if o.get("option") == "training"
        }
        for attr in ranked[1:2] + ranked[:1]:
            i = next((i for i, o in oik.items() if _attr(o) == attr), None)
            if i is not None:
                return i, f"刷回忆 BAD 第 {step} 周：追い込み {attr}（社区：可以拿第 2 属性，故意不通关）"
        i = next(iter(oik), None)
        return (i, f"刷回忆 BAD 第 {step} 周：追い込み（故意不通关）") if i is not None else None
    if want == "rest":
        i = first(lambda o: o.get("option") == "rest")
        return (i, f"刷回忆 BAD 第 {step} 周：休息（不拿卡）") if i is not None else None
    if want == "shop_or_outing":
        if has_junk and shop_ok and (i := first(lambda o: o.get("option") == "shop")) is not None:
            return i, f"刷回忆 BAD 第 {step} 周：相談，删掉这局拿到的卡（不让它们进抽选池）"
        if (i := first(lambda o: o.get("option") == "outing")) is not None:
            return i, f"刷回忆 BAD 第 {step} 周：外出（第 7 / 8 周不能连着休息，外出确定不掉卡）"
        i = first(lambda o: o.get("option") == "rest")
        return (i, f"刷回忆 BAD 第 {step} 周：休息（没有外出可选）") if i is not None else None
    return None


def junk_cards(cards: list[dict], targets: set[str], picked) -> list[dict]:
    """这一局拿到的、该删掉的卡（不让它们进回忆抽选池）。正向名单：只有本局确认拿到的卡（picked：card_id → 张数，
    选卡领取成功时记、续跑从 trace 里选卡成功的步骤恢复）才删，没有记账的卡一律不删（CodeRabbit #198：原来是「保护名单以外都删」，
    借用的回忆带进来的卡不在任何名单里，也会被删）。即使在名单里，初始的「○○の基本」、偶像固有卡（-ido-）、转写目标、
    回忆编成带进来的卡也不删；同一张卡名单里记了几张最多删几张。定制过的、没认出的格不碰。"""
    from collections import Counter

    from hiro.brain.deciders import _is_basic, _is_trouble_card

    keep = set(targets) | _memory_cards()  # 回忆编成带进来的卡也留着（和目标卡一样，不是这局拿的；CodeRabbit #198）
    left = Counter(picked)
    # 牌组里这张卡的份数比本局领到的多：有的副本是别处带进来的（借用回忆的卡、强化过的），按卡号分不清删哪份，一份都不删（CodeRabbit #198）
    in_deck = Counter(c.get("card_id") for c in cards if c.get("card_id"))
    out = []
    for c in cards:
        cid = c.get("card_id")
        if not cid or c.get("customized") or "-ido-" in cid or cid in keep or _is_basic(cid) or left[cid] <= 0:
            continue
        if in_deck[cid] > Counter(picked)[cid]:
            continue
        left[cid] -= 1
        out.append(dict(c, farm_trouble=_is_trouble_card(cid)))
    out.sort(key=lambda c: (not c["farm_trouble"], c.get("index", 0)))  # 眠気等トラブル先删（不删会塞牌组）
    return out


def forget_picked(picked, cid: str | None) -> None:
    """决定删掉一张以后从「本局拿到的」名单里减一张（决策时就减：删没成功的话少删一张，是不删错的方向）。"""
    if cid and picked.get(cid, 0) > 0:
        picked[cid] -= 1


def _memory_cards() -> set[str]:
    """回忆编成带进培育的技能卡（HIRO_MEMORY_CARDS / 开局编成方案，同 hif.py 开局记进 picked_musts 的那份）。"""
    try:
        from hiro.formation_env import MEMORY_CARDS

        return {c for c in MEMORY_CARDS if c}
    except Exception:  # noqa: BLE001
        return set()


def target_cards(env: dict | None = None) -> set[str]:
    """转写目标：HIRO_FARM_TARGET（逗号分隔，技能卡 id 或卡名，中日文都行）；没设时用回忆编成带进来的卡（HIRO_MEMORY_CARDS / 开局编成方案）。
    11-27 实机：界面里填的是卡名「輝くキミへ」，原来只认 id；没设时的来源还指向已删的 kaa_env，结果目标是空的。"""
    src = env if env is not None else os.environ
    raw = (
        src.get("HIRO_FARM_TARGET") or ""
    ).strip()  # 和 check_targets 同一口径：只有空格也回退到编成（CodeRabbit #198）
    if not raw:
        return _memory_cards()
    return set(_resolve_targets(raw)[0])


_TARGET_CACHE: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {}


def _resolve_targets(raw: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """HIRO_FARM_TARGET 原文 → (解析出的技能卡 id, 解析不出的那几项)。按原文缓存：选行动、删卡每次决策都要用，
    不再每次重查资料库、重复打警告（审查 #198 第 2 条）。"""
    if (got := _TARGET_CACHE.get(raw)) is not None:
        return got
    ids: set[str] = set()
    bad: list[str] = []
    for tok in raw.replace("，", ",").split(","):
        if not (tok := tok.strip()):
            continue
        if cid := card_id(tok):
            ids.add(cid)
        else:
            bad.append(tok)
    got = _TARGET_CACHE[raw] = (frozenset(ids), tuple(bad))
    return got


def check_targets(env: dict | None = None) -> set[str]:
    """开局前核对转写目标（审查 #198 第 2 条）：填了的每一项都要在资料库里找得到，一个都没有也不行；否则 ResumeBlocked 停下。
    原来解析不出的名字直接丢掉，目标卡没被保护，相談删卡会把它当成这局拿到的卡删掉。"""
    from hiro.loop.errors import ResumeBlocked

    src = env if env is not None else os.environ
    raw = (src.get("HIRO_FARM_TARGET") or "").strip()
    if raw:
        ids, bad = _resolve_targets(raw)
        # 填的是技能卡编号：也要在资料库里精确查到（写错的编号原来原样当目标，等于没保护真正的目标；CodeRabbit #198）
        bad = (*bad, *sorted(c for c in ids if c.startswith("p_card-") and not _known_card(c)))
        if not ids and not bad:  # 只填了「，,」之类：显式填了但一项都没有，同样不开始（CodeRabbit #198）
            raise ResumeBlocked(
                f"刷回忆：转写目标「{raw}」里一张卡都没有，没开始（相談删卡会把目标卡一起删掉）。请填技能卡名或编号，逗号分隔"
            )
        if bad:
            raise ResumeBlocked(
                f"刷回忆：转写目标「{'、'.join(bad)}」在资料库里找不到，没开始（不停下的话相談删卡会把它当成这局拿到的卡删掉）。"
                "请检查卡名和游戏里写的是否完全一致（也可以填技能卡编号 p_card-…），以及资料库有没有下载好"
            )
        return set(ids)
    ids = target_cards(env)
    if not ids:
        raise ResumeBlocked(
            "刷回忆：没有转写目标（没填转写目标，回忆编成里也没读到卡），没开始（相談删卡会把目标卡一起删掉）"
        )
    return ids


def _known_card(cid: str) -> bool:
    """技能卡编号在资料库里有没有（精确匹配）。资料库读不到也当没有（开局前停下，提示检查资料库）。"""
    try:
        from hiro.cli.catalog import load

        return cid in (load("ja").get("cards") or {})
    except Exception as e:  # noqa: BLE001  资料库没下载等
        logger.warning("刷回忆：查技能卡编号失败（%s）：%s", cid, e)
        return False


def card_id(name_or_id: str) -> str | None:
    """技能卡 id 原样返回；卡名在资料库里按名字找（完全一致优先，中日文都查；带「+」的强化版去掉 +）。找不到 None（记一条警告）。"""
    s = name_or_id.strip()
    if not s or s.startswith("p_card-"):
        return s or None
    base = s.rstrip("+＋")
    from hiro.cli.catalog import search

    # 先看两种语言的完全一致，都没有才接受唯一的模糊结果（原来日文里模糊唯一就返回，中文的完全一致轮不到；CodeRabbit #198）
    fuzzy: set[str] = set()
    for lang in ("ja", "zh"):
        try:
            hs = [h for h in search(base, kind="cards", lang=lang) if h.get("id")]
        except Exception as e:  # noqa: BLE001  资料库没下载等：这种语言跳过
            logger.warning("刷回忆：按卡名找技能卡失败（%s，%s）：%s", s, lang, e)
            continue
        if exact := [h for h in hs if (h.get("name") or "").rstrip("+＋") == base]:
            return exact[0]["id"]
        fuzzy |= {h["id"] for h in hs}
    if len(fuzzy) == 1:
        return next(iter(fuzzy))
    logger.warning("刷回忆：转写目标「%s」在资料库里没找到（或对上不止一张；填技能卡 id 也行）", s)
    return None


PASSIVE_SAFETY = 1.2  # 好印象自动加分的估计再放大一点（P 道具等会让好印象加成更高）


def impression_gain(impression: int | None, turns: int | None) -> int:
    """好印象 G 层、还剩 T 回合：每回合结束按当前层数加一次参数再减 1 层 → G + (G−1) + … 共 T 项（减到 0 为止）。读不到为 0。"""
    if not impression or not turns or impression <= 0 or turns <= 0:
        return 0
    n = min(int(turns), int(impression))
    return int(n * impression - n * (n - 1) // 2)


class BadLessonAdvisor:
    """练习课的出牌顾问外面包一层：离通关线只差 margin 以内（画面「CLEARまで N」的 N）就跳过回合，不再出牌，
    让这节课以「刚好没过」结束（不掉技能卡）。读不到 N 时照常交给原来的顾问。live 由练习课的结束判断每帧更新。
    read_left：决策时现读一次 N（给了就优先用它）。live 是循环开头那张截图读的，出牌后多半还在分数动画里、N 偏大，
    读不到时还留着上一次的旧值（审查 #198 第 4 条 / CodeRabbit hajime.py:276）；现读读不到再退回 live。"""

    def __init__(self, inner, live: dict, clear_target, ratio: float, read_left=None):
        self.inner, self.live, self.clear_target, self.ratio = inner, live, clear_target, ratio
        self.read_left = read_left
        # 这节课停过手没有：停过以后只要能跳过就一直跳过（好印象自动加分会让余量变小，又恢复出牌就过线了；CodeRabbit #198）。
        # 每节课 run_exam 包一个新实例，新一节课自然重置
        self.stopped = False

    def _left(self):
        if self.read_left is not None:
            try:
                fresh = self.read_left()
            except Exception as e:  # noqa: BLE001  截图 / OCR 出错：退回 live
                logger.warning("刷回忆 BAD：决策时读「CLEARまで」失败，用上一帧的读数：%s", e)
                fresh = None
            if fresh is not None:
                self.live["left"] = fresh
                return fresh
        return self.live.get("left")

    def margin(self) -> float | None:
        t = self.clear_target() if callable(self.clear_target) else self.clear_target
        return None if not isinstance(t, (int, float)) or not t else max(1.0, float(t) * self.ratio)

    def decide(self, state, memory, previews, legal):
        from hiro.contracts import Action, ActionKind, Decision

        m = self.margin()
        can_skip = any(getattr(a, "kind", None) is ActionKind.SKIP for a in legal)
        if self.stopped and can_skip:
            return Decision(Action.skip(), "刷回忆 BAD：这节课已经停手，继续跳过（不再出牌，以刚好没过结束）", "rule", 1.0,
                            decider="battle")  # fmt: skip
        left = self._left() if m is not None and can_skip else None  # 只在停得了手时才多截一次图
        if left is not None and m is not None and can_skip:
            # 11-27 实机（藤田ことね 好印象流）：停手后好印象每回合结束还自动加参数，差 5 停手照样过线、拿了卡。
            # 余量 = 通关线 × 比例 + 之后几回合好印象自动加的分 × PASSIVE_SAFETY
            passive = impression_gain(getattr(state, "impression", None), getattr(state, "remaining_turns", None))
            limit = m + passive * PASSIVE_SAFETY
            if left <= limit:
                extra = f"，之后好印象还会自动加约 {passive}" if passive else ""
                if passive >= left:
                    logger.warning("刷回忆 BAD：离通关差 %s，好印象之后还会自动加约 %s，停手也会过线", left, passive)
                self.stopped = True
                return Decision(Action.skip(), f"刷回忆 BAD：离通关只差 {left}（≤ {limit:.0f}{extra}），不再出牌，这节课以刚好没过结束",
                                "rule", 1.0, decider="battle")  # fmt: skip
        return self.inner.decide(state, memory, previews, legal)

    def __getattr__(self, name):
        return getattr(self.inner, name)


# ---- 回忆结果页：生成的回忆带的技能卡不是转写目标 → 用「再生成」再抽一次（11-27 用户：不对就刷新一次）----

REGEN_MAX = 1  # 一局最多再生成几次（用户：刷新一次）
_regen = {"done": 0, "pending": False, "reported": set()}
WAIT = "wait"  # regen_step：在等再生成的确认框，这一帧什么都别点（也别交给收尾的通用分支）
REGEN_WAIT_MAX_S = 20.0  # 点了再生成以后这么久还认不清画面：停下交给人
DIALOG_WORDS = ("キャンセル", "決定", "OK", "はい", "ジュエル", "ジユエル")  # 画面上有这些 = 还有框
DIALOG_GRACE_S = 4.0  # 点了再生成以后这么久没见到框也先等着（框可能弹得晚；早早放手，晚弹的框会被收尾的通用分支点掉）
_clock = time.monotonic
CLEAR_FRAMES = 2  # 已决定取消的框：连着这么多帧有字、没有框，才算框关了
REGEN_CONFIRM_WORDS = ("再生成する", "再生成", "決定", "OK", "はい")


def _txt(b) -> str:
    return (b.text or "").replace(" ", "").strip()


def memory_result(boxes) -> dict | None:
    """回忆结果页（「獲得可能スキルカード」+「再生成」+「次へ」）→ {name, left, regen_xy}；不是这页 None。
    卡名在「獲得可能スキルカード」下面一行（11-27 DMM：标题 y≈685，卡名「リズミカル」y≈730，右边）。"""
    head = next((b for b in boxes if "獲得可能スキルカード" in _txt(b)), None)
    regen = next((b for b in boxes if _txt(b) == "再生成"), None)
    if head is None or regen is None or not any(_txt(b) == "次へ" for b in boxes):
        return None
    hy = head.rect.center.y
    name = next(
        (_txt(b) for b in sorted(boxes, key=lambda b: b.rect.center.y)
         if 20 < b.rect.center.y - hy < 110 and b.rect.center.x > 180 and "獲得" not in _txt(b) and "試験" not in _txt(b)),
        None,
    )  # fmt: skip
    left = None
    for b in boxes:
        if (m := re.search(r"あと(\d+)回", _txt(b))) and abs(b.rect.center.y - regen.rect.center.y) < 80:
            left = int(m.group(1))
    return {"name": name, "left": left, "regen_xy": (int(regen.rect.center.x), int(regen.rect.center.y))}


def _target_names() -> set[str]:
    """转写目标的卡名（日文、中文都收），和结果页读到的卡名比。按编号在资料库里精确取名字；每种语言单独查，
    一种语言读不到 / 出错不影响别的语言、别的卡（CodeRabbit #198）。有哪张目标卡一个名字都没取到时返回空集合：
    regen_step 见到空集合就不再生成（名字不全时没法判「确定不是目标」，可能把已经刷到的那张换掉）。"""
    from hiro.cli.catalog import load

    names: set[str] = set()
    targets = target_cards()
    got: dict[str, set[str]] = {cid: set() for cid in targets}
    for lang in ("ja", "zh"):
        try:
            cards = load(lang).get("cards") or {}
        except Exception as e:  # noqa: BLE001  这种语言的资料库读不到：换下一种
            logger.warning("刷回忆：查转写目标的卡名失败（%s）：%s", lang, e)
            continue
        for cid in targets:
            name = (cards.get(cid) or {}).get("name")
            if name:
                got[cid].add(str(name).replace(" ", "").rstrip("+＋"))
    if missing := sorted(cid for cid, n in got.items() if not n):
        logger.warning("刷回忆：转写目标 %s 一个名字都没取到，不判「不是目标」（不再生成）", "、".join(missing))
        return set()
    for n in got.values():
        names |= n
    return names


NAME_MIN_LEN = 3  # 结果页读到的卡名比这短：读不准，不拿它判「不是目标」
NAME_SIMILAR = 0.6  # 和某个目标的相似度到这个数：可能是 OCR 读错一两个字的目标，不再生成


def _norm_name(s: str) -> str:
    """比卡名前归一：全半角、空格、末尾「+」，片假名转平假名、小写假名转大写（OCR 常把「へ / ヘ」「ュ / ユ」读混）。"""
    import unicodedata

    s = unicodedata.normalize("NFKC", s or "").replace(" ", "").rstrip("+＋")
    s = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)
    return s.translate(str.maketrans("ぁぃぅぇぉっゃゅょゎ", "あいうえおつやゆよわ"))


def target_match(name: str | None, targets: set[str]) -> str:
    """结果页卡名和转写目标比：hit = 是目标；miss = 读得清楚、确定不是；unsure = 名字太短或和目标很像（可能读错了字）。
    只有 miss 才再生成：读得不确定时宁可留下非目标，也不把已经刷到的目标再生成掉（审查 #198 第 5 条）。"""
    from difflib import SequenceMatcher

    n = _norm_name(name or "")
    tn = [t for t in (_norm_name(x) for x in targets) if t]
    if any(t in n or (len(n) >= NAME_MIN_LEN and n in t) for t in tn):
        return "hit"
    if len(n) < NAME_MIN_LEN or not tn:
        return "unsure"
    return "unsure" if max(SequenceMatcher(None, n, t).ratio() for t in tn) >= NAME_SIMILAR else "miss"


def regen_step(boxes) -> tuple[int, int] | str | None:
    """刷回忆收尾：回忆结果页 / 再生成确认框要点哪里；不归这里管返回 None（照常点「次へ」）；WAIT = 这一帧什么都别点。
    只在：刷回忆开着、读到了卡名、不是目标、还有再生成次数、这局还没再生成过 REGEN_MAX 次。
    点了「再生成」以后（pending）画面一律这里管，不交回收尾的通用分支（那边会点「決定」「OK」，不看费用）：
    - 有キャンセル的框：只有读到「チケット / 無料」和「使用 / 消費」同一行、没有ジュエル、和キャンセル同一行认得出确认按钮时才确认（白名单），否则点キャンセル；
    - 干净的结果页（没有框）：确认框关了 / 没弹，pending 结束；
    - 别的（框认不全、过场）：WAIT，REGEN_WAIT_MAX_S 秒还这样就停下交给人（CodeRabbit #198）。
    pending 结束以后（框弹得比 DIALOG_GRACE_S 还晚、进程在框开着时重启）：只要画面是带キャンセル的再生成 / 含ジュエル的框，
    也由这里按白名单确认或取消，绝不交回通用分支（审查 #198 第 1 条）。"""
    if mode() != "bad":
        return None
    texts = [_txt(b) for b in boxes]
    cancel = next((b for b in boxes if "キャンセル" in _txt(b)), None)
    if _regen["pending"]:
        if cancel is not None:
            if _regen.get("confirmed"):
                return _wait_or_stop(texts)  # 已经点过确认：框还在淡出，不再点
            # 点了キャンセル框还一直在（没反应）：同样受 REGEN_WAIT_MAX_S 上限，超时停下交给人（CodeRabbit #198）
            _stop_if_late(texts, "确认框一直关不掉（点キャンセル没反应）")
            _regen["clear_frames"] = 0
            if _regen.get("cancelled"):
                # 这个框已经决定取消（有ジュエル / 不在白名单）：框关掉之前一直点キャンセル，不再按白名单重判
                # （漏读ジュエル的那一帧会被当成免费点了确认；CodeRabbit #198）
                return int(cancel.rect.center.x), int(cancel.rect.center.y)
            return _answer_dialog(boxes, texts, cancel)
        if any(w in t for t in texts for w in DIALOG_WORDS):
            _regen["clear_frames"] = 0
            return _wait_or_stop(texts)  # 还有框（认不全）：等
        if _regen.get("cancelled"):
            # 「已决定取消」只在框确实关了才清：空帧（OCR 没读到字）不算，要连着 CLEAR_FRAMES 帧有字、又没有框。
            # 之前一帧空帧就清掉，框再露出来、漏读ジュエル时会按白名单重判（CodeRabbit #198）。等的这几帧照样计入 20 秒超时
            _regen["clear_frames"] = _regen.get("clear_frames", 0) + 1 if any(texts) else 0
            if _regen["clear_frames"] < CLEAR_FRAMES:
                return _wait_or_stop(texts)
            _regen.update(cancelled=False, clear_frames=0)  # 框关了：「已决定取消」只管那一个框
        if not _regen.get("confirmed") and _clock() - _regen.get("since", 0.0) < DIALOG_GRACE_S:
            return WAIT  # 刚点了再生成、框还没弹：先等一会儿
        # 没有框了：确认过的已经在重抽，没确认的 = 框没弹（直接重抽了）。交回收尾照常走（次へ / 点空白跳过演出）
        _regen["pending"] = False
    elif cancel is not None and _is_regen_dialog(boxes, texts, cancel):
        if _regen.get("confirmed"):
            # 这局已经确认过一次再生成：又弹出来的框不是这里要的，取消（REGEN_MAX）；一直关不掉就停下（CodeRabbit #198）
            t0 = _regen.setdefault("stray_since", _clock())
            if _clock() - t0 > REGEN_WAIT_MAX_S:
                _raise_blocked(texts, "多出来的再生成 / 钻石确认框一直关不掉（点キャンセル没反应）")
            logger.warning("刷回忆：已经再生成过，又见到再生成 / 钻石确认框，点キャンセル：%s", " ".join(texts)[:160])
            return int(cancel.rect.center.x), int(cancel.rect.center.y)
        logger.warning(
            "刷回忆：等确认框的时间过了 / 重启后才见到再生成 / 钻石确认框，照样按白名单处理：%s", " ".join(texts)[:160]
        )
        _regen["done"] = max(_regen["done"], 1)  # 框是点过再生成才会弹的（进程重启后计数从 0 开始）：不再点第二次
        _regen.update(pending=True, since=_clock())  # 回到「等确认框」：点完以后框淡出那几帧也由这里管
        return _answer_dialog(boxes, texts, cancel)
    elif cancel is None and _is_regen_dialog_no_cancel(texts):
        # pending 清掉以后才弹的框、キャンセル又没读到：不交回通用分支（会点「決定」），回到「等确认框」，
        # 由上面的 pending 分支等着读出キャンセル再按白名单处理，REGEN_WAIT_MAX_S 还不行就停下（CodeRabbit #198）
        logger.warning("刷回忆：见到再生成 / 钻石确认框但没读到キャンセル，先等：%s", " ".join(texts)[:160])
        _regen["done"] = max(_regen["done"], 1)
        _regen.update(pending=True, since=_clock())
        return WAIT
    elif cancel is not None:
        # 开着的是别的框（照片保存确认等）：不归这里管。不读框后面的结果页、不点被遮住的「再生成」、不动再生成状态（CodeRabbit #198）
        return None
    else:
        _regen.pop("stray_since", None)
        _regen["cancelled"] = False
    r = memory_result(boxes)
    if r is None or not r["name"]:
        return None
    targets = _target_names()
    match = target_match(r["name"], targets)
    key = (r["name"], _regen["done"])
    if key not in _regen["reported"]:
        _regen["reported"].add(key)
        logger.info(
            "刷回忆：生成的回忆带「%s」（目标 %s）%s",
            r["name"],
            "、".join(sorted(targets)) or "?",
            {"hit": "，刷到了", "unsure": "，卡名读得不确定（像目标 / 太短），不再生成"}.get(match, ""),
        )
    if match != "miss" or not targets or _regen["done"] >= REGEN_MAX or not r["left"]:
        return None
    _regen["done"] += 1
    _regen.update(pending=True, since=_clock(), confirmed=False, cancelled=False)
    logger.info("刷回忆：不是目标，点「再生成」（还剩 %s 次，这局第 %d 次）", r["left"], _regen["done"])
    return r["regen_xy"]


_JEWEL = re.compile(r"[ジシ][ュユ][エェ]ル")  # ジュエル 和 OCR 读错的变体（ジユエル / シュエル / ジュェル）
DIALOG_SPAN = 650  # 确认框的字在キャンセル上方这么多像素以内（720×1280：框约 y 350–1050，キャンセル y≈1000）


def _is_jewel(texts: list[str]) -> bool:
    """整屏任何地方读到ジュエル（含 OCR 变体）都算：宁可错取消。"""
    return any(_JEWEL.search(t) for t in texts)


def _is_regen_dialog(boxes, texts: list[str], cancel) -> bool:
    """带キャンセル的框是不是再生成 / 花钻石的确认框：有ジュエル，或除了结果页那颗「再生成」按钮以外还有含「再生成」的字
    （框标题「メモリー再生成」「〜再生成しますか」、和キャンセル同一行的「再生成する」）。结果页底下那颗「再生成」（y≈1160，
    不在キャンセル那一行）不算，免得别的框（照片保存确认等）也被这里拦下。"""
    if _is_jewel(texts):
        return True
    cy = cancel.rect.center.y
    # 只看确认框区域（キャンセル那一行往上 DIALOG_SPAN）：结果页底下「再生成」按钮、「あと N 回再生成可能」之类的说明不算，
    # 免得结果页上弹的别的框也被这里取消掉
    return any(
        "再生成" in _txt(b) and (_txt(b) != "再生成" or abs(b.rect.center.y - cy) < 40)
        for b in boxes
        if cy - DIALOG_SPAN <= b.rect.center.y <= cy + 40
    )


DIALOG_BUTTONS = ("決定", "OK", "はい", "再生成する")  # 确认框的确认按钮（整格读到才算；结果页只有「再生成」「次へ」）


def _is_regen_dialog_no_cancel(texts: list[str]) -> bool:
    """读不到キャンセル时判「是不是再生成 / 花钻石的确认框」（口径同 _is_regen_dialog，但没有キャンセル定不了框的区域）：
    要有确认按钮（決定 / OK / はい / 再生成する），并且有框标题那样含「再生成」的字（结果页的「再生成」按钮、
    「あと N 回」「再生成可能」说明不算），或者ジュエル和「使用 / 消費」同屏（付费确认；只有ジュエル的领奖画面不算，免得卡住正常收尾）。"""
    if not any(t in DIALOG_BUTTONS for t in texts):
        return False
    if _is_jewel(texts) and any("使用" in t or "消費" in t for t in texts):
        return True
    return any(
        "再生成" in t
        and t != "再生成"
        and t not in DIALOG_BUTTONS
        and "可能" not in t
        and not re.search(r"あと\d+回", t)
        for t in texts
    )


DIALOG_X_PAD = (
    160  # 确认框左右边界：キャンセル与确认按钮的 x 范围往外扩这么多（720 宽：按钮约 x 218 / 500，框约 x 60–660）
)


def _dialog_box_texts(boxes, cancel, ok) -> list[str] | None:
    """确认框边界内的字（CodeRabbit #198）：y 在框标题（キャンセル上方 DIALOG_SPAN 以内、最靠上的含「再生成」的那行，
    结果页的「再生成」按钮和「あと N 回 / 再生成可能」说明不算）和キャンセル那一行之间，x 在キャンセル与确认按钮的范围往外扩
    DIALOG_X_PAD 以内。找不到框标题返回 None（认不出框在哪：不确认）。"""
    cy = cancel.rect.center.y
    x0 = min(cancel.rect.center.x, ok.rect.center.x) - DIALOG_X_PAD
    x1 = max(cancel.rect.center.x, ok.rect.center.x) + DIALOG_X_PAD
    titles = [b for b in boxes if cy - DIALOG_SPAN <= b.rect.center.y < cy - 20 and x0 <= b.rect.center.x <= x1
              and "再生成" in _txt(b) and _txt(b) != "再生成" and "可能" not in _txt(b)
              and not re.search(r"あと\d+回", _txt(b))]  # fmt: skip
    if not titles:
        return None
    ty = min(b.rect.center.y for b in titles)
    return [_txt(b) for b in boxes if ty <= b.rect.center.y < cy - 20 and x0 <= b.rect.center.x <= x1]


def _answer_dialog(boxes, texts: list[str], cancel) -> tuple[int, int]:
    """再生成确认框：白名单（框边界内读到免费 / 用券、整屏没有ジュエル、和キャンセル同一行认得出确认按钮、认得出框标题）才确认，
    否则点キャンセル。"""
    jewel = _is_jewel(texts)  # ジュエル看整屏
    cy = cancel.rect.center.y
    # 确认按钮要和キャンセル在同一行（框里的按钮）：底下结果页的「再生成」（约 y 1160）不算
    ok = next((b for b in boxes if _txt(b) in REGEN_CONFIRM_WORDS and abs(b.rect.center.y - cy) < 40
               and abs(b.rect.center.x - cancel.rect.center.x) > 100), None)  # fmt: skip
    inside = _dialog_box_texts(boxes, cancel, ok) if ok is not None else None
    free = inside is not None and _free_regen(inside)  # 白名单只看框边界内的字
    if jewel or ok is None or inside is None or not free:
        why = ("有钻石" if jewel else "认不出确认按钮" if ok is None else "认不出框标题（框边界不明）" if inside is None
               else "没读到免费 / 再生成券")  # fmt: skip
        logger.warning("刷回忆：再生成确认框%s，点キャンセル：%s", why, " ".join(texts)[:160])
        _regen["cancelled"] = True  # 记住这个框已决定取消，框关掉之前不再重判
        return int(cancel.rect.center.x), int(cancel.rect.center.y)
    logger.info("刷回忆：确认再生成（%s）", " ".join(texts)[:120])
    _regen.update(confirmed=True, since=_clock())  # 超时从确认那一刻重新算
    return int(ok.rect.center.x), int(ok.rect.center.y)


def _free_regen(texts: list[str]) -> bool:
    """确认框说的是免费 / 用再生成券（白名单）：「無料」或「チケット」要和「使用 / 消費」在同一行（单独一个「無料」不算：
    可能是框外别处的字、或「無料分は使い切りました」这类说明；CodeRabbit #198）；
    出现「不足」或券数是 0（所持 0、0枚）一律不算（框里写「所持チケット 0」时不能当成免费，自检）。
    0 前面不能紧挨着数字：「所持チケット10」「20枚」是 10 / 20 张，不是 0 张（审查 #198 第 6 条）。"""
    if any("不足" in t or re.search(r"(所持|残り)\D*?(?<!\d)0(枚|個)?$|(^|\D)0枚", t) for t in texts):
        return False
    if any(re.search(r"\d+個", t) and "チケット" not in t and "所持" not in t for t in texts):
        return False  # 「50個」这类不是券的数量 = 付费样式（ジュエル只显示成图标、字没读到时）：不算免费
    return any(("無料" in t or "チケット" in t) and ("使用" in t or "消費" in t) for t in texts)


def _raise_blocked(texts: list[str], why: str):
    from hiro.loop.errors import ResumeBlocked

    raise ResumeBlocked(f"刷回忆：点了再生成以后 {REGEN_WAIT_MAX_S:.0f} 秒{why}，停下交给人：{' '.join(texts)[:120]}")


def _stop_if_late(texts: list[str], why: str) -> None:
    if _clock() - _regen.get("since", _clock()) > REGEN_WAIT_MAX_S:
        _raise_blocked(texts, why)


def _wait_or_stop(texts: list[str]) -> str:
    _stop_if_late(texts, "画面还认不清（没点确认）")
    return WAIT
