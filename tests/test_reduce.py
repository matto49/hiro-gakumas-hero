"""reduce：纯函数、防抖、来源优先级、字段归属、数据集重放确定性。"""

import copy
from pathlib import Path  # noqa: F401

from hiro.contracts import GameState, Observation, PageObs
from hiro.loop.reduce import DEBOUNCE, STABLE, reduce
from hiro.loop.replay import flaps, run  # noqa: F401
from hiro.loop.transitions import allowed, mode_of, successors

import pytest  # noqa: F401


def obs(seq, page, fields=None, *, source="screen", area="produce"):
    return Observation(seq, float(seq), PageObs(page, area), fields=fields or {}, source=source)


def test_transitions():
    assert allowed("unknown", "ACTION_SELECT") and allowed("ACTION_SELECT", "CONSULT") and allowed("HIF_EXAM", "commu")
    assert allowed("HIF_EXAM", "IDLE") and allowed("IDLE", "SELECT_CARD")
    assert not allowed("HIF_EXAM", "ACTION_SELECT") and not allowed("ACTION_SELECT", "HIF_EXAM_RESULT")
    assert (
        "HIF_EXAM_RESULT" in successors("HIF_EXAM")
        and mode_of("ACTION_SELECT") == "produce"
        and mode_of("home") == "home"
    )
    assert mode_of("commu", "produce") == "produce" and mode_of("commu") == "unknown"


def test_pure_and_transient():
    s0 = GameState()
    frozen = copy.deepcopy(s0)
    s1 = reduce(s0, obs(1, "ACTION_SELECT", {"stamina": 20}))
    assert s0 == frozen and s1.page.name == "ACTION_SELECT" and s1.mode == "produce" and s1.produce["stamina"] == 20
    s2 = reduce(s1, obs(2, "unknown", {"stamina": 99}))
    assert s2.page.name == "ACTION_SELECT" and s2.produce["stamina"] == 20 and s2.seq == 2
    s3 = reduce(s2, obs(3, "LOADING"))
    assert s3.page.name == "ACTION_SELECT" and s3.page.frames == 1


def test_none_keeps_old_value_and_stable_counter():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {"stamina": 20, "star": 15}))
    s = reduce(s, obs(2, "ACTION_SELECT", {"stamina": None, "star": 45}))
    assert (s.produce["stamina"], s.produce["star"]) == (20, 45)
    assert s.sources["produce.stamina"].seq == 1 and s.sources["produce.star"].seq == 2
    assert s.page.stable is (STABLE <= 2) and s.page.frames == 2


def test_debounce_illegal_jump_and_flap():
    s = reduce(GameState(), obs(1, "HIF_EXAM", {"hp": 30}))
    for i in range(DEBOUNCE - 1):
        s = reduce(s, obs(2 + i, "ACTION_SELECT", {"stamina": 1}))
        assert s.page.name == "HIF_EXAM" and s.pending_page.name == "ACTION_SELECT" and s.produce in (None, {})
    s = reduce(s, obs(10, "HIF_EXAM"))  # 抖回去：候选清空
    assert s.page.name == "HIF_EXAM" and s.pending_page is None
    for i in range(DEBOUNCE):
        s = reduce(s, obs(20 + i, "ACTION_SELECT", {"stamina": 1}))
    assert s.page.name == "ACTION_SELECT" and s.page.since_seq == 20 and s.produce["stamina"] == 1
    assert any("不在转移图" in c for c in s.conflicts)


def test_source_priority_and_conflict_log():
    s = reduce(GameState(), obs(1, "HIF_EXAM", {"hp": 30, "genki": 5}))
    s = reduce(s, obs(1, "HIF_EXAM", {"hp": 29}, source="save"))  # 同一帧的存档
    assert s.exam["hp"] == 29 and s.sources["exam.hp"].origin == "save" and s.conflicts
    s = reduce(s, obs(1, "HIF_EXAM", {"hp": 30}))  # 同帧屏幕不能覆盖存档
    assert s.exam["hp"] == 29
    s = reduce(s, obs(2, "HIF_EXAM", {"hp": 27}))  # 下一帧屏幕更新
    assert s.exam["hp"] == 27 and s.sources["exam.hp"].origin == "screen"


def test_exam_entry_resets_and_counts_turns():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {"stamina": 20, "options": [{"option": "shop"}], "complete": True}))
    assert s.page_fields["options"] and s.page_fields["complete"] is True
    s = reduce(s, obs(2, "HIF_EXAM", {"remaining_turns": 10, "hp": 33, "hand": [{"slot": 0}]}))
    assert s.page_fields == {} and s.exam["hp"] == 33 and s.exam_session.turn == 1 and s.produce["stamina"] == 20
    s = reduce(s, obs(3, "HIF_EXAM", {"remaining_turns": 10, "hand": [{"slot": 0}, {"slot": 1}]}))
    assert s.exam_session.turn == 1 and len(s.exam["hand"]) == 2
    s = reduce(s, obs(4, "HIF_EXAM", {"remaining_turns": 9}))
    assert s.exam_session.turn == 2
    s = reduce(s, obs(5, "HIF_EXAM", {"remaining_turns": 10}))  # 加回合不算新回合
    assert s.exam_session.turn == 2
    s = reduce(s, obs(6, "commu"))
    s = reduce(s, obs(7, "HIF_EXAM_RESULT", {"score": 1000}))
    assert s.page_fields["score"] == 1000 and s.exam["hp"] == 33  # 结算页不清考试数据
    s = reduce(s, obs(8, "HIF_EXAM", {"remaining_turns": 10}))  # 结算页不能直接回考试：进防抖
    assert s.page.name == "HIF_EXAM_RESULT" and s.pending_page.name == "HIF_EXAM"
    for i in range(DEBOUNCE):
        s = reduce(s, obs(9 + i, "IDLE"))  # IDLE 是弱证据，连续多帧才接受
    assert s.page.name == "IDLE" and s.mode == "produce"
    s = reduce(s, obs(20, "HIF_EXAM", {"remaining_turns": 10}))  # 下一场考试：exam 重置
    assert s.page.name == "HIF_EXAM" and s.exam_session.turn == 1 and "hp" not in s.exam


def test_weak_idle_does_not_flap_dialog():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {"stamina": 20}))
    s = reduce(s, obs(2, "SELECT_CARD", {"candidates": [{"slot": 0}]}))
    s = reduce(s, obs(3, "IDLE"))  # 选中某张卡时 KAA 判成 IDLE
    assert s.page.name == "SELECT_CARD" and s.page_fields["candidates"] and s.pending_page.name == "IDLE"
    s = reduce(s, obs(4, "SELECT_CARD", {"candidates": [{"slot": 1}]}))
    assert s.page.name == "SELECT_CARD" and s.pending_page is None and s.page_fields["candidates"] == [{"slot": 1}]


# ---- 插件观测（契约 1.3，2026-11-25 插件架构 spec 4.3）


def plugin_obs(seq, page, fields=None, *, trust="save", by="hook@1", hidden=None):
    return Observation(
        seq,
        float(seq),
        PageObs(page, "produce"),
        fields=fields or {},
        source="plugin",
        trust=trust,
        by=by,
        hidden=hidden or {},
    )


def test_plugin_obs_uses_declared_trust():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {"stamina": 30}))
    s = reduce(s, obs(2, "ACTION_SELECT", {"stamina": 30}))
    s = reduce(s, plugin_obs(3, "ACTION_SELECT", {"stamina": 25}, trust="save"))
    assert s.produce["stamina"] == 25
    assert s.sources["produce.stamina"].origin == "save" and s.sources["produce.stamina"].by == "hook@1"
    s = reduce(s, obs(3, "ACTION_SELECT", {"stamina": 30}))  # 同一帧之后的画面读数不覆盖 save 档
    assert s.produce["stamina"] == 25


def test_plugin_obs_screen_trust_is_screen_origin():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {"stamina": 30}))
    s = reduce(s, obs(2, "ACTION_SELECT", {}))
    s = reduce(s, plugin_obs(2, "ACTION_SELECT", {"produce_point": 100}, trust="screen"))
    assert s.sources["produce.produce_point"].origin == "screen"
    assert s.sources["produce.produce_point"].by == "hook@1"


def test_plugin_hidden_merged():
    s = reduce(GameState(), obs(1, "HIF_EXAM", {}))
    s = reduce(s, obs(2, "HIF_EXAM", {}))
    s = reduce(s, plugin_obs(2, "HIF_EXAM", {}, hidden={"deck_order": ["a", "b"]}))
    assert s.hidden == {"deck_order": ["a", "b"]}
    s = reduce(s, plugin_obs(3, "HIF_EXAM", {}, hidden={"seed": 7}))
    assert s.hidden == {"deck_order": ["a", "b"], "seed": 7}


def test_screen_obs_has_no_by():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {"stamina": 30}))
    s = reduce(s, obs(2, "ACTION_SELECT", {"stamina": 30}))
    assert s.sources["produce.stamina"].by is None


def test_plugin_obs_does_not_touch_page():
    s = reduce(GameState(), obs(1, "ACTION_SELECT", {}))
    frames = s.page.frames
    s2 = reduce(s, plugin_obs(1, "ACTION_SELECT", {"stamina": 9}, trust="screen"))
    assert s2.page.frames == frames and s2.produce["stamina"] == 9
    s3 = reduce(s2, plugin_obs(2, "HIF_EXAM", {"hp": 3}))  # 插件说在别的页：不改页面、不填字段
    assert s3.page.name == "ACTION_SELECT" and s3.pending_page is None and not s3.exam
