"""培育模式表：produce id → 家族（hif / hajime / nia）、难度、步数、考试过关名次、各场考试的档位。

设计稿 （作者本机，未发布） 第二节。步数和考试档位来自主数据（`Produce.steps`、`ProduceStepAuditionDifficulty`），
家族 / 难度按 produce id 固定（主数据的 `Produce.name` 只有「プロ」「マスター」，初和 N.I.A 同名，分不出家族）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

HIF = "hif"
HAJIME = "hajime"
NIA = "nia"


@dataclass(frozen=True)
class Mode:
    produce_id: str
    family: str  # hif / hajime / nia
    difficulty: str  # regular / pro / master / legend（初、N.I.A）；selection / finals（H.I.F）
    pass_rank: int  # 考试 / 试镜要到的名次：初前 3、N.I.A 第 1（主数据 rankThreshold）；H.I.F 按各自规则，这里记 1


MODES: dict[str, Mode] = {
    m.produce_id: m
    for m in (
        Mode("produce-001", HAJIME, "regular", 3),
        Mode("produce-002", HAJIME, "pro", 3),
        Mode("produce-003", HAJIME, "master", 3),
        Mode("produce-006", HAJIME, "legend", 3),
        Mode("produce-004", NIA, "pro", 1),
        Mode("produce-005", NIA, "master", 1),
        Mode("produce-007", HIF, "selection", 1),
        Mode("produce-008", HIF, "finals", 1),
    )
}


def mode(produce_id: str) -> Mode:
    return MODES[produce_id]


def family(produce_id: str | None) -> str | None:
    m = MODES.get(produce_id or "")
    return m.family if m else None


@lru_cache(maxsize=1)
def master():
    """引擎的主数据（gakumasu-diff 快照，vendor/gakumas-player）。不用 hiro.state 的 KAA game.db：那份太旧，新偶像卡查不到（8a3a1e7 11-17）。"""
    import hiro.sim  # noqa: F401  引擎路径
    from gakumas_exam.catalog import Catalog

    return Catalog()


def steps(produce_id: str) -> int:
    """日程步数（主数据 Produce.steps：初 13 / 16 / 18，N.I.A 27 / 26）。"""
    return int(master().one("Produce", produce_id)["steps"])


# ---------------------------------------------------------------- N.I.A PRO 简单模式
# 账号偏好 ProduceNextIdolAuditionProEasyMode 打开时，N.I.A PRO 开局拿到 P 道具「N.I.Aキー（黄緑）」：对手分数 −80%
#（主数据 Produce.easyProduceItemIds，逆向 10.4）。MASTER 没有。偏好只读，不改。
NIA_EASY_KEY = "pitem_00-3-330-0"


# ---------------------------------------------------------------- 初的课程档位
LESSON_KINDS = ("normal", "sp", "hard")


def lesson_levels(produce_id: str | None, idol_card_id: str | None, kind: str, clear: int | None) -> list[dict]:
    """初课程画面上的 CLEAR 线 → 主数据 ProduceStepLessonLevel 的候选档位（这个偶像的 plan、这一种课、successThreshold 等于 clear）。
    档位 id 是 p_step_lesson_level-<produce 号>-plan<N>-<kind>-<序号>，只看 plan + 种类 + 序号（课程 id 里的 exam_* 前缀不影响档位）。
    本模式对不上时按 LESSON_LEVEL_FALLBACK 再查（初 REGULAR 的追い込み 画面 CLEAR 90 / 165 是 produce-002 的 hard 档）；都对不上返回空列表。"""
    if not produce_id or not idol_card_id or kind not in LESSON_KINDS or clear is None:
        return []
    try:
        c = master()
        plan = str(c.one("IdolCard", idol_card_id).get("planType") or "").replace("ProducePlanType_Plan", "")
        rows = list(c.rows("ProduceStepLessonLevel"))
        for pid in (produce_id, *LESSON_LEVEL_FALLBACK.get((produce_id, kind), ())):
            prefix = f"p_step_lesson_level-{pid.split('-')[-1]}-plan{plan}-{kind}-"
            hit = [r for r in rows if str(r.get("id", "")).startswith(prefix) and int(r.get("successThreshold") or -1) == int(clear)]
            if hit:
                return hit
        return []
    except Exception:  # noqa: BLE001  没有主数据 / 偶像卡查不到
        return []


# 本模式的档位对不上画面时再查的模式：初 REGULAR 的追い込み用的是初 PRO（produce-002）的 hard 档
#（8a3a1e7 11-18：bench 实录 5 节都是；平台两局实机 CLEAR 90 / 165 = produce-002 hard-1 / hard-2）
LESSON_LEVEL_FALLBACK = {("produce-001", "hard"): ("produce-002",)}


def lesson_limits(levels: list[dict]) -> tuple[int, int] | None:
    """候选档位的 (limitTurn, resultTargetValueLimit)：全部一样才给（normal-1 / normal-2 数值相同算一样），否则 None。"""
    vals = {(int(r["limitTurn"]), int(r["resultTargetValueLimit"])) for r in levels}
    return vals.pop() if len(vals) == 1 else None


# ---------------------------------------------------------------- 页头周数 → step
# 各场考试在日程里的位置（第几步）。主数据没有这张表（Produce 只有总步数），按实机页头序列定：
# 初 REGULAR（13 步）：中間まで 5→1 週 = 第 1–5 步，第 6 步中間；最終まで 6→1 週 = 第 7–12 步，第 13 步最終（初 Mac 11-17 两局）。
# N.I.A PRO（27 步）：1次 / 2次 / 最終まで 各 8→1 週，考试在第 9 / 18 / 27 步（N.I.A Mac 11-17）。
# 最终考试总在最后一步（steps）；其余模式的中间几场没有实机数据，不猜（给 None）。
EXAM_STEPS = {"produce-001": {"mid1": 6, "final": 13}, "produce-004": {"mid1": 9, "mid2": 18, "final": 27}}


def exam_step(produce_id: str, stage: str) -> int | None:
    """这场考试（mid1 / mid2 / final）在日程里是第几步；不知道 None。"""
    pos = EXAM_STEPS.get(produce_id, {}).get(stage)
    if pos is None and stage == "final":
        try:
            pos = steps(produce_id)
        except Exception:  # noqa: BLE001  没有主数据
            return None
    return pos


def step_from_header(produce_id: str | None, stage: str | None, weeks: int | None) -> int | None:
    """选行动页头「<stage>まで N週」→ 当前 step（= 考试所在步 − N，同 H.I.F「選抜試験1まで 6日」→ 7 − 6）。算出来不在 1..steps 里给 None。"""
    if not produce_id or not stage or weeks is None or family(produce_id) not in (HAJIME, NIA):
        return None
    pos = exam_step(produce_id, stage)
    if pos is None:
        return None
    step = pos - int(weeks)
    return step if 1 <= step < pos else None


# ---------------------------------------------------------------- 考试 / 试镜档位

STAGE_KIND = {"ProduceStepType_AuditionMid1": "mid1", "ProduceStepType_AuditionMid2": "mid2", "ProduceStepType_AuditionFinal": "final"}
STAGE_ORDER = ("mid1", "mid2", "final")


@dataclass(frozen=True)
class Tier:
    """一场考试的一个档位（主数据 ProduceStepAuditionDifficulty 一行）。初每场只有 1 档；N.I.A 每场 3–4 档。"""

    number: int
    audition_type: str  # 例如 Mid1Easy；初是 Unknown
    base_score: int
    vote_unlock: int  # voteCount：选这一档要先攒到的投票数（N.I.A；0 = 不要票）
    vote_baseline: int  # voteCountBaseLine
    rank_threshold: int  # 要到的名次
    parameter_baseline: int
    force_end_score: int  # 到这个分数考试就结束（初 REGULAR 中間 900）；0 = 不封顶
    battle_config_id: str
    npc_group_id: str
    gimmick_group_id: str


@dataclass(frozen=True)
class Stage:
    kind: str  # mid1 / mid2 / final
    tiers: tuple[Tier, ...] = field(default_factory=tuple)


def auditions(produce_id: str, idol_card_id: str) -> list[Stage]:
    """这个偶像卡在这个培育里的各场考试，按 mid1 → mid2 → final 排，每场的档位按编号排。偶像卡没有配置时返回空列表。"""
    c = master()
    ref = c.one("IdolCard", idol_card_id).get("produceStepAuditionDifficultyId")
    rows = [r for r in c.rows("ProduceStepAuditionDifficulty") if r.get("id") == ref and r.get("produceId") == produce_id]
    out: list[Stage] = []
    for kind in STAGE_ORDER:
        tiers = sorted(
            (Tier(number=int(r["number"]), audition_type=str(r.get("auditionType", "")).replace("ProduceStepAuditionType_", ""),
                  base_score=int(r.get("baseScore") or 0), vote_unlock=int(r.get("voteCount") or 0),
                  vote_baseline=int(r.get("voteCountBaseLine") or 0), rank_threshold=int(r.get("rankThreshold") or 0),
                  parameter_baseline=int(r.get("parameterBaseLine") or 0), force_end_score=int(r.get("forceEndScore") or 0),
                  battle_config_id=r.get("produceExamBattleConfigId", ""), npc_group_id=r.get("produceExamBattleNpcGroupId", ""),
                  gimmick_group_id=r.get("produceExamGimmickEffectGroupId", ""))
             for r in rows if STAGE_KIND.get(r.get("stepType")) == kind),
            key=lambda t: t.number,
        )  # fmt: skip
        if tiers:
            out.append(Stage(kind, tuple(tiers)))
    return out


def audition_turns(produce_id: str, idol_card_id: str, stage: str) -> int | None:
    """这一场考试（mid1 / mid2 / final）的总回合数（第 1 档的战斗配置 turn）；查不到 None。初：中間 9、最終 11。"""
    st = next((x for x in auditions(produce_id, idol_card_id) if x.kind == stage), None)
    if st is None or not st.tiers:
        return None
    cid = st.tiers[0].battle_config_id
    cfg = next((r for r in master().rows("ProduceExamBattleConfig") if r.get("id") == cid), None)
    return int(cfg["turn"]) if cfg and cfg.get("turn") else None
