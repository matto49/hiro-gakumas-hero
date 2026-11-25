"""认不出的画面交给 LLM 看图（hiro.loop.rescue）：安全拦截、框复核、同屏次数上限、调用失败停机、留档。"""

import json

from hiro.brain.llm import MockLLM, prompt_version
from hiro.brain.llm import MockLLM as _Mock
from hiro.loop import rescue
from hiro.loop.rescue import Rescuer, check

import numpy as np
import pytest

NEED_IMAGE = pytest.mark.skipif(
    not hasattr(_Mock, "ask_json_image"), reason="开源 hiro.brain.llm 还没有看图接口 ask_json_image，等策略层同步"
)


COMM_ERROR = [("通信エラー", 1.0, (260, 480, 460, 520)), ("通信中にエラーが発生しました", 1.0, (150, 600, 570, 640)),
              ("タイトルへ", 1.0, (120, 760, 300, 800)), ("リトライ", 1.0, (420, 760, 600, 800))]  # fmt: skip
BUY = [("ジュエル消費確認", 1.0, (220, 480, 500, 520)), ("ジュエルを30個消費して回復しますか", 1.0, (100, 600, 620, 640)),
       ("キャンセル", 1.0, (120, 760, 300, 800)), ("購入する", 1.0, (420, 760, 600, 800))]  # fmt: skip
CHOICE = [
    ("受け取るスキルカードを選んでください。", 1.0, (60, 460, 660, 500)),
    ("受け取る", 1.0, (280, 1090, 440, 1130)),
]
START = [("開始確認", 1.0, (40, 20, 200, 60)), ("H.I.F本戦へ", 1.0, (400, 1180, 680, 1230))]
PLAIN = [("TAP TO NEXT", 1.0, (260, 1100, 460, 1140)), ("OK", 1.0, (330, 900, 390, 940))]


def _img(v=40):
    return np.full((1280, 720, 3), v, np.uint8)


def test_prompt_versioned():
    assert prompt_version("rescue.md").startswith("rescue@") and "?" not in prompt_version("rescue.md")


@pytest.mark.parametrize("reply, boxes, action, blocked", [
    ({"action": "tap_box", "box": 3}, COMM_ERROR, "tap_box", False),  # リトライ
    ({"action": "tap_box", "box": 2}, COMM_ERROR, "stop", True),  # タイトルへ：危险词
    ({"action": "tap_box", "box": 3}, BUY, "stop", True),  # 購入する
    ({"action": "tap_box", "box": 1}, BUY, "stop", True),  # 正文含ジュエル
    ({"action": "tap_box", "box": 2}, BUY, "stop", True),  # 有ジュエル的画面上一律不点（连キャンセル也不点）
    ({"action": "tap_blank"}, BUY, "stop", True),  # 危险画面不点空白
    ({"action": "tap_blank"}, PLAIN, "tap_blank", False),
    ({"action": "tap_box", "box": 1}, CHOICE, "stop", True),  # 选择页不点框
    ({"action": "tap_blank"}, CHOICE, "tap_blank", False),
    ({"action": "tap_box", "box": 1}, START, "stop", True),  # 开局确认：画面有「開始確認」
    ({"action": "tap_box", "box": 9}, PLAIN, "stop", True),  # 编号不在列表
    ({"action": "tap_box", "box": "1"}, PLAIN, "stop", True),
    ({"action": "click", "x": 100, "y": 200}, PLAIN, "stop", True),  # 不允许任意坐标
    ({"action": "wait", "seconds": 99}, PLAIN, "wait", False),
    ({"action": "stop"}, PLAIN, "stop", False),
    (None, PLAIN, "stop", True),
])  # fmt: skip
def test_check(reply, boxes, action, blocked):
    a, why = check(reply, boxes)
    assert a["action"] == action
    assert (why is not None) == blocked
    if action == "wait":
        assert a["seconds"] == 10.0


@pytest.mark.parametrize("words", [("破棄",), ("リタイア",), ("開始する",), ("課金",), ("有償",)])  # 画面级
def test_check_danger_words(words):
    boxes = [(f"データを{words[0]}しますか", 1.0, (100, 500, 600, 540)), ("いいえ", 1.0, (120, 760, 300, 800)),
             (f"{words[0]}", 1.0, (420, 760, 600, 800))]  # fmt: skip
    assert check({"action": "tap_box", "box": 2}, boxes)[0]["action"] == "stop"
    assert check({"action": "tap_box", "box": 1}, boxes)[0]["action"] == "stop"
    assert check({"action": "tap_blank"}, boxes)[0]["action"] == "stop"
    assert check({"action": "wait", "seconds": 3}, boxes)[0]["action"] == "wait"  # 等待 / 停机不受限


class _Fake:
    """可控的截图 / OCR / 点击：click 后把画面换成 after。"""

    def __init__(self, boxes, *, change=True):
        self.img, self.boxes, self.change = _img(), boxes, change
        self.clicks, self.sleeps = [], []

    def shoot(self):
        return self.img

    def ocr(self, img):
        return self.boxes

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.change:
            self.img = _img(200)


def _rescuer(fake, replies, tmp_path=None, **kw):
    t = [1000.0]
    r = Rescuer(MockLLM(replies=list(replies)), ocr=fake.ocr, shoot=fake.shoot, click=fake.click, sleep=fake.sleeps.append,
                clock=lambda: t[0], out_dir=tmp_path, enabled=True, **kw)  # fmt: skip
    return r, t


@NEED_IMAGE
def test_tap_retry_and_archive(tmp_path):
    f = _Fake(COMM_ERROR)
    r, _ = _rescuer(f, [{"page": "通信エラー", "reason": "选リトライ", "action": "tap_box", "box": 3}], tmp_path)
    rec = r.tick(f.img, {"mode": "finals"})
    assert f.clicks == [(510, 780)]
    assert rec["changed"] and rec["page"] == "通信エラー" and not rec.get("blocked")
    d = json.loads((tmp_path / next(p.name for p in tmp_path.iterdir()) / "record.json").read_text(encoding="utf-8"))
    assert d["prompt"] == prompt_version("rescue.md") and d["boxes"][3][0] == "リトライ"
    assert "3: リトライ @ (510, 780)" in r.llm.calls[0][1]
    assert r.summary() == {"count": 1, "actions": {"tap_box": 1}, "blocked": 0, "pages": {"通信エラー": 1}}


@NEED_IMAGE
def test_blocked_purchase_no_click():
    f = _Fake(BUY)
    r, _ = _rescuer(f, [{"page": "回复确认", "reason": "回复体力", "action": "tap_box", "box": 3}])
    rec = r.tick(f.img, {})
    assert f.clicks == [] and rec["action"]["action"] == "stop" and "危险词" in rec["blocked"]


@NEED_IMAGE
def test_verify_box_before_tap():
    f = _Fake(PLAIN)
    r, _ = _rescuer(f, [{"action": "tap_box", "box": 1}])
    first = [True]

    def ocr(img):  # 第一次（给 LLM 看）有 OK；复核时 OK 不见了
        if first[0]:
            first[0] = False
            return PLAIN
        return PLAIN[:1]

    f.ocr = r.ocr = ocr
    rec = r.tick(f.img, {})
    assert f.clicks == [] and "复核" in rec["skipped"]


def test_screen_changed_while_thinking_no_tap():
    f = _Fake(PLAIN)
    r, _ = _rescuer(f, [{"action": "tap_box", "box": 1}])
    img = f.img
    f.img = _img(220)  # LLM 想的时候画面变了
    rec = r.tick(img, {})
    assert f.clicks == [] and rec["changed"] and "画面已变" in rec["skipped"] and rec["action"]["action"] == "none"


@NEED_IMAGE
def test_same_action_twice_stops():
    f = _Fake(PLAIN, change=False)
    r, t = _rescuer(f, [{"action": "tap_blank"}, {"action": "tap_blank"}])
    assert r.tick(f.img, {})["action"]["action"] == "tap_blank"
    t[0] += 10
    rec = r.tick(f.img, {})
    assert rec["action"]["action"] == "stop" and "同一动作" in rec["blocked"] and len(f.clicks) == 1


@NEED_IMAGE
def test_max_per_screen():
    f = _Fake(PLAIN, change=False)
    r, t = _rescuer(
        f,
        [
            {"action": "wait", "seconds": 2},
            {"action": "tap_blank"},
            {"action": "wait", "seconds": 3},
            {"action": "tap_blank"},
        ],
    )
    acts = []
    for _ in range(4):
        acts.append(r.tick(f.img, {})["action"]["action"])
        t[0] += 10
    assert acts == ["wait", "tap_blank", "wait", "stop"]
    assert len(r.llm.calls) == 3


@NEED_IMAGE
def test_new_screen_resets_count():
    f = _Fake(PLAIN, change=False)
    r, t = _rescuer(f, [{"action": "tap_blank"}] * 4)
    for v in (40, 120, 200, 40):
        f.img = _img(v)
        assert r.tick(f.img, {})["action"]["action"] == "tap_blank"
        t[0] += 10


@NEED_IMAGE
def test_window_limit():
    f = _Fake(PLAIN)
    r, t = _rescuer(f, [{"action": "tap_blank"}] * 20)
    acts = []
    for i in range(rescue.MAX_PER_WINDOW[0] + 1):
        f.img = _img(20 * i)
        acts.append(r.tick(f.img, {})["action"]["action"])
        t[0] += 10
    assert acts[-1] == "stop" and set(acts[:-1]) == {"tap_blank"}


@NEED_IMAGE
def test_llm_failure_stops():
    f = _Fake(PLAIN)
    r, _ = _rescuer(f, [TimeoutError("timeout")])
    rec = r.tick(f.img, {})
    assert rec["action"]["action"] == "stop" and "调用失败" in rec["blocked"] and f.clicks == []


@NEED_IMAGE
def test_garbage_reply_stops():
    f = _Fake(PLAIN)
    r, _ = _rescuer(f, ["我觉得应该点 OK"])
    assert r.tick(f.img, {})["action"]["action"] == "stop" and f.clicks == []


@NEED_IMAGE
def test_due():
    f = _Fake(PLAIN)
    r, t = _rescuer(f, [{"action": "wait", "seconds": 1}])
    assert not r.due(5) and r.due(rescue.RESCUE_AFTER) and r.due(rescue.RESCUE_AFTER_DIALOG, dialog=True)
    r.tick(f.img, {})
    assert not r.due(100)  # 刚问过：隔 RESCUE_EVERY
    t[0] += rescue.RESCUE_EVERY
    assert r.due(100)
    r.enabled = False
    assert not r.due(100)


class _Box:
    def __init__(self, text, x1, y1, x2, y2):
        self.text = text
        self.rect = type(
            "R",
            (),
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "center": type("C", (), {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2})(),
            },
        )()


@pytest.mark.parametrize("button", ["除去する", "削除する", "タイトルへ", "H.I.F本戦へ"])
def test_target_danger_buttons(button):
    """删卡 / 回标题 / 开局按钮：目标框禁点（画面上有这些字但不点它们时不受影响）。"""
    boxes = [
        ("確認", 1.0, (260, 480, 460, 520)),
        ("キャンセル", 1.0, (120, 760, 300, 800)),
        (button, 1.0, (420, 760, 600, 800)),
    ]
    assert check({"action": "tap_box", "box": 2}, boxes)[0]["action"] == "stop"


@NEED_IMAGE
def test_tap_blank_prefers_upper_blank():
    effect_bar = [("好印象効果のスキルカード獲得時、ダンスが700以上の", 1.0, (201, 450, 634, 473))]
    from hiro.loop.advance import ALT_TAP

    assert check({"action": "tap_blank"}, effect_bar)[0]["xy"] == ALT_TAP
    near = effect_bar + [("お知らせ", 1.0, (300, 230, 420, 270))]
    assert check({"action": "tap_blank"}, near)[0]["xy"] == rescue.BLANK_TAP


@NEED_IMAGE
def test_animated_background_same_texts_not_voided():
    """第二十三局：获得演出背景一直闪，LLM 回复都被当成「画面已变」作废。像素变了但文字一半以上还在 = 同一画面，照常执行。"""
    boxes = [
        ("Da.公開レッスン", 1.0, (35, 41, 186, 63)),
        ("審査基準", 1.0, (549, 30, 631, 52)),
        ("好印象効果のスキルカード獲得時、ダンスが700以上の", 1.0, (201, 450, 634, 473)),
        ("（プロデュース中2回）", 1.0, (208, 528, 378, 547)),
    ]
    f = _Fake(boxes)
    r, _ = _rescuer(f, [{"page": "P 道具效果说明", "reason": "无按钮", "action": "tap_blank"}])
    img = f.img
    f.img = _img(90)  # 背景闪光：像素差很大
    rec = r.tick(img, {})
    from hiro.loop.advance import ALT_TAP

    assert not rec.get("skipped") and f.clicks == [ALT_TAP]


def test_exam_screen_never_tapped():
    """决策审 1.2：考试画面 (360,250) 附近没有文字框（c38 实测），LLM 答错成 tap_blank 也不能点到排名区；点框同样不点。"""
    exam = [
        ("残りターン", 1.0, (20, 100, 140, 130)),
        ("ビジュアル", 1.0, (500, 40, 600, 70)),
        ("SKIP", 1.0, (620, 900, 700, 940)),
    ]
    from hiro.loop.advance import alt_clear

    assert not alt_clear(exam)
    assert check({"action": "tap_blank"}, exam)[0]["action"] == "stop"
    assert check({"action": "tap_box", "box": 2}, exam)[0]["action"] == "stop"
    assert check({"action": "wait", "seconds": 2}, exam)[0]["action"] == "wait"
