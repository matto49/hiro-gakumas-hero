"""页面转移图与 mode 推断。页面名 = hiro.vision.scene 的场景名（KAA SceneType + HIF_EXAM / HIF_EXAM_RESULT + home / commu）。

转移图只拦「不可能直接发生」的跳变（例如 HIF_EXAM → ACTION_SELECT 中间必有结算），其余一律放行；
拦下的跳变由 reduce 防抖后接受。识别会话新增页面时在这里登记。
"""

TRANSIENT = {"unknown", "LOADING", "TRANSITION"}  # 转场 / 没认出：不改 page、不改字段

PRODUCE_PAGES = {
    "IDLE", "ACTION_SELECT", "PRACTICE", "EXAM", "STUDY", "OUTING", "CONSULT", "ALLOWANCE", "HIF_ROUND_INTERVAL",
    "SELECT_DRINK", "SELECT_CARD", "SELECT_PITEM", "SKILL_CARD_ENHANCE", "SKILL_CARD_REMOVAL", "SKILL_CARD_CHANGE_1",
    "SKILL_CARD_CHANGE_2", "INITIAL_DRINK_OR_CARD_SELECT", "PDRINK_MAX", "PDRINK_MAX_CONFIRM", "OUTING_STAMINA_MAX",
    "PRODUCE_END", "HIF_EXAM", "HIF_EXAM_RESULT", "DECK_LIST",
    "DIALOG", "MENU", "ACQUIRE", "STAR_GAIN", "HIF_EXAM_BRIEFING", "PITEM_CUSTOMIZE", "PITEM_CUSTOMIZE_DONE", "UPGRADE_RESULT",
    "SPECIAL_GUIDANCE", "HIF_FINALS_RANKING", "HIF_FINALS_START", "PRODUCE_LOG",
    # 识屏 ce615b6 快速判页：底部结果气泡。DELETE_RESULT（「A と B を削除しました」→ CARD_DELETE 事件，考试后自动删基础卡 / 相談删卡）、
    # CUSTOMIZE_RESULT：任何培育页面之间都可能出现，当覆盖层（HUB），事件照常记
    "DELETE_RESULT", "CUSTOMIZE_RESULT", "GIFT",
    "INITIAL_CHOICE", "EVENT_CHOICE",  # 事件选项页：随机事件 / 开局二选一，几乎在任何行动之后都可能出现，当枢纽
}  # fmt: skip
EXAM_PAGES = {"HIF_EXAM", "HIF_EXAM_RESULT"}
HUB = {
    "IDLE",
    "commu",
    "DIALOG",
    "MENU",
    "PRODUCE_LOG",
    "DELETE_RESULT",
    "CUSTOMIZE_RESULT",
    "INITIAL_CHOICE",
    "EVENT_CHOICE",
}  # PRODUCE_LOG：行动记录覆盖层（底栏文档图标），点 ✕ 关闭  # 任何培育页面都可能经过：剧情、弹窗之间的空档
WEAK = {"IDLE"}  # 「在培育里但没认出具体页面」：弹窗选中项时 KAA 也会判成 IDLE，要连续多帧才离开当前页面
SELECTS = {"SELECT_CARD", "SELECT_DRINK", "SELECT_PITEM"}
CARD_OPS = {"SKILL_CARD_ENHANCE", "SKILL_CARD_REMOVAL", "SKILL_CARD_CHANGE_1", "SKILL_CARD_CHANGE_2"}
DRINK_FULL = {"PDRINK_MAX", "PDRINK_MAX_CONFIRM"}

_NEXT: dict[str, set[str]] = {
    "ACTION_SELECT": {"CONSULT", "STUDY", "OUTING", "PRACTICE", "HIF_EXAM", "ALLOWANCE", "OUTING_STAMINA_MAX"} | SELECTS | CARD_OPS | DRINK_FULL,
    "CONSULT": {"ACTION_SELECT"} | CARD_OPS | DRINK_FULL,
    "STUDY": {"ACTION_SELECT", "OUTING_STAMINA_MAX"} | SELECTS | CARD_OPS,
    "OUTING": {"ACTION_SELECT", "OUTING_STAMINA_MAX"} | SELECTS | CARD_OPS | DRINK_FULL,
    "OUTING_STAMINA_MAX": {"ACTION_SELECT", "OUTING", "STUDY"} | SELECTS,
    "PRACTICE": {"ACTION_SELECT"} | SELECTS | DRINK_FULL,
    "EXAM": {"ACTION_SELECT", "PRODUCE_END", "HIF_ROUND_INTERVAL"} | SELECTS | DRINK_FULL,
    "ALLOWANCE": {"ACTION_SELECT"} | SELECTS,
    "HIF_EXAM": {"HIF_EXAM_RESULT"},
    "PRODUCE_START_CONFIRM": {"home", "INITIAL_DRINK_OR_CARD_SELECT", "ACTION_SELECT"} | SELECTS,  # 开局「4.開始確認」（outside）
    "HIF_EXAM_RESULT": {"ACTION_SELECT", "HIF_ROUND_INTERVAL", "PRODUCE_END", "home"} | SELECTS | DRINK_FULL,
    "HIF_ROUND_INTERVAL": {"HIF_EXAM", "ACTION_SELECT"} | SELECTS | CARD_OPS | DRINK_FULL,
    "INITIAL_DRINK_OR_CARD_SELECT": {"ACTION_SELECT"} | SELECTS,
    "PRODUCE_END": {"home"},
    "home": {"PRODUCE_START_CONFIRM", "INITIAL_DRINK_OR_CARD_SELECT", "ACTION_SELECT", "HIF_EXAM", "HIF_EXAM_RESULT", "HIF_ROUND_INTERVAL", "CONSULT", "STUDY",
             "OUTING", "PRODUCE_END"} | SELECTS,  # 继续进行中的培育会直接回到当时的页面
}  # fmt: skip
# 所持スキルカード一览（底栏入口，识别-01 61c37ab）：从这些页面打开、关掉后回到原页面
DECK_LIST_FROM = {"ACTION_SELECT", "SELECT_CARD", "SELECT_DRINK", "CONSULT", "STUDY", "OUTING", "HIF_EXAM"}
_NEXT["DECK_LIST"] = set(DECK_LIST_FROM)
for _p in SELECTS:
    _NEXT[_p] = (
        {"ACTION_SELECT", "STUDY", "OUTING", "HIF_ROUND_INTERVAL", "HIF_EXAM", "PRODUCE_END", "OUTING_STAMINA_MAX"}
        | SELECTS
        | CARD_OPS
        | DRINK_FULL
    )
for _p in DECK_LIST_FROM:
    _NEXT.setdefault(_p, set()).add("DECK_LIST")

# H.I.F 页面地图（识别-01 54a368b，docs/pages-hif.md §3）
ACQUIRE_FROM = {"ACTION_SELECT", "STUDY", "OUTING", "CONSULT", "SELECT_CARD", "SELECT_DRINK", "SELECT_PITEM", "PRACTICE", "EXAM",
                "HIF_EXAM_RESULT", "ALLOWANCE", "HIF_ROUND_INTERVAL"}  # fmt: skip
_NEXT["ACQUIRE"] = ACQUIRE_FROM | {"STAR_GAIN", "PDRINK_MAX", "ACQUIRE"}
# 考试后「スター獲得 → P 道具三选一」是正常流程（D39 第三十三局 seq 229：以前漏了 SELECT_PITEM，要防抖 3 帧，带候选的帧被丢）
_NEXT["STAR_GAIN"] = {"ACTION_SELECT", "ACQUIRE", "STUDY", "OUTING", "CONSULT", "PDRINK_MAX", "STAR_GAIN"} | SELECTS
for _p in ACQUIRE_FROM:
    _NEXT.setdefault(_p, set()).add("ACQUIRE")
_NEXT["ACQUIRE"].add("CONSULT")  # 相談购买后的获得演出（6c6f0b0）
for _p in {"ACQUIRE", "SELECT_CARD", "STUDY", "OUTING", "CONSULT", "PRACTICE"}:
    _NEXT.setdefault(_p, set()).add("STAR_GAIN")
_NEXT["ACTION_SELECT"] |= {"HIF_EXAM_BRIEFING", "SPECIAL_GUIDANCE"}
_NEXT["HIF_EXAM_BRIEFING"] = {"HIF_EXAM", "HIF_FINALS_RANKING"}
_NEXT["HIF_FINALS_RANKING"] = {"HIF_EXAM", "ACTION_SELECT", "HIF_ROUND_INTERVAL", "PRODUCE_END"}
_NEXT["ACTION_SELECT"].add("HIF_FINALS_RANKING")
_NEXT["HIF_EXAM_RESULT"].add("HIF_FINALS_RANKING")
_NEXT["HIF_EXAM"].add("HIF_FINALS_RANKING")
# 本战开始确认（e5e48d5）：结算 / 首页 → HIF_FINALS_START →（设置弹窗）→ 本战选行动
_NEXT["HIF_FINALS_START"] = {"ACTION_SELECT", "DIALOG", "home"}
_NEXT["PRODUCE_END"].add("HIF_FINALS_START")
_NEXT["home"].add("HIF_FINALS_START")  # 本战轮次结果 / 最终结果（识别-06）
_NEXT["SPECIAL_GUIDANCE"] = {"ACTION_SELECT", "UPGRADE_RESULT"}
for _p in ("SELECT_PITEM", "HIF_EXAM_RESULT"):
    _NEXT[_p].add("PITEM_CUSTOMIZE")
_NEXT["PITEM_CUSTOMIZE"] = {"PITEM_CUSTOMIZE_DONE", "ACTION_SELECT", "HIF_ROUND_INTERVAL"}
_NEXT["PITEM_CUSTOMIZE_DONE"] = {"ACTION_SELECT", "HIF_ROUND_INTERVAL"}
_NEXT["UPGRADE_RESULT"] = {"CONSULT", "SKILL_CARD_ENHANCE", "STUDY", "OUTING", "ACTION_SELECT", "SPECIAL_GUIDANCE"}
for _p in ("CONSULT", "SKILL_CARD_ENHANCE", "STUDY", "OUTING"):
    _NEXT.setdefault(_p, set()).add("UPGRADE_RESULT")
_NEXT["HIF_EXAM_RESULT"].add("PRODUCE_END")
# 识屏 ce615b6 快速判页新页面 GIFT（差し入れ礼物箱）→ 打开后进三选一（第十八局本战：GIFT → SELECT_DRINK 不在图里，防抖 3 帧，候选没进 state 就停机）
_NEXT["GIFT"] = {"ACTION_SELECT", "ACQUIRE"} | SELECTS | DRINK_FULL
_NEXT.setdefault("ACTION_SELECT", set()).add("GIFT")
# 礼物箱里有几个袋子时，选完一个袋子的三选一会回到礼物箱再开下一个：GIFT → SELECT_DRINK → GIFT → SELECT_CARD
# （DMM 实机三次都是这个顺序，SELECT_DRINK → GIFT 不在图里，每次记一次 page_misjudged）
for _p in SELECTS:
    _NEXT.setdefault(_p, set()).add("GIFT")
for _p in CARD_OPS:
    _NEXT[_p] = {"CONSULT", "ACTION_SELECT", "HIF_ROUND_INTERVAL", "STUDY", "OUTING"} | SELECTS | CARD_OPS
for _p in DRINK_FULL:
    _NEXT[_p] = (
        {"CONSULT", "ACTION_SELECT", "HIF_ROUND_INTERVAL", "OUTING", "PRACTICE", "EXAM", "HIF_EXAM_RESULT"}
        | SELECTS
        | DRINK_FULL
    )

ALL_PAGES = PRODUCE_PAGES | {"home", "commu", "unknown"}


def allowed(src: str, dst: str) -> bool:
    """src → dst 是否可以直接发生。同页面、起点未知、经过枢纽页面（IDLE / 剧情）一律放行。"""
    if src == dst or src in TRANSIENT or dst in HUB or src in HUB:
        return True
    return dst in _NEXT.get(src, set())


def is_transient(page: str) -> bool:
    return page in TRANSIENT


def mode_of(page: str, prev: str = "unknown") -> str:
    if page == "home":
        return "home"
    if page in PRODUCE_PAGES:
        return "produce"
    if page == "commu":
        return prev if prev != "unknown" else "unknown"
    return prev


def successors(page: str) -> set[str]:
    return ({page} | HUB | _NEXT.get(page, set())) if page not in TRANSIENT else ALL_PAGES
