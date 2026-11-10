"""考试引擎入口：gakumas-player 的 gakumas_exam（按客户端规则实现，与实机记录整体对账），默认用它的 Rust 后端 gakumas_exam_rs。

- 已解码的考试存档 → 局面：battle(raw, ...)（开源版不带存档读取，由插件提供）；结算中的存档用 settle=True 把那张牌算完。
- 开考快照 → 按种子重新开局：Exam(obs).episode(seed)。
- 公开投影（离线快照、测试）→ 局面：Exam(...).restore(obs, seed)。
隐藏信息口径统一：山札顺序按 seed 重洗、对局随机数重设、未来回合属性按配额重新抽样。

后端：GAKUMAS_EXAM_NATIVE=0 强制用 Python 版；没装 gakumas_exam_rs 时自动用 Python 版（同一套规则，慢约 8 倍）。
"""

from __future__ import annotations

import copy
import random

import hiro.sim  # noqa: F401  引擎路径
from gakumas_exam.battle import FidelityBattle
from gakumas_exam.catalog import Catalog
from gakumas_exam.examconfig import ATTR_BY_TYPE, exam_meta, turn_attributes_for
from gakumas_exam.examconfig import turn_attributes as sample_turn_attributes
from gakumas_exam.master import shared

_COMMIT: list = []


def master_commit() -> str:
    if not _COMMIT:
        c = Catalog()
        _COMMIT.append(c.info["commit"])
        c.db.close()
    return _COMMIT[0]


def remaining_turn_attributes(meta: dict, obs: dict, seed: int) -> list:
    """本回合起的剩余回合属性（含追加回合）：末三回合固定，前段未公开部分按配额随机。"""
    s = obs["scalars"]
    rng = random.Random(seed)
    remaining = s["remainTurn"]
    base_left = max(0, s["limitTurn"] - s["currentTurn"] + 1)
    current = ATTR_BY_TYPE.get(obs.get("current_parameter_type"))
    attrs = turn_attributes_for(meta, s["currentTurn"], s["limitTurn"], rng, current)
    last = attrs[-1] if attrs else (current or meta["allocation"]["fixed_tail"][-1])
    return attrs[:base_left] + [last] * (remaining - base_left) if remaining > base_left else attrs[:remaining]


class Exam:
    """一份开考配置（开考 observation，gakumas_exam.snapshot.project 的投影）。hp / drinks 给了就替换开考体力与饮料。

    meta：三属性倍率、回合配额、每回合抽牌数、实机第一回合属性（gakumas_exam.examconfig.exam_meta）。"""

    def __init__(self, observation: dict, *, hp: int | None = None, drinks: list | None = None, commit: str | None = None):
        obs = copy.deepcopy(observation)
        if hp is not None:
            obs["scalars"]["stamina"] = min(int(hp), obs["scalars"]["maxStamina"])
        if drinks is not None:
            obs["drinks"] = [{"_id": d} for d in drinks]
        self.observation = obs
        self.meta = exam_meta(obs)
        self.commit = commit or master_commit()

    def episode(self, seed: int, *, keep_first: bool = True) -> FidelityBattle:
        """从开考前重新开一局：洗牌、发牌、支援强化、开考附魔全部按 seed 重来。"""
        attrs = sample_turn_attributes(self.meta, random.Random(seed), keep_first=keep_first)
        return FidelityBattle.episode_from_observation(self.observation, shared(self.commit), seed, turn_attributes=attrs,
                                                       draw=self.meta["draw"])

    def restore(self, obs: dict, seed: int) -> FidelityBattle:
        """同一场考试的任意回合（公开投影）→ 局面。"""
        attrs = remaining_turn_attributes(self.meta, obs, seed)
        env = FidelityBattle.from_observation(obs, shared(self.commit), seed, turn_attributes=attrs, draw=self.meta["draw"])
        env.turn_attributes = attrs
        return env


def battle(raw: dict, exam: Exam, obs: dict, seed: int, *, settle: bool = False, commit: str | None = None) -> FidelityBattle:
    """原始存档 → 局面。settle=True：存档停在「卡牌结算中」，把这张牌算完（费用已付）。exam 只用它的 meta。"""
    attrs = remaining_turn_attributes(exam.meta, obs, seed)
    make = FidelityBattle.settle_from_save if settle else FidelityBattle.from_save
    env = make(raw, shared(commit or exam.commit), seed, turn_attributes=attrs, draw=exam.meta["draw"])
    env.turn_attributes = attrs
    return env
