"""偶像之路本机缓存（hiro.runtime.tower_cache）：编成 loadout 按 tower_id + 层存取、编成签名、从手牌变化推抽牌顺序（只留有把握的前缀）。"""

import json
from types import SimpleNamespace as NS

from hiro.runtime import tower_cache as tc
from hiro.runtime import tower_play as tp
from hiro.runtime.tower import TowerRun

import pytest


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_TOWER_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("HIRO_TOWER_LOADOUT", raising=False)


def _h(*cards):
    return [
        {"slot": i, "card_id": c.split("+")[0], "upgrade": 1 if c.endswith("+") else 0} for i, c in enumerate(cards)
    ]


def _st(turn, hand, played=None, unrecognized=0):
    return {"remaining_turns": turn, "hand": hand, "played": played, "unrecognized": unrecognized}


def test_signature():
    assert (
        tc.signature("i_card-ttmr-2-000", [12075, 10773, 9978], [2348, 1701, 611])
        == "i_card-ttmr-2-000.12075-10773-9978"
    )
    assert (
        tc.signature("i_card-ttmr-2-000", None, [2348, 1701, 611]) == "i_card-ttmr-2-000.2348-1701-611"
    )  # 评价值读不全退回三属性
    assert tc.signature(None, [1, 2, 3], None) is None and tc.signature("i", [1, 2], None) is None


def test_loadout_save_load(tmp_path):
    lo = {"tower_id": "tower_001-ttmr", "layer": 15, "turns": 15}
    assert tc.save_loadout(lo) == tmp_path / "cache" / "tower_001-ttmr-15.json"
    tc.save_draws("tower_001-ttmr", 15, "sig", [], [])  # 抽牌文件不当成 loadout
    assert tc.load_loadout("tower_001-ttmr", 15) == lo and tc.load_loadout("tower_001-ttmr", None) == lo
    assert tc.load_loadout("tower_001-ttmr", 16) is None and tc.load_loadout(None, 15) is None
    assert tc.save_loadout({"tower_id": "t", "layer": None}) is None


DECK = {"p_card-a": {0, 1}, "p_card-b": {0}, "p_card-c": {0}, "p_card-d": {0}, "p_card-e": {0}}


def test_infer_draws_turns_and_mid_turn_draw():
    a, b, c, d, e = "p_card-a", "p_card-b", "p_card-c", "p_card-d", "p_card-e"
    steps = [
        _st(15, _h(a + "+", b, c), played=2),  # 开局 3 张；打出 c，抽到 d（同一回合）
        _st(15, _h(d, a + "+", b), played=0),
        _st(14, _h(e, b, a + "+")),
    ]  # 新回合：整手算抽到（同名的 b、a+ 也是新抽的）
    draws, skipped, why = tc.infer_draws(steps, DECK)
    assert why is None and skipped == 0
    assert draws == [{"id": a, "u": 1}, {"id": b, "u": 0}, {"id": c, "u": 0}, {"id": d, "u": 0},
                     {"id": e, "u": 0}, {"id": b, "u": 0}, {"id": a, "u": 1}]  # fmt: skip


def test_infer_draws_skips_generated_and_in_place_upgrade():
    a, b, c, d = "p_card-a", "p_card-b", "p_card-c", "p_card-d"
    sleepy = "p_card-00-acc-0_002"  # 眠気：生成效果只往山札 / 弃牌堆塞，抽到时跳过、计数，继续推（8a3a1e7 11-14）
    steps = [_st(15, _h(a, sleepy, b)),
             _st(14, _h(c, b, sleepy, d), played=0),  # 打出 c；b 原地强化成 b+（位置没变）：同一张
             _st(14, _h("p_card-b+", sleepy, d)),
             _st(13, _h(a, sleepy))]  # fmt: skip
    draws, skipped, why = tc.infer_draws(steps, DECK)
    assert why is None and skipped == 3
    assert [(x["id"], x["u"]) for x in draws] == [(a, 0), (b, 0), (c, 0), (b, 0), (d, 0), (a, 0)]


def test_infer_draws_fixes_first_frame_misread():
    """r1 T1：トークタイム+ 刚抽到那帧读成準備運動+（不在牌组里，先当生成卡跳过），同一回合下一步同一位置读对了：补回原位。"""
    a, b, c, d = "p_card-a", "p_card-b", "p_card-c", "p_card-d"
    ghost = "p_card-01-act-1_022"
    steps = [
        _st(15, _h(ghost, b, c), played=2),  # 打出 c
        _st(15, _h(a, b)),  # 位置 0 读对了：是 a
        _st(14, _h(d, "p_card-00-acc-0_002", b), played=0),
        _st(14, _h("p_card-00-acc-0_002", b)),
    ]  # 眠気一直是眠気：照样跳过
    draws, skipped, why = tc.infer_draws(steps, DECK)
    assert why is None and skipped == 1
    assert [x["id"] for x in draws] == [a, b, c, d, b]


def test_infer_draws_stops_on_doubt():
    a, b, c = "p_card-a", "p_card-b", "p_card-c"
    # 认不出的卡：停在那之前
    draws, _, why = tc.infer_draws([_st(15, _h(a, b)), _st(14, _h(c), unrecognized=1)], DECK)
    assert [x["id"] for x in draws] == [a, b] and "认不出" in why
    # 同一步 a 消失、a+ 出现在别的位置：分不清
    draws, _, why = tc.infer_draws([_st(15, _h(a, b, c), played=2), _st(15, _h(b, "p_card-a+"))], DECK)
    assert len(draws) == 3 and "分不清" in why
    # 被效果消耗掉的卡（少了）不影响
    draws, _, why = tc.infer_draws([_st(15, _h(a, b, c), played=0), _st(15, _h(c))], DECK)
    assert why is None and len(draws) == 3
    # 强化没读到：牌组里只有一种就补，不止一种就停
    unk = [{"slot": 0, "card_id": b, "upgrade": None}]
    assert tc.infer_draws([_st(15, unk)], DECK) == ([{"id": b, "u": 0}], 0, None)
    draws, _, why = tc.infer_draws([_st(15, _h(b)), _st(14, [{"slot": 0, "card_id": a, "upgrade": None}])], DECK)
    assert len(draws) == 1 and "强化读不出" in why
    # 牌组不知道：分不出生成卡，不记
    assert tc.infer_draws([_st(15, _h(a))], None) == ([], 0, "编成牌组没读到，分不出生成卡")


def test_exam_steps_from_trace(tmp_path):
    rows = [{"seq": 3, "observation": {"fields": {"hand": _h("p_card-x"), "remaining_turns": 9, "exam_label": "tower-14"}}},
            {"seq": 8, "observation": {"fields": {"hand": _h("p_card-a", "p_card-b"), "remaining_turns": 15}},
             "decision": {"action": {"kind": "PlayCard", "args": {"slot": 1}}}, "execution": {"ok": True}},
            {"seq": 9, "observation": {"fields": {"hand": _h("p_card-a"), "remaining_turns": 15}},
             "decision": {"action": {"kind": "PlayCard", "args": {"slot": 0}}}, "execution": {"ok": False}},
            {"seq": 10, "observation": {"fields": {"remaining_turns": 15}}}]  # fmt: skip
    p = tmp_path / "steps.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    st = tc.exam_steps(p, "tower-15", after_seq=5)
    assert [(s["remaining_turns"], s["played"]) for s in st] == [
        (15, 1),
        (15, None),
    ]  # 开考前的、没手牌的不算；没点成的不算打出


def _play(tmp_path):
    return tp.TowerPlay(
        NS(), TowerRun("tower_001-ttmr"), idol_name="月村手毬", layers=1, loadout_path=tmp_path / "lo.json"
    )


def test_retry_without_formation_uses_cached_loadout(tmp_path):
    lo = {"produce_id": "tower", "tower_id": "tower_001-ttmr", "layer": 15, "formation_signature": "sig"}
    tc.save_loadout(lo)
    p = _play(tmp_path)
    rt = NS(device=NS(tap=lambda x, y: None), sleep=lambda s: None)
    res = {"cleared": True, "layer": 15, "stars": 1, "retry": [129, 1160], "back": [350, 1160]}
    p.handlers()["TOWER_RESULT"](rt, None, NS(fields=res))  # 从结果页起跑
    assert p.layer == 15 and p.loadout == lo
    assert json.loads((tmp_path / "lo.json").read_text(encoding="utf-8")) == lo
    import os

    assert os.environ["HIRO_TOWER_LOADOUT"] == str(tmp_path / "lo.json")


def test_exam_start_without_layer_takes_latest(tmp_path):
    tc.save_loadout({"tower_id": "tower_001-ttmr", "layer": 15})
    p = _play(tmp_path)
    assert p.ensure_loadout()["layer"] == 15 and p.layer == 15  # 考试中起跑：层号从缓存补上，标签不再是 tower-x


@pytest.mark.master
@pytest.mark.sim_master
def test_draws_saved_with_rivals(tmp_path):
    p = _play(tmp_path)
    p.loadout = {"tower_id": "tower_001-ttmr", "layer": 15, "formation_signature": "sig", "idol_card_id": "i_card-ttmr-2-000",
                 "produce_cards": ["p_card-a+1", "p_card-b"]}  # fmt: skip
    steps = tmp_path / "steps.jsonl"
    steps.write_text(
        json.dumps({"seq": 8, "observation": {"fields": {"hand": _h("p_card-a+", "p_card-b"), "remaining_turns": 15}}}),
        encoding="utf-8",
    )
    p.record_draws(steps, "tower-15", 0)
    out = tmp_path / "cache" / "tower_001-ttmr-15-sig.json"
    assert json.loads(out.read_text(encoding="utf-8"))["rivals"] == []
    rows = [{"rank": 1, "name": "白草四音", "score": 105438}, {"rank": 2, "name": "暁美みずき", "score": 76661},
            {"rank": 3, "name": "月村手毬", "score": 54009}]  # fmt: skip
    p.on_ranking(
        NS(device=NS(tap=lambda x, y: None)), None, NS(fields={"my_rank": 3, "rows": rows, "next": [361, 1160]})
    )
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "tower_id": "tower_001-ttmr", "layer": 15, "formation_signature": "sig",
        "draws": [{"id": "p_card-a", "u": 1}, {"id": "p_card-b", "u": 0}], "rivals": [105438, 76661], "skipped_generated": 0}  # fmt: skip


def test_rivals_unread_score_gives_empty():
    rows = [{"rank": 1, "name": "A", "score": None}, {"rank": 2, "name": "月村手毬", "score": 5}]
    assert tp.rivals({"my_rank": 2, "rows": rows}, "月村手毬") == []
    assert tp.rivals({"my_rank": None, "rows": rows[:1]}, "x") == []


def test_progress_survives_restart_same_game_day(tmp_path, monkeypatch):
    """跨进程记每层挑战次数（协调 11-17：重启后从 0 算，手毬 16 层实际打了 3 次、日志每次都写 1）：同一游戏日读回来，换日不读。"""
    from datetime import datetime, timedelta, timezone

    from hiro.runtime.tower import TowerRun

    assert (
        tc.game_day(datetime(2026, 11, 17, 4, 59)) == "2026-11-16"
        and tc.game_day(datetime(2026, 11, 17, 5, 0)) == "2026-11-17"
    )  # 日本时间 5 点换日
    cst = timezone(timedelta(hours=8))  # 北京时间 04:05 = 日本 05:05：协调 11-17 实机这时弹了「日付変更」
    assert tc.game_day(datetime(2026, 11, 17, 4, 5, tzinfo=cst)) == "2026-11-17"
    assert tc.game_day(datetime(2026, 11, 17, 3, 59, tzinfo=cst)) == "2026-11-16"  # 登录奖励「04:59まで」（日本时间）
    utc = timezone.utc  # 别的时区的机器：照样按日本时间算
    assert (
        tc.game_day(datetime(2026, 11, 16, 19, 59, tzinfo=utc)) == "2026-11-16"
        and tc.game_day(datetime(2026, 11, 16, 20, 0, tzinfo=utc)) == "2026-11-17"
    )
    run = TowerRun("tower_001-ttmr")
    run.after_result(16, False, 0)
    run.after_result(16, False, 0)
    tc.save_progress(run)
    again = TowerRun("tower_001-ttmr")
    assert tc.load_progress(again) and again.tries == {16: 2}
    assert again.after_result(16, False, 0) == "stop"  # 第 3 次：跨进程也算到上限
    monkeypatch.setattr(tc, "game_day", lambda now=None: "2099-01-01")  # 换日：重新算
    fresh = TowerRun("tower_001-ttmr")
    assert not tc.load_progress(fresh) and fresh.tries == {}


def test_tower_play_loads_and_saves_progress(tmp_path):
    from hiro.runtime.tower import TowerRun

    run = TowerRun("tower_001-ttmr")
    run.after_result(16, True, 1)
    tc.save_progress(run)
    p = tp.TowerPlay(
        NS(), TowerRun("tower_001-ttmr"), idol_name="月村手毬", layers=1, loadout_path=tmp_path / "lo.json"
    )
    assert p.run.tries == {16: 1} and 16 in p.run.cleared  # 启动时读回
    rt = NS(device=NS(tap=lambda x, y: None), sleep=lambda s: None)
    p.layer = 16
    p.on_result(
        rt, None, NS(fields={"cleared": True, "layer": 16, "stars": 2, "retry": [129, 1160], "back": [350, 1160]})
    )
    assert json.loads(tc.progress_path("tower_001-ttmr").read_text(encoding="utf-8"))["tries"] == {
        "16": 2
    }  # 每次结果后落盘
