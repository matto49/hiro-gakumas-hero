"""状态不变量（docs/state-events.md §4）：不依赖事件推算，只用上一状态与本帧观测抓读错。

    check_invariants(prev, obs, *, events=None, debug=False) -> list[Check]
    filter_observation(obs, checks, prev) -> Observation  # 去掉 read_error 的字段（不写入、不交给决策）

返回契约 1.2 的 hiro.contracts.Check。字段路径与 hiro.loop.events 一致：「分区.字段」（produce.params.Da、exam.score），
没有分区的页面（三选一等）用 page_fields.<字段>。
verdict：read_error（读数不合理，丢弃重读）/ model_gap（读数合理但推算不出，标 pending）/ consistent（只在 debug 时输出）。

规则（名称即 Check.rule）：
- range            属性 0..param_cap、体力 0..max_stamina、P 点 / 星数 ≥ 0
- digit_count      分数 / 属性 / 星数比上一采信值少了位数，且新值是旧值去掉一位后的连续片段（37719→3771、1070→70）；
                   考试分数还拦「多读一位」：新值是旧值末尾或开头多一位（2890→28900）；
                   考试元気只拦「四位数去掉开头一两位」（1388→388、1010→10，_genki_lost_head）；
                   P 点会正常花掉，只认「去掉末位」的情形
- score_monotonic  同一场考试内分数不减（同一场：剩余回合不增加超过 1）
- rank_consistency 同一帧里 玩家名次 / 玩家分数 / 对手分数 自洽：对手分数按名次不增，玩家分数夹在相邻名次之间
                   （上一帧没有可比的值时也能抓，例如续跑后第一帧的 2231）
- turn_step        剩余回合每次减 0 或 1；+1 视为加回合（model_gap，待事件确认），减 2 以上为漏观测（model_gap）
- param_monotonic  培育中属性只增不减（目前没有减属性的事件）
- star_monotonic   星数只增不减
- attr_multiplier  考试页 (attr, 倍率) 等于考前说明页的值（exam_session.brief_permil，差 ≤ 1%）；倍率等于另一属性的值时判属性读错
- model_permil     模拟器 / finals_context 的倍率 vs 说明页真值（model_permil_checks，考试第一帧由 battle 调；差 > 1% 为 model_gap）
- drink_capacity   持有饮料数 ≤ drink_capacity（未观测到时按 UI 4 格）
- counters         三选一刷新 / 除去次数只减不增
- p_delta          P 点变化 = 本步事件里的价格 / 奖励之和（需传 events；没有事件不判）
- deck_delta       牌组张数变化 = 本步事件的增减（需传 events 且观测里有 deck；没有不判）
"""

from hiro.contracts import Check
from hiro.loop.reduce import SECTION

ATTRS = ("Vo", "Da", "Vi")
DEFAULT_PARAM_CAP = 3000
DEFAULT_DRINK_SLOTS = 4  # H.I.F 饮料栏 4 格；ProduceState.drink_capacity 观测到后以它为准
EXAM_PAGES = {"HIF_EXAM"}
PRODUCE_PAGES = {"ACTION_SELECT"}
COUNTER_PAGES = {"SELECT_CARD", "SKILL_CARD_CHANGE_1"}

# 事件 → P 点变化：args.price 为花费（减），args.points 为获得（加）
SPEND_KINDS = {"SHOP_BUY", "SHOP_UPGRADE", "SHOP_DELETE", "CARD_DELETE", "INTERVAL_BUY", "INTERVAL_UPGRADE",
               "INTERVAL_RECOVER", "OUTING_PAID", "CUSTOMIZE"}  # fmt: skip
DECK_ADD = {"CARD_ACQUIRE", "SHOP_BUY"}  # SHOP_BUY 只在买的是卡（有 card_id）时算
DECK_REMOVE = {"CARD_DELETE", "SHOP_DELETE"}
SAME_ACT = {"SHOP_DELETE": "CARD_DELETE"}  # 同一件事的不同叫法


def _item(e: dict) -> tuple:
    a = e.get("args") or {}
    return a.get("card_id") or a.get("drink_id") or a.get("pitem_id"), a.get("upgrade")


def _merge(events: list[dict], kinds: set[str], cls) -> list[dict]:
    """同一件事可能有好几条事件：动作事件、画面上的确认弹窗（basis=observe_only）、相談的交换弹窗……
    按 (cls(e), 物品) 分组，组内再按 (basis, kind) 分，取条数最多的那一份——同一来源同一叫法的才是真的多次。
    条数相同时优先 price 齐全的、再优先非 observe_only 的。"""
    groups: dict[tuple, dict[tuple, list[dict]]] = {}
    for e in events:
        if e.get("kind") not in kinds:
            continue
        key = (cls(e), *_item(e))
        src = (e.get("basis") == "observe_only", e.get("kind"))
        groups.setdefault(key, {}).setdefault(src, []).append(e)
    out = []
    for by_src in groups.values():

        def rank(item):
            (observed, _), es = item
            return len(es), all((x.get("args") or {}).get("price") is not None for x in es), not observed

        out += max(by_src.items(), key=rank)[1]
    return out


def deck_delta(events: list[dict]) -> int:
    """本步事件让牌组张数变化多少。买饮料 / P 道具不算。"""
    cards = [e for e in events if e.get("kind") != "SHOP_BUY" or (e.get("args") or {}).get("card_id")]
    add = _merge(cards, DECK_ADD, lambda e: "+")
    rm = _merge(cards, DECK_REMOVE, lambda e: "-")
    return len(add) - len(rm)


def p_spend(events: list[dict]) -> int:
    spent = _merge(events, SPEND_KINDS, lambda e: SAME_ACT.get(e["kind"], e["kind"]))
    return sum(int((e.get("args") or {}).get("price") or 0) for e in spent)


_HAS_NOTE = "note" in Check.__dataclass_fields__


def _check(seq, field, predicted, observed, verdict, rule, note=None) -> Check:
    action = {"read_error": "discard_reread", "model_gap": "mark_pending", "consistent": "accept"}[verdict]
    extra = {"note": note} if _HAS_NOTE else {}
    return Check(
        seq=seq, field=field, predicted=predicted, observed=observed, verdict=verdict, rule=rule, action=action, **extra
    )


def _path(page: str, key: str) -> str:
    section = SECTION.get(page)
    return f"{section}.{key}" if section else f"page_fields.{key}"


def _dropped_digit(old: int, new: int, *, suffix_only: bool = False) -> bool:
    """new 比 old 少了位数，且 new 是 old 去掉若干位后剩下的连续片段（OCR 掉位的特征）。"""
    if old is None or new is None or new >= old:
        return False
    a, b = str(old), str(new)
    if len(b) >= len(a):
        return False
    if suffix_only:
        return a.startswith(b)
    return b in a


def _added_digit(old: int, new: int) -> bool:
    """new 正好是 old 多读了一位：末尾多一位（2890→28900、2890→28905）或开头多一位（2890→12890）。
    Windows 第十五局 #123：2890 读成 28900 被采信，之后 10 步的正确读数（10012…19255）全被 score_monotonic 拦下。
    正常出牌让分数恰好落在这种值上的概率极小；old < 100 时不判（小分数段自然增长容易撞上）。"""
    if old is None or new is None or old < 100 or new <= old:
        return False
    a, b = str(old), str(new)
    if len(b) != len(a) + 1:
        return False
    return b.startswith(a) or b.endswith(a)


def _genki_lost_head(old: int, new: int) -> bool:
    """元気四位数掉首位：old ≥ 1000、new < 1000，且 new 正好是 old 去掉开头一位或两位后的尾巴（1388→388、1010→10、1283→283）。
    元気会正常下降，所以不用 _dropped_digit 的「任意片段」（1283→28 那种当成正常值也说得通）；只拦切掉开头的特征。
    第二十～二十五局 80 处（数据确认 d412748），读数框已放宽，这条是兜底。"""
    if old is None or new is None or old < 1000 or new >= 1000:
        return False
    a = str(old)
    return any(a[k:].lstrip("0") == str(new) or (new == 0 and set(a[k:]) <= {"0"}) for k in (1, 2))


def _same_exam(prev_exam: dict, obs_fields: dict) -> bool:
    """剩余回合不增加超过 1：同一场考试（新的一场从更多回合开始）。"""
    last, now = prev_exam.get("remaining_turns"), obs_fields.get("remaining_turns")
    return last is not None and now is not None and now <= last + 1


NPC_JUMP = 3.0  # 同一场考试里对手分数一帧内涨到 3 倍以上，按读错处理（实测掉 / 多一位是 10 倍）


def _suspicious_npcs(prev_npcs, npcs) -> list[int]:
    """与上一帧相比暴涨（多读一位）或减少（掉位）的对手下标。名次变化会让同一下标换人，所以只认量级上的突变。"""
    if not prev_npcs or len(prev_npcs) != len(npcs):
        return []
    out = []
    for i, (a, b) in enumerate(zip(prev_npcs, npcs, strict=True)):
        if a is None or b is None or a < 100:
            continue
        if b > a * NPC_JUMP or b * NPC_JUMP < a:
            out.append(i)
    return out


def _rank_check(seq: int, f: dict, prev_exam: dict, same: bool) -> list[Check]:
    """玩家名次 / 分数 / 对手分数自洽：对手分数按显示顺序（名次）不增；玩家分数夹在第 r-1 名与第 r 名对手之间。
    矛盾时判谁读错：玩家分数与上一帧相比合理、且有对手分数相对上一帧突变 → 对手读错；否则玩家分数读错。
    （第六局 seq 331：玩家 41261 正确，对手 32450 吞进相邻的「9」读成 324509。）"""
    me, rank, npcs = f.get("score"), f.get("my_rank_index"), f.get("npc_scores")
    if me is None or rank is None or not npcs or any(v is None for v in npcs):
        return []
    if any(a < b for a, b in zip(npcs, npcs[1:], strict=False)):  # noqa: RUF007
        return [
            _check(
                seq, "exam.npc_scores", "按名次不增", npcs, "read_error", "rank_consistency", "对手分数顺序与名次矛盾"
            )
        ]
    above = npcs[: rank - 1]
    below = npcs[rank - 1 :]
    lo = max(below) if below else 0
    hi = min(above) if above else None
    if lo <= me and (hi is None or me <= hi):
        return []
    prev_score = prev_exam.get("score") if same else None
    me_ok = prev_score is not None and me >= prev_score and not _dropped_digit(prev_score, me)
    bad_npcs = _suspicious_npcs(prev_exam.get("npc_scores") if same else None, npcs)
    if me_ok and bad_npcs:
        return [_check(seq, "exam.npc_scores", {"rank": rank, "score": me}, npcs, "read_error", "rank_consistency",
                       f"玩家分数与上一帧连续，对手第 {[i + 1 for i in bad_npcs]} 个分数突变")]  # fmt: skip
    return [
        _check(
            seq,
            "exam.score",
            {"min": lo, "max": hi},
            me,
            "read_error",
            "rank_consistency",
            f"第 {rank} 名应在 {lo}..{hi}",
        )
    ]


def check_invariants(prev, obs, *, events: list[dict] | None = None, debug: bool = False) -> list[Check]:
    """prev：上一 GameState（reduce 前）；obs：本帧 Observation。返回 Check 列表（debug=False 只返回不一致的）。"""
    page = obs.page.name
    f = obs.fields or {}
    seq = obs.seq
    out: list[Check] = []
    ok: list[Check] = []

    def judge(cond_bad: bool, field: str, predicted, observed, rule: str, *, verdict: str = "read_error", note=None):
        if cond_bad:
            out.append(_check(seq, field, predicted, observed, verdict, rule, note))
        elif debug:
            ok.append(_check(seq, field, predicted, observed, "consistent", rule, note))

    produce = (prev.produce or {}) if prev is not None else {}
    exam = (prev.exam or {}) if prev is not None else {}

    if page in PRODUCE_PAGES:
        params = f.get("params") or {}
        cap = f.get("param_cap") or produce.get("param_cap") or DEFAULT_PARAM_CAP
        old_params = produce.get("params") or {}
        for a in ATTRS:
            v = params.get(a)
            if v is None:
                continue
            path = f"produce.params.{a}"
            judge(not 0 <= v <= cap, path, {"min": 0, "max": cap}, v, "range")
            old = old_params.get(a)
            if old is not None:
                judge(_dropped_digit(old, v), path, old, v, "digit_count", note="掉位")
                judge(v < old, path, {"min": old}, v, "param_monotonic")
        st, mx = f.get("stamina"), f.get("max_stamina") or produce.get("max_stamina")
        if st is not None and mx:
            judge(not 0 <= st <= mx, "produce.stamina", {"min": 0, "max": mx}, st, "range")
        star = f.get("star")
        if star is not None:
            judge(star < 0, "produce.star", {"min": 0}, star, "range")
            old = produce.get("star")
            if old is not None:
                judge(_dropped_digit(old, star), "produce.star", old, star, "digit_count", note="掉位")
                judge(star < old, "produce.star", {"min": old}, star, "star_monotonic")
        pp = f.get("produce_point")
        if pp is not None:
            judge(pp < 0, "produce.produce_point", {"min": 0}, pp, "range")
            old = produce.get("produce_point")
            if old is not None:
                judge(
                    _dropped_digit(old, pp, suffix_only=True),
                    "produce.produce_point",
                    old,
                    pp,
                    "digit_count",
                    note="末位掉位",
                )
                if events:
                    _p_delta(seq, old, pp, events, out, ok, debug)

    if page in EXAM_PAGES:
        same = _same_exam(exam, f)
        score = f.get("score")
        if score is not None and same and exam.get("score") is not None:
            old = exam["score"]
            judge(_dropped_digit(old, score), "exam.score", old, score, "digit_count", note="掉位")
            judge(_added_digit(old, score), "exam.score", old, score, "digit_count", note="多读一位")
            judge(score < old, "exam.score", {"min": old}, score, "score_monotonic")
        genki, old_genki = f.get("genki"), exam.get("genki")
        if genki is not None and same and old_genki is not None:
            judge(
                _genki_lost_head(old_genki, genki),
                "exam.genki",
                old_genki,
                genki,
                "digit_count",
                note="四位数首位掉位（1388→388）",
            )
        rank_bad = _rank_check(seq, f, exam, same)
        out.extend(rank_bad)
        if debug and not rank_bad and score is not None and f.get("my_rank_index") is not None:
            ok.append(_check(seq, "exam.score", "名次自洽", score, "consistent", "rank_consistency"))
        now, last = f.get("remaining_turns"), exam.get("remaining_turns")
        if same and now is not None and last is not None:
            if now == last + 1:
                out.append(
                    _check(
                        seq,
                        "exam.remaining_turns",
                        last,
                        now,
                        "model_gap",
                        "turn_step",
                        "回合 +1：加回合效果，待事件确认",
                    )
                )
            elif now < last - 1:
                out.append(
                    _check(
                        seq,
                        "exam.remaining_turns",
                        {"min": last - 1},
                        now,
                        "model_gap",
                        "turn_step",
                        f"一次少了 {last - now} 回合：中间回合没观测到",
                    )
                )
            elif debug:
                ok.append(_check(seq, "exam.remaining_turns", last, now, "consistent", "turn_step"))
        brief = prev.exam_session.brief_permil if prev is not None else None
        out.extend(_brief_check(seq, f.get("attr"), f.get("multiplier_permil"), brief, ok if debug else None))

    drinks = f.get("drinks")
    if isinstance(drinks, list):
        held = sum(1 for d in drinks if d is not None)
        cap = produce.get("drink_capacity") or DEFAULT_DRINK_SLOTS
        judge(held > cap, _path(page, "drinks"), {"max": cap}, held, "drink_capacity")

    if page in COUNTER_PAGES:
        prev_fields = prev.page_fields if prev is not None and prev.page.name == page else {}
        for key in ("rerolls_left", "excludes_left"):
            v = f.get(key)
            old = prev_fields.get(key)
            if old is None:
                old = produce.get(key)
            if v is not None and old is not None:
                judge(v > old, _path(page, key), {"max": old}, v, "counters")

    if events and page == "DECK_LIST" and f.get("deck") is not None and produce.get("deck") is not None:
        want = len(produce["deck"]) + deck_delta(events)
        judge(len(f["deck"]) != want, "produce.deck", want, len(f["deck"]), "deck_delta", verdict="model_gap")

    return out + ok


MULT_TOL = 0.01  # 倍率 vs 说明页真值：差 ≤ 1% 算一致（画面是整数 %，换算千分比有舍入）


def _pct_diff(mp: int, want: int) -> str:
    return f"差 {mp - want:+d}‰（{(mp - want) / want * 100:+.1f}%）"


def _brief_check(seq: int, attr: str | None, mp: int | None, brief: dict | None, ok: list | None) -> list[Check]:
    """考试页的 (attr, 倍率) 必须等于考前说明页的值（整场考试倍率不变；说明页是真值，用户 2026-11-02）。差 ≤ 1% 算一致。
    倍率恰好等于说明页上另一个属性的值时，读错的是属性（只报 exam.attr）；否则报倍率，note 写差值。
    现有读错都是首位被回合转盘的箭头遮住：1481→7481、1085→11085、1905→905。"""
    if not brief or attr not in brief or mp is None:
        return []
    want = brief[attr]
    if abs(mp - want) <= want * MULT_TOL:
        if ok is not None:
            ok.append(
                _check(
                    seq,
                    "exam.multiplier_permil",
                    want,
                    mp,
                    "consistent",
                    "attr_multiplier",
                    _pct_diff(mp, want) if mp != want else None,
                )
            )
        return []
    others = [a for a, v in brief.items() if a != attr and v == mp]
    if len(others) == 1 and list(brief.values()).count(want) == 1:
        return [
            _check(
                seq,
                "exam.attr",
                others[0],
                attr,
                "read_error",
                "attr_multiplier",
                f"倍率 {mp} 是说明页 {others[0]} 的值",
            )
        ]
    return [_check(seq, "exam.multiplier_permil", {"attr": attr, "value": want}, mp, "read_error", "attr_multiplier",
                   f"说明页 {attr} = {want}，{_pct_diff(mp, want)}")]  # fmt: skip


def model_permil_checks(seq: int, label: str, model: dict | None, brief: dict | None) -> list[Check]:
    """模型倍率（模拟器考试元数据 / 本战 finals_context.permil，按参数算的）vs 考前说明页真值：每个属性一条，差 > 1% 记 model_gap。"""
    out = []
    for a in ATTRS:
        m, want = (model or {}).get(a), (brief or {}).get(a)
        if not m or not want:
            continue
        bad = abs(int(m) - want) > want * MULT_TOL
        out.append(_check(seq, f"exam.model_permil.{a}", want, int(m), "model_gap" if bad else "consistent", "model_permil",
                          f"{label}：模型 {int(m)} vs 说明页 {want}，{_pct_diff(int(m), want)}"))  # fmt: skip
    return out


def _p_delta(seq, old, new, events, out, ok, debug) -> None:
    spend = p_spend(events)
    gain = sum(int((e.get("args") or {}).get("points") or 0) for e in events)
    want = old - spend + gain
    if new != want:
        out.append(
            _check(seq, "produce.produce_point", want, new, "model_gap", "p_delta", f"花费 {spend}、获得 {gain}")
        )
    elif debug:
        ok.append(_check(seq, "produce.produce_point", want, new, "consistent", "p_delta"))


def filter_observation(obs, checks: list[Check], prev=None):
    """去掉 read_error 的字段，返回新的 Observation：读数不写入、不交给决策。与主循环用同一个实现（hiro.loop.events.gate）。"""
    from hiro.loop.events import gate

    return gate(obs, checks, prev)


def summarize(checks: list[Check]) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(f"{c.rule}:{c.verdict}" for c in checks))
