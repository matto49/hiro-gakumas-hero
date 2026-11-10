# 来源：digital-hiro tests/test_blunders.py（develop 4770d2b）
"""低级失误审计（tools/audit/blunders.py）：每个检测器用第九–十二局里真实出现过的片段做 fixture。

真实数据里还没出现过的种类（点错牌、结果页没分数、手牌重建）用真实片段做「不该报」的用例，再改一个字段做「该报」的用例。
"""

import copy
import json
from pathlib import Path

import pytest

from tools.audit import blunders as B

FIX = Path(__file__).parent / "fixtures" / "blunders"
pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="需要样例 tests/fixtures/blunders（实机片段，不随仓库发布）")


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def seg(name: str, *, steps=None, log=None, events=None, run=None) -> B.Segment:
    d = load(name) if name else {}
    s = d.get("segment", "seg")
    return B.Segment(s, B.ROOT / "traces" / s, run if run is not None else d.get("run", {}), steps if steps is not None else d.get("steps", []),
                     log if log is not None else d.get("log", []), events or [])  # fmt: skip


def kinds(findings):
    return [f.kind for f in findings]


# ------------------------------------------------------------------ 出牌
def test_skip_with_playable_blocked_by_drink_budget():
    """q12-r1 #22：专家被饮料预算硬规则拦下，结束回合；喝 初星ホエイプロテイン 期望分多 3,867。"""
    f = B.skip_with_playable(seg("skip_drink_budget"))
    assert kinds(f) == ["skip_with_playable"]
    assert f[0].impact["expected_loss"] == 3867 and "drink_budget" in f[0].detail


def test_skip_is_fine_when_it_is_best():
    s = seg("skip_drink_budget")
    for c in s.steps[0]["decision"]["candidates"]:
        if c["action"]["kind"] != "Skip":
            c["expected"] = 0
    assert not B.skip_with_playable(s)


def test_slot_mismatch():
    f = B.slot_mismatch(seg("slot_mismatch"))
    assert [x.seq for x in f] == [15, 16] and "止められない想い" in f[0].detail


def test_repeat_tap_two_cards_gone():
    """main10-r3 #58：打 えいえいおー 一次，下一帧 ゆめみごこち 也没了（双击第二下落到相邻的牌上）。"""
    f = B.wrong_card_exam(seg("repeat_tap"))
    assert kinds(f) == ["repeat_tap"] and f[0].seq == 58


def test_play_uses_reason_card_not_save_slot():
    """main12-r1 #63：决策 args.slot=3 是存档顺序；画面第 3 张是 ワクワク，实际打的是理由里的 えいえいおー+，不是点错。"""
    assert not B.wrong_card_exam(seg("exam_play_ok"))


def test_wrong_card_when_other_card_gone():
    s = seg("exam_play_ok")
    s.steps[0]["decision"]["reason"] = "预演专家：play:イメトレ+ 期望终局分 1"
    f = B.wrong_card_exam(s)
    assert kinds(f) == ["wrong_card"] and "えいえいおー" in f[0].detail


def test_select_card_real_steps_ok():
    assert not B.wrong_card_select(seg("select_card"))


def test_select_card_acquired_other_card():
    s = seg("select_card")
    st = next(x for x in s.steps if x["decision"] and x["decision"]["action"]["kind"] == "ActivateEffect")
    st["observations_after"].append({"seq": 30, "page": {"name": "ACQUIRE"}, "fields": {"card_id": "p_card-02-men-2_100"}})
    f = B.wrong_card_select(s)
    assert kinds(f) == ["wrong_card"] and "p_card-02-men-2_100" in f[0].detail


def test_hand_mismatch_from_recorder():
    ev = [{"kind": "live_hand_rebuilt", "t": 1.0, "exam": "r2", "remaining": 7, "notes": ["紅葉ランニング+ → 成長痛"]},
          {"kind": "live_hand_unfixable", "t": 2.0, "exam": "r2", "remaining": 6, "notes": ["画面 3 张、存档 4 张"]}]  # fmt: skip
    f = B.hand_mismatch(seg(None, events=ev))
    assert kinds(f) == ["hand_mismatch"] * 2 and f[0].impact["auto_fixed"] is True and f[1].impact["auto_fixed"] is False


# ------------------------------------------------------------------ 识别
def test_read_error_from_checks():
    f = B.read_error(seg("read_error"))
    assert kinds(f) == ["read_error"] and "produce.params.Da=253" in f[0].detail


def test_model_mismatch_from_checks():
    """倍率 vs 说明页真值：HUD 读错（attr_multiplier）和模型偏差（model_permil）计「读数 / 模型不一致」，不重复计进 read_error；一致的不计。"""
    steps = [{"seq": 3, "t": 0, "observation": {"page": {"name": "HIF_EXAM"}, "fields": {}}, "checks": [
        {"seq": 3, "field": "exam.multiplier_permil", "observed": 15200, "predicted": {"attr": "Vo", "value": 14810}, "verdict": "read_error",
         "rule": "attr_multiplier", "note": "说明页 Vo = 14810，差 +390‰（+2.6%）"},
        {"seq": 3, "field": "exam.model_permil.Da", "observed": 13000, "predicted": 12470, "verdict": "model_gap", "rule": "model_permil", "note": "r1"},
        {"seq": 3, "field": "exam.model_permil.Vo", "observed": 14810, "predicted": 14810, "verdict": "consistent", "rule": "model_permil"}]}]  # fmt: skip
    sg = seg("", steps=steps)
    assert kinds(B.model_mismatch(sg)) == ["model_mismatch", "model_mismatch"] and B.read_error(sg) == []
    assert "+2.6%" in B.model_mismatch(sg)[0].detail


def test_unknown_page_keeps_longest():
    """q10 #399：同一段剧情 31s、62s 各记一次，只算一条 62s。"""
    f = B.unknown_page(seg("unknown_page"))
    assert [(x.seq, x.impact["seconds"]) for x in f] == [(399, 62)]


def test_result_page_score():
    s = seg("result_page")
    assert not B.result_no_score(s)
    for st in s.steps:
        for o in B._obs_all(st):
            o["fields"].pop("score", None)
    assert kinds(B.result_no_score(s)) == ["result_no_score"]


# ------------------------------------------------------------------ 执行 / 资源
def test_special_guidance_loop_is_one_stuck():
    """q12 #383 起：特別指導 定制同一动作反复失败。只记一条空转，不再逐步记执行失败。"""
    f = B.execution(seg("special_guidance_loop"))
    stuck = [x for x in f if x.kind == "stuck"]
    assert len(stuck) == 1 and stuck[0].impact["repeats"] >= 5
    assert len(f) <= 2  # 循环前一步（画面读数不同）最多单独一条


def test_stuck_oscillating_selection():
    """main13 R2 再挑戦 T3：同一回合 play:2 在两张牌间切换选中（体力读数 21 / 25 交替、手牌读不到），执行都算「成功」。
    状态在两个值间来回 → 空转；P 道具匹配分数每帧抖动不影响。"""
    s = seg("stuck_oscillation")
    f = B.stuck(s)
    assert kinds(f) == ["stuck"] and f[0].impact["repeats"] == 11 and "2 个画面状态间来回" in f[0].detail


def test_normal_plays_are_not_stuck():
    """正常出牌：同一动作连打，但每步状态都在变（不回到出现过的画面）。"""
    s = seg("stuck_oscillation")
    for i, st in enumerate(s.steps):
        st["observation"]["fields"]["score"] = 1000 + i
    assert not B.stuck(s)


def test_stuck_breaks_on_new_turn():
    s = seg("stuck_oscillation")
    for i, st in enumerate(s.steps):
        st["observation"]["fields"]["remaining_turns"] = 12 - i  # 每步都换回合：不是同一回合里来回
    assert not B.stuck(s)


def test_selection_logs(monkeypatch):
    """main13-r3：05:32:50 卡停在选中态再点一次（第 26 步）、05:34:36 出牌前别的牌停在选中态先取消（第 30 步）；
    「没看到 SELECT」按 battle.py 的日志格式补一行（现有日志里还没出现过）。"""
    import time

    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    try:
        s = seg("select_logs")
        s.log.append("[2026-11-02 05:34:55,100] [INFO] [hiro.produce.battle] 出牌 (332,1010)：1.6s 内没看到 SELECT，底部文字 ['SELEC1']")
        f = B.selection_log(s)
    finally:
        monkeypatch.undo()
        time.tzset()
    assert [(x.kind, x.seq) for x in f] == [("select_retap", 26), ("select_cancel", 30), ("select_not_seen", 31)]


def test_consult_shop_not_spent():
    """q12 相談：货架确认框里不是要买的卡（#234）、点「強化」没打开选卡页（#236、#237）。"""
    f = [x for x in B.execution(seg("consult_shop")) if x.kind == "shop_not_spent"]
    assert [x.seq for x in f] == [234, 236, 237]


def test_drink_replace_counted_once():
    """q11 #261 勾选框没勾上 → #262 说明「饮料替换没完成」：同一件事只记一条。"""
    f = B.execution(seg("drink_replace"))
    assert kinds(f) == ["drink_full_unhandled"]


def test_pdrink_confirm_not_closed():
    f = B.execution(seg("pdrink_confirm"))
    assert kinds(f) == ["dialog_not_closed"] and "PDRINK_MAX_CONFIRM" in f[0].detail


def test_tap_no_effect_retry():
    f = B.execution(seg("receive_retry"))
    assert kinds(f) == ["tap_no_effect"] and "受け取る" in f[0].detail


def test_exec_failed_enhance():
    f = B.execution(seg("enhance_failed"))
    assert "exec_failed" in kinds(f)


# ------------------------------------------------------------------ 流程
def test_recovered_crash_then_killed():
    """main9-r1：进入培育超时 → 进程内重来成功 → 卡在认不出的画面 64s 被停。"""
    s = seg("log_main9_r1")
    assert B.end_reason(s)[0] == "killed"
    f = B.flow([s])
    assert kinds(f) == ["crash_restart", "unknown_page"] and f[1].impact["seconds"] == 64


def test_resume_blocked_by_discard_prompt():
    s = seg("log_main12_r2")
    assert B.end_reason(s)[0] == "resume_blocked"
    assert kinds(B.flow([s])) == ["resume_failed"]


def test_crash_after_produce_end_and_manual_restart():
    """q12-r1 打到 PRODUCE_END 后收尾 IndexError；前面接一段被停的段 → 一次人工介入。"""
    prev = B.Segment("lead35-qualify12-20261101-164908", Path("."), {"started": 0}, [{"seq": 1, "t": 100.0, "observation": {"page": {"name": "HIF_EXAM"}}}],
                     ["[x] [INFO] [hiro.loop.resume] 直接开始主循环"], [])  # fmt: skip
    last = seg("log_q12_r1", steps=load("produce_end")["steps"])
    f = B.flow([prev, last])
    stop = [x for x in f if x.kind == "run_stopped"]
    assert {x.segment for x in stop} == {prev.name, last.name}
    assert next(x for x in stop if x.segment == last.name).impact.get("after_end") is True
    manual = [x for x in f if x.kind == "manual_intervention"]
    assert len(manual) == 1 and manual[0].segment == last.name


def test_manual_log(tmp_path):
    p = tmp_path / "manual.jsonl"
    p.write_text(json.dumps({"run": "lead35-qualify12-20261101-164908", "reason": "全屏切换", "detail": "手动点了一下"}, ensure_ascii=False) + "\n")
    f = B.manual_log([seg(None, run={}), B.Segment("lead35-qualify12-20261101-164908-r1", Path("."), {}, [], [], [])], p)
    assert kinds(f) == ["manual_intervention"] and "全屏切换" in f[0].detail


def test_retry_unused():
    """main9 r2 没过线直接点了结束培育；main10-r7 本战第 3 名但已用到上限 3 次，不算。"""
    assert kinds(B.retry_unused([seg("retry_unused")])) == ["retry_unused"]
    assert not B.retry_unused([seg("log_main10_r7")])


# ------------------------------------------------------------------ 报告 / 复盘页
def test_report_and_viewer(tmp_path):
    s = seg("repeat_tap")
    findings = B.audit([s], tmp_path / "none.jsonl")
    data = B.write("t", [s], findings, tmp_path)
    assert (tmp_path / "t-blunders.md").exists() and data["counts"]["出牌"]["repeat_tap"] == 1
    md = (tmp_path / "t-blunders.md").read_text(encoding="utf-8")
    assert "检测缺口" in md and "同一动作重复生效" in md

    import tools.viewer.build_exam_view as V

    base = "lead35-main10-20261101-124830"
    view = {"run": base, "exams": [{"steps": [{"seg": "-r3", "seq": 58}, {"seg": "-r3", "seq": 59}]}]}
    V.attach_blunders(view, copy.deepcopy(data))
    steps = view["exams"][0]["steps"]
    assert [b["kind"] for b in steps[0]["blunders"]] == ["repeat_tap"] and steps[1]["blunders"] == []
    assert view["blunders"]["total"] == 1 and view["blunders"]["items"][0]["at"] == [0, 0]


# ------------------------------------------------------------------ 考试 HUD 读数（和存档对比 / 无故减少）
def test_exam_hud_dropped_leading_one_vs_save():
    """第二十五局 R1 末回合：存档元気 1,185 / 1,283 / 1,388，HUD 读成 185 / 28 / 388（掉首位「1」）。"""
    d = load("exam_hud_save")
    s = B.Segment(d["segment"], B.ROOT / "traces" / d["segment"], {}, d["steps"], [], d["events"])
    hits = [(st["seq"], fld, obs, ref) for st, fld, obs, ref, _ in B.exam_reads(s)]
    assert (203, "exam.genki", 388, 1388) in hits and (202, "exam.genki", 28, 1283) in hits
    assert all(st != 197 for st, *_ in hits)  # 989 和存档一致
    f = B.read_error(s)
    assert any("exam.genki=388" in x.detail and "存档" in x.detail for x in f)


def test_exam_unexplained_drop():
    d = load("exam_hud_save")
    steps = copy.deepcopy(d["steps"][:2])
    steps[0]["observation"]["fields"].update(genki=1500, score=20000)
    steps[1]["observation"]["fields"].update(genki=300, score=2000)
    s = B.Segment(d["segment"], B.ROOT / "traces" / d["segment"], {}, steps, [], [])
    fields = {fld for _, fld, *_ in B.exam_reads(s)}
    assert fields == {"exam.genki", "exam.score"}
    # 上一步打的是清空元気的牌：元気减少不算读错
    slot = steps[0]["execution"]["action"]["args"]["slot"]
    for c in steps[0]["observation"]["fields"]["hand"]:
        if c["slot"] == slot:
            c["tags"] = {"clears_genki": True}
    assert {fld for _, fld, *_ in B.exam_reads(s)} == {"exam.score"}


# ------------------------------------------------------------------ 轮间饮料账
def test_interval_drinks_phantom_after_r1():
    """第二十九局：R1 最后一步画面饮料栏空（四瓶都喝了），轮间状态仍按开局 4 瓶算、判成饮料栏满。"""
    d = load("interval_drinks")
    f = B.interval_drinks([B.Segment(d["segment"], B.ROOT / "traces", {}, d["steps"], [], [])])
    assert [(x.kind, x.seq, x.impact["phantom"]) for x in f] == [("drink_ledger", 187, 4)]
    assert "剩 0 瓶" in f[0].detail and "按持有 4 瓶" in f[0].detail


def test_interval_drinks_consistent():
    d = load("interval_drinks")
    steps = copy.deepcopy(d["steps"])
    for st in steps:
        if (st.get("decision") or {}).get("decider") == "interval":
            st["state"]["produce"]["drinks"] = []
    assert not B.interval_drinks([B.Segment(d["segment"], B.ROOT / "traces", {}, steps, [], [])])


def test_interval_drinks_corrected_by_decision():
    """决策已按 R1 结束剩余取持有（warnings 报「轮间持有：…不一致，按 R1 结束剩余」）：produce.drinks 过时也不算失误。"""
    d = load("interval_drinks")
    steps = copy.deepcopy(d["steps"])
    for st in steps:
        if (st.get("decision") or {}).get("decider") == "interval":
            st["decision"]["warnings"] = ["轮间持有：produce.drinks [4 瓶]（未在本帧读到）和 R1 结束剩余 [] 不一致，按 R1 结束剩余"]
    assert not B.interval_drinks([B.Segment(d["segment"], B.ROOT / "traces", {}, steps, [], [])])
