"""GameState → GKMS 的培育记录（spec 4.6），给 hajime_route.plan 用。

只用画面上读得到的东西：属性、体力、P 点、成长率、P 道具、支援卡编成，加上日程列表探查读到的整局每周选项
（produce.schedule.weeks）。本周以选行动页的选项为准（含 SP）；以后几周读不到 SP，按普通课算。

GKMS 的日程类型名直接用字符串（它的 STEP_TYPE.get(t, t) 认名字），不用游戏枚举号。
"""

from __future__ import annotations

from typing import Any

from hiro.state.modes import HAJIME, NIA, family

ATTR = {"Vo": "vo", "Da": "da", "Vi": "vi"}
GKMS_ATTR = {"Vo": "vocal", "Da": "dance", "Vi": "visual"}
LEGEND = "produce-006"
# 画面选项名（hiro.vision.observe.OPTION_NAME 的输出）→ GKMS 日程类型；训练另算
SIMPLE = {"class": "event_school", "shop": "shop", "allowance": "event_activity", "outing": "event", "gift": "present",
          "present": "present", "rest": "refresh", "special_guidance": "customize"}  # fmt: skip


def _lesson(mode: str, attr: str | None, *, sp: bool = False, hard: bool = False) -> list[str]:
    attrs = [ATTR[attr]] if attr in ATTR else list(ATTR.values())
    if hard:
        return [f"lesson_{a}_hard" for a in attrs]
    head = "legend_lesson" if mode == LEGEND else "lesson"
    return [f"{head}_{a}{'_sp' if sp else ''}" for a in attrs]


def _types(mode: str, options: list[dict], *, current: bool) -> list[str] | None:
    """一周的选项 → GKMS 类型；有认不出的选项返回 None。current：本周（画面选项，带 SP）。"""
    out: list[str] = []
    for o in options:
        name = o.get("option")
        if name == "training":
            out += _lesson(mode, o.get("attr"), sp=bool(current and o.get("sp")), hard=bool(o.get("oikomi")))
        elif name in SIMPLE:
            out.append(SIMPLE[name])
        else:
            return None
    if "refresh" not in out:
        out.append("refresh")  # 休む随时可选（日程列表上不画）
    return list(dict.fromkeys(out))


def _growth(v: Any) -> int:
    """画面的成长率是 1000 + x（1122 = +12.2%），GKMS 要 x。"""
    v = int(v or 0)
    return v - 1000 if v >= 1000 else v


def record(state) -> tuple[dict | None, str | None]:
    """(GKMS 培育记录, None) 或 (None, 为什么拼不出来)。记录里另带 from_step（本周）。"""
    p = dict(state.produce or {})
    mode = p.get("mode")
    fam = family(mode)
    if fam not in (HAJIME, NIA):
        return None, f"{mode} 不是初 / N.I.A（H.I.F 的选行动这一版不接）"
    if fam == NIA:
        return None, "N.I.A 的日程列表读数认不出试镜周，这一版不接 N.I.A 选行动"
    step = p.get("step")
    if not step:
        return None, "不知道现在是第几周（produce.step 没读到）"
    sched = p.get("schedule") or {}
    weeks = {int(w["week"]): w for w in sched.get("weeks") or [] if w.get("week")}
    total = int(sched.get("total") or (max(weeks) if weeks else 0))
    if not weeks or not total:
        return None, "没有日程列表（日程列表探查没开或没读到）"
    params, growth = p.get("params") or {}, p.get("growth_permil") or {}
    if any(params.get(k) is None for k in GKMS_ATTR) or p.get("stamina") is None or p.get("max_stamina") is None:
        return None, "属性 / 体力没读全"
    exams = sorted(w for w, x in weeks.items() if x.get("kind") == "exam")
    rows = []
    options_now = (state.page_fields or {}).get("options") or []
    for wk in range(int(step), total + 1):
        w = weeks.get(wk)
        if wk == step:
            t = _types(mode, [o for o in options_now if o.get("option")], current=True)
            if t is None:
                return None, f"本周选项里有认不出的：{[o.get('option') for o in options_now]}"
        elif w is None:
            return None, f"日程列表缺第 {wk} 周"
        elif w.get("kind") == "exam":
            t = ["audition_final" if wk == (exams[-1] if exams else total) else "audition_mid1"]
        elif w.get("kind") == "oikomi":
            t = _lesson(mode, None, hard=True) + ["refresh"]
        else:
            t = _types(mode, w.get("options") or [], current=False)
            if t is None:
                return None, f"第 {wk} 周的选项认不出：{[o.get('option') for o in w.get('options') or []]}"
        rows.append({"step_number": wk, "step_types": t})
    st = {GKMS_ATTR[k]: params[k] for k in GKMS_ATTR}
    st.update(stamina=p["stamina"], max_stamina=p["max_stamina"], produce_point=p.get("produce_point") or 0)
    for k, g in GKMS_ATTR.items():
        st[f"{g}_growth_rate_permil"] = _growth(growth.get(k))
    lesson = getattr(state.exam_session, "lesson", None) or {}
    if lesson.get("lesson_limit_up_score"):
        st["lesson_limit_up_score"] = lesson["lesson_limit_up_score"]
    sups = []
    for s in p.get("supports") or []:
        cid = s.get("support_card_id") or s.get("card_id") if isinstance(s, dict) else None
        if cid:
            sups.append({"support_card_id": cid, "level": s.get("level") or 60})  # 等级读不到按满级
    rec = {
        "uuid": "hiro-live",
        "produce_id": mode,
        "idol_card_id": p.get("idol_card_id"),
        "occasions": [{"rpc": "Start", "at_step": int(step) - 1, "state": st, "items_gained": list(p.get("items") or []), "deck": []}],
        "schedule": rows,
        "support_state": sups,
        "activations": [],
        "from_step": int(step),
    }
    return rec, None


def option_for(choice: str, options: list[dict]) -> int | None:
    """GKMS 计划里本周的那一项 → 画面选项序号；对不上返回 None。"""
    c = str(choice)
    if c.startswith("legend_"):
        c = c[len("legend_") :]
    for i, o in enumerate(options):
        name = o.get("option")
        if name == "training" and c.startswith("lesson_"):
            parts = c.split("_")  # lesson_da[_sp|_hard]
            if ATTR.get(o.get("attr")) == parts[1] and bool(o.get("sp")) == ("sp" in parts[2:]) and bool(o.get("oikomi")) == ("hard" in parts[2:]):
                return i
            if o.get("oikomi") and "hard" in parts[2:] and o.get("attr") is None:
                return i  # 追い込み选项在选行动页上不分属性（属性在下一页选）
        elif SIMPLE.get(name) == c:
            return i
    return None
