# 来源：digital-hiro tests/test_retry_reenter_exam.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""第十八局本战 R2：点「再挑戦」后画面从结果页直接回到同一场考试，KAA 记的上一页还是 EXAM，
_dispatch 只在「上一页不是 EXAM」时进 on_exam_entered → 主循环空转（两次各卡 18 / 7 分钟）。
策略置 exam_restarted 后，下一次看到考试页要重新进入 on_exam_entered，且只进一次。"""

import hiro  # noqa: F401  兼容层（先于 kaa）
from kaa.tasks.produce.new.consts import Scene, SceneType

from hiro.loop.controller import HiroController


class Strategy:
    exam_restarted = False

    def __init__(self):
        self.entered = 0

    def on_exam_entered(self, ctx):
        self.entered += 1


def _ctrl():
    c = HiroController.__new__(HiroController)
    c.strategy, c.page, c._last_scene = Strategy(), None, None
    return c


def _tick(c, scene):
    c._rearm_exam(scene)
    c._dispatch(scene, c._last_scene)
    c._last_scene = scene


def test_retry_reenters_same_exam_once():
    c, exam = _ctrl(), Scene(SceneType.EXAM)
    _tick(c, exam)
    assert c.strategy.entered == 1
    c.strategy.exam_restarted = True  # on_exam_entered 里点了再挑戦
    _tick(c, exam)
    assert c.strategy.entered == 2 and c.strategy.exam_restarted is False
    _tick(c, exam)  # 同一场考试后续帧：不重复进入
    assert c.strategy.entered == 2


def test_without_flag_kaa_does_not_reenter():
    """旧行为（复现 bug）：没有标志时连续 EXAM 只进一次。"""
    c, exam = _ctrl(), Scene(SceneType.EXAM)
    _tick(c, exam)
    _tick(c, exam)
    assert c.strategy.entered == 1


def test_flag_waits_for_exam_page():
    """再挑戦后先过一帧别的页面（转场）：标志保留到真正看到考试页。"""
    c = _ctrl()
    c.strategy.exam_restarted = True
    c._last_scene = Scene(SceneType.EXAM)
    c._rearm_exam(Scene(SceneType.UNKNOWN))
    assert c.strategy.exam_restarted is True


def test_strategy_sets_flag_on_finals_retry(monkeypatch):
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.s = NS(note=lambda *a: None)
    st.rec = NS(log=lambda *a, **k: None)
    monkeypatch.setattr(pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", {"kind": "final", "rank": 2, "outcome": "敗退"}))
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    monkeypatch.setattr(hif, "ocr", NS(ocr=lambda: [btn]))
    st._click_retry = lambda label, b, texts, **kw: "free"
    assert st._finals_retry(None, "r2") is True and st.exam_restarted is True


def test_declined_retry_not_reopened_on_same_page(monkeypatch):
    """Windows 第七局：止损（RETRY_STOP_LOSS）取消再挑戦后，收尾循环每轮又点开确认框、再取消，一直没点「次へ」，空转看门狗停机。
    同一张结算页决定不再挑戦后，不再点开确认框；换了一张结算页（分数不同）照常判断。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.s = NS(note=lambda *a: None)
    st.rec = NS(log=lambda *a, **k: None)
    page = {"kind": "final", "rank": 3, "outcome": "敗退", "score": 756342, "round_score": 158038}
    monkeypatch.setattr(pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", page))
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    monkeypatch.setattr(hif, "ocr", NS(ocr=lambda: [btn]))
    calls = []
    st._click_retry = lambda label, b, texts, **kw: calls.append(kw) or None
    assert st._finals_retry(None, "r2") is False and st._finals_retry(None, "r2") is False and len(calls) == 1
    page.update(score=900000, round_score=300000)
    assert st._finals_retry(None, "r2") is False and len(calls) == 2


def test_retries_left_from_screen_overrides_trace_count():
    """「あと N回」以画面为准：通信エラー期间 trace 里多记的「再挑戦」不该把剩余次数吃掉。"""
    from types import SimpleNamespace as NS

    from hiro.produce.hif import screen_retries_left

    assert screen_retries_left([NS(text="あと3回"), NS(text="再挑戦")]) == 3
    assert screen_retries_left([NS(text="あと 2 回")]) == 2
    assert screen_retries_left([NS(text="次へ")]) is None


def test_comm_error_after_retry_not_counted(monkeypatch):
    """点再挑戦后弹的是通信エラー：返回 None（不记成免费再挑戦）。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    notes = []
    st.s = NS(note=notes.append, trace=NS(dir="/nonexistent/x"), notes=[])
    st._retries_so_far = lambda label: 0
    monkeypatch.setattr(hif, "device", NS(click=lambda *a: None, screenshot=lambda: None))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "ocr", NS(ocr=lambda: [NS(text="通信エラー"), NS(text="リトライしますか?"), NS(text="はい")]))
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    assert st._click_retry("r2", btn, [btn]) is None and any("通信エラー" in n for n in notes)


def _finals_strategy(tmp_path, notes_by_seg):
    """本战策略替身：trace 基名 main-x 的各段 steps.jsonl 里放给定说明（模拟之前各段的再挑戦记录）。"""
    import json
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    from kaa.config.const import HifScenario

    for name, notes in notes_by_seg.items():
        d = tmp_path / name
        d.mkdir()
        with (d / "steps.jsonl").open("w", encoding="utf-8") as f:
            for n in notes:
                f.write(json.dumps(n if isinstance(n, dict) else {"seq": 1, "notes": [n]}, ensure_ascii=False) + "\n")
    cur = tmp_path / "main-x-r9"
    cur.mkdir()
    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.s = NS(note=lambda *a: None, notes=[], trace=NS(dir=str(cur)))
    st.scenario = HifScenario.MAIN
    st.exam_count = 1
    return st


def test_retry_cap_ignores_label(tmp_path):
    """DMM dmm2：R2 四次挑战标签是 lesson-1 / lesson-2 / r2 / lesson-3。不管标签，本局累计 3 次「本战…使用再挑戦」就到上限。"""
    notes = ["本战敗退（第 3 名，总分 374041），使用再挑戦（free）", "本战敗退（第 3 名，总分 414205），使用再挑戦（ticket）",
             "本战敗退（第 3 名，总分 423062），使用再挑戦（ticket）"]  # fmt: skip
    st = _finals_strategy(tmp_path, {"main-x": notes})
    for label in ("lesson-3", "r2", "lesson-9"):
        assert st._retries_so_far(label) >= st.MAX_RETRIES_PER_EXAM


def test_r2_label_after_r1_result_ignores_scene(tmp_path, monkeypatch):
    """R1 结算过（trace 里有 HIF_FINALS_RANKING round_result）之后的考试一律 r2，不看那一刻的场景（DMM 过场里不是 HIF_EXAM）。"""
    import hiro.vision.scene as scene

    r1 = {"seq": 147, "observation": {"page": {"name": "HIF_FINALS_RANKING"}, "fields": {"kind": "round_result"}}}
    st = _finals_strategy(tmp_path, {"main-x": [r1]})
    monkeypatch.setattr(scene, "classify_current", lambda: (_ for _ in ()).throw(AssertionError("不该再看场景")))
    assert st._battle_label(None) == "r2"


def test_exam_scene_settles_before_lesson(tmp_path, monkeypatch):
    """没有 R1 结算记录、开头那一刻认不出：连看几秒，认出 HIF_EXAM 就不当レッスン。"""
    from types import SimpleNamespace as NS

    import hiro.vision.scene as scene

    st = _finals_strategy(tmp_path, {})
    seq = iter(["IDLE", "IDLE", "HIF_EXAM"])
    monkeypatch.setattr(scene, "classify_current", lambda: NS(scene=next(seq, "HIF_EXAM")))
    t = [0.0]
    assert st._settle_exam_scene("IDLE", clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)) == "HIF_EXAM"
    seq2 = iter(["IDLE"] * 100)
    monkeypatch.setattr(scene, "classify_current", lambda: NS(scene=next(seq2)))
    t[0] = 0.0
    assert st._settle_exam_scene("IDLE", clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)) == "IDLE"
    assert t[0] >= st.EXAM_SCENE_SETTLE_S


def test_fourth_finals_retry_refused_without_screen_count(tmp_path, monkeypatch):
    """画面上没读到「あと N回」时按 trace 累计：三次标签各不相同的「本战…使用再挑戦」之后，第 4 次（标签又变了）必须拒绝、不点按钮。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    notes = ["lesson-1 本战敗退（第 3 名），使用再挑戦（free）", "lesson-2 本战敗退（第 3 名），使用再挑戦（ticket）",
             "r2 本战敗退（第 3 名），使用再挑戦（ticket）"]  # fmt: skip
    st = _finals_strategy(tmp_path, {"main-x": notes})
    clicks = []
    monkeypatch.setattr(hif, "device", NS(click=lambda *a: clicks.append(a)))
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    assert st._click_retry("lesson-3", btn, [btn]) is None and clicks == []
