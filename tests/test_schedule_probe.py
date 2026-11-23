"""审查日程探测（HIRO_EXAM_SCHEDULE_PROBE，默认关）：点开 → 存帧 → 关掉 → 回到考试画面才继续；关不掉就停机。
假面板：考试帧中间刷白代替「盖上来的面板」，OCR 用假的。"""

from pathlib import Path

from hiro.exec import schedule_probe as sp
from hiro.loop.errors import ResumeBlocked

import cv2
import numpy as np
import pytest

FIX = Path(__file__).parent / "fixtures" / "exam" / "status" / "dmmw1q-133-faint-slot.jpg"


class Screen:
    def __init__(self, exam_img, close_by):
        # 「盖上来的面板」：考试帧中间一大块刷白（打开按白色大面板认，hiro.vision.schedule.panel_open）
        self.exam, self.panel = exam_img, exam_img.copy()
        self.panel[300:1100, 40:680] = 255
        self.state, self.close_by, self.taps, self.t, self.logged = "exam", close_by, [], 0.0, []

    def shoot(self):
        return self.exam if self.state == "exam" else self.panel

    def click(self, x, y):
        self.taps.append((x, y))
        if self.state == "exam" and (x, y) == sp.BONUS_TAP and self.close_by != "never_opens":
            self.state = "panel"
        elif self.state == "panel" and (
            (self.close_by == "text" and (x, y) == (360, 1180)) or (self.close_by == "retap" and (x, y) == sp.BONUS_TAP)
        ):
            self.state = "exam"

    def ocr(self, img):
        return [("閉じる", 0.99, (320, 1160, 400, 1200))] if self.state == "panel" and self.close_by == "text" else []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def run(self):
        return sp.probe(self.exam, exam="qualify-1", shoot=self.shoot, click=self.click, ocr_boxes=self.ocr, is_exam=lambda im: im is self.exam,
                        log=lambda kind, **k: self.logged.append(kind), clock=self.clock, sleep=self.sleep)  # fmt: skip


@pytest.fixture
def exam_img():
    if not FIX.exists():
        pytest.skip(f"需要样例 {FIX}")
    return cv2.imread(str(FIX))


def test_default_off(monkeypatch):
    monkeypatch.delenv(sp.ENABLED_ENV, raising=False)
    assert not sp.enabled()


def test_closes_with_close_text(exam_img):
    s = Screen(exam_img, "text")
    out = s.run()
    assert out["opened"] and out["closed"] and out["close_by"] == "close_text" and s.state == "exam"
    assert s.taps == [
        sp.BONUS_TAP,
        sp.CLOSE_X,
        sp.CLOSE_X,
        (360, 1180),
    ]  # 先点底部 ✕ 两次（这个假面板不认），再点「閉じる」
    assert s.logged == ["exam_schedule_probe_open", "exam_schedule_probe_closed"]
    assert out["total_ms"] >= out["open_ms"]


def test_retap_when_no_close_text(exam_img):
    s = Screen(exam_img, "retap")
    out = s.run()
    assert (
        out["closed"] and out["close_by"] == "retap" and s.taps == [sp.BONUS_TAP, sp.CLOSE_X, sp.CLOSE_X, sp.BONUS_TAP]
    )


def test_not_opened_taps_once(exam_img):
    s = Screen(exam_img, "never_opens")
    out = s.run()
    assert not out["opened"] and out["closed"] and s.taps == [sp.BONUS_TAP]


def test_stuck_panel_stops(exam_img):
    s = Screen(exam_img, "stuck")
    with pytest.raises(ResumeBlocked):
        s.run()
    assert s.state == "panel" and s.taps == [
        sp.BONUS_TAP,
        sp.CLOSE_X,
        sp.CLOSE_X,
        sp.BONUS_TAP,
    ]  # 只试了 ✕ 和点同一块，没在考试里乱点


def test_tap_far_from_hand():
    x, y = sp.BONUS_TAP
    assert 9 <= x <= 222 and 40 <= y <= 140 and y < 600  # BonusButtonArea 内，远离手牌（y≈860 以下）


# ---------------------------------------------------------------- 只在考试 / 试镜点；关闭后认课程 / 初 / N.I.A 考试 HUD（协调 11-18 初 PRO 实机停机）
@pytest.mark.parametrize(("label", "ok"), [
    ("qualify-1", True), ("r1", True), ("r2", True), ("hajime-mid1", True), ("hajime-final", True), ("nia-mid1", True),
    ("nia-final#audition-2", True), ("hajime-lesson-da", False), ("hajime-lesson-vi-hard", False), ("hajime-lesson", False),
    ("tower", False), ("tower-12", False), (None, False), ("", False),
])  # fmt: skip
def test_should_probe_only_exams(label, ok):
    assert sp.should_probe(label) is ok


@pytest.mark.parametrize(("name", "ok"), [
    ("hajime-lesson-probe-stuck.png", True),  # 课程画面（原来只认 H.I.F 考试，判成没关掉停机）
    ("hajime-lesson-vo.jpg", True), ("hajime-exam.jpg", True),  # 初的考试判页认成 TOWER_EXAM
    ("hajime-action-lessons.jpg", False),
])  # fmt: skip
def test_back_in_exam_recognizes_lesson_and_hajime_exam(name, ok):
    p = Path(__file__).parent / "fixtures" / "pages" / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    from hiro.vision.offline import load

    assert sp.back_in_exam(load(cv2.imread(str(p)))) is ok


# ---------------------------------------------------------------- 面板读数（初 PRO 中間第 1 回合前实拍，协调 11-18）
PANEL = Path(__file__).parent / "fixtures" / "pages" / "exam-schedule-panel-hajime-mid1.png"


def _panel_img():
    if not PANEL.exists():
        pytest.skip("需要实机截图 pages/exam-schedule-panel-hajime-mid1.png")
    return cv2.imread(str(PANEL))


def test_read_schedule_panel_real_frame():
    from hiro.vision.schedule import read_schedule_panel, turn_types

    p = read_schedule_panel(_panel_img())
    assert p["current_turn"] == 1 and p["pass_rank"] == 3 and p["pass_score"] == 1700
    assert (
        "中間" in p["pass_text"] and "突破" in p["pass_text"]
    )  # 过线条件原文 → exam.pass_line.stage（8a3a1e7 按关键词认场次）
    assert p["permil"] == {"Vo": 6440, "Da": 6800, "Vi": 4280}
    assert turn_types([p], 9) == ["Vo", "Vi", "Da", "Da", "Vo", "Vo", "Vi", "Da", "Vo"]  # 协调核对的真值


def test_panel_rows_by_icon_not_text():
    """属性按每行图标颜色认（文字只兜底）：9 行图标单独读也全对。"""
    from hiro.vision import schedule as sc

    hsv = cv2.cvtColor(_panel_img(), cv2.COLOR_BGR2HSV)
    assert [sc._attr_by_icon(hsv, y) for y in (356, 445, 533, 621, 710, 797, 885, 974, 1061)] == [
        "Vo",
        "Vi",
        "Da",
        "Da",
        "Vo",
        "Vo",
        "Vi",
        "Da",
        "Vo",
    ]


def test_turn_types_merges_scrolled_screens_and_marks_missing():
    from hiro.vision.schedule import turn_types

    first = {"current_turn": 1, "rows": dict.fromkeys(range(9), "Vo")}
    second = {"current_turn": 1, "rows": {8: "Vo", 9: "Da", 10: None, 11: "Vi"}}
    assert turn_types([first, second], 12) == ["Vo"] * 9 + ["Da", None, "Vi"]
    assert turn_types([{"current_turn": 3, "rows": {0: "Da", 1: "Vi"}}], 5) == [
        None,
        None,
        "Da",
        "Vi",
        None,
    ]  # 第 3 回合才点开
    assert turn_types([{"current_turn": None, "rows": {0: "Da"}}], 5) is None


def test_probe_reads_panel_and_closes_with_x():
    """面板读数 + 关闭先点底部 ✕ (360,1160)（原来先找文字再点左上，面板盖在 y≥107，点左上关不掉）。"""
    from hiro.vision.schedule import read_schedule_panel

    exam_img, panel_img = (_panel_img() * 0.5).astype("uint8"), _panel_img()
    state = {"s": "exam"}
    taps = []

    def click(x, y):
        taps.append((x, y))
        if state["s"] == "exam" and (x, y) == sp.BONUS_TAP:
            state["s"] = "panel"
        elif state["s"] == "panel" and (x, y) == (360, 1160):
            state["s"] = "exam"

    t = [0.0]
    out = sp.probe(exam_img, exam="hajime-mid1", shoot=lambda: exam_img if state["s"] == "exam" else panel_img, click=click,
                   ocr_boxes=lambda img: [], is_exam=lambda im: im is exam_img, clock=lambda: t[0],
                   sleep=lambda s: t.__setitem__(0, t[0] + s), read_panel=read_schedule_panel, total_turns=9)  # fmt: skip
    assert out["closed"] and out["close_by"] == "close_x" and taps == [sp.BONUS_TAP, (360, 1160)]
    assert out["panel"]["turn_types"] == ["Vo", "Vi", "Da", "Da", "Vo", "Vo", "Vi", "Da", "Vo"]


def test_apply_panel_writes_exam_state():
    from types import SimpleNamespace as NS

    from hiro.contracts import GameState

    st = GameState(exam={"attr_permil": {"Vo": 1000}})
    mem = NS(attr_permil={})
    sp.apply_panel({"turn_types": ["Vo", "Da"], "permil": {"Vo": 6440, "Da": 6800, "Vi": 4280}, "pass_rank": 3, "pass_score": 1700},
                   NS(state=st), mem)  # fmt: skip
    assert st.exam["turn_types"] == ["Vo", "Da"] and st.sources["exam.turn_types"].origin == "screen"
    assert st.exam["attr_permil"] == {"Vo": 6440, "Da": 6800, "Vi": 4280} and mem.attr_permil["Da"] == 6800
    assert st.exam["pass_line"] == {"rank": 3, "score": 1700, "stage": None}


# ---------------------------------------------------------------- 最終面板（滑过、有回合效果行，协调 11-18 初 PRO 最終）
FINAL = Path(__file__).parent / "fixtures" / "pages" / "exam-schedule-panel-hajime-final-scrolled.png"


def _final_img():
    if not FINAL.exists():
        pytest.skip("需要实机截图 pages/exam-schedule-panel-hajime-final-scrolled.png")
    return cv2.imread(str(FINAL))


def test_final_scrolled_panel_rows_and_effects():
    """行高不一致（回合效果行）也按每行「Nターン後」文字定位；「ろターン後」= 3；回合效果按行读出。"""
    from hiro.vision.schedule import read_schedule_panel

    p = read_schedule_panel(_final_img())
    assert p["rows"] == {1: "Vo", 2: "Vi", 3: "Da", 4: "Vi", 5: "Vo", 6: "Vo", 7: "Da"}
    assert p["effects"] == {1: "やる気+5", 5: "元気が18以上の場合、やる気+8"}
    assert p["permil"] == {"Vo": 12860, "Da": 13320, "Vi": 7580} and p["pass_rank"] == 3 and p["pass_score"] is None
    assert "最終" in (p["pass_text"] or "")


def test_turn_effects_aligned_with_turns():
    from hiro.vision.schedule import turn_effects

    assert (
        turn_effects([{"current_turn": 1, "effects": {1: "やる気+5", 5: "x"}}], 11)
        == [None, "やる気+5", None, None, None, "x"] + [None] * 5
    )


@pytest.mark.parametrize(
    ("name", "open_"),
    [
        ("exam-schedule-panel-hajime-final-scrolled.png", True),
        ("exam-schedule-panel-hajime-mid1.png", True),
        ("hajime-exam.jpg", False),
        ("hajime-lesson-probe-stuck.png", False),
        ("hajime-exam-result.jpg", False),
    ],
)
def test_panel_open(name, open_):
    from hiro.vision.offline import load
    from hiro.vision.schedule import panel_open

    p = Path(__file__).parent / "fixtures" / "pages" / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    assert panel_open(load(cv2.imread(str(p)))) is open_


def test_close_after_scroll_first_tap_only_stops_inertia():
    """滑过以后：第一下点 ✕ 只停住了惯性滚动（实机 11-18），第二下才关上；之前要等停稳。"""
    import numpy as np

    panel = _final_img()
    exam_img = np.full((1280, 720, 3), 60, np.uint8)
    state = {"s": "exam", "x_taps": 0}
    taps = []

    def click(x, y):
        taps.append((x, y))
        if state["s"] == "exam" and (x, y) == sp.BONUS_TAP:
            state["s"] = "panel"
        elif state["s"] == "panel" and (x, y) == sp.CLOSE_X:
            state["x_taps"] += 1
            if state["x_taps"] >= 2:
                state["s"] = "exam"

    t = [0.0]
    out = sp.probe(exam_img, exam="hajime-final", shoot=lambda: exam_img if state["s"] == "exam" else panel, click=click,
                   ocr_boxes=lambda img: [], is_exam=lambda im: im is exam_img, clock=lambda: t[0],
                   sleep=lambda s: t.__setitem__(0, t[0] + s))  # fmt: skip
    assert out["closed"] and out["close_by"] == "close_x" and taps == [sp.BONUS_TAP, sp.CLOSE_X, sp.CLOSE_X]


def test_apply_panel_writes_turn_effects():
    from types import SimpleNamespace as NS

    from hiro.contracts import GameState

    st = GameState(exam={})
    sp.apply_panel({"turn_types": ["Vo"], "turn_effects": [None, "やる気+5"]}, NS(state=st), NS(attr_permil={}))
    assert st.exam["turn_effects"] == [None, "やる気+5"] and st.sources["exam.turn_effects"].origin == "screen"


def test_pass_line_stage_keeps_condition_text():
    """8a3a1e7 11-18：MASTER 那局 pass_line 没有原文，场次只能按回合数猜。面板过线条件原文写进 exam.pass_line.stage；
    只有原文、没读出名次 / 分数也写（N.I.A「…オーディション突破」）。"""
    from types import SimpleNamespace as NS

    from hiro.contracts import GameState
    from hiro.vision.schedule import read_schedule_panel

    import numpy as np

    def box(text, x, y):
        return NS(text=text, rect=NS(center=NS(x=x, y=y)))

    img = np.zeros((1280, 720, 3), np.uint8)
    p = read_schedule_panel(img, texts=[box("現在1ターン目", 360, 120), box("3位以上で最終試験突破", 360, 170)])
    assert p["pass_text"] == "3位以上で最終試験突破" and p["pass_rank"] == 3
    n = read_schedule_panel(img, texts=[box("現在1ターン目", 360, 120), box("1位で2次オーディション突破", 360, 170)])
    assert n["pass_text"] == "1位で2次オーディション突破" and n["pass_rank"] == 1
    st = GameState(exam={})
    sp.apply_panel({"pass_text": "最終オーディション突破"}, NS(state=st), None)
    assert st.exam["pass_line"] == {"rank": None, "score": None, "stage": "最終オーディション突破"}
    assert st.sources["exam.pass_line"].origin == "screen"


def test_nia_dancing_idol_is_not_opened():
    """协调 11-18 N.I.A MASTER nia-mid1#audition-3：点了以后面板没出来，偶像在跳舞、整屏差过阈值，原来判「打开了」，读数全 None，
    还去点了出牌区下面 ✕ 的位置。现在按白色大面板认：没出来就不点关闭。"""
    p = Path(__file__).parent / "fixtures" / "pages" / "nia-exam-probe-not-opened.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 pages/nia-exam-probe-not-opened.jpg")
    dancing = cv2.imread(str(p))
    before = cv2.flip(dancing, 1)  # 和打开前差很多（跳舞）
    taps, t = [], [0.0]
    out = sp.probe(before, exam="nia-mid1#audition-3", shoot=lambda: dancing, click=lambda x, y: taps.append((x, y)),
                   ocr_boxes=lambda img: [], is_exam=lambda im: True, clock=lambda: t[0],
                   sleep=lambda s: t.__setitem__(0, t[0] + s))  # fmt: skip
    assert sp.screen_diff(before, dancing) >= sp.CHANGED_MIN
    assert out["opened"] is False and out["close_by"] == "not_opened" and taps == [sp.BONUS_TAP]


# ---------------------------------------------------------------- 倍率合理性 / 当前回合那一行（协调 11-19 N.I.A MASTER 最終）
def test_permil_outlier_dropped():
    """Da 1467% 读成 31467%（图标认成「3」）：和另外两项比差 5 倍以上，丢掉，不修补；另外两项照用。"""
    from hiro.vision.schedule import sane_permil

    assert sane_permil({"Vo": 14090, "Da": 314670, "Vi": 10930}) == {"Vo": 14090, "Vi": 10930}
    assert sane_permil({"Vo": 6440, "Da": 6800, "Vi": 4280}) == {"Vo": 6440, "Da": 6800, "Vi": 4280}
    assert sane_permil({"Vo": 6440, "Da": 64400}) is None  # 两项互相差 10 倍：分不出哪个错
    assert sane_permil({}) is None


def test_merge_permil_takes_first_sane_per_attr():
    assert sp._merge_permil([{"permil": {"Vo": 14090, "Vi": 10930}}, {"permil": {"Vo": 14090, "Da": 14670, "Vi": 10930}}]) == \
        {"Vo": 14090, "Vi": 10930, "Da": 14670}  # fmt: skip
    assert sp._merge_permil([{"permil": None}]) is None


def test_reads_current_row_from_top_first():
    """面板打开时第一行已是「1ターン後」：先往下拖回顶读「現在のターン」，再往后滑；当前回合那一项有值。"""
    reads = iter([{"current_turn": 1, "rows": {1: "Vi", 2: "Vo", 3: "Vo", 4: "Vo", 5: "Vo", 6: "Da", 7: "Vi"}},
                  {"current_turn": 1, "rows": {0: "Da", 1: "Vi", 2: "Vo", 3: "Vo", 4: "Vo", 5: "Vo", 6: "Da"}},
                  {"current_turn": 1, "rows": {5: "Vo", 6: "Da", 7: "Vi", 8: "Da", 9: "Vi", 10: "Da", 11: "Vo"}}])  # fmt: skip
    swipes, t = [], [0.0]
    img = np.zeros((1280, 720, 3), np.uint8)
    panels = sp._read_panels(img, lambda im: next(reads, None), lambda *a: swipes.append(a[:4]), 12, lambda: img,
                             lambda s: t.__setitem__(0, t[0] + s), None, "nia-final", clock=lambda: t[0])  # fmt: skip
    from hiro.vision.schedule import turn_types

    assert swipes[0] == (*sp.TOP_FROM, *sp.TOP_TO) and swipes[1] == (*sp.SCROLL_FROM, *sp.SCROLL_TO)
    assert turn_types(panels, 12) == ["Da", "Vi", "Vo", "Vo", "Vo", "Vo", "Da", "Vi", "Da", "Vi", "Da", "Vo"]


def test_nia_final_panel_real_frames():
    """N.I.A 最終实拍：过线条件原文、三项倍率、行。"""
    from hiro.vision.schedule import read_schedule_panel

    p1 = Path(__file__).parent / "fixtures" / "pages" / "exam-schedule-panel-nia-final.jpg"
    if not p1.exists():
        pytest.skip("需要实机截图 pages/exam-schedule-panel-nia-final.jpg")
    p = read_schedule_panel(cv2.imread(str(p1)))
    assert p["pass_text"] == "1位で最終オーディション突破" and p["pass_rank"] == 1 and p["current_turn"] == 1
    assert p["permil"] == {"Vo": 14090, "Da": 14670, "Vi": 10930}
    assert p["rows"] == {1: "Vi", 2: "Vo", 3: "Vo", 4: "Vo", 5: "Vo", 6: "Da", 7: "Vi"}
