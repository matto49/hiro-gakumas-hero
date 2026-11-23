"""培育状态。字段命名对齐客户端协议 Transaction.UserProduceProgress
（gakumas-player mechanics/培育状态与操作（客户端协议）.md），只收决策需要、实机能观测到的部分。"""

from dataclasses import asdict, dataclass, field

ATTRS = ("Vo", "Da", "Vi")
SELECTION = "produce-007"
FINALS = "produce-008"


@dataclass
class DeckCard:
    """UserProduceProgressProduceCard：produceCardId / upgradeCount / customizes。"""

    card_id: str
    upgrade: int = 0
    customized: bool = False
    customize: dict = field(
        default_factory=dict
    )  # 特別指導记账：定制项 id → 级数（hiro.loop.events CARD_CUSTOMIZE，ddecb43）


@dataclass
class SupportCard:
    """UserProduceProgressSupportCard：supportCardId / level。属性类型从 master SupportCard.type 查。"""

    support_card_id: str
    level: int | None = None


@dataclass
class LessonOption:
    """日程页可见的课程信息：副属性开局就定，SP 当天才知道（None = 未知）。"""

    main: str
    sub: str | None = None  # None = 没读到（预演按社区统计取最可能的副属性）
    sp: bool | None = None


@dataclass
class ProduceState:
    idol_card_id: str
    mode: str  # produce id：007 选拔 / 008 本战（H.I.F）；001–003 初；004 / 005 N.I.A（hiro.state.modes）
    step: int  # stepNumber：日程位置（本战 7/8/9 = R1/轮间/R2）
    stamina: int
    max_stamina: int
    produce_point: int
    params: dict  # vocal/dance/visual → {"Vo","Da","Vi"}
    growth_permil: dict  # *GrowthRatePermil 合计（1000 + 加成），例如 Vo 1449
    star: int | None = (
        None  # H.I.F 的星星（スター性）；初 / N.I.A 没有星星，一直是 None（用户 11-17：完全不用处理）。H.I.F 缺它时 GameState.produce_state() 不构造
    )
    star_permil: int = 1000  # starPermil：星星获得合计（亲爱度 37 为 1500）
    param_cap: int = 3000  # 3000 + *AdditionalLimit
    deck: list[DeckCard] = field(default_factory=list)
    drinks: list[str] = field(default_factory=list)  # produceDrinkIds
    # 饮料栏容量：缺省按主数据 ProduceSetting.produceDrinkPossessLimit = 3（最大 4，第 4 格要解锁）。画面确实看到第 4 格（有饮料或空杯图标）
    # 时识别给 4（平台 7a8eed4）。以前缺省 4：账号 2 只有 3 格，p15 相談第一次买饮料一定浪费（协调 11-17）
    drink_capacity: int = 3
    items: list[str] = field(default_factory=list)  # produceItems
    wappen_left: int = 20  # H.I.F ワッペン剩余触发次数
    rerolls_left: int = 0  # produceCardRemainSelectRerollCount
    excludes_left: int = 0  # produceCardRemainExcludeCount
    excluded: list[str] = field(default_factory=list)  # excludeProduceCardIds
    upgrades_done: int = 0
    deletes_done: int = 0
    lessons: dict = field(default_factory=dict)  # step → [LessonOption]（日程页可见的未来课程）
    supports: list[SupportCard] = field(default_factory=list)  # 本局支援卡编成（KAA 自动编成时由识别读出）
    main_attr: str | None = None  # 显式指定的主属性（运行配置）；None 时按支援卡编成推出，再不行按成长率推测
    votes: int | None = None  # N.I.A ファン投票数（决定试镜能选哪档）；其它模式 None
    rest_left: int | None = None  # 还能休息几次（N.I.A 最多 4 次）；None = 不限 / 没读到
    challenge_items: list[str] = field(default_factory=list)  # MASTER 带进来的チャレンジPアイテム

    @property
    def family(self) -> str | None:
        """hif / hajime / nia（hiro.state.modes）；不认识的 produce id 是 None。"""
        from hiro.state.modes import family

        return family(self.mode)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProduceState":
        d = dict(d)
        d["deck"] = [c if isinstance(c, DeckCard) else DeckCard(**c) for c in d.get("deck", [])]
        d["supports"] = [c if isinstance(c, SupportCard) else SupportCard(**c) for c in d.get("supports", [])]
        d["lessons"] = {
            int(k): [o if isinstance(o, LessonOption) else LessonOption(**o) for o in v]
            for k, v in d.get("lessons", {}).items()
        }  # fmt: skip
        return cls(**d)
