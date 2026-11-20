"""轮间 / 相談「リフレッシュ」刷新货架（协调 2026-11-02 最高优先级：第十六、十七局 R2 只有 1 瓶饮料，轮间货架没有好饮料、P 没花完）。

动作：ActionKind.REROLL，args {"scope": "interval" | "consult"}（与决策 99fa922 对齐）。
画面：第十七局本战轮间实拍（calib 20261102-172120 / 172458）。刷新后的画面和确认框离线没有样本：
「刷新后」用另一张轮间帧（P 580 → 435），确认框用替身帧 + 指定 OCR 文字只验证流程。
"""

from pathlib import Path

import cv2

from hiro.contracts import Action, ActionKind, Decision, ExecutionResult
from hiro.produce import interval, shop
from tests.test_interval import Fake

FIX = Path(__file__).parent / "fixtures/interval_reroll"


def _frames():
    # 刷新前后：第十八局本战轮间真实刷新的前后两帧（calib 223235 / 223240，货架差 24.8）；
    # 第十七局两张轮间帧是同一批货（只差几格已买，货架差 9.7），不能当「刷新后」
    other = Path(__file__).parent / "fixtures/reroll_dialog"
    return {"idle": cv2.imread(str(other / "r1-before.jpg")), "after": cv2.imread(str(other / "r1-after.jpg")),
            "confirm": cv2.imread(str(FIX / "main17-dialog.jpg"))}  # fmt: skip


def _run(fake, fields=None):
    return interval.execute_interval(Action(ActionKind.REROLL, {"scope": "interval"}), fields or {}, shoot=fake.shoot, click=fake.click,
                                     clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False)  # fmt: skip


REFRESH = (108, 1070)  # 实拍「リフレッシュ」字样中心


def test_reroll_taps_refresh_and_sees_shelf_change():
    fake = Fake(_frames(), {("idle", REFRESH): "after"}, "idle")
    res = _run(fake)
    assert res.ok, res.error
    assert abs(res.taps[0][0] - REFRESH[0]) <= 30 and abs(res.taps[0][1] - REFRESH[1]) <= 15


def test_reroll_confirms_dialog():
    texts = {"confirm": [("リフレッシュ確認", (200, 600)), ("Pポイントを使用してラインナップを更新しますか？", (360, 700)),
                         ("キャンセル", (220, 1160)), ("リフレッシュする", (500, 1160))]}  # fmt: skip
    fake = Fake(_frames(), {("idle", REFRESH): "confirm", ("confirm", (500, 1160)): "after"}, "idle", texts=texts)
    res = _run(fake)
    assert res.ok, res.error
    assert fake.state == "after"
    assert (500, 1160) in fake.clicks


def test_reroll_never_spends_jewels():
    texts = {"confirm": [("ジュエルを消費してリフレッシュしますか？", (360, 700)), ("キャンセル", (220, 1160)), ("リフレッシュする", (500, 1160))]}
    fake = Fake(_frames(), {("idle", REFRESH): "confirm", ("confirm", (220, 1160)): "idle"}, "idle", texts=texts)
    res = _run(fake)
    assert not res.ok and "ジュエル" in res.error
    assert (220, 1160) in fake.clicks and (500, 1160) not in fake.clicks


def test_reroll_unavailable_taps_nothing():
    fake = Fake(_frames(), {}, "idle")
    res = _run(fake, {"interval": {"refresh": {"price": 10, "available": False}}})
    assert not res.ok and fake.clicks == []


def test_reroll_no_change_fails():
    fake = Fake(_frames(), {}, "idle")
    res = _run(fake)
    assert not res.ok and "货架没换" in res.error


def test_consult_reroll_uses_rerolls_left():
    fake = Fake(_frames(), {}, "idle")
    res = shop.execute_shop(Action(ActionKind.REROLL, {"scope": "consult"}), {"rerolls_left": 0}, shoot=fake.shoot, click=fake.click,
                            clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False)  # fmt: skip
    assert not res.ok and fake.clicks == []
    fake = Fake(_frames(), {("idle", (108, 1085)): "after"}, "idle")
    res = shop.execute_shop(Action(ActionKind.REROLL, {"scope": "consult"}), {"rerolls_left": 1}, shoot=fake.shoot, click=fake.click,
                            clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False)  # fmt: skip
    assert res.ok, res.error


def test_interval_loop_clears_purchased_after_reroll():
    """刷新成功后：已买的格子号不再沿用（新货架同一格是新商品），旧货架也不沿用。"""
    shelf = {"interval": {"drinks": [{"index": 6, "drink_id": "d", "purchased": False}], "cards": [], "changes": []}}
    seen, plan = [], iter([Action(ActionKind.INTERVAL_BUY, {"index": 6}), Action(ActionKind.REROLL, {"scope": "interval"}),
                           Action(ActionKind.INTERVAL_END)])  # fmt: skip

    def decide(pf):
        seen.append(pf["interval"]["drinks"][0]["purchased"])
        return Decision(action=next(plan))

    def execute(action, pf):
        return ExecutionResult(action=action, ok=True)

    interval.interval_loop(lambda: shelf, decide, execute=execute, status=lambda: (33, 33, 0))
    assert seen == [False, True, False]  # 买了第 6 格 → 标已买；刷新后 → 不再标


def test_reroll_confirm_titles_from_settings():
    """总控设置页确认：轮间「リフレッシュ実行確認」、相談「相談リフレッシュ確認」都开着。按钮文字读不到时按标题点右下确认。"""
    for title in ("リフレッシュ実行確認", "相談リフレッシュ確認"):
        texts = {"confirm": [(title, (200, 600)), ("Pポイントを10消費してリフレッシュしますか？", (360, 700)), ("キャンセル", (220, 1160))]}
        fake = Fake(_frames(), {("idle", REFRESH): "confirm", ("confirm", shop.REROLL_OK_TAP): "after"}, "idle", texts=texts)
        res = _run(fake)
        assert res.ok, (title, res.error)
        assert shop.REROLL_OK_TAP in fake.clicks and (220, 1160) not in fake.clicks


def test_reroll_slow_server_still_counts_after_confirm():
    """z5（11-19 02:42 DMM）：点了「更新する」，服务器慢（刚出过通信エラー），货架 10 秒后才换（P 544→534、刷新价 10→20），
    超时判了「货架没换」三次。点过确认后再多等 REROLL_GRACE 秒。"""
    texts = {"confirm": [("リフレッシュ確認", (200, 600)), ("キャンセル", (220, 1160)), ("更新する", (500, 1160))]}
    frames = _frames()
    frames["loading"] = frames["idle"].copy()  # 确认框关了、货架还没换（转圈）
    fake = Fake(frames, {("idle", REFRESH): "confirm", ("confirm", (500, 1160)): "loading"}, "idle",
                timed={"loading": (shop.REROLL_TIMEOUT + 2, "after")}, texts=texts)  # fmt: skip
    res = _run(fake)
    assert res.ok, res.error


def test_reroll_without_confirm_does_not_wait_grace():
    fake = Fake(_frames(), {}, "idle")  # 没点到确认框：照旧 10 秒判没换
    res = _run(fake)
    assert not res.ok and fake.t <= shop.REROLL_TIMEOUT + 3
