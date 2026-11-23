"""Action：决策 → 操作。枚举对齐客户端 Api.Produce*Request，不含像素坐标。

坐标由操作层按 hiro.vision.layout 或识别结果里的按钮位置解析；Action 只说「做什么」。
考试内三种动作与 hiro.brain.rules.Decision.action 的字符串形式（play:<slot> / drink:<slot> / skip）互转。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hiro.contracts.base import VERSION, Contract


class ActionKind(str, Enum):
    # ---- 培育流程（Api.Produce*Request）
    PRODUCE_NEXT = "ProduceNext"  # ProduceNextRequest(stepType, selectNumber)：选行动页选一项
    ACTIVATE_EFFECT = "ActivateEffect"  # ProduceActivateEffectRequest(pickIndexes)：三选一拿卡 / 拿饮料 / 奖励
    EXCLUDE_CARD = "ExcludeCard"  # ProduceExcludeProduceCardRequest(pickIndex)：三选一不拿、删除候选
    REROLL = "Reroll"  # 候选刷新（セレクトチェンジ 再抽選 / ProduceStepShopRerollRequest）
    SHOP_BUY = "ShopBuy"  # ProduceStepShopBuyRequest(index)
    SHOP_UPGRADE_CARD = "ShopUpgradeCard"  # ProduceStepShopUpgradeCardRequest(cardIndex)
    SHOP_END = "ShopEnd"  # ProduceStepShopEndRequest
    EVENT_SELECT = "EventSelect"  # ProduceStepEventRequest(select)：授業 / 外出 的选项
    PRESENT_RECEIVE = "PresentReceive"  # ProduceStepPresentReceiveRequest
    INTERVAL_BUY = "IntervalBuy"  # ProduceStepIntervalBuyRequest(index)
    INTERVAL_UPGRADE_CARD = "IntervalUpgradeCard"  # ProduceStepIntervalUpgradeCardRequest(cardIndex)
    INTERVAL_CHANGE_CARD = "IntervalChangeCard"  # ProduceStepIntervalChangeCardRequest
    INTERVAL_END = "IntervalEnd"  # 轮间结束
    CUSTOMIZE_CARD = "CustomizeCard"  # ProduceStepCustomizeSelectRequest(deckProduceCardIndex, produceCardCustomizeId)：特別指導 定制一张卡
    CUSTOMIZE_END = "CustomizeEnd"  # ProduceStepCustomizeEndRequest：特別指導 结束
    SELECT_FORMATION = (
        "SelectFormation"  # 开局编成：args {support_set, memory_set, main_attr}（KAA prepare 的编成编号 1–20）
    )
    CHANGE_CARD = "ChangeCard"  # 授業セレクトチェンジ：阶段 1 args {stage: 1, pick_index}（换入候选序号）；阶段 2 args {stage: 2, remove_card_id, remove_index, remove_upgrade}；args {cancel: True} 表示取消换卡
    REMOVE_CARD = "RemoveCard"  # 删卡事件页（SKILL_CARD_REMOVAL）：args {card_id, index, upgrade}（卡格序号）；{cancel: True} 表示取消 / 停止，不盲选
    ENHANCE_CARD = "EnhanceCard"  # 强化事件页（SKILL_CARD_ENHANCE）：args 同 RemoveCard
    DIALOG = "Dialog"  # 确认弹窗：args {kind, confirm}，例如 PDRINK_MAX_CONFIRM 的「報酬スキップ」{kind: "reward_skip", confirm: True / False}
    SELECT_QUALIFY_MEMORY = (
        "SelectQualifyMemory"  # 本战用哪份选拔回忆：args {index}（回忆列表从上到下 / 从左到右，0 起）
    )
    DISCARD_DRINK = "DiscardDrink"  # ProduceDiscardDrinkRequest(drinkIndexes)：饮料满了，丢 discard（持有饮料下标）后收下新饮料；discard=[] 表示不收
    # ---- 考试内（ExamPlay）
    PLAY_CARD = "PlayCard"  # slot：手牌从左到右的位置
    USE_DRINK = "UseDrink"  # slot：饮料栏位置
    SKIP = "Skip"  # 跳过回合
    # ---- 界面推进（没有协议对应；主循环用来过弹窗、过结算）
    CONTINUE = "Continue"  # 次へ / 确认 / 关闭当前弹窗
    WAIT = "Wait"  # 等画面稳定（转场、动画、complete=False）
    RECOVER = "Recover"  # 故障恢复动作（返回已知页面等），args.strategy 说明
    NOOP = "Noop"


EXAM_KINDS = {ActionKind.PLAY_CARD, ActionKind.USE_DRINK, ActionKind.SKIP}

# 选行动页选项（hiro.vision.observe.OPTION_NAME 的输出）→ 客户端 ProduceStepType 名
# special_guidance（特別指導）在 3.4.1 枚举中没有可确认的对应项，先用 observe 的名字占位
STEP_TYPE = {
    ("training", "Vo", False): "OpenLessonVocalNormal",
    ("training", "Vo", True): "OpenLessonVocalSp",
    ("training", "Da", False): "OpenLessonDanceNormal",
    ("training", "Da", True): "OpenLessonDanceSp",
    ("training", "Vi", False): "OpenLessonVisualNormal",
    ("training", "Vi", True): "OpenLessonVisualSp",
    ("class", "Vo", None): "EventSchoolVocal",
    ("class", "Da", None): "EventSchoolDance",
    ("class", "Vi", None): "EventSchoolVisual",
    ("outing", None, None): "EventActivity",
    ("gift", None, None): "Present",
    ("shop", None, None): "Shop",
    ("rest", None, None): "Refresh",
    ("special_guidance", None, None): "SpecialGuidance",
}


def step_type(option: str, attr: str | None = None, sp: bool | None = None) -> str:
    key = (option, attr, sp if option == "training" else None)
    if key in STEP_TYPE:
        return STEP_TYPE[key]
    if option == "training" and (option, attr, False) in STEP_TYPE:
        return STEP_TYPE[(option, attr, False)]  # SP 未知时按普通课程命名
    return option


@dataclass
class Action(Contract):
    """一个要执行的动作。args 的键按 kind 固定（见各工厂方法），全部是 JSON 标量或列表。"""

    kind: ActionKind
    args: dict[str, Any] = field(default_factory=dict)
    version: str = VERSION

    # ---- 考试内
    @classmethod
    def play_card(cls, slot: int) -> "Action":
        return cls(ActionKind.PLAY_CARD, {"slot": int(slot)})

    @classmethod
    def use_drink(cls, slot: int) -> "Action":
        return cls(ActionKind.USE_DRINK, {"slot": int(slot)})

    @classmethod
    def skip(cls) -> "Action":
        return cls(ActionKind.SKIP)

    # ---- 培育
    @classmethod
    def produce_next(
        cls, option: str, *, attr: str | None = None, sp: bool | None = None, select_number: int | None = None
    ) -> "Action":
        """选行动页：option 为 observe 的选项名（training/class/outing/gift/shop/special_guidance/rest）。"""
        return cls(ActionKind.PRODUCE_NEXT, {"step_type": step_type(option, attr, sp), "option": option, "attr": attr, "sp": sp,
                                              "select_number": select_number})  # fmt: skip

    @classmethod
    def activate_effect(cls, *pick_indexes: int) -> "Action":
        return cls(ActionKind.ACTIVATE_EFFECT, {"pick_indexes": [int(i) for i in pick_indexes]})

    @classmethod
    def exclude_card(cls, pick_index: int) -> "Action":
        return cls(ActionKind.EXCLUDE_CARD, {"pick_index": int(pick_index)})

    @classmethod
    def event_select(cls, select: int) -> "Action":
        return cls(ActionKind.EVENT_SELECT, {"select": int(select)})

    @classmethod
    def ui(cls, kind: ActionKind, **args) -> "Action":
        assert kind in (ActionKind.CONTINUE, ActionKind.WAIT, ActionKind.RECOVER, ActionKind.NOOP)
        return cls(kind, args)

    # ---- 与决策层考试动作字符串互转
    @classmethod
    def from_legal(cls, s: str) -> "Action":
        """'play:2' / 'drink:0' / 'skip' → Action。"""
        head, _, rest = s.partition(":")
        if head == "play":
            return cls.play_card(int(rest))
        if head == "drink":
            return cls.use_drink(int(rest))
        if head == "skip":
            return cls.skip()
        raise ValueError(f"不是考试动作：{s!r}")

    def legal_key(self) -> str | None:
        if self.kind is ActionKind.PLAY_CARD:
            return f"play:{self.args['slot']}"
        if self.kind is ActionKind.USE_DRINK:
            return f"drink:{self.args['slot']}"
        if self.kind is ActionKind.SKIP:
            return "skip"
        return None

    @property
    def slot(self) -> int | None:
        return self.args.get("slot")

    def __str__(self) -> str:
        return self.legal_key() or f"{self.kind.value}{self.args or ''}"
