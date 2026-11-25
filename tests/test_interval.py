"""轮间执行器（hiro.produce.interval）离线测试。

画面：KAA 参考图 hif_interval.png / hif_interval_confirm.png（私有样例 tests/fixtures/kaa_refs/，原来直接读已安装的 KAA）、第四局进入动画帧（tests/fixtures/interval/）；买的流程与相談同一套
底部按钮，用相談录像的实拍帧驱动；回体力、强化之后的画面没有样本，用替身帧 + 指定 OCR 文字只验证流程。
"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.contracts import Action, ActionKind
from hiro.produce import interval, ops, shop
from hiro.produce.special_guidance import grid_center

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures"
REFS = FIX / "kaa_refs"
needs_ref = pytest.mark.skipif(
    not (REFS / "hif_interval.png").exists(), reason="没有轮间参考图 tests/fixtures/kaa_refs/"
)


def img(rel):
    return cv2.imread(str(FIX / rel))


def ref(name):
    return cv2.imread(str(REFS / name))


def near(xy, target, r=40):
    return abs(xy[0] - target[0]) <= r and abs(xy[1] - target[1]) <= r


class Fake:
    """transitions {(状态, 点): 新状态}；timed {状态: (秒, 新状态)}；texts {状态: [(文字, (x, y))]} 覆盖该状态的 OCR。"""

    def __init__(self, frames, transitions, start, timed=None, texts=None):
        self.frames, self.transitions, self.state = frames, transitions, start
        self.timed, self.texts = timed or {}, texts or {}
        self.clicks, self.t, self._entered, self._ocr = [], 0.0, 0.0, {}

    def _go(self, s):
        self.state, self._entered = s, self.t

    def shoot(self):
        if self.state in self.timed and self.t - self._entered >= self.timed[self.state][0]:
            self._go(self.timed[self.state][1])
        return self.frames[self.state]

    def click(self, x, y):
        self.clicks.append((x, y))
        for (s, t), nxt in self.transitions.items():
            if s == self.state and near((x, y), t):
                self._go(nxt)
                return

    def ocr(self, im):
        state = next(k for k, v in self.frames.items() if v is im)
        if state in self.texts:
            return [(t, 1.0, (x - 20, y - 10, x + 20, y + 10)) for t, (x, y) in self.texts[state]]
        if state not in self._ocr:
            self._ocr[state] = ops.ocr_boxes(im)
        return self._ocr[state]

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(fake, action, fields=None, **kw):
    return interval.execute_interval(action, fields or {}, shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep,
                                     ocr=fake.ocr, calib=False, **kw)  # fmt: skip


# ---------------------------------------------------------------- 读数与坐标


@needs_ref
def test_read_hp_and_page_predicates():
    live = img("interval/enter-anim-q4.jpg")
    assert interval.read_hp(ref("hif_interval.png"), ops.ocr_boxes) == (34, 34)
    assert interval.read_hp(live, ops.ocr_boxes) == (18, 33) and shop.read_p(live, ops.ocr_boxes) == 380
    assert shop.consult_idle(ref("hif_interval.png")) and not shop.consult_idle(live)  # 进入动画不是主画面
    assert shop.cancel_visible(ref("hif_interval_confirm.png"))


def test_find_item_prefers_recognized_center():
    fields = {
        "interval": {
            "drinks": [{"index": 6, "drink_id": "pdrink_a", "center": [440, 750]}],
            "cards": [{"index": 1, "card_id": "c"}],
        }
    }
    assert interval.find_item(fields, 6)["center"] == [440, 750]
    assert interval.find_item(fields, 1) == {"index": 1, "card_id": "c", "center": list(interval.DEFAULT_CENTERS[1])}
    assert interval.find_item({}, 5)["center"] == list(interval.DEFAULT_CENTERS[5])  # セレクトチェンジ：第一行第 4 列
    assert interval.find_item({}, 9) is None


@needs_ref
def test_default_centers_sit_above_price_pills():
    """默认格子中心在参考图价格条（OCR 到的价格数字）正上方约 88 像素。"""
    prices = [((x1 + x2) // 2, (y1 + y2) // 2) for t, _, (x1, y1, x2, y2) in ops.ocr_boxes(ref("hif_interval.png"))
              if t.strip().isdigit() and 600 < (y1 + y2) // 2 < 870]  # fmt: skip
    for cx, cy in interval.DEFAULT_CENTERS.values():
        assert any(abs(px - cx) <= 8 and 70 <= py - cy <= 100 for px, py in prices)


# ---------------------------------------------------------------- 结束


@needs_ref
def test_end_waits_enter_animation_then_confirms_honsen():
    frames = {"anim": img("interval/enter-anim-q4.jpg"), "idle": ref("hif_interval.png"), "confirm": ref("hif_interval_confirm.png"),
              "left": img("shop/flow/00151-left.jpg")}  # fmt: skip
    honsen = ops.find_text(
        ops.ocr_boxes(frames["confirm"]), ("本戦へ",), region=(360, 1080, 720, 1240)
    )  # 正文里也有「本戦へ」
    fake = Fake(
        frames, {("idle", shop.END_TAP): "confirm", ("confirm", honsen): "left"}, "anim", timed={"anim": (2.0, "idle")}
    )
    res = run(fake, Action(ActionKind.INTERVAL_END))
    assert res.ok, res.error
    assert res.taps[0] == list(shop.END_TAP) and near(res.taps[1], honsen, 5) and fake.state == "left"


def end_frames():
    frames = {"idle": ref("hif_interval.png"), "confirm": ref("hif_interval_confirm.png"), "fading": img("interval/end-fading-q5.jpg"),
              "round2": img("interval/round2-transition-q5.jpg")}  # fmt: skip
    honsen = ops.find_text(ops.ocr_boxes(frames["confirm"]), ("本戦へ",), region=(360, 1080, 720, 1240))
    return frames, honsen


@needs_ref
def test_end_waits_until_interval_title_gone():
    """第五局 21:26：「本戦へ」后淡出帧左上仍是「インターバル」——要等它消失（进 ラウンド2 转场）才算离开。"""
    frames, honsen = end_frames()
    fake = Fake(
        frames,
        {("idle", shop.END_TAP): "confirm", ("confirm", honsen): "fading"},
        "idle",
        timed={"fading": (3.0, "round2")},
    )
    res = run(fake, Action(ActionKind.INTERVAL_END))
    assert res.ok, res.error
    assert fake.state == "round2" and fake.t >= 3.0 and res.page_after is None


@needs_ref
def test_end_fails_if_still_on_interval():
    frames, honsen = end_frames()
    fake = Fake(frames, {("idle", shop.END_TAP): "confirm", ("confirm", honsen): "fading"}, "idle")
    res = run(fake, Action(ActionKind.INTERVAL_END))
    assert not res.ok and "仍是「インターバル」" in res.error


def test_repeat_end_during_transition_is_success_without_taps():
    """主循环在转场中又发 IntervalEnd：已不在轮间，直接成功、不点、不等 8 秒。"""
    fake = Fake({"round2": img("interval/round2-transition-q5.jpg")}, {}, "round2")
    res = run(fake, Action(ActionKind.INTERVAL_END))
    assert res.ok and fake.clicks == [] and fake.t < 1.0


def test_gives_up_when_page_never_ready():
    fake = Fake({"anim": img("interval/enter-anim-q4.jpg")}, {}, "anim")
    res = run(fake, Action(ActionKind.INTERVAL_END))
    assert not res.ok and "主画面没出现" in res.error and fake.clicks == []


# ---------------------------------------------------------------- 买（与相談同一套按钮，用相談实拍帧）


def consult_frames():
    f = {n: img(f"shop/flow/{p}") for n, p in (("idle", "00115-idle.jpg"), ("selected0", "00116-selected0.jpg"), ("confirm", "00127-confirm.jpg"),
                                                ("anim", "00128-acquire_anim.jpg"), ("bought", "00140-purchased01.jpg"))}  # fmt: skip
    return f


def buy_fake():
    return Fake(consult_frames(), {("idle", interval.DEFAULT_CENTERS[0]): "selected0", ("selected0", shop.EXCHANGE_TAP): "confirm",
                                   ("confirm", shop.CONFIRM_TAP): "anim", ("anim", shop.SKIP_TAP): "bought"}, "idle")  # fmt: skip


@pytest.mark.fixtures
def test_buy_card_verified_by_confirm_dialog():
    fake = buy_fake()
    fields = {"interval": {"cards": [{"index": 0, "card_id": "p_card-02-act-1_037", "name": "デイドリーミング+"}]}}
    res = run(fake, Action(ActionKind.INTERVAL_BUY, {"index": 0}), fields)
    assert res.ok, res.error
    assert fake.state == "bought" and res.page_before == interval.PAGE


@pytest.mark.fixtures
def test_buy_cancels_when_dialog_shows_other_item():
    fake = buy_fake()
    fields = {"interval": {"cards": [{"index": 0, "card_id": "p_card-01-men-2_011", "name": "届いて！"}]}}
    res = run(fake, Action(ActionKind.INTERVAL_BUY, {"index": 0}), fields)
    assert not res.ok and "已取消" in res.error and "デイドリーミング" in res.error
    assert res.taps[-1] == list(shop.CANCEL_TAP)


# ---------------------------------------------------------------- 回体力（无样本：替身帧 + 指定 OCR）


def recover_fake(effect=True):
    idle = ref("hif_interval.png")
    frames = {"idle": idle, "dialog": ref("hif_interval_confirm.png"), "after": idle.copy()}
    texts = {"idle": [("20/34", (378, 72)), ("200", (412, 114))], "dialog": [("回復する", (513, 1160))],
             "after": [("34/34", (378, 72)), ("130", (412, 114))] if effect else [("20/34", (378, 72)), ("200", (412, 114))]}  # fmt: skip
    return Fake(
        frames, {("idle", interval.RECOVER_TAP): "dialog", ("dialog", (513, 1160)): "after"}, "idle", texts=texts
    )


@needs_ref
def test_recover_confirms_and_sees_hp_rise():
    fake = recover_fake()
    res = run(fake, Action(ActionKind.INTERVAL_BUY, {"service": "recover"}))
    assert res.ok, res.error
    assert res.taps == [list(interval.RECOVER_TAP), [513, 1160]]


@needs_ref
def test_recover_without_effect_fails():
    fake = recover_fake(effect=False)
    res = run(fake, Action(ActionKind.INTERVAL_BUY, {"service": "recover"}))
    assert not res.ok and "体力没增加" in res.error


@needs_ref
def test_recover_unavailable_not_tapped():
    fake = recover_fake()
    res = run(
        fake, Action(ActionKind.INTERVAL_BUY, {"service": "recover"}), {"interval": {"recover": {"available": False}}}
    )
    assert not res.ok and fake.clicks == []


# ---------------------------------------------------------------- 强化（牌组页用相談削除页实拍帧）


@needs_ref
def test_upgrade_picks_card_and_waits_toast():
    idle = ref("hif_interval.png")
    frames = {"idle": idle, "deck": img("events/removal-none-00141.jpg"), "deck_sel": img("events/removal-sel0-00142.jpg"),
              "toast": img("shop/consult-r5.jpg").copy(), "back": idle.copy()}  # fmt: skip
    fake = Fake(frames, {("idle", interval.UPGRADE_TAP): "deck", ("deck", grid_center(0)): "deck_sel", ("deck_sel", shop.CONFIRM_TAP): "toast"},
                "idle", timed={"toast": (3.0, "back")}, texts={"toast": [("距離感の基本を強化しました", (360, 990))]})  # fmt: skip
    res = run(fake, Action(ActionKind.INTERVAL_UPGRADE_CARD, {"card_index": 0}))
    assert res.ok, res.error
    assert (
        res.taps == [list(interval.UPGRADE_TAP), list(grid_center(0)), list(shop.CONFIRM_TAP)]
        and res.page_after == interval.PAGE
    )


@needs_ref
def test_upgrade_cancels_when_card_not_selected():
    frames = {"idle": ref("hif_interval.png"), "deck": img("events/removal-none-00141.jpg")}
    fake = Fake(frames, {("idle", interval.UPGRADE_TAP): "deck"}, "idle")
    res = run(fake, Action(ActionKind.INTERVAL_UPGRADE_CARD, {"card_index": 1}))
    assert not res.ok and "已取消" in res.error and res.taps[-1] == list(shop.CANCEL_TAP)


def test_unsupported_action():
    fake = Fake({"idle": ref("hif_interval.png")}, {}, "idle") if (REFS / "hif_interval.png").exists() else None
    if fake is None:
        pytest.skip("找不到 KAA 轮间参考图")
    assert "不支持" in run(fake, Action(ActionKind.SKIP)).error


# ---------------------------------------------------------------- 整个轮间循环（第八局复盘 B1，main8 -r2 #95–101 离线复现）

import json as _json  # noqa: E402

from hiro.brain.deciders import ProduceDeciders  # noqa: E402
from hiro.brain.state import CAMPUS_HIRO  # noqa: E402
from hiro.contracts import ExecutionResult, GameState  # noqa: E402


class _Main8(dict):
    """第八局本战 R2 轮间页的实机状态（tests/fixtures，仓库不带）：用到时才读，没有就跳过这条用例。"""

    def __getitem__(self, k):
        if not dict.__len__(self):
            f = FIX / "interval" / "main8-r2-interval-states.json"
            if not f.exists():
                import pytest

                pytest.skip("没有截图样例 tests/fixtures（第八局轮间状态）")
            self.update(_json.loads(f.read_text(encoding="utf-8")))
        return dict.__getitem__(self, k)


MAIN8 = _Main8()


class Shop:
    """轮间的游戏状态：买过的格子再买失败（main8 #101 的「格子没有被选中」），回体力每次 +2 / −10P。"""

    def __init__(self, hp=15, max_hp=33, p=380, fail_first=()):
        self.hp, self.max_hp, self.p = hp, max_hp, p
        self.bought, self.log, self.fail_first, self.ended = set(), [], set(fail_first), False
        self.prices = {
            it["index"]: it["price"]
            for k in ("cards", "changes", "drinks")
            for it in MAIN8["95"]["page_fields"]["interval"][k]
        }

    def execute(self, action, pf):
        args = action.args or {}
        self.log.append((action.kind.value, args.get("index", args.get("service"))))
        ok, err = True, None
        if action.kind is ActionKind.INTERVAL_END:
            self.ended = True
        elif args.get("service") == "recover":
            ok = self.p >= 10 and self.hp < self.max_hp
            if ok:
                self.hp, self.p = min(self.max_hp, self.hp + 2), self.p - 10
        elif "index" in args:
            i = args["index"]
            if i in self.fail_first:
                self.fail_first.discard(i)
                ok, err = False, "点「交換する」后 3s 内没出现「交換確認」弹窗"
            elif i in self.bought or self.p < self.prices[i]:
                ok, err = False, f"点货架第 {i} 格后 3s 内格子没有被选中"
            else:
                self.bought.add(i)
                self.p -= self.prices[i]
        return ExecutionResult(
            action=action, ok=ok, error=err, page_after="HIF_ROUND_INTERVAL" if not self.ended else None
        )

    def status(self):
        return self.hp, self.max_hp, self.p


def main8_loop(game, observe_seq):
    deciders = ProduceDeciders(CAMPUS_HIRO, None)
    frames = iter(observe_seq)
    state = GameState.from_dict(MAIN8["95"])

    def observe():
        return next(frames, {})  # 用完就一直缺 interval 字段（main8：连续 4 帧缺）

    def decide(pf):
        state.page_fields = pf
        state.produce.update(
            produce_point=game.p, stamina=game.hp
        )  # 实机每次观测都会刷新 P / 体力（produce 是字段 dict）
        return deciders.decide("HIF_ROUND_INTERVAL", state)

    return interval.interval_loop(observe, decide, execute=game.execute, status=game.status)


def test_main8_stale_fields_do_not_rebuy_or_exit_and_hp_is_topped_up():
    """#97 之后识别缺帧：沿用的旧字段里已买的格子仍是 purchased=false。以前会再买第 0 格、失败后直接退出，体力 15/33 没回。"""
    game = Shop()
    seq = [MAIN8["95"]["page_fields"], MAIN8["96"]["page_fields"], MAIN8["97"]["page_fields"], {}, {}, {}, {}]
    assert main8_loop(game, seq) is True
    buys = [i for k, i in game.log if k == "IntervalBuy" and i != "recover"]
    assert len(buys) == len(set(buys))  # 每格最多买一次
    assert game.hp == game.max_hp  # 不给目标时回满
    assert game.log[-1] == ("IntervalEnd", None) and game.ended


def test_buy_failure_reobserves_and_continues():
    game = Shop(fail_first={7})  # 第一次买 特製ハツボシエキス 失败（确认框没出现）
    seq = [MAIN8["95"]["page_fields"]] * 8
    assert main8_loop(game, seq) is True
    assert game.log.count(("IntervalBuy", 7)) == 2 and 7 in game.bought  # 失败后重新观测、再试一次成功，没有退出
    assert game.log[-1] == ("IntervalEnd", None)


def test_finish_recovers_only_while_p_lasts():
    game = Shop(hp=15, p=40)  # 只够回 4 次（+8）
    assert interval.finish_interval(game.execute, game.status) is True
    assert game.hp == 23 and game.p == 0 and game.log[-1] == ("IntervalEnd", None)


def test_mark_known_marks_bought_and_blocked():
    pf = interval.mark_known(MAIN8["97"]["page_fields"], {0}, {5})
    items = {it["index"]: it for k in ("cards", "changes", "drinks") for it in pf["interval"][k]}
    assert items[0]["purchased"] and items[5]["purchased"] and not items[1]["purchased"]
    assert MAIN8["97"]["page_fields"]["interval"]["cards"][0]["purchased"] is False  # 不改原字段


def test_wait_decisions_are_not_executed():
    """决策器给 Wait（例如字段缺失 / 异常）：不执行、重新观测；连续几次后收尾（先回体力再结束）。"""
    from hiro.contracts import Decision

    game = Shop(hp=29, p=100)
    wait = Decision(Action.ui(ActionKind.WAIT, why="测试"), "等", "fallback", 0.1)
    assert interval.interval_loop(dict, lambda pf: wait, execute=game.execute, status=game.status) is True
    assert ("Wait", None) not in game.log and game.hp == 33 and game.log[-1] == ("IntervalEnd", None)


def test_hp_target_just_over_half_for_hiro():
    """用户 2026-11-01：広 不用回满，刚过半血（33 → 17）。15/33 → 回 1 次到 17 就结束。"""
    game = Shop(hp=15, p=380)
    assert interval.finish_interval(game.execute, game.status, hp_target=lambda m: m // 2 + 1) is True
    assert game.hp == 17 and game.p == 370 and game.log == [("IntervalBuy", "recover"), ("IntervalEnd", None)]
    game2 = Shop(hp=20, p=380)
    interval.finish_interval(game2.execute, game2.status, hp_target=lambda m: m // 2 + 1)
    assert game2.log == [("IntervalEnd", None)]  # 已过半：不回


# ---------------------------------------------------------------- 顶部体力读数（q10 本战轮间收尾：「21/3ろ」→ None，目标检查没生效）


def test_read_hp_tolerates_ocr_digit_confusions():
    im = img("interval/q10-interval-open.jpg")
    assert interval.read_hp(im, ops.ocr_boxes) == (21, 33)  # OCR 读成「21/3ろ」
    assert interval.screen_status(shoot=lambda: im) == (21, 33, 250)
    boxes = lambda texts: lambda im_: [(t, 1.0, (330, 60, 430, 85)) for t in texts]  # noqa: E731
    assert interval.read_hp(im, boxes(["♥16/33"])) == (16, 33)
    assert interval.read_hp(im, boxes(["2l/33"])) == (21, 33)
    assert interval.read_hp(im, boxes(["33/21"])) is None  # 体力不会超过上限：读错了就不信


def test_screen_status_falls_back_to_observed_state():
    import numpy as np

    blank = np.zeros((1280, 720, 3), np.uint8)
    st = interval.screen_status(shoot=lambda: blank, ocr=lambda im: [], fallback=lambda: (21, 33, 250), tries=2)
    assert st == (21, 33, 250)
    assert interval.screen_status(shoot=lambda: blank, ocr=lambda im: [], tries=1) == (None, None, None)


def test_ouenbou_fill_by_pool():
    """応援棒按真实卡池补：缺几张按权重最大余数法分（広 やる気流派：距離感 75、其余 25 → 缺 5 张 = 距離感 ×2 + 另三种各 1）；
    不给卡池照旧轮换；够 22 张不补。"""
    from hiro.sim import interval as iv

    pool = (
        ("p_card-02-act-0_037", 25),
        ("p_card-02-act-0_038", 25),
        ("p_card-02-men-0_039", 75),
        ("p_card-02-men-0_041", 25),
    )
    got = iv.ouenbou_fill([("x", 0)] * 17, pool)[17:]
    assert sorted(c for c, _ in got) == sorted(
        ["p_card-02-men-0_039"] * 2 + ["p_card-02-act-0_037", "p_card-02-act-0_038", "p_card-02-men-0_041"]
    )
    assert iv.ouenbou_fill([("x", 0)] * 21)[21:] == [(iv.OUENBOU_BASICS[0], 0)]
    assert iv.ouenbou_fill([("x", 0)] * 22, pool) == [("x", 0)] * 22
    assert (
        iv.R2Setup(deck=[], drinks=[], hp=1, max_hp=1, permil={}, ouenbou_pool=pool).key()
        != iv.R2Setup(deck=[], drinks=[], hp=1, max_hp=1, permil={}).key()
    )
