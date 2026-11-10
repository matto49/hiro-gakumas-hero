# 来源：digital-hiro src/hiro/state/knowledge.py @ c3fc27f（GPL-3.0-or-later）
"""社区攻略落成的结构化知识（出处见 brain/prompts/hif_produce.md 与 idols/campus_hiro.md）。

只放「按 ID 查得到的事实与评级」，打法说明留在提示词里。
"""

# ---- やる気（ロジック）卡牌评级 [もりし][game8] ----
# tier：S/A/B（社区原评级）；must：缺了分数上不去 [game8]（デイドリーミング 由决策-07 升为本战核心，用户 2026-10-31 批准）；
# loop：末段循环核心 [game8]；role：本战得分里的角色（ROLES）；protect：换卡 / 删卡时不动（社区没评级、但实机证明有用的卡）
YARUKI_CARDS = {
    "p_card-02-act-3_186": dict(name="せのびの魔法", tier="S", must=True, role="delayed", keep=2,
                                note="倒数第4–5回合用特製ハツボシエキス复制；定制优先元气；第二张也要（R2 +4.3 万 ± 1.6 万，决策-17 §3；不清零元気，两张能叠）"),
    "p_card-02-act-3_039": dict(name="届いて！", tier="S", must=True, loop=True, role="finisher", note="最后一回合打；定制优先火力，不必复制"),
    "p_card-02-men-3_040": dict(name="夢色リップ", tier=None, note="用户 2026-10-31 判断：不到必拿，有更好的卡（game8 曾列为道中必拿）"),
    "p_card-02-act-2_062": dict(name="きらきら紙吹雪", tier="S", loop=True, role="loop", note="末段连打+抽牌，帮最后一回合抽到届いて！"),
    "p_card-02-act-1_037": dict(name="デイドリーミング", tier="S", must=True, role="sustained",
                                note="本战持续得分核心（决策-07；夺冠局两层覆盖第 9–12 回合）；倒数第4回合用最强，与せのび 抢时机时优先せのび"),
    "p_card-02-men-3_002": dict(name="私がスター", tier="S", role="extend", note="追加 1 回合；道中容易拿到"),
    "p_card-00-men-3_003": dict(name="アイドル魂", tier="S", role="compress", note="防ハツボシエキス 的体力消耗增加；共通卡，不易拿到"),
    "p_card-00-men-3_005": dict(name="仕切り直し", tier="S", role="compress", note="抽牌+使用数，压缩牌组；共通卡，不易拿到"),
    "p_card-00-men-2_012": dict(name="アイドル宣言", tier="S", role="compress", note="抽牌+使用数，压缩牌组；共通卡，不易拿到"),
    "p_card-02-men-2_004": dict(name="ワクワクが止まらない", tier="A", role="motivation", note="开局入手定制非常强"),
    "p_card-02-men-2_111": dict(name="成長痛", tier="A", role="motivation", note="强但会生成眠気；末段循环里最多容忍 1 张眠気"),
    "p_card-02-act-3_045": dict(name="あのときの約束", tier="A", role="cash", note="普通地强"),
    "p_card-00-men-3_012": dict(name="テレビ出演", tier="B", role="genki", note="定制「使用数+1」后才强"),
    "p_card-00-men-3_011": dict(name="叶えたい夢", tier="B", role="genki", note="能攒元气"),
    "p_card-02-men-2_057": dict(name="ゆめみごこち", tier=None, loop=True, role="loop", note="末段循环，攒やる気 [game8]"),
    "p_card-02-act-2_049": dict(name="みんな大好き", tier=None, role="loop", note="PLv 低、火力牌少时的末段循环卡 [game8]"),
    "p_card-02-men-2_056": dict(name="止められない想い", tier=None, role="hp", note="广专属高相性：扣体力且不占行动 [seesaa]"),
    "p_card-02-men-2_052": dict(name="ひなたぼっこ", tier=None, role="hp", note="体力消耗大 [もりし]；广可用来强行压到半血 [seesaa]"),
    "p_card-02-men-1_031": dict(name="えいえいおー", tier=None, role="item", protect=True, keep=2, value=6.0,
                                note="广固有 P 道具的触发牌（エウレカ 靠它压血），第二张更稳；换卡 / 删卡保留 2 张（main8-r1 被换掉）；定制优先やる気+ [seesaa]"),
    "p_card-02-act-1_071": dict(name="がむしゃら", tier=None, role="cash", protect=True,
                                note="不清零元气的兑现牌（第 2 局选拔② 末回合兑现）；第 4 局本战被 KAA 盲换掉，换卡保护名单要有它"),
    "p_card-02-ido-3_105": dict(name="エウレカ！", tier=None, role="idol", protect=True,
                                note="Campus 広 固有卡：半血时按元气 40% 得分；删卡 / 换卡永远不动（决策-08）"),
    "p_card-00-sup-3_093": dict(name="紅葉ランニング", tier=None, role="compress", note="压缩效率高，やる気 推荐获得 [wiki]"),
}

# 本战得分里的角色（决策-07 方案 §2）：选拔③ 之前 finisher / delayed / sustained 各至少 1 张，loop 尽量 1–2 张
ROLES = {"finisher": "终结（届いて！）", "delayed": "延迟兑现（せのびの魔法）", "sustained": "持续兑现（デイドリーミング）",
         "loop": "循环 / 抽牌（紙吹雪、ゆめみごこち）", "compress": "压缩（仕切り直し、アイドル宣言、紅葉ランニング）", "extend": "加回合",
         "cash": "不清零兑现", "motivation": "やる気", "genki": "元気", "hp": "控血", "item": "道具触发", "idol": "偶像固有卡", "conflict": "冲突"}
CORE_ROLES = ("finisher", "delayed", "sustained", "loop")

# ---- 饮料：按主数据效果自动归类（用户 2026-11-02 第十六局复盘：取代 4 瓶的静态优先表） ----
# 分类与社区用法 [playbook「饮料」][もなみ][wiki][いおん ep07/08]；档位是 やる気（广）路线的持有价值，数字小的好。
# 这些都是默认参考（用户更正 2026-11-02）：完整对局模拟显著更好（> 2 个标准误）时可以调整，跨档也可以（hiro.brain.drinks）。
# 档位 0「优先」：第一瓶复制、前 BURST_MUST 瓶爆发——由 hiro.brain.drinks 按持有组合判断；这里给的是这一类的默认档位与同档内的默认顺序（order 小的先）。
DRINK_CLASSES = {
    "copy": dict(label="复制", tier=1, order=0, note="下一张主动卡再发动一次：高价值；复制哪张由考试预演按收益定（社区默认 せのび 倒数 4–5 回合，约束 / デイドリ / 紙吹雪 也可能）[もなみ][wiki]"),
    "draw": dict(label="抽牌", tier=1, order=1, note="抽牌找 せのび / 届いて；限量（喝早会转乱牌堆，用户批注）[もなみ][playbook]"),
    "action": dict(label="加行动", tier=1, order=2, note="使用数 +1：兑现回合多打一张 [playbook]"),
    "burst": dict(label="爆发", tier=1, order=3, note="参数上升量增加：配大额兑现 [playbook]"),
    "yaruki": dict(label="やる気", tier=2, order=0, note="やる気 路线的资源"),
    "fetch": dict(label="检索", tier=2, order=1, note="把固有卡拿到手里"),
    "card": dict(label="生成卡", tier=2, order=2, note="生成强化 SSR 卡"),
    "swap": dict(label="换手", tier=2, order=3, note="手牌全换：也会转乱牌堆，算进抽牌限量"),
    "upgrade": dict(label="强化手牌", tier=3, order=0, note="手牌レッスン中强化 + 少量回复"),
    "recover": dict(label="回复", tier=3, order=1, note="回体力"),
    "genki": dict(label="元気", tier=3, order=2, note="少量元気"),
    "param": dict(label="平加", tier=3, order=3, note="参数 +N"),
    "other": dict(label="其他", tier=3, order=4, note="未归类"),
    "impression": dict(label="好印象", tier=4, order=0, note="好印象 路线用，对 やる気 无用"),
    # 按当时的 好印象 加分（おしゃれハーブティー「好印象の100%分パラメータ上昇」）：一次性兑现、不叠加，好印象 高时喝才有值，不进 S0
    # （第二十五局 R2 开局被 S0 强制先喝，好印象 约 0，专家估 −2.7 万）
    "impression_cash": dict(label="好印象兑现", tier=4, order=1, note="按当时 好印象 加分：好印象 高时喝，不开局喝"),
    # 识别读不出的持有饮料（drink_id=None，第十八局选拔）：按中等价值算——不当空位，也不优先丢它，宁可先丢已知的差饮料（回复 / 好印象 / 超限抽牌）
    "unknown": dict(label="未知", tier=2, order=9, note="识别没认出：按中等价值"),
}
DRAW_CLASSES = ("draw", "swap")  # 算进抽牌限量的类
# 抽牌饮料默认上限（用户：最多 1–2 瓶、不能占满饮料栏；模拟显著更好时可调整）：选拔 1 瓶（もなみ：センブリ 带 1 瓶找 せのび）；本战 2 瓶（playbook：R1 一爆发 + 两抽牌、R2 两爆发 + 两抽牌）
DRAW_CAP = {"selection": 1, "r1": 2, "r2": 2}
# 必留的爆发瓶数（用户：至少 1 瓶给兑现回合；R2 两瓶爆发 [playbook]）
BURST_MUST = {"selection": 1, "r1": 1, "r2": 2}


# 纯加数值的饮料（用户 2026-11-03：「这种数值越早叠，总是越正向的」）：永久叠加、没有时机依赖，开局就喝（exam_policy S0、续打 drink_timing_ok）
STAT_DRINK_CLASSES = frozenset({"yaruki", "genki", "impression", "param"})


def drink_class(drink_id: str | None) -> str:
    """饮料 → DRINK_CLASSES 的键。按 master 的 produceDrinkEffectIds（效果类型）归类；没认出（None）时 unknown，master 查不到时 other。"""
    if not drink_id:
        return "unknown"
    return _drink_class(drink_id)


def _drink_class(drink_id: str) -> str:
    if drink_id in _DRINK_CLASS_CACHE:
        return _DRINK_CLASS_CACHE[drink_id]
    try:
        from hiro.state.context import catalog

        row = catalog().by_id("ProduceDrink", drink_id)[0]
        effs = " ".join(row["produceDrinkEffectIds"])
        text = "".join(d.get("text") or "" for d in row.get("produceDescriptions") or [])
    except Exception:
        _DRINK_CLASS_CACHE[drink_id] = "other"
        return "other"
    if "card_search_effect_play_count_buff" in effs:
        k = "copy"
    elif "playable_value_add" in effs:
        k = "action"
    elif "hand_grave_count_card_draw" in effs:
        k = "swap"
    elif "card_draw" in effs:
        k = "draw"
    elif "lesson_value_multiple" in effs:
        k = "burst"
    elif "lesson_depend_exam_review" in effs:
        k = "impression_cash"
    elif "review" in effs or "好印象" in text:
        k = "impression"
    elif "card_play_aggressive" in effs or "やる気" in text:
        k = "yaruki"
    elif "card_move" in effs:
        k = "fetch"
    elif "card_create" in effs or "force_play_card" in effs:
        k = "card"
    elif "card_upgrade" in effs:
        k = "upgrade"
    elif "stamina_recover" in effs:
        k = "recover"
    elif "exam_block" in effs:
        k = "genki"
    elif "exam_lesson-" in effs:
        k = "param"
    else:
        k = "other"
    _DRINK_CLASS_CACHE[drink_id] = k
    return k


_DRINK_CLASS_CACHE: dict = {}


def drink_label(drink_id: str | None) -> str:
    return DRINK_CLASSES[drink_class(drink_id)]["label"]


# ---- 考试 ----
# 选拔考试：属性奖励 / 星星奖励拿满的分数（社区公式反推，见 gakumas_produce.community_rules）
SELECTION_TARGETS = {1: (10463, 14001), 2: (80611, 146364), 3: (204894, 390910)}
FINALS_WIN_LINE = 1_110_000  # 优胜评价点目安（R1×1.2 + R2）[seesaa]

# 回合属性分配 [seesaa]：审查基准类型 → 场次 → (流1, 流2, 流3) 回合数
TURN_SPLIT = {
    "balance": {"s1": (5, 3, 2), "s2": (5, 4, 3), "s3": (5, 4, 3), "r1": (4, 3, 2), "r2": (5, 4, 3)},
    "spike": {"s1": (5, 3, 2), "s2": (6, 3, 3), "s3": (6, 3, 3), "r1": (4, 3, 2), "r2": (6, 3, 3)},
}
# 偶像的审查基准类型与流行顺序 [seesaa]（キャラ单位，与卡面版本无关）
IDOL_TREND = {"shro": ("balance", ("Vo", "Da", "Vi"))}


def idol_trend(idol_card_id: str) -> tuple[str, tuple[str, str, str]]:
    key = idol_card_id.split("-")[1]
    return IDOL_TREND[key]


# ---- 日程模板 [もりし]（选拔 / 本战）：位置 → 首选行动；None 表示看局面 ----
# 选拔第 8、14 天：考试后体力 < OUTING_HP_LINE 选外出回体力，否则差し入れ。
SCHEDULE_TEMPLATE = {
    "produce-007": {1: "gift", 2: "training", 3: "class", 4: "training", 5: "outing", 6: "class", 8: None, 9: "training",
                    10: "class", 11: "training", 12: "shop", 14: None, 15: "training", 16: "gift", 17: "class",
                    18: "training", 19: "special_guidance"},
    "produce-008": {1: "class", 2: "training", 3: "gift", 4: "class", 5: "training", 6: "shop"},
}  # fmt: skip
OUTING_HP_LINE = 21  # SP 课程 8 + 授業 5 + 余量：考试后体力至少 21 [もりし]


def template_option(mode: str, step: int, stamina: int) -> str | None:
    opt = SCHEDULE_TEMPLATE.get(mode, {}).get(step)
    if opt is None and step in SCHEDULE_TEMPLATE.get(mode, {}):
        return "outing" if stamina < OUTING_HP_LINE else "gift"
    return opt


# ---- 授業 / 外出的选项排列（实机核实，2026-10-31 授業 #100/#145、外出 #125）----
# 授業：第一项 = おすすめ効果A（ロジック为好印象：选它之后给出的候选全是好印象卡），第二项 = B（やる気），第三项 = 眠気档。
# 外出：第一项 = 付 50P，第二项 = 眠気档（トラブル追加），第三项 = 「なし」。识别给出选项文字前按这个位置选。
STUDY_LAYOUT = ("A", "B", "trouble")
OUTING_LAYOUT = ("paid", "trouble", "free")
EFFECT_JA = {"ExamCardPlayAggressive": "やる気", "ExamReview": "好印象", "ExamParameterBuff": "好調",
             "ExamLessonBuff": "集中", "ExamConcentration": "強気", "ExamFullPower": "全力"}  # fmt: skip


# ---- 特別指導（定制）优先表 [もりし][seesaa][game8]：(卡 ID, 卡名, 定制 ID 里要找的关键字（按优先顺序）, 依据) ----
# 定制 ID（master ProduceCardCustomize）带效果类型：lesson_depend_block_add = 按元气得分比例提高（社区「パラメータ+」），
# initial_add = 开局入手，aggressive_add = やる気+，playable_value_add = 使用数+1，cost_reduce = 降费，
# せのびの魔法 的 wrapper-a = 元気+4（exam_block）、wrapper-b = やる気+2。
# 顺序按离线反事实（决策-11 §5，第十四局 R1 开局、社区续打 200 局配对，每一级的收益）：
#   えいえいおー やる気 1/2/3 级 +2.5k/+6.5k/+19.3k（只有做满才值，40+40+70P）、届いて 1 级 +10.7k（100P）、
#   紙吹雪 第 2 级 +6.9k（70P）、デイドリ 第 2 级（70%→80%）+3.4k（40P）。总控 2026-11-02：えいえいおー 的 やる気 定制优先做满。
CUSTOMIZE_PRIORITY = [
    ("p_card-02-men-1_031", "えいえいおー", ["aggressive_add"], "定制 やる気+，做满 3 级（只有满级才值：+19.3k）[seesaa][决策-11]"),
    ("p_card-02-act-3_039", "届いて！", ["lesson_depend_block_add"], "火力定制（元気%）优先 [もりし][seesaa]"),
    ("p_card-02-act-2_062", "きらきら紙吹雪", ["lesson_depend_block_add"], "パラメータ+ 定制（110% → 130% → 170%）[もりし]"),
    ("p_card-02-act-1_037", "デイドリーミング", ["p_card-02-act-1_037-enc"], "持续效果定制（60% → 80%）；本战持续核心 [もりし]"),
    ("p_card-02-act-3_186", "せのびの魔法", ["exam_block"], "元気定制优先 [もりし]"),
    ("p_card-02-men-2_004", "ワクワクが止まらない", ["initial_add"], "开局入手定制非常强 [もりし][seesaa]"),
    ("p_card-00-men-3_012", "テレビ出演", ["playable_value_add"], "定制「使用数+1」后才强 [もりし]"),
    ("p_card-00-men-3_003", "アイドル魂", ["cost_reduce"], "降费定制，省体力 [もりし]"),
]
# 特別指導的游戏规则（第十五局实机帧 reports/live/calib/20261102-103948-特別指導）：一次特別指導最多定制 2 张卡（「カスタマイズ可能スキルカード あと N 枚」），
# 每张卡在菜单里最多做 3 级（「合計あと3回」，可分到不同项），实行后这张卡本次就结束了，回不来升级。所以多级的目标要在一个动作里一次做完（levels）。
# 卡 → (目标级数, 是否必须做满才做)。えいえいおー：やる気+ 每级 40P，1/2/3 级 +2.5k/+6.5k/+19.3k，只有满级才值；
# 紙吹雪 第 2 级（130% → 170%）+6.9k、デイドリ 第 2 级（70% → 80%）+3.4k（决策-11 §5）。没列的按 1 级
CUSTOMIZE_LEVELS = {"p_card-02-men-1_031": (3, True), "p_card-02-act-2_062": (2, False), "p_card-02-act-1_037": (2, False)}


# ---- 编成（支援卡 / 记忆）[seesaa][もりし][もなみ][game8] ----
# 支援卡：主属性 4 张（4-2-0 / 4-1-1 编成），4 张里尽量多张带 SP 课程发生率（「全てのレッスンで、その○○レッスンだけを選択する」[seesaa]、
# 「メインステータス4枚のうち2~3枚くらいがSPレッスン枠で安定」[もりし]）；主属性由编成决定（决策层主属性也跟着它）。
SUPPORT_MAIN_TARGET = 4
# ---- 选拔的 P 点预算（用户 2026-11-02 第十六局复盘：相談 + 特別指導 统一预算；选拔 P 点带不进本战）----
# 日程模板（wiki 选拔日程 + もなみ / ep07）：相談只在第 12 天去；第 16 天差し入れ（+80P、SR+ 饮料、卡），第 19 天特別指導。
# wiki：选拔期相談有 5 次出现机会（第 1 / 5 / 12 / 16 / 19 天），其余 4 天和 差し入れ / おでかけ / 特別指導 互斥，模板不去（理由见决策-14）。
SELECTION_CONSULT_DAYS = (12,)
SELECTION_GUIDANCE_DAY = 19
# 第 13–18 天预计的 P 点进出（第十六局实测：第 12 天相談后 165P → 第 19 天 255P，净 +90）：
# 课程（公開レッスン，选拔 6 次共 180P [wiki]）第 15、18 天各 30；差し入れ 第 16 天 +80；体力不够时第 14 天外出 −50（もなみ：体力 < 21 就外出）
SELECTION_P_FLOW = {14: -50, 15: 30, 16: 80, 18: 30}


def selection_p_income(after_step: int, before_step: int = SELECTION_GUIDANCE_DAY) -> int:
    """after_step（不含）到 before_step（不含）之间预计的 P 点净进出。"""
    return sum(v for d, v in SELECTION_P_FLOW.items() if after_step < d < before_step)


# ---- 本战相談：留 P 的机会成本（用户 2026-11-02：本战相談买不买饮料按价值，不用瓶数阈值）----
# 留到轮间的 P 能换多少 R2 分：第十六局轮间 B3 逐项评估（启发式 200 局配对）——回体力到 24 +318/P、强化 せのび +168/P、
# 补卡到 22（20 张牌组时 イメトレ / ゆるふわ）+124–158/P；之后的选项（多回体力、弱饮料、废卡）≤ 50/P 或为负。
# 所以按分段计：轮间需要量（200 + 每差一张到 22 张 50P）以内每 P 150 分，超出部分每 P 20 分。R1 结束按分数给 P（第十六局 +198，约 200）。
FINALS_P_VALUE = {"r1_reward": 200, "need_base": 200, "per_card": 50, "high": 150, "low": 20}
# 本战倍率没有时的参考（第十六局本战 finals_context）：R1 的分和 R2 的分在同一刻度上，才能和留 P 的价值比
FINALS_REFERENCE_PERMIL = {"Vo": 37260, "Da": 33410, "Vi": 23230}


def interval_p_value(p_at_interval: float, deck_size: int | None) -> float:
    """轮间有 p_at_interval 点 P 时，这些 P 预计能换多少 R2 分（FINALS_P_VALUE 的分段线性）。"""
    v = FINALS_P_VALUE
    need = v["need_base"] + v["per_card"] * max(0, 22 - (deck_size if deck_size is not None else 22))
    p = max(0.0, p_at_interval)
    return v["high"] * min(p, need) + v["low"] * max(0.0, p - need)


# 记忆：社区点名要带进来的卡与定制（定制关键字见 CUSTOMIZE_PRIORITY 的说明），权重越大越重要。
MEMORY_WANT = [  # 决策-07：先按 game8（やる気型持ち込みメモリー）准备，用户回忆编成待确认
    ("p_card-02-act-1_037", "デイドリーミング", 5, ["initial_add", "p_card-02-act-1_037-enc"], "开局入手 / 持续效果定制；本战持续核心 [game8][もりし]"),
    ("p_card-02-act-2_062", "きらきら紙吹雪", 4, ["lesson_depend_block_add", "initial_add"], "定制后是循环核心；开局入手让选拔①轻松 [game8][もりし]"),
    ("p_card-00-men-3_005", "仕切り直し", 3, [], "SSR 共通卡，选拔里很难拿到，优先带入 [game8]"),
    ("p_card-00-men-3_003", "アイドル魂", 3, ["cost_reduce"], "SSR 共通卡，选拔里很难拿到，优先带入；降费定制 [game8][もりし]"),
    ("p_card-02-act-3_039", "届いて！", 2, ["lesson_depend_block_add"], "パラメータ+ 定制；必拿，但在候选池里能找 [もりし]"),
    ("p_card-02-act-3_186", "せのびの魔法", 2, ["exam_block"], "元気定制；必拿，但在候选池里能找 [もりし]"),
    ("p_card-02-men-2_004", "ワクワクが止まらない", 2, ["initial_add"], "开局入手定制非常强 [もりし][seesaa][もなみ]"),
    ("p_card-00-men-2_012", "アイドル宣言", 2, [], "抽牌 + 使用数；不在候选池，适合记忆带入 [もりし]"),
]


# ---- 本战用哪份选拔回忆（選抜試験メモリー）[wiki][game8][もりし] ----
# 回忆保存选拔通过时的状态：属性、星星（スター性）、牌组、P 道具；P 点、饮料、再挑戦次数、体力不继承（本战满体力开始）[wiki]。
# 分数受星星补正与 H.I.F 加成影响很大，回忆弱就很难赢，赢不了就回选拔重做回忆 [wiki][game8]。
STAR_RANK_LINES = (0, 20, 50, 100, 200, 300, 400, 600, 800, 1000, 1200)  # スター性ランク 0–10 的下限 [wiki]
FINALS_STAR_LINES = {"r1": 600, "r2": 800}  # 本战审查基准的星星 [wiki]；达标时倍率 2–3 倍，未达约 1.85–2 倍
# 本战属性审查线（バランス型，wiki 「審査基準△〇閾値」本戦，未知的为 None）：任何一项 △ / × 都会压低另外两项的加成
FINALS_PARAM_LINES = {"balance": {"流1": (None, 1926), "流2": (None, None), "流3": (1088, 1092)}}
FINALS_DECK_TARGET = 22  # 轮间会发 P 道具，牌组不足 22 张时混入基本卡 [game8]；广（やる気）可不勉强 [seesaa]


# ---- 换卡（授業「セレクトチェンジ」/ 轮间チェンジ）用的卡牌价值 ----
# 冲突卡：与やる気 广的打法直接冲突，不拿、优先删 / 换出（决策-07，用户 2026-10-31 批准）
CONFLICT_CARDS = {
    "p_card-00-men-2_014": "ハイテンション：打出后 2 回合元気增加无效，与やる気 路线冲突（第 5 局本战多次当废牌，模拟器也因它无法预演）",
}
# 偏离路线：好印象系，对やる気 广收益小（第 4、5 局本战 LLM 反复判「对这套构筑没用」）
OFF_PLAN_CARDS = {
    "p_card-02-men-1_035": "幸せのおまじない（好印象）",
    "p_card-02-men-3_004": "星屑センセーション（好印象）",
    "p_card-02-act-3_050": "輝くキミへ（好印象）",
    "p_card-02-men-1_032": "思い出し笑い（好印象；第六局牌组里有 3 种强化度，协调 4a 判为冲突）",
    "p_card-02-men-1_009": "励まし（好印象 + やる気少量；第六局 LLM 判「好印象路线，不要」）",
    "p_card-02-men-2_060": "オトメゴコロ（やる気消費3 换好印象；用户 q11 批注：不应该拿）",
}
# 永不换出：必拿 + 本战得分核心（终结 / 延迟 / 持续 / 循环）[game8][もりし]；第 4 局本战 KAA 盲换第 0 张换走了 デイドリーミング
CORE_CARDS = {"p_card-02-act-3_039", "p_card-02-act-3_186", "p_card-02-act-1_037", "p_card-02-act-2_062"}


# 强化顺序（决策-07）：届いて！ > せのび > デイドリーミング > 紙吹雪，之后按社区评级
UPGRADE_ORDER = ["p_card-02-act-3_039", "p_card-02-act-3_186", "p_card-02-act-1_037", "p_card-02-act-2_062"]


def card_role(card_id: str | None) -> str | None:
    if not card_id:
        return None
    if card_id in CONFLICT_CARDS:
        return "conflict"
    return (YARUKI_CARDS.get(card_id) or {}).get("role")


def is_conflict(card_id: str | None) -> bool:
    """不拿、优先删 / 换出的卡：冲突卡 + 好印象系。"""
    return bool(card_id) and (card_id in CONFLICT_CARDS or card_id in OFF_PLAN_CARDS)


def protected_card_ids() -> set[str]:
    """换卡 / 删卡时不动的卡：核心、必拿、末段循环、S / A 级、protect、记忆想带的卡。执行侧 kaa_patches 用它。"""
    ids = {cid for cid, k in YARUKI_CARDS.items() if k.get("must") or k.get("loop") or k.get("protect") or k.get("tier") in ("S", "A")}
    return ids | CORE_CARDS | {m[0] for m in MEMORY_WANT}


def is_protected(card_id: str | None) -> bool:
    """保护名单 + 任何偶像固有卡（卡 ID 含 -ido-，换角色时也成立）。"""
    return bool(card_id) and (card_id in protected_card_ids() or "-ido-" in card_id)


# ---- 轮间体力目标（兜底规则用；有模拟器时回多少由模拟决定）----
# 用户 2026-11-01：体力不用回满。Campus 広 有被动 / 卡牌回血，エウレカ 要 HP ≤ 50% 才触发，所以刚过半血就够（例如 17/33）。
INTERVAL_HP_TARGET = {"shro": "half+1"}


def interval_hp_target(idol_id: str | None, max_hp: int) -> int:
    rule = INTERVAL_HP_TARGET.get((idol_id or "").split("-")[1] if idol_id and "-" in idol_id else "")
    return max_hp // 2 + 1 if rule == "half+1" else max_hp


# ---- 本战删卡（协调 4a 第八局分析 A5，按模拟取舍）----
# main8 R2 实际牌组（23 张）上逐张删除，模拟 R2 期望变化（启发式 200 局配对，reports/analysis/interval-sim-main8.md 同法）：
#   ノートの端の決意 +29,667±8,636、トキメキ +19,338±7,510、手書きのメッセージ +18,284±8,352、止められない想い +9,474±7,893；
#   気まぐれハート +2,488±8,412、夢色リップ −1,701±7,950（≈0，不删）；6 张全删 −46,729±7,205。
# 本战开局牌组不足 22 张时引擎会混入基本卡 [game8]，所以只在牌组多于 FINALS_DECK_TARGET 张时删，删到 22 为止。
FINALS_DELETE = ["p_card-02-men-3_041", "p_card-02-men-3_043", "p_card-02-men-3_044", "p_card-02-men-2_056"]
# 原来的「留最后一张好印象来源」（MIN_IMPRESSION_SOURCES，协调 4a）已去掉（用户 2026-11-03：「这个保护规则去了吧。我们还有别的卡也能拿好印象啊。」
# 第十九局换卡因它保住了 思い出し笑い+、换掉了 えいえいおー）。好印象来源交给选卡、考试预演自己权衡。


# ---- 小牌组（用户 2026-11-01，协调 4a 转达）----
# H.I.F 删牌 / 换牌机会多，小牌组是游戏重点支持的方向：不是一次性（レッスン中1回，打出后除外）的非核心牌一律不拿；
# 好印象系只拿一次性的；换牌 / 删牌同一个顺序（brain.deciders.cull_pool，用户 2026-11-03）：眠気等トラブル（只有删卡能选）> 非一次性的非核心牌 >
# 会塞眠気的非一次性牌 > 一次性的牌（按价值）> 核心牌的多余副本。
# 可以反复回到牌堆、但仍然要拿的核心牌（持续得分、终结、关键循环件）明确列在这里：
SMALL_DECK_CORE = {
    "p_card-02-act-2_062": "きらきら紙吹雪：循环兑现核心（定制后元気依存 170%）[game8][もりし]",
    "p_card-02-men-1_031": "えいえいおー：Campus 広 固有 P 道具的触发牌（エウレカ 控血）[seesaa]",
    "p_card-02-men-2_057": "ゆめみごこち：末段循环（使用数追加 + やる気）[game8]",
    "p_card-02-men-2_111": "成長痛：用户 2026-11-01「勉强能拿」——会回牌堆、生成眠気，但有再動（使用数追加 +1），同时加元気和やる気",
}
# 一次性、可以拿但价值很低的牌：选卡排在最后（有更好的就不拿）；拿到了不专门花删卡次数去删（用户 2026-11-01）
LOW_PICK_NO_DELETE = {
    "p_card-02-men-3_041": "ノートの端の決意：好印象 +3/+5、やる気 +3，没有再動，占一次行动，好印象对 やる気 路线没用",
}


# 删牌 / 换牌按「好印象系」处理的一次性牌：效果依赖好印象（用户 2026-10-31：夢色リップ 不到必拿；协调 4a 第九局列为应删）
CULL_AS_IMPRESSION = {"p_card-02-men-3_040": "夢色リップ：好印象 15 以上时好印象 1.1 倍"}


def core_keep(card_id: str) -> int:
    """可复用核心牌最多保留几张（えいえいおー 2 张，其余 1 张）：超过的副本按「可复用的非核心牌」处理。"""
    return (YARUKI_CARDS.get(card_id) or {}).get("keep") or 1


def is_once(card_id: str | None) -> bool | None:
    """打出后除外（レッスン中1回，master playMovePositionType=Lost）。查不到返回 None。"""
    if not card_id:
        return None
    from hiro.state.context import catalog

    try:
        return "Lost" in str(catalog().card(card_id, 0).get("playMovePositionType"))
    except Exception:
        return None


def small_deck_reject(card_id: str | None) -> str | None:
    """小牌组规则下不拿的原因（可复用的非核心牌）；允许拿时返回 None。眠気 / 基础卡另算。"""
    if not card_id or card_id in SMALL_DECK_CORE or "-ido-" in card_id or card_id in CORE_CARDS:
        return None
    if is_once(card_id) is False:
        name = (YARUKI_CARDS.get(card_id) or {}).get("name") or card_id
        return f"{name} 打出后会回到牌堆，不是核心牌（小牌组：可复用的非核心牌不拿）"
    return None
