# 来源：digital-hiro src/hiro/vision/observe.py（develop c3fc27f）；保留开源版的 register_adapter 注册接口
"""识别结果 → 决策层数据结构。识别模块保留各自的原始输出，这里是两边唯一的对接点。

- 培育：字段名对齐 hiro.state.produce.ProduceState（客户端协议 UserProduceProgress），
  只给出当前画面能观测到的字段，可直接 ProduceState.from_dict 或合并进已有状态；
  选项名对齐 gakumas_produce 日程（training / class / outing / gift / shop / special_guidance）。
- 考试：BattleState.build 的观测参数（手牌 CardView、饮料 DrinkView 等）。
"""

import re
from functools import lru_cache

SELECTION, FINALS = "produce-007", "produce-008"
SELECTION_EXAM_STEPS = {1: 7, 2: 13, 3: 20}  # 选拔①②③在日程中的位置
ATTR_KEY = {"vo": "Vo", "da": "Da", "vi": "Vi"}
OPTION_NAME = {
    "open_lesson": "training",
    "lesson": "training",
    "class": "class",
    "outing": "outing",
    "present": "gift",
    "consult": "shop",
    "special_training": "special_guidance",
    "rest": "rest",
}
# 考试状态栏图标键 → BattleState 字段
STATUS_FIELD = {"examcardplayaggressive": "motivation", "examreview": "impression"}
EXAM_ATTR = {"vocal": "Vo", "dance": "Da", "visual": "Vi"}
# 状态栏数值单位（识别给「%」「ターン」「回」或无）→ 契约 unit：stack / percent / turns / none
UNIT = {"%": "percent", "ターン": "turns", "回": "stack"}


def _attrs(d: dict | None) -> dict | None:
    if not d or any(v is None for v in d.values()):
        return None
    return {ATTR_KEY[k]: v for k, v in d.items()}


FINALS_EXAM_STEPS = {1: 7, 2: 9}  # 本战 R1 / R2 在日程中的位置（ProduceState：7=R1、8=轮间、9=R2）


def mode_and_step(exam: str | None, days_left: int | None) -> tuple[str | None, int | None]:
    """头部 → (produceId, stepNumber)。
    - 选拔赛「選抜試験1まで 6日」→ (produce-007, 7 − 6 = 1)；
    - 本战「H.I.F本戦まで 6日」→ (produce-008, 7 − 6 = 1)（第四局实机 2026-10-31）；「本戦2」类写法（R2 前，尚无样本）按 R2 = 9 推。
      天数读不出时 mode 照给 produce-008、step 为 None。"""
    exam = exam or ""
    if "本戦" in exam:
        m = re.search(r"本戦\s*(\d)", exam)
        pos = FINALS_EXAM_STEPS.get(int(m.group(1)) if m else 1)
        return FINALS, (pos - days_left if pos and days_left is not None else None)
    m = re.search(r"選抜試験(\d)", exam)
    if not m or days_left is None:
        return None, None
    pos = SELECTION_EXAM_STEPS.get(int(m.group(1)))
    return SELECTION, (pos - days_left if pos else None)


def produce_observation(action: dict) -> dict:
    """read_action_select(...).to_dict() → ProduceState 字段子集 + 本页选项。读不到的字段不出现。"""
    mode, step = mode_and_step(action.get("exam"), action.get("days_left"))
    growth = action.get("growth_pct") or {}
    caps = {v for v in (action.get("param_caps") or {}).values() if v}
    obs = {
        "mode": mode,
        "step": step,
        "stamina": action.get("stamina"),
        "max_stamina": action.get("max_stamina"),
        "produce_point": action.get("p_points"),
        "params": _attrs(action.get("params")),
        # 画面上的成长率是加成部分（41.3%），协议字段是合计千分比（1413）
        "growth_permil": _attrs({k: round(1000 + v * 10) for k, v in growth.items() if v is not None}) if len(growth) == 3 else None,
        "star": action.get("star"),
        "param_cap": caps.pop() if len(caps) == 1 else None,
        "advice": action.get("advice"),
        "drinks": action.get("drinks"),  # 底栏饮料 [drink_id…]（items.read_bar_drinks；有一格认不出时 None）
        "drink_capacity": action.get("drink_capacity"),
    }
    obs = {k: v for k, v in obs.items() if v is not None}
    obs["options"] = [
        {
            "option": OPTION_NAME.get(o["kind"], o["kind"]),
            "attr": ATTR_KEY.get(o["attr"]) if o.get("attr") else None,
            "hp_cost": o.get("hp_cost"),
            "hp_gain": o.get("hp_gain"),
            "center": list(o["center"]),
        }
        for o in action.get("options", [])
    ]
    obs["complete"] = bool(action.get("complete"))
    return obs


def battle_observation(exam: dict) -> dict:
    """read_hif_exam(...) → BattleState.build(memory, profile, **obs) 的观测参数。"""
    from hiro.brain.cards import card_view, drink_view  # 用到时再导入：基础安装（不带 KAA / 策略层）也能 import 本模块
    from hiro.vision.cards import card_version

    status = exam.get("status") or {}
    hand = []
    for c in exam.get("hand") or []:
        card = card_version(c["card_id"], c.get("upgrade") or 0) if c.get("card_id") else None
        view = card_view(c["slot"], card, available=bool(c.get("available")))
        if card is not None and "upgrade" in c and c["upgrade"] is None:
            view.upgrade = None  # 强化次数未知（被下一张卡盖住）：效果按基础版，upgrade 传 None 不冒充 0
        view.badge_score = c.get("badge_score")
        hand.append(view)
    drinks = []
    for d in exam.get("drinks") or []:
        if d is None:
            continue
        from kaa.db.drink import Drink

        drink = Drink.from_asset_id(d["asset"])
        if drink is not None:
            drinks.append(drink_view(d["slot"], drink))
    obs = {
        "remaining_turns": exam.get("remaining_turns"),
        "hp": exam.get("hp"),
        "genki": exam.get("genki"),
        "score": exam.get("my_score"),
        "hand": hand,
        "drinks": drinks,
        "buffs": {e.get("name") or e.get("key"): e.get("value") for e in exam.get("statuses") or []
                  if e.get("key") not in STATUS_FIELD and (e.get("name") or e.get("key"))},
    }  # fmt: skip
    statuses = exam.get("statuses")
    # 状态栏读到了槽位且每个蓝底槽都认出了图标：没有某个图标即该状态为 0（看到了「没有」，不是猜）
    column_complete = bool(statuses) and all(e.get("key") for e in statuses if e.get("kind") == "status")
    for key, fieldname in STATUS_FIELD.items():
        value = status.get(key, status.get(fieldname))  # 早期数据集的 status 直接用 motivation / impression 作键
        obs[fieldname] = value if value is not None else (0 if column_complete else None)
    return obs


# ---------------------------------------------------------------- Observation 契约
def _select_card_observation(fields: dict) -> dict:
    """extract._select_card 的输出 → 三选一候选（协议 pickIndex 顺序）。"""
    cands = []
    for o in fields.get("options") or []:
        cands.append({"slot": o.get("slot"), "card_id": o.get("card_id"), "upgrade": o.get("upgrade", 1 if o.get("upgraded") else 0),
                      "name": o.get("name"), "recommended": bool(o.get("recommended"))})  # fmt: skip
    return {"candidates": cands, "recommended": fields.get("recommended"),
            "rerolls_left": fields.get("rerolls_left"), "excludes_left": fields.get("excludes_left")}  # fmt: skip


def _select_drink_observation(fields: dict) -> dict:
    from kaa.db.drink import Drink

    cands = []
    for o in fields.get("options") or []:
        drink = Drink.from_asset_id(o["asset_id"]) if o.get("asset_id") else None
        cands.append({"slot": o.get("slot"), "drink_id": drink.id if drink else None, "name": o.get("name"), "score": o.get("score")})
    # held：底栏已有饮料（饮料满时先丢一瓶再领：点 held[i].center → 捨てる）；full：「Pドリンク所持上限」
    held = [{k: d.get(k) for k in ("index", "drink_id", "name", "center", "method")} for d in fields["held"]] if "held" in fields else None
    return {"candidates": cands, "held": held, "full": fields.get("full")}


def _jsonable_battle(obs: dict) -> dict:
    out = dict(obs)
    out["hand"] = [c.to_dict() for c in obs.get("hand") or []]
    out["drinks"] = [d.to_dict() for d in obs.get("drinks") or []]
    return out


@lru_cache(maxsize=1)
def _effect_types() -> dict[str, str]:
    """图标键（小写）→ ProduceExamEffectType_*。素材包 index 缺名的 H.I.F 新类型，用 KAA game.db 的 effectType 补。"""
    from kaa.db.sqlite import select_many

    rows = select_many("SELECT DISTINCT effectType FROM ProduceExamEffect;")
    return {r[0].removeprefix("ProduceExamEffectType_").lower(): r[0] for r in rows if r[0]}


def statuses_observation(entries: list[dict] | None) -> list[dict]:
    """状态栏条目 → 契约 statuses[{type, name, value, unit}]。
    状态效果：type 为 ProduceExamEffectType_*，查不到正式名的 H.I.F 新类型为 None、name 用图标键；
    卡牌持续效果（enchant）：type 为 None，name 为来源卡名。无单位的状态效果是层数（stack），卡牌常驻效果为 none。"""
    out = []
    for e in entries or []:
        if e.get("kind") == "enchant":
            out.append({"type": None, "name": e.get("name"), "value": e.get("value"), "unit": UNIT.get(e.get("unit"), "none")})
            continue
        key = e.get("key")
        etype = e.get("type") or (_effect_types().get(key) if key else None)
        out.append({"type": etype, "name": e.get("name") or key, "value": e.get("value"), "unit": UNIT.get(e.get("unit"), "stack")})
    return out


def _hif_exam_observation(fields: dict) -> dict:
    obs = _jsonable_battle(battle_observation(fields))
    for k in ("deck_count", "pitem_charges", "rank_line", "participants", "pitems", "unrecognized_cards", "npc_scores", "my_rank_index",
              "drink_slots", "finals", "finals_round"):
        obs[k] = fields.get(k)
    if fields.get("hand_layout") in UNSTABLE_LAYOUT:
        obs["hand"] = None  # 手牌不在稳定布局（有卡被抬起 / 动画中）：读到的几张不是完整手牌，不冒充
    obs["attr"] = EXAM_ATTR.get(fields.get("attr"))
    pct = fields.get("multiplier_pct")
    obs["multiplier_pct"] = pct  # 旧字段，契约保留
    obs["multiplier_permil"] = pct * 10 if pct is not None else None
    obs["statuses"] = statuses_observation(fields.get("statuses"))
    if any(not (e.get("name") or e.get("key")) for e in fields.get("statuses") or []):
        obs["buffs"] = None  # 状态栏有完全没认出的槽：buffs 不完整，不能当成「没有其他状态」
    # 识别原始结果里没有这个键 = 没读（不是读到了空）：给 None，免得和「0 张手牌 / 空状态栏」混淆
    for name, raw_key in RAW_KEY.items():
        if raw_key not in fields:
            obs[name] = None
    return obs


UNSTABLE_LAYOUT = {"partial": "有一张手牌被选中抬起或正在打出，手牌不完整", "shifted": "手牌位置与任何张数的布局都对不上（动画中）",
                   "empty": "画面上没有手牌（出牌放大 / 回合切换 / 发牌动画），不是 0 张",
                   "overlay": "过场白光叠在手牌上（スキルカードサポート 等），可打 / 不可打判不准"}  # fmt: skip

# 容器字段 → 识别原始结果里的来源键（缺键时契约字段为 None；键在但为空才是空容器）
RAW_KEY = {"hand": "hand", "drinks": "drinks", "buffs": "statuses", "statuses": "statuses", "drink_slots": "drink_slots"}


def _hif_result_observation(fields: dict) -> dict:
    """read_hif_result → 结算页字段。score 为玩家分数，rank 为玩家在名单（同场选手）里的名次。"""
    return {"score": fields.get("my_score"), "rank": fields.get("rank"), "passed": fields.get("passed"),
            "exam_no": fields.get("exam_no"), "rank_line": fields.get("rank_line"),
            "participants": fields.get("participants"), "entries": fields.get("entries"), "retry": fields.get("retry")}  # fmt: skip


def _event_observation(fields: dict) -> dict:
    """read_event_choices → 授業 / おでかけ 页字段：choices[{index, text, cost_hp, cost_p, trouble, selected, center}]、
    selected、effect（选中项的效果说明）、complete。代价与眠気是结构化字段，不拼在 text 里。"""
    return {"choices": fields.get("choices"), "selected": fields.get("selected"), "effect": fields.get("effect"),
            "complete": fields.get("complete")}  # fmt: skip


def _select_pitem_observation(fields: dict) -> dict:
    """read_pitem_select → 三个背包（选之前看不到道具，只有颜色）与选中态。"""
    return {"pitems": fields.get("options"), "selected": fields.get("selected"), "confirm_enabled": fields.get("confirm_enabled")}


def _start_confirm_observation(fields: dict) -> dict:
    """read_supports → produce.supports[{support_card_id, level}]（另附 slot / name / type 便于核对）与按类型多数定的 main_attr。"""
    supports = [{"slot": s["slot"], "support_card_id": s["support_card_id"], "level": s["level"], "name": s["name"], "type": s["type"]}
                for s in fields.get("supports") or []]  # fmt: skip
    return {"supports": supports or None, "main_attr": fields.get("main_attr")}


def _consult_observation(fields: dict) -> dict:
    """read_consult → 相談 shelf[{index, kind, card_id / drink_id, upgrade, price, sale, purchased, ...}]、services、rerolls_left（docs/llm-context.md 商店字段）。"""
    # 货架没完整出现（入场剧情、获得动画、详情弹窗盖住）时格子会读出幻影（实机第四局 r4 00092 剧情帧读出「私がスター+」）：
    # shelf 给 None，reduce 保留上一帧完整的货架，observe_complete 会等到货架出现
    shelf = fields.get("shelf") if fields.get("complete") is not False else None
    return {"shelf": shelf, "services": fields.get("services"), "rerolls_left": fields.get("rerolls_left"),
            "complete": fields.get("complete"), "drinks": fields.get("drinks"), "drink_capacity": fields.get("drink_capacity"),
            "stamina": fields.get("stamina"), "max_stamina": fields.get("max_stamina"), "produce_point": fields.get("produce_point")}  # fmt: skip


DECK_CARD_KEYS = ("index", "row", "col", "card_id", "name", "upgrade", "customized", "center", "dimmed")  # dimmed：压暗（强化对话框里 = 不能再强化）
DECK_MERGED_KEYS = ("deck_index", "screen")  # 多屏拼接的整副才有：全局序号、来自第几屏


def _deck_card_fields(c: dict) -> dict:
    return {k: c.get(k) for k in DECK_CARD_KEYS} | {k: c[k] for k in DECK_MERGED_KEYS if k in c}


def _deck_list_observation(fields: dict) -> dict:
    """read_deck_list / merge_deck_pages → 牌组一览页字段。deck 为 ProduceState.deck 格式（[{card_id, upgrade, customized}]，
    一览顺序 = deckProduceCardIndex），只在看到全部 deck_size 张且每张都认出时给出，否则 None（原因见 problems）。
    cards 为本屏（或拼接后）可见的卡，含位置，便于操作层点选。"""
    cards = [_deck_card_fields(c) for c in fields.get("cards") or []]
    return {"deck": fields.get("deck"), "deck_size": fields.get("deck_size"), "cards": cards if "cards" in fields else None,
            "complete": fields.get("complete"), "problems": fields.get("problems")}  # fmt: skip


def _card_dialog_observation(fields: dict) -> dict:
    """read_card_dialog（+ read_confirm_dialog）→ 删除 / 强化 / 换卡第 2 步页面字段。selected 是本页卡格 index
    （弹窗按牌组顺序排、不滚动时 = deckProduceCardIndex）；selected_card {card_id, name, upgrade} 由橙色角框与详情栏卡名互验，
    不一致时 None、agree=False；confirm 为确认弹窗 {kind, item, p_before, p_after, price, sale}（没有弹窗为 None）。"""
    cards = [_deck_card_fields(c) for c in fields.get("cards") or []]
    out = {"cards": cards, "selected": fields.get("selected"), "selected_card": fields.get("selected_card"),
           "agree": fields.get("agree"), "confirm": fields.get("confirm")}  # fmt: skip
    if "cancelable" in fields:
        out["cancelable"] = fields["cancelable"]  # D42：False = 必选（没有キャンセル），None = 按钮行没读到
    if "upgradable" in fields:
        out["upgradable"] = fields["upgradable"]  # 强化对话框：选中的卡能不能强化（详情框「強化できません / これ以上強化不可能」→ False）
    for k in ("deck_complete", "deck_screens"):
        if k in fields:
            out[k] = fields[k]  # 执行侧滑动读整副后（merge_dialog_screens）：cards 换成整副，complete / 屏数一起给
    return out


def _special_guidance_observation(fields: dict) -> dict:
    """read_special_guidance → 特別指導页（hiro.vision.customize 文档）：view（select 选卡 / menu 定制项）、cards[{index, row, col, card_id, name, upgrade,
    customized, available, center, customizations[{id, item_index, price, available, source}]}]（选卡画面；选中那张的定制项来自画面 source=screen，
    其余按主数据 source=master）、selected、selected_card、menu{items[{item_index, id, name, price, left, maxed, available, selected, center}], total_left}、
    cards_left、scrollable / at_top / complete。agree 保留（恒为 None）。"""
    keys = ("view", "cards", "selected", "selected_card", "menu", "cards_left", "scrollable", "at_top", "complete", "produce_point", "stamina", "max_stamina")
    out = {k: fields.get(k) for k in keys}
    if out["menu"]:
        out["menu"] = {k: v for k, v in out["menu"].items() if k != "y"}
    out["agree"] = None
    return out


def _pdrink_max_observation(fields: dict) -> dict:
    """read_pdrink_max → 饮料溢出页：held[{index, drink_id, name, y, method}]（从上到下，只含看到的行）、incoming（最下面那瓶新饮料）、
    incomings（全部新饮料，一次可能两瓶）、select_left（「あと N個選択」）、
    complete（列表滚到底 / 不滚动；False 时下面可能还有没看到的已有饮料）。
    格式见 docs/llm-context.md「PDRINK_MAX」；认不出的瓶 drink_id / name 为 None。"""
    keep = ("index", "drink_id", "name", "y", "center", "method")
    # partial / candidates：最后一行被视口切掉时才有（D41，见 items.read_pdrink_max）
    held = [{**{k: d.get(k) for k in keep}, **{k: d[k] for k in ("partial", "candidates") if k in d}} for d in fields.get("held") or []]
    inc = fields.get("incoming")
    return {"held": held if "held" in fields else None, "incoming": {k: inc.get(k) for k in keep} if inc else None,
            "incomings": [{k: d.get(k) for k in keep} for d in fields.get("incomings") or []] if "incomings" in fields else None,
            "select_left": fields.get("select_left"), "complete": fields.get("complete")}  # fmt: skip


# 页面地图（hiro.vision.pages，docs/pages-hif.md）：识别字段原样透传，只取登记过的键
MAP_PAGE_KEYS = {'DIALOG': ('kind', 'title', 'danger', 'confirm'), 'ACQUIRE': ('kind', 'card_id', 'drink_id', 'pitem_id', 'pitem_ids', 'name', 'upgrade', 'agree'), 'STAR_GAIN': ('gain', 'star', 'star_shown'), 'HIF_EXAM_BRIEFING': ('exam_no', 'rank_line', 'participants', 'star', 'star_ok', 'params', 'multiplier_pct', 'complete', 'finals'), 'PITEM_CUSTOMIZE': (), 'PITEM_CUSTOMIZE_DONE': (), 'MENU': ('kind',), 'UPGRADE_RESULT': ('name', 'card_id', 'upgrade'), 'TRANSITION': ('kind',), 'GIFT': ('bag',), 'PRODUCE_LOG': ('close',), 'CUSTOMIZE_RESULT': ('name', 'card_id', 'upgrade'), 'DELETE_RESULT': ('cards', 'text'), 'HIF_FINALS_START': ('selected_memory', 'qualify_memories'), 'HIF_FINALS_RANKING': ('kind', 'score', 'round_score', 'entries', 'next', 'my_score', 'rank', 'finals_round', 'round_result', 'retry', 'my_gain', 'final', 'outcome', 'passed'), 'HIF_ROUND_INTERVAL': ('stamina', 'max_stamina', 'produce_point', 'interval', 'complete', 'dialog')}


def _map_page_adapter(page: str):
    def adapt(fields: dict) -> dict:
        return {k: fields.get(k) for k in MAP_PAGE_KEYS[page]}

    return adapt


def _change1_observation(fields: dict) -> dict:
    """deck.read_change_candidates → 换卡第 1 步候选（格式同 SELECT_CARD，另带 index = slot）。"""
    keep = ("index", "slot", "card_id", "name", "upgrade", "recommended", "center")
    return {"candidates": [{k: c.get(k) for k in keep} for c in fields.get("candidates") or []], "recommended": fields.get("recommended"),
            "rerolls_left": fields.get("rerolls_left"), "excludes_left": fields.get("excludes_left"), "complete": fields.get("complete")}  # fmt: skip


# 场景 → (原始识别字段 → 契约字段) 的转换；不在表里的页面只有 page，没有 fields
FIELD_ADAPTERS = {
    "ACTION_SELECT": produce_observation,
    "HIF_EXAM": _hif_exam_observation,
    "HIF_EXAM_RESULT": _hif_result_observation,
    "SELECT_CARD": _select_card_observation,
    "SELECT_DRINK": _select_drink_observation,
    "CONSULT": _consult_observation,
    "SPECIAL_GUIDANCE": _special_guidance_observation,
    "STUDY": _event_observation,
    "OUTING": _event_observation,
    "SELECT_PITEM": _select_pitem_observation,
    "PRODUCE_START_CONFIRM": _start_confirm_observation,
    "DECK_LIST": _deck_list_observation,
    "SKILL_CARD_REMOVAL": _card_dialog_observation,
    "SKILL_CARD_ENHANCE": _card_dialog_observation,
    "SKILL_CARD_CHANGE_2": _card_dialog_observation,
    "SKILL_CARD_CHANGE_1": _change1_observation,
    "PDRINK_MAX": _pdrink_max_observation,
    **{page: _map_page_adapter(page) for page in MAP_PAGE_KEYS},
}

# ---------------------------------------------------------------- 字段级 FieldMeta
# 方法：digits = 游戏字体比对（没把握时退回 ddddocr）；ocr = Apple Vision；template = 图标 / 卡面 / 状态图标库相关；
# kaa = KAA 的识别函数；derived = 由其他字段推出
FIELD_METHODS: dict[str, dict[str, str]] = {
    "ACTION_SELECT": {"mode": "derived", "step": "derived", "stamina": "ocr", "max_stamina": "ocr", "produce_point": "ocr",
                      "params": "digits", "growth_permil": "ocr", "star": "digits", "param_cap": "ocr", "options": "ocr",
                      "complete": "derived", "advice": "ocr", "drinks": "template", "drink_capacity": "template"},
    "HIF_EXAM": {"remaining_turns": "digits", "hp": "digits", "genki": "digits", "score": "digits", "pitem_charges": "digits",
                 "hand": "template", "drinks": "template", "pitems": "template", "motivation": "template", "impression": "template",
                 "buffs": "template", "drink_slots": "template", "attr": "ocr", "multiplier_pct": "ocr", "rank_line": "ocr", "participants": "ocr",
                 "unrecognized_cards": "derived", "exam_label": "derived"},
    "HIF_EXAM_RESULT": {"score": "ocr", "rank": "derived", "passed": "ocr", "exam_no": "ocr", "rank_line": "ocr",
                        "participants": "ocr", "entries": "ocr", "retry": "ocr"},
    "SELECT_CARD": {"candidates": "kaa", "recommended": "kaa", "rerolls_left": "ocr", "excludes_left": "ocr"},
    "SELECT_DRINK": {"candidates": "template", "held": "template", "full": "ocr"},
    "STUDY": {"choices": "ocr", "selected": "derived", "effect": "ocr", "complete": "derived"},
    "OUTING": {"choices": "ocr", "selected": "derived", "effect": "ocr", "complete": "derived"},
    "SELECT_PITEM": {"pitems": "template", "selected": "derived", "confirm_enabled": "kaa"},
    "PRODUCE_START_CONFIRM": {"supports": "template", "main_attr": "derived"},
    "CONSULT": {"shelf": "template", "services": "ocr", "rerolls_left": "ocr", "complete": "ocr", "drinks": "template", "drink_capacity": "template",
                "stamina": "ocr", "max_stamina": "ocr", "produce_point": "ocr"},
    "DECK_LIST": {"deck": "template", "deck_size": "ocr", "cards": "template", "complete": "derived", "problems": "derived"},
    **{page: {"cards": "template", "selected": "template", "selected_card": "derived", "agree": "derived", "confirm": "ocr",
              "deck_complete": "derived", "deck_screens": "derived"}
       for page in ("SKILL_CARD_REMOVAL", "SKILL_CARD_ENHANCE", "SKILL_CARD_CHANGE_2")},
    "SKILL_CARD_REMOVAL": {"cards": "template", "selected": "template", "selected_card": "derived", "agree": "derived", "confirm": "ocr",
                           "deck_complete": "derived", "deck_screens": "derived", "cancelable": "ocr"},
    "SKILL_CARD_ENHANCE": {"cards": "template", "selected": "template", "selected_card": "derived", "agree": "derived", "confirm": "ocr", "upgradable": "ocr",
                           "deck_complete": "derived", "deck_screens": "derived", "cancelable": "ocr"},
    "SKILL_CARD_CHANGE_1": {"candidates": "kaa", "recommended": "ocr", "rerolls_left": "ocr", "excludes_left": "ocr", "complete": "derived"},
    "PDRINK_MAX": {"held": "ocr", "incoming": "ocr", "incomings": "ocr", "select_left": "ocr", "complete": "template"},
    "SPECIAL_GUIDANCE": {"cards": "template", "selected": "template", "selected_card": "ocr", "agree": "template", "scrollable": "template", "complete": "template",
                         "view": "ocr", "menu": "ocr", "cards_left": "ocr", "at_top": "template", "produce_point": "ocr", "stamina": "ocr", "max_stamina": "ocr"},
    "DIALOG": {"kind": "ocr", "title": "ocr", "danger": "ocr", "confirm": "ocr"}, "ACQUIRE": {"kind": "ocr", "card_id": "ocr", "drink_id": "ocr", "pitem_id": "ocr",
    "pitem_ids": "ocr", "name": "ocr", "upgrade": "ocr", "agree": "template"}, "STAR_GAIN": {"gain": "ocr", "star": "ocr", "star_shown": "ocr"},
    "HIF_EXAM_BRIEFING": {"exam_no": "ocr", "rank_line": "ocr", "participants": "ocr", "star": "ocr", "star_ok": "ocr", "params": "ocr",
                          "multiplier_pct": "ocr", "complete": "ocr", "finals": "ocr"},
    "MENU": {"kind": "ocr"}, "UPGRADE_RESULT": {"name": "ocr", "card_id": "ocr", "upgrade": "ocr"}, "TRANSITION": {"kind": "ocr"}, "GIFT": {"bag": "template"}, "PRODUCE_LOG": {"close": "ocr"}, "CUSTOMIZE_RESULT": {"name": "ocr", "card_id": "ocr", "upgrade": "ocr"}, "DELETE_RESULT": {"cards": "ocr", "text": "ocr"}, "HIF_FINALS_START": {"selected_memory": "ocr", "qualify_memories": "ocr"}, "HIF_FINALS_RANKING": {"kind": "ocr", "score": "ocr", "round_score": "ocr", "entries": "ocr", "next": "ocr", "my_score": "ocr", "rank": "derived", "finals_round": "derived",
                           "round_result": "ocr", "retry": "ocr", "my_gain": "ocr", "final": "derived", "outcome": "ocr", "passed": "derived"},
    "HIF_ROUND_INTERVAL": {"stamina": "ocr", "max_stamina": "ocr", "produce_point": "ocr", "interval": "template", "complete": "derived", "dialog": "ocr"},
}  # fmt: skip


def _min(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(min(values), 3) if values else None


def _hif_exam_confidence(raw: dict, fields: dict) -> dict[str, tuple[float | None, str | None]]:
    """识别分数 → 置信度。卡名栏 OCR 精确命中数据库的卡记 1.0，否则用卡面相关分。"""
    out: dict[str, tuple[float | None, str | None]] = {}
    hand = raw.get("hand") or []
    if hand:
        notes = [f"{raw.get('unrecognized_cards')} 张未认出"] if raw.get("unrecognized_cards") else []
        overruled = [f"{c['art_name']}→{c['name']}" for c in hand if c.get("art_name")]
        if overruled:
            notes.append("卡名否决卡面：" + "、".join(overruled))
        partial = [str(c["slot"]) for c in hand if c.get("badge_score") is None and c.get("badge_text")]
        if partial:
            notes.append(f"槽位 {','.join(partial)} 的徽章读不全（被下一张卡盖住或有字认不出），badge_score 为 None")
        # 卡置信度：卡面与卡名一致 1.0；以卡名为准 0.9（前缀命中 0.7）；只看卡面为相关分；没认出 0。旧数据没有 confidence 键时沿用旧算法
        conf = _min(c["confidence"] if "confidence" in c else (1.0 if c.get("source") == "name" else c.get("art_score")) for c in hand)
        out["hand"] = (conf, "；".join(notes) or None)
    votes = raw.get("multiplier_votes")
    if votes and votes[0] != votes[1]:
        note = (f"倍率两种读法不一致 {votes}（OCR / ddddocr，回合转盘箭头盖住首位），"
                + ("取该属性上次一致的读数" if raw.get("multiplier_pct") is not None else "不给"))
        conf = 0.7 if raw.get("multiplier_pct") is not None else None
        out["multiplier_pct"] = out["multiplier_permil"] = (conf, note)
    if raw.get("hand_layout") in UNSTABLE_LAYOUT:
        out["hand"] = (0.0, UNSTABLE_LAYOUT[raw["hand_layout"]])
    read = raw.get("my_score_read") or {}
    if read:  # 分数：字体比对 1.0；OCR 兜底与字体一致 0.8–0.9，OCR 补位 0.6（见 digits.read_score）
        out["score"] = (read.get("confidence"), read.get("note"))
    out["drinks"] = (_min(d.get("score") for d in raw.get("drinks") or [] if d), None)
    out["pitems"] = (_min(p.get("score") for p in raw.get("pitems") or []), None)
    statuses = raw.get("statuses") or []
    for key, name in STATUS_FIELD.items():
        hit = next((e for e in statuses if e.get("key") == key), None)
        if hit is not None:
            out[name] = (hit.get("score"), None)
        elif fields.get(name) == 0:
            out[name] = (None, "状态栏各槽均已认出且无此图标")
    if statuses:
        out["buffs"] = (_min(e.get("score") for e in statuses if e.get("key") not in STATUS_FIELD), None)
    return out


def _select_drink_confidence(raw: dict, fields: dict) -> dict:
    out = {"candidates": (_min(o.get("score") for o in raw.get("options") or []), None)}
    held = raw.get("held") or []
    unknown = [d["index"] for d in held if d.get("drink_id") is None]
    out["held"] = (_min(d.get("confidence") for d in held), f"底栏第 {unknown} 格图标没认出" if unknown else None)
    return out


def _deck_list_confidence(raw: dict, fields: dict) -> dict:
    note = "；".join(raw.get("problems") or []) or None
    plus = "强化只分 0 / 1：二次强化（++）的缩略图还没有样本，看到「+」记 1"
    return {"complete": (None, note), "deck": (1.0, plus), "cards": (None, plus)}


def _hif_result_confidence(raw: dict, fields: dict) -> dict:
    out = {}
    if raw.get("passed_derived"):
        out["passed"] = (None, "玩家行的「突破 / 敗退」还没出现（名单入场动画），按该行在突破线横幅之上 / 之下推出")
    votes = raw.get("my_score_votes")
    if votes:
        agree = sum(1 for v in votes if v is not None and v == str(fields.get("score")))
        out["score"] = (round(agree / 3, 2), None if agree == 3 else f"三次读数 {votes}（名单 OCR / 单格放大 OCR / ddddocr），取多数")
    return out


def _consult_confidence(raw: dict, fields: dict) -> dict:
    if raw.get("complete") is False:
        return {"complete": (None, "货架没有完整出现（剧情 / 获得动画 / 弹窗盖住），shelf 给 None")}
    return {}


FIELD_CONFIDENCE = {"CONSULT": _consult_confidence, "HIF_EXAM_RESULT": _hif_result_confidence, "HIF_EXAM": _hif_exam_confidence, "SELECT_DRINK": _select_drink_confidence, "DECK_LIST": _deck_list_confidence}


FIELD_META_FNS: dict = {}


def register_adapter(scene: str, fn, meta_fn=None) -> None:
    """注册某个页面的字段转换（以及可选的字段元信息 fn(scene, raw, fields) -> dict[str, FieldMeta]）。
    接入自己的识别器、或给新页面加适配时用；内置的 H.I.F 页面已在 FIELD_ADAPTERS 里。"""
    FIELD_ADAPTERS[scene] = fn
    if meta_fn is not None:
        FIELD_META_FNS[scene] = meta_fn


def field_meta(scene: str, raw: dict, fields: dict) -> dict:
    """每个读到的字段的 FieldMeta：方法按字段登记，置信度取识别分数（有的话）。"""
    from hiro.contracts import FieldMeta

    if scene in FIELD_META_FNS:
        return FIELD_META_FNS[scene](scene, raw, fields)
    methods = FIELD_METHODS.get(scene, {})
    if scene == "HIF_EXAM_RESULT" and raw.get("passed_derived"):
        methods = {**methods, "passed": "derived"}
    conf = FIELD_CONFIDENCE[scene](raw, fields) if scene in FIELD_CONFIDENCE else {}
    meta = {}
    for k, v in fields.items():
        c, note = conf.get(k, (None, None))
        if v is None and note is None:
            continue  # 没读到且没有说明；有说明的（如手牌在动画中给 None）照样记下原因
        meta[k] = FieldMeta(method=methods.get(k, "vision"), confidence=c, note=note)
    return meta


def observation(seq: int, t: float, scene: str, raw: dict | None, *, area: str = "unknown", screenshot: str | None = None,
                source: str = "screen", stable_frames: int = 1, timings_ms: dict | None = None, ready: bool = False):  # fmt: skip
    """识别原始输出（hiro.vision.extract 的 fields）→ hiro.contracts.Observation。

    raw 为 None 或带 error 时 fields 为空；ready=True 表示 raw 已经是契约字段（例如存档投影、考试循环自己拼的手牌），不再转换。
    每个读到的字段附 FieldMeta（方法与置信度按字段，见 FIELD_METHODS / FIELD_CONFIDENCE）。
    """
    from hiro.contracts import FIELD_CATALOG, FieldMeta, Observation, PageObs

    fields: dict = {}
    meta: dict = {}
    adapter = FIELD_ADAPTERS.get(scene)
    if ready and raw:
        fields = dict(raw)
        meta = {k: FieldMeta(method=source) for k, v in fields.items() if v is not None}
    elif adapter is not None and raw and "error" not in raw:
        try:
            fields = adapter(raw)
            meta = field_meta(scene, raw, fields)
        except Exception as e:  # 转换失败只记错误，不拖垮采集 / 主循环
            fields, meta = {}, {"_error": FieldMeta(method="vision", note=f"adapter: {e!r}")}
        for n in FIELD_CATALOG.get(scene, ()):
            fields.setdefault(n, None)  # 登记过、这帧没读到 → 显式 None（不猜）
    elif raw and "error" in raw:
        meta = {"_error": FieldMeta(method="vision", note=str(raw["error"]))}
    return Observation(
        seq=seq, t=t, page=PageObs(name=scene, area=area, stable_frames=stable_frames),
        fields=fields, field_meta=meta, screenshot=screenshot, source=source, timings_ms=dict(timings_ms or {}),
    )  # fmt: skip
