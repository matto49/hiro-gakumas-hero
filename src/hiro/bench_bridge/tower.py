"""偶像之路（tower）的考试：层设置、考试倍率、编成评估（bench 引擎上按题目编成打若干局）。

公开主数据里 TowerLayer / TowerLayerExam 是空表，「某层考什么」查不到，这里拼：
- 已抓到的绑定（tower_id, layer, mechanic → battle_config / gimmick / npc_group / 层道具）：从 bench 题目或抓包整理成 JSON，
  放在 HIRO_TOWER_BINDINGS（缺省 ~/.cache/hiro/bench/tower_bindings.json），用 `python -m hiro.bench_bridge.tower bindings` 生成。
  这些是采集来的，不进仓库。同一层有几种考试（机制不同，battle_config 也各不相同），按机制分开记。
- 能按命名规则推的：对手组 p_npc_group-<塔>-<偶像>-<机制>-stage_<层>、层道具 pitem_<塔>-<机制>-<槽>-stage_<层>。
- 对手分数区间（ProduceExamBattleNpcGroup）、倍率曲线（ProduceExamBattleScoreConfig）、审查基准（ProduceExamBattleConfig）在主数据里。

考试倍率（score_bonus_permils）：参数 = 主回忆三属性 + Σ floor(副回忆三属性 × 0.2)；按 battle_config 的曲线插值，
低于审查基准的每项扣 0.25 − 参数 / 基准 × 0.15（扣分加总后乘到三项上），再 +1000 按 10 向上取整。
2026-11-14 用 6 场实机开局（1 副 4 场、3 副 2 场）核过，6 场都对，且换别的系数 / 取整都对不上。

星：按名次给，★1 = 第 3 名、★2 = 第 2 名、★3 = 第 1 名（GKMS 按 10 局实机 Tower/ListLayers 抓包确认）；26、27 层的★3 另要
通关时条件（主数据 ProduceExamTrigger 里 …-for_tower_stage<层> 的触发器，现在只有这两个：26「クリア時に低下状態が2個以上」、
27「クリア時に除外にあるスキルカード(SSR)が5枚以下」）。对手的实际分数引擎不给、只有区间：名次保守算（超过对手 scoreMax
才算赢过他），另给乐观值（超过 scoreMin 就算）。border = 第 1 名对手 scoreMax + 1。

evaluate 要 gkbench（bench 的裁判）和 GKMS 引擎（本机克隆，见 gkbench.engine），开源包里没有 GKMS 时报错说明。
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import os
import re
import time
from functools import cache, lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

SUB_RATE = 0.2
INITIAL_DECK_THEME = {"card_play_aggressive": "aggressive"}  # examEffectType 的写法 → ExamInitialDeck 行名（やる気）
COLUMNS = ("vocal", "dance", "visual")
BATTLE_CONFIG = re.compile(
    r"^p_exam_battle_config-(?P<tower>tower_\d+)-(?:\d+_){2}\d+-(?:\d+_){2}\d+-turn_(?P<turn>\d+)$"
)
GIMMICK = re.compile(r"^p_exam_gimmick-(?P<tower>tower_\d+)-(?P<mechanic>.+?)-(?P<variant>\d+)-stage_(?P<stage>\d+)$")
NPC = re.compile(r"^p_npc_group-(?P<tower>tower_\d+)-(?P<idol>[^-]+)-(?P<mechanic>.+?)-stage_(?P<stage>\d+)$")
ITEM = re.compile(r"^pitem_(?P<tower>tower_\d+)-(?P<mechanic>.+?)-(?P<slot>\d+)-stage_(?P<stage>\d+)(?:-\d+)?$")


def bindings_path() -> Path:
    env = os.environ.get("HIRO_TOWER_BINDINGS")
    if env:
        return Path(env).expanduser()
    from hiro.bench_bridge.advisor import cache_dir

    return cache_dir() / "tower_bindings.json"


def _split(tower_id: str) -> tuple[str, str]:
    """tower_001-shro → (tower_001, shro)。"""
    family, _, idol = tower_id.partition("-")
    return family, idol


# ---------------------------------------------------------------- 主数据


class Master:
    """偶像之路用到的主数据（引擎 catalog）。测试可以直接传 rows={表名: [行]}。"""

    def __init__(self, catalog=None, rows: dict | None = None):
        self._rows = rows
        if rows is None and catalog is None:
            import importlib

            importlib.import_module(
                "hiro.sim"
            )  # 先把 vendor 里的考试引擎放进 sys.path（写成调用：import 排序不会把它挪到后面）
            Catalog = importlib.import_module("gakumas_exam.catalog").Catalog

            catalog = Catalog()
        self.cat = catalog

    def rows(self, table: str) -> list[dict]:
        if self._rows is not None:
            return list(self._rows.get(table, ()))
        return list(self.cat.rows(table))

    @cache  # noqa: B019
    def battle_config(self, config_id: str) -> dict | None:
        return next((r for r in self.rows("ProduceExamBattleConfig") if r.get("id") == config_id), None)

    @lru_cache(maxsize=1)  # noqa: B019
    def _score_points(self) -> dict:
        out: dict = {}
        # 全部收（以前只收 -tower_ 的：初 / N.I.A 的 p_exam_battle_score_config-voda-03-produce-001-1 等查不到，实机按三属性算倍率会失败）
        for r in self.rows("ProduceExamBattleScoreConfig"):
            if r.get("id"):
                out.setdefault(r["id"], []).append(r)
        for pts in out.values():
            pts.sort(key=lambda r: int(r.get("parameter") or 0))
        return out

    def score_points(self, score_config_id: str) -> list[dict]:
        return self._score_points().get(score_config_id, [])

    @lru_cache(maxsize=1)  # noqa: B019
    def _npc(self) -> dict:
        out: dict = {}
        for r in self.rows("ProduceExamBattleNpcGroup"):
            if NPC.match(r.get("id", "")):
                out.setdefault(r["id"], []).append(r)
        for g in out.values():
            g.sort(key=lambda r: int(r.get("number") or 0))
        return out

    def npc_group(self, group_id: str) -> list[dict]:
        return self._npc().get(group_id, [])

    @lru_cache(maxsize=1)  # noqa: B019
    def _stage_items(self) -> dict:
        out: dict = {}
        for r in self.rows("ProduceItem"):
            if m := ITEM.match(r.get("id", "")):
                out.setdefault((m["tower"], m["mechanic"], int(m["stage"])), []).append(r["id"])
        return {k: sorted(v) for k, v in out.items()}

    def stage_items(self, family: str, mechanic: str, layer: int) -> list[str]:
        return list(self._stage_items().get((family, mechanic, int(layer)), ()))

    @lru_cache(maxsize=1)  # noqa: B019
    def _gimmicks(self) -> dict:
        out: dict = {}
        for r in self.rows("ProduceExamGimmickEffectGroup"):
            if (m := GIMMICK.match(r.get("id", ""))) and r["id"] not in out.setdefault(
                (m["tower"], m["mechanic"], int(m["stage"])), []
            ):
                out[(m["tower"], m["mechanic"], int(m["stage"]))].append(r["id"])
        return {k: sorted(v) for k, v in out.items()}

    @lru_cache(maxsize=1)  # noqa: B019
    def _tower_triggers(self) -> dict:
        out: dict = {}
        for r in self.rows("ProduceExamTrigger"):
            if m := re.search(r"-for_tower_stage(\d+)$", r.get("id", "")):
                out.setdefault(int(m[1]), []).append(r)
        return out

    def tower_triggers(self, layer: int) -> list[dict]:
        return list(self._tower_triggers().get(int(layer), ()))

    def all_tower_triggers(self) -> list[dict]:
        return [r for rows in self._tower_triggers().values() for r in rows]

    def idol_initial_cards(self, idol_card_id: str) -> list[str]:
        """偶像之路开局另加的基本卡（编成页不显示）：ExamInitialDeck「initial_deck-<主题>」，主题 = 主回忆偶像卡的 examEffectType
        （ExamLessonBuff → lesson_buff）。GKMS 6 局实机开局对过（ttmr / hmsz / hski / hrnm）。查不到返回空。"""
        card = next((r for r in self.rows("IdolCard") if r.get("id") == idol_card_id), None)
        effect = (card or {}).get("examEffectType") or ""
        if not effect.startswith("ProduceExamEffectType_Exam"):
            return []
        theme = re.sub(r"(?<!^)(?=[A-Z])", "_", effect.removeprefix("ProduceExamEffectType_Exam")).lower()
        theme = INITIAL_DECK_THEME.get(theme, theme)
        row = next((r for r in self.rows("ExamInitialDeck") if r.get("id") == f"initial_deck-{theme}"), None)
        return list((row or {}).get("produceCardIds") or ())

    @lru_cache(maxsize=1)  # noqa: B019
    def _rarity(self) -> dict:
        return {r["id"]: r.get("rarity") for r in self.rows("ProduceCard") if r.get("id")}

    def card_rarity(self, card_id: str) -> str | None:
        return self._rarity().get(card_id)

    def gimmick_variants(self, family: str, mechanic: str, layer: int) -> list[str]:
        theme = "exam_full_power" if mechanic == "exam_preservation" else mechanic  # 温存和全力共用一套时间表
        return list(self._gimmicks().get((family, theme, int(layer)), ()))


@lru_cache(maxsize=1)
def master() -> Master:
    return Master()


# ---------------------------------------------------------------- 倍率


def stage_params(main: dict | list, subs=()) -> list[int]:
    """主回忆 + 副回忆 → 进考试的三属性：主 + Σ floor(副 × 0.2)。回忆写成 {vocal, dance, visual} 或 [Vo, Da, Vi]。"""

    def vec(m):
        return [int(m[k]) for k in COLUMNS] if isinstance(m, dict) else [int(v) for v in m]

    out = vec(main)
    for s in subs:
        out = [a + math.floor(b * SUB_RATE) for a, b in zip(out, vec(s))]
    return out


def _interpolate(points: list[dict], column: str, value: int) -> float:
    key = f"{column}Permil"
    if value <= int(points[0]["parameter"]):
        return float(points[0][key])
    if value >= int(points[-1]["parameter"]):
        return float(points[-1][key])
    for a, b in itertools.pairwise(points):
        x0, x1 = int(a["parameter"]), int(b["parameter"])
        if x0 <= value <= x1:
            return float(a[key]) + (value - x0) * (float(b[key]) - float(a[key])) / (x1 - x0)
    raise ValueError("曲线不单调")


def shortfall(config: dict, params: list[int]) -> float:
    """低于审查基准的扣分（三项加总）：每项 0.25 − 参数 / 基准 × 0.15。"""
    total = 0.0
    for column, v in zip(COLUMNS, params):
        crit = int(config.get(column) or 0)
        if crit and v < crit:
            total += 0.25 - (v / crit) * 0.15
    return total


def score_bonus_permils(battle_config_id: str, params: list[int], m: Master | None = None) -> list[int]:
    """本层的考试倍率 [Vo, Da, Vi]（千分比，同题目的 score_bonus_permils）。params 用 stage_params 算。"""
    m = m or master()
    config = m.battle_config(battle_config_id)
    if config is None:
        raise KeyError(battle_config_id)
    points = m.score_points(config["produceExamBattleScoreConfigId"])
    if not points:
        raise KeyError(config["produceExamBattleScoreConfigId"])
    factor = 1.0 - shortfall(config, params)
    return [math.ceil((_interpolate(points, c, v) * factor + 1000) / 10.0) * 10 for c, v in zip(COLUMNS, params)]


# ---------------------------------------------------------------- 星


STAR_OF_PLACE = {1: 3, 2: 2, 3: 1}
SSR = "ProduceCardRarity_Ssr"
SEARCH_FIELDS = {"p_card_search-ssr-lost": "lost_ssr"}  # 触发器里的卡检索 → 局末状态字段


def _names(v) -> list[str]:
    return [x.get("name") if isinstance(x, dict) else x for x in v or ()]


def parse_trigger(row: dict) -> dict:
    """通关时条件的触发器 → {trigger, text, field, op, value}。只认「最后一回合结束时 + 低下状態个数 / 卡检索张数」两种，
    其余 field 为 None（不认识就不判，记下来）。"""
    text = "".join(d.get("text") or "" for d in row.get("produceDescriptions") or ())
    out = {"trigger": row.get("id"), "text": text, "field": None, "op": None, "value": None}
    types, values = _names(row.get("fieldStatusTypes")), list(row.get("fieldStatusValues") or ())
    negate = "ProduceExamTriggerCheckType_Not" in _names(row.get("fieldStatusCheckTypes"))
    searches = list(row.get("fieldStatusProduceCardSearchIds") or ())
    if (
        _names(row.get("phaseTypes")) != ["ProduceExamPhaseType_ExamEndTurn"]
        or "ProduceExamFieldStatusType_RemainingTurn" not in types
    ):
        return out
    rest = [(t, v) for t, v in zip(types, values) if t != "ProduceExamFieldStatusType_RemainingTurn"]
    if len(rest) != 1 or int(values[types.index("ProduceExamFieldStatusType_RemainingTurn")]) != 1:
        return out
    kind, value = rest[0][0], int(rest[0][1])
    if kind == "ProduceExamFieldStatusType_DebuffCountUp":
        field = "debuffs"
    elif kind == "ProduceExamFieldStatusType_CardSearchCountUp" and len(searches) == 1 and searches[0] in SEARCH_FIELDS:
        field = SEARCH_FIELDS[searches[0]]
    else:
        return out
    op, bound = ("<=", value - 1) if negate else (">=", value)
    return {**out, "field": field, "op": op, "value": bound}


def star_conditions(layer: int, m: Master | None = None) -> list[dict]:
    """★3 在名次之外还要的通关时条件（没有就空表）。"""
    return [parse_trigger(r) for r in (m or master()).tower_triggers(layer)]


def condition_by_text(text: str, m: Master | None = None) -> dict | None:
    """画面上读到的条件原文 → 条件（按触发器文案对；去掉空白比）。对不上返回 None。"""
    key = re.sub(r"\s+", "", text or "")
    for r in (m or master()).all_tower_triggers():
        c = parse_trigger(r)
        if re.sub(r"\s+", "", c["text"]) == key:
            return c
    return None


def _place(score: int, rivals: list, *, optimistic: bool) -> int:
    """名次：保守 = 1 + scoreMax ≥ 分数的对手数；乐观 = 1 + scoreMin > 分数的对手数。rivals 是 [[min, max], …]。"""
    return 1 + sum(1 for lo, hi in rivals if (lo > score if optimistic else hi >= score))


def judge(score: int, rivals: list, conditions: list[dict], state: dict) -> dict:
    """一局的判定：{score, place, place_optimistic, first, stars, stars_optimistic, conditions: [{text, field, got, ok}]}。
    state 是局末的 {debuffs, lost_ssr}。条件不认识（field None）或局末没读到时 ok=None，★3 不给（宁可少算）。"""
    checks = []
    for c in conditions:
        got = state.get(c["field"]) if c.get("field") else None
        ok = None if got is None else (got <= c["value"] if c["op"] == "<=" else got >= c["value"])
        checks.append({"text": c["text"], "field": c.get("field"), "got": got, "ok": ok})
    cond_ok = all(x["ok"] is True for x in checks)

    def stars(place):
        n = STAR_OF_PLACE.get(place, 0)
        return 2 if n == 3 and not cond_ok else n  # 第 1 名但没满足条件：★2（GKMS 实测 27 层第 1 名给了 rank 2）

    place, opt = _place(score, rivals, optimistic=False), _place(score, rivals, optimistic=True)
    return {"score": int(score), "place": place, "place_optimistic": opt, "first": place == 1,
            "stars": stars(place), "stars_optimistic": stars(opt), "conditions": checks}  # fmt: skip


# ---------------------------------------------------------------- 层设置


@lru_cache(maxsize=1)
def _bindings() -> dict:
    p = bindings_path()
    if not p.is_file():
        logger.info("没有偶像之路层绑定 %s（python -m hiro.bench_bridge.tower bindings 生成）", p)
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {(r["tower_id"], int(r["layer"]), r["mechanic"]): r for r in doc.get("exams", ())}


def layer_mechanics(tower_id: str, layer: int) -> list[str]:
    """绑定里这一层已知的考试机制（一层通常有几种可选）。"""
    return sorted(k[2] for k in _bindings() if k[0] == tower_id and k[1] == int(layer))


def layer_exam(tower_id: str, layer: int, mechanic: str | None = None, m: Master | None = None) -> dict | None:
    """本层考试设置：{mechanic, turns, exam: {produce_exam_battle_config_id, produce_exam_gimmick_effect_group_id,
    produce_exam_battle_npc_group_id, produce_item_ids}, criteria, border, rivals, gimmick_variants, source}。
    star_conditions：★3 在第 1 名之外要的通关时条件（见 star_conditions）。
    mechanic 不给时：这一层只知道一种就用它，否则 None。battle_config / gimmick 只有绑定里有（没有就是 None，要从画面读）；
    对手组、层道具按命名规则推；border = 第 1 名对手 scoreMax + 1，对手组查不到时 None。"""
    m = m or master()
    layer = int(layer)
    if mechanic is None:
        known = layer_mechanics(tower_id, layer)
        if len(known) != 1:
            return None
        mechanic = known[0]
    family, idol = _split(tower_id)
    bound = _bindings().get((tower_id, layer, mechanic)) or {}
    variants = m.gimmick_variants(family, mechanic, layer)
    gimmick = bound.get("gimmick") or (variants[0] if len(variants) == 1 else None)
    exam = {
        "produce_exam_battle_config_id": bound.get("battle_config"),
        "produce_exam_gimmick_effect_group_id": gimmick,
        "produce_exam_battle_npc_group_id": bound.get("npc_group")
        or f"p_npc_group-{family}-{idol}-{mechanic}-stage_{layer:03d}",
        "produce_item_ids": stage_items_for(gimmick, family, mechanic, layer, m) if gimmick else None,
    }
    if bound.get("exam_effect_type") is not None:
        exam["exam_effect_type"] = bound["exam_effect_type"]
    config_id = exam["produce_exam_battle_config_id"]
    config = m.battle_config(config_id) if config_id else None
    rivals = [[int(r["scoreMin"]), int(r["scoreMax"])] for r in m.npc_group(exam["produce_exam_battle_npc_group_id"])]
    turns = int(BATTLE_CONFIG.match(config_id)["turn"]) if config_id and BATTLE_CONFIG.match(config_id) else None
    return {
        "tower_id": tower_id,
        "layer": layer,
        "mechanic": mechanic,
        "turns": turns,
        "exam": exam,
        "criteria": [int(config[c]) for c in COLUMNS] if config else None,
        "border": rivals[0][1] + 1 if rivals else None,
        "rivals": rivals,
        "gimmick_variants": variants,
        "star_conditions": star_conditions(layer, m),
        "source": bound.get("source") or "rule",
    }


def stage_items_for(gimmick_id: str, family: str, mechanic: str, layer: int, m: Master | None = None) -> list[str]:
    """层自带的 P 道具：槽号 = gimmick 变体号（idolroad 163 题全符：变体 1 只带槽 1、变体 2 只带槽 2，主数据没有的层不带）。"""
    m = m or master()
    g = GIMMICK.match(gimmick_id or "")
    if g is None:
        return []
    return [i for i in m.stage_items(family, mechanic, layer) if int(ITEM.match(i)["slot"]) == int(g["variant"])]


def build_bindings(problem_dirs=(), captures=()) -> list[dict]:
    """bench 题目（gkms.exam_loadout.v1，produce_id=tower）和抓包绑定（{"exams": [{towerId, number, examEffectType, …}]}）
    → 绑定行。同一 (tower_id, layer, mechanic) 设置不一致时报错（不该发生）。"""
    rows: dict = {}

    def put(key, row):
        old = rows.get(key)
        if old is not None and any(old[k] != row[k] for k in ("battle_config", "gimmick", "npc_group")):
            raise ValueError(f"{key} 两处设置不一致：{old['source']} / {row['source']}")
        rows.setdefault(key, row)

    for d in problem_dirs:
        for f in sorted(Path(d).glob("*.gksave.json")):
            if f.name.startswith("._"):
                continue
            lo = json.loads(f.read_text(encoding="utf-8")).get("loadout") or {}
            if lo.get("produce_id") != "tower" or not lo.get("exam"):
                continue
            e = lo["exam"]
            key = (lo["tower_id"], int(lo["layer"]), lo["mechanic"])
            put(key, {"tower_id": key[0], "layer": key[1], "mechanic": key[2], "exam_effect_type": e.get("exam_effect_type"),
                      "battle_config": e["produce_exam_battle_config_id"], "gimmick": e["produce_exam_gimmick_effect_group_id"],
                      "npc_group": e["produce_exam_battle_npc_group_id"], "produce_item_ids": list(e.get("produce_item_ids") or ()),
                      "source": f"bench:{f.stem.split('.')[0]}"})  # fmt: skip
    for c in captures:
        for r in json.loads(Path(c).read_text(encoding="utf-8")).get("exams", ()):
            effect = r["examEffectType"]
            name = (
                (effect.get("name") if isinstance(effect, dict) else effect)
                .removeprefix("PRODUCE_EXAM_EFFECT_TYPE_")
                .lower()
            )
            key = (r["towerId"], int(r["number"]), name)
            put(key, {"tower_id": key[0], "layer": key[1], "mechanic": key[2], "exam_effect_type": None,
                      "battle_config": r["produceExamBattleConfigId"], "gimmick": r["produceExamGimmickEffectGroupId"],
                      "npc_group": r["produceExamBattleNpcGroupId"], "produce_item_ids": [],
                      "source": f"capture:{Path(c).name}"})  # fmt: skip
    return [rows[k] for k in sorted(rows)]


# ---------------------------------------------------------------- 编成评估


def _gkbench():
    try:
        from gkbench import runner
    except ImportError as e:
        raise RuntimeError("evaluate 要 gkbench 和 GKMS 引擎：装 gkbench（或把 gakumas-bench 仓库放进 PYTHONPATH），"
                           "GKMS_ROOT 指向 GKMSProject 克隆") from e  # fmt: skip
    return runner


_W: dict = {}  # 每个进程一份：runner、算法、记下最后一个裁判（取局末状态）


def _setup(algo: str, step_seconds: float) -> None:
    runner = _gkbench()
    from hiro.bench_bridge.advisor import load_algo

    base = getattr(runner, "Referee", None)
    if base is not None and not getattr(base, "_hiro_keeps_last", False):

        class Keep(base):  # play_episode 自己建裁判；记下来好读局末的低下状態 / 除外区
            _hiro_keeps_last = True

            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                _W["ref"] = self

        runner.Referee = Keep
    _W.update(runner=runner, bot=load_algo(algo), step=step_seconds)


def _end_state(ref) -> dict:
    s = getattr(getattr(ref, "battle", None), "session", None)
    out = {"debuffs": None, "lost_ssr": None}
    if s is None:
        return out
    try:
        out["debuffs"] = int(s.debuff_count())
    except Exception:
        logger.debug("局末低下状態读不到", exc_info=True)
    try:
        m = master()
        out["lost_ssr"] = sum(1 for c in s.deck.lost or () if m.card_rarity(c.get("produce_card_id")) == SSR)
    except Exception:
        logger.debug("局末除外区读不到", exc_info=True)
    return out


def _play(li: int, si: int, loadout: dict, seed: int, detail: bool, deadline: float | None):
    if deadline is not None and time.time() > deadline:
        return li, si, None
    problem = {"id": f"eval-{li}", "family": f"eval-{li}", "set": "hiro-eval", "loadout": loadout}
    _W.pop("ref", None)
    ep = _W["runner"].play_episode(problem, {"index": si, "seed": int(seed)}, _W["bot"], _W["step"], keep_obs=False)
    if ep.get("err"):
        logger.info("编成 %d 种子 %d 出错：%s", li, seed, ep["err"])
        return li, si, None
    score = int(ep["final"]["score"])
    if not detail:
        return li, si, score
    rivals = [[int(r["score_min"]), int(r["score_max"])] for r in (ep.get("meta") or {}).get("rival_scores") or ()]
    layer = loadout.get("layer")
    conds = star_conditions(int(layer)) if layer else []
    return li, si, judge(score, rivals, conds, _end_state(_W.get("ref")))


def _preflight() -> None:
    """多进程前先在主进程把 GKMS 原生核心载一次：子进程初始化失败时进程池只报 BrokenProcessPool、看不到原因
    （2026-11-14 3940a9：GKMSCLIGPU_LIB 指到规则 55 的旧核心、GKMS_ROOT 是规则 59，子进程一个个退出）。"""
    _gkbench()
    from gkbench import engine

    try:
        engine.native()
    except Exception as e:
        raise RuntimeError(f"GKMS 原生核心载不上（{type(e).__name__}: {e}）：GKMSCLIGPU_LIB 要和 GKMS_ROOT 是同一份 GKMS 编出来的"
                           f"（现在 GKMS_ROOT={engine.GKMS_ROOT}，核心={engine.NATIVE_LIB}）；不设 GKMSCLIGPU_LIB 就用 GKMS_ROOT 自己的") from e  # fmt: skip


def evaluate(loadouts: list[dict], seeds: list[int], *, algo: str = "uinet", budget_s: float | None = None,
             step_seconds: float = 5.0, detail: bool = False, workers: int = 1) -> list[list]:  # fmt: skip
    """每个编成（gkms.exam_loadout.v1）在同一批种子上各打一局。
    返回 [编成][种子]：detail=False 时是终局分；detail=True 时是 judge() 的判定（名次、星、条件）；出错 / 没跑到 None。
    budget_s：总时限，到了就停。workers > 1 时多进程（每个进程各载一次引擎和模型）。
    algo 写法同 HIRO_BENCH_ALGO（uinet 用我们的模型和主数据）。同一编成、同一种子、同一算法，分数和 bench 上的一样
    （2026-11-14：idolroad 163 题 × 4 种子 652 局逐局相同）。"""
    deadline = None if budget_s is None else time.time() + budget_s
    jobs = [(li, si, lo, int(seed)) for li, lo in enumerate(loadouts) for si, seed in enumerate(seeds)]
    out: list[list] = [[None] * len(seeds) for _ in loadouts]
    if workers <= 1:
        _setup(algo, step_seconds)
        for li, si, lo, seed in jobs:
            out[li][si] = _play(li, si, lo, seed, detail, deadline)[2]
        return out
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed

    _preflight()

    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"), initializer=_setup,
                             initargs=(algo, step_seconds)) as pool:  # fmt: skip
        futs = [pool.submit(_play, li, si, lo, seed, detail, deadline) for li, si, lo, seed in jobs]
        for f in as_completed(futs):
            li, si, v = f.result()
            out[li][si] = v
    return out


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bindings", help="从 bench 题目 / 抓包整理层绑定")
    b.add_argument("--problems", nargs="*", default=[], help="题目目录（gakumas-bench-data/problems/idolroad）")
    b.add_argument("--captures", nargs="*", default=[], help="抓包绑定 JSON（如 GKMS 的 tower_capture_bindings.json）")
    b.add_argument("--out", default=None)
    q = sub.add_parser("layer", help="查一层的考试设置")
    q.add_argument("tower_id")
    q.add_argument("layer", type=int)
    q.add_argument("--mechanic", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "bindings":
        rows = build_bindings(a.problems, a.captures)
        out = Path(a.out) if a.out else bindings_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"schema": "hiro.tower_bindings.v1", "exams": rows}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"{len(rows)} 组 → {out}")
    else:
        print(json.dumps(layer_exam(a.tower_id, a.layer, a.mechanic), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
