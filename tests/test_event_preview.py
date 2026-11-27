"""11-27 实机（刷回忆第一局）：二选一事件只看得到选中那项的效果，另一项没读到 → 规则选不了、没开 LLM 就停。
现在像玩家一样逐个单击选项（只选中不确认），读它上方的说明框；说明框在选中按钮正上方约 100 px（不在授業三选一的 EFFECT_Y）。"""

from types import SimpleNamespace as NS

from hiro.produce import hif
from hiro.vision import event


def _box(t, cx, cy, w=200, h=24):
    return (t, 0.9, (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2))


OPTS = ["梦想的进展怎么样了?", "有个新的课程建议"]


def test_effect_above_reads_tooltip_not_buttons():
    boxes = [_box("特定のPドリンクを選択して獲得", 440, 701, 420), _box(OPTS[0], 220, 801), _box(OPTS[1], 220, 904)]
    assert event.effect_above(boxes, 801, OPTS) == "特定のPドリンクを選択して獲得"
    # 选第二项：说明框盖在第一项按钮上，露出来的第一项按钮字不算
    boxes = [
        _box("スキルカードを選択して獲得", 440, 801, 420),
        _box("梦想的进展", 120, 801, 100),
        _box(OPTS[1], 220, 904),
    ]
    assert event.effect_above(boxes, 904, OPTS) == "スキルカードを選択して獲得"
    assert event.effect_above([_box(OPTS[0], 220, 801)], 904, OPTS) == ""


class _Screen:
    """假的事件页：单击没选中的项 = 选中它；单击已选中的项 = 确认（页面走了）。截图就是「当前选中项的 y」。"""

    def __init__(self, sel_y, *, ignore_taps=0, slow_leave=False):
        self.sel, self.taps, self.confirmed = sel_y, [], False
        self.ignore, self.slow_leave, self.shots = ignore_taps, slow_leave, 0

    def click(self, x, y):
        self.taps.append(y)
        if self.ignore:
            self.ignore -= 1
        elif y == self.sel:
            self.confirmed = True
        else:
            self.sel = y

    def screenshot(self):
        self.shots += 1
        return "left" if self.confirmed else self.sel


SHOWN = {801: "特定のPドリンクを選択して獲得", 904: "スキルカードを選択して獲得"}


def _fake(monkeypatch, screen, choices, notes=None):
    monkeypatch.setattr(hif, "device", screen)
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(
        hif.ops,
        "ocr_boxes",
        lambda img: (
            []
            if img == "left"
            else [_box(SHOWN[img], 440, img - 100, 420), _box(OPTS[0], 220, 801), _box(OPTS[1], 220, 904)]
        ),
    )
    import hiro.vision.classify as classify

    monkeypatch.setattr(classify, "option_regions", lambda img: [] if img == "left" else [1, 2])
    fake = NS(
        s=NS(state=NS(page_fields={"choices": choices}), note=(notes if notes is not None else []).append),
        PREVIEW_WAIT_S=0,
        PREVIEW_RECHECK_S=0,
        SELECTED_DY=hif.HifCore.SELECTED_DY,
        _selected_y=lambda img: None if img in ("left", None) else img,
    )
    fake._is_selected = lambda img, y: hif.HifCore._is_selected(fake, img, y)
    return fake


def _choices(sel):
    return [
        {"index": 0, "text": OPTS[0], "center": [360, 801], "selected": sel == 0},
        {"index": 1, "text": OPTS[1], "center": [360, 904], "selected": sel == 1},
    ]


def test_preview_taps_each_unselected_option_once_and_reads_effects(monkeypatch):
    screen = _Screen(801)
    choices = _choices(0)
    sel = hif.HifCore._preview_event_effects(_fake(monkeypatch, screen, choices), choices)
    assert screen.taps == [904, 801] and sel == 0 and not screen.confirmed  # 选中项放最后，那时它已不是选中态
    assert choices[0]["effect"] == SHOWN[801] and choices[1]["effect"] == SHOWN[904]
    from hiro.brain import event_choice as ev

    assert all(ok for _, ok in (ev.parse(c) for c in choices))  # 规则能解析：不用 LLM


def test_preview_never_taps_selected_option(monkeypatch):
    """识别层记的选中项和画面不一致（实际选中的是第 2 项）：点之前重新核对，已选中的不点、直接读。"""
    screen = _Screen(904)
    choices = _choices(0)  # 识别层以为是第 1 项
    hif.HifCore._preview_event_effects(_fake(monkeypatch, screen, choices), choices)
    assert not screen.confirmed and screen.taps == [801]
    assert choices[1]["effect"] == SHOWN[904] and choices[0]["effect"] == SHOWN[801]


def test_preview_skipped_when_selected_unknown(monkeypatch):
    screen = _Screen(801)
    choices = _choices(None)
    notes = []
    assert hif.HifCore._preview_event_effects(_fake(monkeypatch, screen, choices, notes), choices) is None
    assert screen.taps == [] and "认不出" in notes[0]
    assert all(c.get("effect_unread") for c in choices if not c.get("effect"))  # 效果未知：标成未读（CodeRabbit #198）


def test_preview_stops_when_tap_did_not_select(monkeypatch):
    screen = _Screen(801, ignore_taps=1)
    choices = _choices(0)
    assert hif.HifCore._preview_event_effects(_fake(monkeypatch, screen, choices), choices) == "abort"
    assert screen.taps == [904] and not screen.confirmed
    # 点过以后对不上：返回 abort，调用方不再走原来的双击提交（CodeRabbit #196 / #198）


def test_preview_skips_dialogue_only():
    choices = [{"index": 0, "text": "a", "center": [1, 1]}, {"index": 1, "text": "b", "center": [1, 2]}]
    fake = NS(s=NS(state=NS(page_fields={"dialogue_only": True}), note=lambda t: None))
    assert hif.HifCore._preview_event_effects(fake, choices) is None


def test_tooltip_row_is_not_an_option():
    """11-27 实机第二次：第 1 项已选中，它的说明框落在第 0 行位置，被读成第 3 个选项；它的文字又把真正的说明当按钮字排除了。"""
    assert event._looks_like_effect("特定のPドリンクを選択して獲得")
    assert event._looks_like_effect("ボーカル+20")
    assert not event._looks_like_effect("梦想的进展怎么样了?")
    assert not event._looks_like_effect("夢の進捗はどう?")
    assert not event._looks_like_effect(None)


def test_unread_effect_is_unknown_not_empty(monkeypatch):
    """说明框没读到：记 None（未知），不记 ""（决策会当成确认没效果、直接选第一项）。"""
    screen = _Screen(801)
    choices = _choices(0)
    fake = _fake(monkeypatch, screen, choices)
    monkeypatch.setattr(
        hif.ops, "ocr_boxes", lambda img: [] if img == "left" else [_box(OPTS[0], 220, 801), _box(OPTS[1], 220, 904)]
    )
    hif.HifCore._preview_event_effects(fake, choices)
    assert choices[0]["effect"] is None and choices[1]["effect"] is None
    from hiro.brain import event_choice as ev

    assert not any(ok for _, ok in (ev.parse(c) for c in choices))  # 效果未知：不完整，不当成没效果去选第一项
    assert ev.parse({"text": "", "effect": ""})[1]  # 确认没有效果（纯台词）照旧算完整


def test_event_abort_strikes_count_per_event():
    """#196 review：「连着两次对不上就停」只算同一个事件；换了事件、页面走了、看成了都重新数。"""
    core = NS()
    strikes = lambda choices, preview: hif.HifCore._event_strikes(core, choices, preview)  # noqa: E731
    a = [{"text": "レッスンする"}, {"text": "休む"}]
    b = [{"text": "話を聞く"}, {"text": "断る"}]
    assert strikes(a, "abort") == 1
    assert strikes(b, "abort") == 1  # 换了事件：不和上一个事件的凑成两次
    assert strikes(b, "abort") == 2  # 同一个事件连着两次：停
    assert strikes(b, "left") == 0
    assert strikes(b, "abort") == 1
    assert strikes(b, 0) == 0  # 看成了
    # 上一拍 abort、这一拍认不出选中项（None）：也算一次，abort / None 交替照样到 2（CodeRabbit #196）
    assert strikes(a, "abort") == 1 and strikes(a, None) == 2
    assert strikes(b, None) == 0  # 没 abort 过的 None：照旧
