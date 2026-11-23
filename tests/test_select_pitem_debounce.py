"""D39 第三十三局 seq 229：STAR_GAIN → SELECT_PITEM 不在转移图里，要防抖 3 帧；控制器补判后 observe(fields=False) 记第 1 帧，
on_select_pitem 带 pitems 的 observe 是第 2 帧，reduce 还停在 STAR_GAIN、字段没进 state，决策器看到空 page_fields 给 Wait 停机。
回放：seq 219 的 state（STAR_GAIN）→ 补判帧 → seq 229 带 pitems 的观测。"""

import json
from pathlib import Path

from hiro.contracts import GameState, Observation
from hiro.loop import transitions
from hiro.loop.reduce import reduce

import pytest  # noqa: F401

FIX = Path(__file__).parent / "data" / "g33-seq229-select-pitem.json"  # 实机状态（page_fields 文本），随仓库发布


def _load():
    d = json.loads(FIX.read_text(encoding="utf-8"))
    state = GameState.from_dict(d["state_219"])
    obs = d["obs_229"]
    bare = Observation.from_dict(
        {**obs, "seq": 228, "fields": {}, "field_meta": {}}
    )  # 控制器补判后 observe(fields=False)
    return state, bare, Observation.from_dict(obs)


def test_star_gain_to_select_pitem_is_direct():
    assert "SELECT_PITEM" in transitions._NEXT["STAR_GAIN"]
    state, bare, obs = _load()
    assert state.page.name == "STAR_GAIN"
    s = reduce(reduce(state, bare), obs)
    assert s.page.name == "SELECT_PITEM"
    assert [it["name"] for it in s.page_fields["pitems"]] == ["ポーチ（緑）", "ポーチ（赤）", "ポーチ（黄）"]


def test_hook_reobserve_settles_unlisted_predecessor(monkeypatch):
    """前置页不在转移图里时（这里把 STAR_GAIN → SELECT_PITEM 临时去掉，模拟以后别的漏项）：钩子按 state 再观测两次，页面落定、字段进 state。"""
    monkeypatch.setitem(transitions._NEXT, "STAR_GAIN", transitions._NEXT["STAR_GAIN"] - {"SELECT_PITEM"})
    state, bare, obs = _load()
    s = reduce(reduce(state, bare), obs)
    assert s.page.name == "STAR_GAIN" and not s.page_fields.get("pitems")  # 复现：第 2 帧还在防抖
    for _ in range(2):  # 与 HiroHifStrategy.on_select_pitem 的重读循环一致
        if s.page.name == "SELECT_PITEM" and s.page_fields.get("pitems"):
            break
        s = reduce(s, obs)
    assert s.page.name == "SELECT_PITEM" and len(s.page_fields["pitems"]) == 3


def test_decider_picks_yellow_pouch_after_fix():
    from hiro.brain.deciders import ProduceDeciders

    state, bare, obs = _load()
    s = reduce(reduce(state, bare), obs)
    d = ProduceDeciders(None, None).decide("SELECT_PITEM", s, n_options=3)
    assert d.action.args.get("pick_indexes") == [2], d  # 社区：黄 > 緑 > 赤
