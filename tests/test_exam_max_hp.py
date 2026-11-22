"""最大体力从画面读，不写死（用户 11-19：随偶像卡等级、开花、账号等级、支援卡等浮动）。

- 开考时取培育页头部 OCR 的「体力 x/上限」（主循环状态 produce.max_stamina），记进本场考试记忆。
- 考试画面只显示当前体力：看到比记下的上限还大，就把上限抬到当前体力（读数过时）。
- 读不到就是 None：半血 / 固有道具判断按未知处理，不拿猜的数值算。
- 续跑没给 --idol 时，从同模式最近一次带偶像的 trace 读回偶像（11-18 选拔第 2 场 idol=null 连败）。
"""

import json
from types import SimpleNamespace

from hiro.brain.cards import CardView
from hiro.brain.state import CAMPUS_HIRO, BattleState, ExamMemory, IdolProfile
from hiro.exec.exam import seed_max_hp


def _build(mem, hp):
    return BattleState.build(mem, CAMPUS_HIRO, source="screen", remaining_turns=8, hp=hp, genki=100, motivation=None, impression=None,
                             score=None, buffs={}, attr=None, multiplier_permil=None, npc_scores=None, my_rank_index=None, deck_count=None,
                             pitem_charges=None, statuses=None, attr_permil={}, hand=[], drinks=[])  # fmt: skip


def _session(produce):
    return SimpleNamespace(state=SimpleNamespace(produce=produce))


def test_profile_has_no_fixed_max_hp():
    assert not hasattr(CAMPUS_HIRO, "max_hp")
    assert IdolProfile.is_half_hp(19, 39) is True
    assert IdolProfile.is_half_hp(20, 39) is False
    assert IdolProfile.is_half_hp(16, None) is None


def test_seed_from_produce_screen_reading():
    mem = ExamMemory("qualify-2")
    assert seed_max_hp(mem, _session({"stamina": 30, "max_stamina": 39})) == 39
    assert (mem.max_hp, mem.max_hp_source) == (39, "produce_screen")


def test_seed_keeps_restored_value_and_unknown_stays_none():
    mem = ExamMemory("r1", max_hp=41, max_hp_source="produce_screen")
    assert seed_max_hp(mem, _session({"max_stamina": 39})) == 41  # 续跑读回的记忆不覆盖
    mem2 = ExamMemory("r1")
    assert seed_max_hp(mem2, _session({"stamina": 30})) is None  # 没读到上限：不猜
    assert seed_max_hp(ExamMemory("r1"), None) is None


def test_battle_state_uses_memory_max_hp_not_profile():
    mem = ExamMemory("qualify-2")
    mem.set_max_hp(39, "produce_screen")
    st = _build(mem, 20)
    assert st.max_hp == 39
    assert st.half_hp is False  # 20 > 39 / 2
    assert _build(mem, 19).half_hp is True


def test_exam_hp_above_recorded_max_raises_floor():
    mem = ExamMemory("qualify-2")
    mem.set_max_hp(33, "produce_screen")
    st = _build(mem, 39)
    assert mem.max_hp == 39 and st.max_hp == 39
    assert st.max_hp_note and "39" in st.max_hp_note


def test_max_hp_survives_memory_round_trip():
    mem = ExamMemory("r1")
    mem.set_max_hp(39, "produce_screen")
    back = ExamMemory.from_dict(json.loads(json.dumps(mem.to_dict())))
    assert (back.max_hp, back.max_hp_source) == (39, "produce_screen")


def test_item_trigger_uses_read_max_hp():
    """固有道具（HP 高于半血才触发）：按读到的上限判断；上限未知时不当作触发。"""
    card = CardView(0, "えいえいおー+", "p_card-02-men-1_031", 1, True, "mental", 2, 0, None, "元気+2やる気+4")
    mem = ExamMemory("r1", item_charges_left=5)
    mem.set_max_hp(39, "produce_screen")
    mem.record_play(card, hp_before=20, profile=CAMPUS_HIRO)  # 20 > 19：触发
    assert mem.item_charges_left == 4
    mem.record_play(card, hp_before=19, profile=CAMPUS_HIRO)  # 19 ≤ 19：半血，不触发
    assert mem.item_charges_left == 4
    unknown = ExamMemory("r1", item_charges_left=5)
    unknown.record_play(card, hp_before=30, profile=CAMPUS_HIRO)
    assert unknown.item_charges_left == 5


def test_resume_recovers_idol_from_latest_trace(tmp_path):
    from hiro.run_hif import last_run_identity

    def put(name, **meta):
        (tmp_path / name).mkdir()
        (tmp_path / name / "run.json").write_text(json.dumps(meta), encoding="utf-8")

    put("a", started=1, mode="qualify", idol="i_card-skin-shro-3-018", main_attr="Vo")
    put("b", started=2, mode="qualify", idol="i_card-skin-shro-3-007", main_attr="Da")
    put("c", started=3, mode="qualify", idol=None, main_attr="Da")  # 之前没带 --idol 的续跑：跳过
    put("d", started=4, mode="main", idol="i_card-skin-shro-3-012", main_attr="Vi")
    got = last_run_identity("qualify", tmp_path)
    assert (got["idol"], got["main_attr"], got["trace"]) == ("i_card-skin-shro-3-007", "Da", "b")
    assert last_run_identity("main", tmp_path)["idol"] == "i_card-skin-shro-3-012"
    assert last_run_identity("qualify", tmp_path / "missing") is None


def test_resume_trace_name_continues_the_same_run(tmp_path):
    from hiro.run_hif import resume_trace_name

    assert resume_trace_name("qualify3-20261119-101656", tmp_path) == "qualify3-20261119-101656-r1"
    (tmp_path / "qualify3-20261119-101656-r1").mkdir()
    (tmp_path / "qualify3-20261119-101656-r2").mkdir()
    assert resume_trace_name("qualify3-20261119-101656-r2", tmp_path) == "qualify3-20261119-101656-r3"


def test_implausible_hp_reading_does_not_move_max():
    mem = ExamMemory("qualify-3")
    mem.set_max_hp(39, "produce_screen")
    st = _build(mem, 391)  # OCR 多读了一位
    assert mem.max_hp == 39 and st.max_hp == 39
    assert "读错" in st.max_hp_note
