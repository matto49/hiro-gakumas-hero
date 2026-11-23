"""过场推进（协调 2026-11-02「动画期间点屏幕跳过」）：认不出的帧上安全时连点空白处；主循环共用一次 OCR；trace 记过场耗时。"""

from types import SimpleNamespace as NS  # noqa: F401

from hiro.loop import advance


def _run(texts, frames, *, header=None, quick=None, live=False):
    clicks, slept = [], []
    it = iter(frames)
    n = advance.burst(texts, shoot=lambda: next(it, None), click=lambda x, y: clicks.append((x, y)), sleep=slept.append,
                      in_live=lambda img: live, quick=lambda img: quick(img) if quick else None,
                      header=lambda img: header(img) if header else None)  # fmt: skip
    return n, clicks, slept


def test_taps_blank_on_plain_animation_frames():
    n, clicks, slept = _run(["審査基準", "20/33", "+950"], ["a", "b", "c"])
    assert n == 3 and clicks == [advance.ADVANCE_TAP] * 3 and slept == [advance.ADVANCE_EVERY] * 3


def test_no_tap_when_buttons_or_purchase_words():
    for w in ("ジュエルを消費", "キャンセル", "決定", "受け取る", "購入する", "リタイア", "次へ", "閉じる"):
        n, clicks, _ = _run(["審査基準", w], ["a"] * 3)
        assert n == 0 and clicks == [], w


def test_stops_when_dialog_or_page_recognized_mid_burst():
    n, clicks, _ = _run([], ["a", "dialog", "c"], header=lambda img: 750 if img == "dialog" else None)  # noqa: RUF059
    assert n == 1
    n, _clicks, _ = _run(
        [], ["a", "b", "pitem"], quick=lambda img: {"page": "SELECT_PITEM"} if img == "pitem" else None
    )
    assert n == 2


def test_no_tap_in_landscape_live():
    n, _, _ = _run([], ["a"] * 3, live=True)
    assert n == 0


def test_tap_point_is_kaa_skip_corner():
    """KAA kaa.tasks.common.skip() 的位置：历次实机每个 UNKNOWN 轮次都点过。"""
    assert advance.ADVANCE_TAP == (12, 12)


def test_session_records_transition_and_taps(tmp_path):
    from hiro.contracts import Action, ActionKind, ExecutionResult, TraceWriter
    from hiro.contracts import Decision as D
    from hiro.loop.session import Session

    import numpy as np

    s = Session(TraceWriter(tmp_path / "t"), shoot=lambda: np.zeros((1280, 720, 3), np.uint8))
    s.observe(scene="ACTION_SELECT", area="produce", img=None, fields=False)
    act = Action(ActionKind.CONTINUE, {})
    d = D(action=act, source="rule", reason="")
    first = s.record(decision=d, execution=ExecutionResult(action=act, ok=True))
    assert "transition" not in first.timings_ms  # 第一步之前没有执行过的步
    s.advance_taps = 4
    obs = s.observe(scene="SELECT_CARD", area="produce", img=None, fields=False)
    obs.t = first.t + 7.5
    step = s.record(obs, decision=d, execution=ExecutionResult(action=act, ok=True))
    assert (
        abs(step.timings_ms["transition"] - 7500) < 1 and step.timings_ms["advance_taps"] == 4 and s.advance_taps == 0
    )


def test_alt_tap_after_corner_fails():
    """第二十三局：P 道具效果说明条 (12,12) 点不掉停 78s。alt=True 时第一下改点上方中间空白，其余仍点角落。"""
    from hiro.loop import advance

    clicks = []
    n = advance.burst(
        [], shoot=lambda: object(), click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None, alt=True
    )
    assert n == advance.ADVANCE_BURST and clicks[0] == advance.ALT_TAP and set(clicks[1:]) == {advance.ADVANCE_TAP}


def test_alt_clear_only_without_text_near():
    from hiro.loop import advance

    effect_bar = [
        ("好印象効果のスキルカード獲得時、ダンスが700以上の", 1.0, (201, 450, 634, 473)),
        ("審査基準", 1.0, (549, 30, 631, 52)),
    ]
    assert advance.alt_clear(effect_bar)  # 第二十三局卡住帧：说明条在 y≈450，上方空白
    assert not advance.alt_clear(effect_bar + [("Pポイントで利用するものを選んでください", 1.0, (120, 230, 600, 260))])
