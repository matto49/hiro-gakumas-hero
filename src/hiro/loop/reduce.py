"""reduce(state, obs) → 新 GameState。纯函数：同样的输入永远得到同样的输出，不读时钟、不读设备。

规则（docs/state-machine.md §3）：
1. 观测不完整不清空：字段为 None 保留旧值；瞬态页面（unknown / LOADING）不改 page，也不改字段。
2. 来源优先级 save > screen > infer：低优先级写入不覆盖同一帧或更新帧里高优先级写的值；值不同且来源不同时记入 conflicts。
3. 页面转移按 transitions.allowed 校验：合法转移立即接受；不合法的、或目标是弱证据页面（IDLE）的，要连续 DEBOUNCE 帧同一页面才接受（pending_page）。
4. mode 由 page 推出。
5. 进入考试（从非考试页面到 HIF_EXAM）清空 exam 与 exam_session；remaining_turns 变小视为新回合。
6. 隐藏信息（GameState.hidden，数据来源插件给的）换页清空，和 page_fields 一样只管当前页。
"""

import copy
from typing import Any

from hiro.contracts import GameState, Observation, PageState, Source
from hiro.contracts.game_state import PRIORITY
from hiro.loop.transitions import EXAM_PAGES, WEAK, allowed, is_transient, mode_of

DEBOUNCE = 3  # 不合法跳变需要连续多少帧才接受
STABLE = 2  # 连续多少帧算稳定

# 页面局部字段：换页清空，不进 produce / exam
PAGE_LOCAL = {
    "options",
    "complete",
    "advice",
    "candidates",
    "recommended",
    "cards",
    "problems",
    "exam_next",
    "weeks_left",
    "schedule",
}
# 哪个页面的字段写进哪个分区
SECTION = {
    "ACTION_SELECT": "produce",
    "DECK_LIST": "produce",
    "HIF_EXAM": "exam",
    "SELECT_CARD": None,
    "SELECT_DRINK": None,
    "HIF_EXAM_RESULT": None,
}
# 考试页面里整段替换（不逐项合并）的列表 / 字典字段
REPLACE_WHOLE = {"hand", "drinks", "buffs", "pitems", "params", "growth_permil", "deck"}
MAX_CONFLICTS = 20
# 不在 produce 分区的页面（CONSULT 等）读到也写进 produce：底栏的持有饮料、头部的体力 / P 点（D43：识屏 140621f 相談页读头部）
ANY_PAGE_PRODUCE = {"drinks", "drink_capacity", "stamina", "max_stamina", "produce_point"}


def reduce(state: GameState, obs: Observation) -> GameState:
    s = copy.deepcopy(state)
    s.seq = obs.seq
    page = obs.page.name
    if is_transient(page):
        return s  # 转场 / 没认出：什么都不改
    if obs.source != "plugin":  # 1.3：页面只由画面决定，插件观测不加帧数、不推进防抖
        _reduce_page(s, obs)
        _easy_key(s, obs)  # 获得演出常只停一两帧、页面还在防抖：看到就记，不等页面确认
    if s.page.name != page:
        return s  # 还在防抖，不采用这帧的字段
    _reduce_fields(s, obs)
    return s


def _reduce_page(s: GameState, obs: Observation) -> None:
    page, area, seq = obs.page.name, obs.page.area, obs.seq
    if s.page.name == page:
        s.page.frames += 1
        s.page.stable = s.page.frames >= STABLE
        s.pending_page = None
        return
    legal = allowed(s.page.name, page)
    weak = page in WEAK and s.page.name != "unknown"  # 弱证据页面：合法但要连续多帧
    if legal and not weak:
        _enter(s, page, area, seq)
        return
    if s.pending_page is not None and s.pending_page.name == page:
        s.pending_page.frames += 1
        if s.pending_page.frames >= DEBOUNCE:
            if not legal:
                _note(s, f"seq {seq}: {s.page.name} → {page} 不在转移图里，连续 {s.pending_page.frames} 帧后接受")
            _enter(s, page, area, s.pending_page.since_seq, frames=s.pending_page.frames)
        return
    s.pending_page = PageState(page, area, seq, False, 1)


def _enter(s: GameState, page: str, area: str, seq: int, *, frames: int = 1) -> None:
    prev = s.page.name
    s.page = PageState(page, area, seq, frames >= STABLE, frames)
    s.pending_page = None
    s.page_fields = {}
    s.hidden = {}  # 数据来源插件给的隐藏信息和页面字段一样只管当前页：上一场的牌库顺序不带进下一页的记录
    s.mode = mode_of(page, s.mode)
    if page in EXAM_PAGES and prev not in EXAM_PAGES and page == "HIF_EXAM":
        s.exam = {}
        s.exam_session.turn = 0
        s.exam_session.last_remaining = None
        for k in [k for k in s.sources if k.startswith("exam.")]:
            del s.sources[k]
    if page == "HIF_EXAM_RESULT":
        s.exam_session.brief_permil = (
            None  # 一场考试结束：说明页倍率作废（下一场没拍到说明页时不拿旧值比，数据确认 a41939a）
        )
    if prev in EXAM_PAGES and page not in EXAM_PAGES:
        _sync_exam_drinks(s, seq)
    if s.mode == "produce" and s.produce is None:
        s.produce = {}


def _easy_key(s: GameState, obs: Observation) -> None:
    """N.I.A PRO 简单模式的 P 道具「N.I.Aキー（黄緑）」（对手分数 −80%）：获得演出（ACQUIRE 的 pitem_id / pitem_ids）、考试 P 道具栏
    （pitems）、P 道具三选一里看到它，就记 produce.nia_easy_key = True。没看到不写（开局给道具时演出可能被快速点过，不能断言没有）。"""
    from hiro.state.modes import NIA_EASY_KEY

    f = obs.fields or {}
    ids = {f.get("pitem_id"), *(f.get("pitem_ids") or [])}
    for key in ("pitems", "options"):
        for p in f.get(key) or []:
            if isinstance(p, dict):
                ids.add(p.get("pitem_id"))
    if NIA_EASY_KEY not in ids or (s.produce or {}).get("nia_easy_key"):
        return
    s.produce = {**(s.produce or {}), "nia_easy_key": True}
    s.sources["produce.nia_easy_key"] = Source({"save": "save"}.get(obs.source, "screen"), obs.seq)
    _note(s, f"seq {obs.seq}: 看到 N.I.Aキー（黄緑）：这局是 N.I.A PRO 简单模式（对手分数 −80%）")


def _declined(s: GameState) -> None:
    """选行动页：中断层记下的「这一周别再选」（produce.declined_options，例如相談開始確認点了キャンセル）同一周放进 page_fields，换周丢掉。"""
    prod = s.produce or {}
    d = prod.get("declined_options")
    if not d:
        return
    if d.get("step") is not None and prod.get("step") is not None and d["step"] != prod["step"]:
        s.produce = {k: v for k, v in prod.items() if k != "declined_options"}
        return
    s.page_fields["declined_options"] = list(d.get("options") or [])


def _consult_price(s: GameState, obs: Observation, origin: str) -> None:
    """相談页的删卡价（services.delete.price）→ produce.consult_delete_price（3940a9：初 / N.I.A 按实际价判断选不选相談）。"""
    price = ((obs.fields.get("services") or {}).get("delete") or {}).get("price")
    if not isinstance(price, int) or price <= 0 or s.produce is None:
        return
    s.produce = {**s.produce, "consult_delete_price": price}
    s.sources["produce.consult_delete_price"] = Source(origin, obs.seq)


def _hajime_nia_step(s: GameState, obs: Observation, origin: str) -> None:
    """初 / N.I.A：选行动页头「中間 / 1次 / 2次 / 最終まで N週」按 produce.mode 换算成 produce.step（hiro.state.modes.step_from_header）。
    H.I.F 的 step 由识别层直接给（mode_and_step）；初 / N.I.A 的 mode 是运行时写进局面的，页头读不出来，所以在这里换算。
    换算不了（不是初 / N.I.A、页头没读到、模式的考试位置不知道）就不写，沿用之前的 step。"""
    prod = s.produce or {}
    try:
        from hiro.state.modes import step_from_header

        step = step_from_header(prod.get("mode"), obs.fields.get("exam_next"), obs.fields.get("weeks_left"))
    except Exception:  # noqa: BLE001  主数据不可用：不写
        return
    if step is None:
        return
    s.produce = {**prod, "step": step}
    s.sources["produce.step"] = Source(origin, obs.seq)


def _sync_exam_drinks(s: GameState, seq: int) -> None:
    """离开考试（R1 结束进轮间、R2 结束）：用考试里最后读到的饮料栏（exam.drinks）覆盖 produce.drinks。
    考试里喝饮料只改 exam.drinks（考试页字段进 exam 分区），produce.drinks 一直停在开局的值（第二十九局 seq 115 起是开局 4 瓶的
    pending 旧值，轮间按它决策）。exam.drinks 里有认不出的饮料（没有 drink_id）时不覆盖，宁可留旧值等下一次看到饮料栏。"""
    drinks = (s.exam or {}).get("drinks")
    if not isinstance(drinks, list) or s.produce is None:
        return
    ids = [d.get("drink_id") if isinstance(d, dict) else d for d in drinks]
    if any(not isinstance(i, str) or not i for i in ids):
        _note(s, f"seq {seq}: 离开考试时饮料栏有认不出的饮料，produce.drinks 保持原值")
        return
    old = s.produce.get("drinks")
    s.produce["drinks"] = sorted(ids)
    src = s.sources.get("exam.drinks")
    s.sources["produce.drinks"] = Source(src.origin if src is not None else "screen", seq)
    s.field_status["produce.drinks"] = "observed"
    if old is not None and sorted(old) != sorted(ids):
        _note(s, f"seq {seq}: 离开考试，produce.drinks 按考试饮料栏更新 {sorted(old)} → {sorted(ids)}")


def _reduce_fields(s: GameState, obs: Observation) -> None:
    section = SECTION.get(obs.page.name)
    origin = {"save": "save", "screen": "screen", "dataset": "screen", "replay": "screen"}.get(obs.source, "screen")
    by = None
    if obs.source == "plugin":  # 1.3：数据来源插件按自己声明的档位合并
        origin = obs.trust if obs.trust in PRIORITY else "screen"
        by = obs.by
    if obs.hidden:
        s.hidden.update(copy.deepcopy(obs.hidden))
    for key, value in obs.fields.items():
        if value is None:
            continue
        if key == "exam_label":
            s.exam_session.label = str(value)
            continue
        if section is None and key in ANY_PAGE_PRODUCE and s.produce is not None:
            section_key = "produce"  # 相談等页面底栏的持有饮料：也写进 produce（识别 3804ac8）  # noqa: F841
            s.produce[key] = copy.deepcopy(value)
            s.sources[f"produce.{key}"] = Source(origin, obs.seq, by)
            s.page_fields[key] = value
            continue
        if key in PAGE_LOCAL or section is None:
            s.page_fields[key] = value
            continue
        target: dict[str, Any] = getattr(s, section) if getattr(s, section) is not None else {}
        setattr(s, section, target)
        full = f"{section}.{key}"
        old_src = s.sources.get(full)
        if old_src is not None and PRIORITY[old_src.origin] > PRIORITY[origin] and old_src.seq >= obs.seq:
            continue  # 高优先级的新值不被低优先级覆盖
        old = target.get(key)
        if old is not None and old != value and old_src is not None and old_src.origin != origin:
            _note(s, f"seq {obs.seq}: {full} {old_src.origin}={old!r} vs {origin}={value!r}，采用 {origin}")
        target[key] = copy.deepcopy(value)
        s.sources[full] = Source(origin, obs.seq, by)
    if obs.page.name == "ACTION_SELECT":
        _hajime_nia_step(s, obs, origin)
        if (s.produce or {}).get("schedule"):
            s.page_fields["schedule"] = s.produce[
                "schedule"
            ]  # 整局日程（选行动页左上点开的日程列表，hiro.produce.schedule_probe）
        _declined(s)
    if obs.page.name == "CONSULT":
        _consult_price(s, obs, origin)
    if section == "exam":
        _observe_turn(s, obs)
        _attr_permil(s, obs)
    if obs.page.name == "HIF_EXAM_BRIEFING":
        pct = obs.fields.get("multiplier_pct")
        got = {k: int(pct[k]) * 10 for k in ("Vo", "Da", "Vi") if pct.get(k)} if isinstance(pct, dict) else {}
        if got:  # 读到几个属性记几个（q17 #431 Da 一度读不到）；整体替换，不与上一场（本战 R1）的旧值混
            s.exam_session.brief_permil = got
        n = obs.fields.get("exam_no")
        if isinstance(n, int) and 1 <= n <= 3:
            s.exam_session.brief_exam_no = n


def _attr_permil(s: GameState, obs: Observation) -> None:
    """倍率按属性记（识别-01 提议）：同帧 attr 与 multiplier_permil 都有时更新该属性；multiplier_permil 始终取当前属性的值，
    不让 None 保留上一个属性的倍率（否则 Vi 会配上 Da 的倍率）。"""
    ex = s.exam if s.exam is not None else {}
    table = ex.setdefault("attr_permil", dict(s.exam_session.brief_permil or {}))
    attr, mp = obs.fields.get("attr"), obs.fields.get("multiplier_permil")
    if attr and mp is not None:
        table[attr] = mp
    cur = ex.get("attr")
    ex["multiplier_permil"] = table.get(cur) if cur else None
    s.exam = ex


def _observe_turn(s: GameState, obs: Observation) -> None:
    remaining = obs.fields.get("remaining_turns")
    if remaining is None:
        return
    last = s.exam_session.last_remaining
    if last is None or remaining < last:
        s.exam_session.turn += 1
    s.exam_session.last_remaining = remaining


def _note(s: GameState, text: str) -> None:
    s.conflicts.append(text)
    del s.conflicts[:-MAX_CONFLICTS]


def reduce_all(obs_seq, state: GameState | None = None):
    """依次 reduce，逐步 yield (obs, state)。"""
    s = state or GameState()
    for obs in obs_seq:
        s = reduce(s, obs)
        yield obs, s
