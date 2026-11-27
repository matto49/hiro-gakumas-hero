"""日常 11-26：竞赛停止条件 / 挑对手、商店（デイリー交換所）、硬币扭蛋的读数（按 11-25 DMM 实机截图的 OCR 文字和位置造的帧）。"""

from hiro.daily import coin_gacha, contest, shop
from hiro.runtime.frame import Frame
from hiro.vision.ocr import Box, Text

import numpy as np


def frame(*texts):
    f = Frame(np.zeros((1280, 720, 3), np.uint8), 0.0)
    f.__dict__["texts"] = [Text(t, Box(x - 40, y - 12, x + 40, y + 12)) for t, x, y in texts]
    return f


def test_contest_used_up_by_text_or_counter(monkeypatch):
    monkeypatch.setattr(contest.nav, "find", lambda f, name, **kw: None)  # 模板没认出（DMM 实测 0.79 < 0.8）
    assert contest.used_up(frame(("本日の挑戦権を消費しました", 360, 885)))
    assert contest.used_up(frame(("0/5", 600, 40)))
    assert contest.remaining(frame(("3/5", 600, 40))) == 3
    assert not contest.used_up(frame(("3/5", 600, 40), ("ランキング", 100, 390)))


def test_contest_pick_choice(monkeypatch):
    monkeypatch.delenv("HIRO_CONTEST_PICK", raising=False)
    assert contest.pick_choice() == "lowest"
    monkeypatch.setenv("HIRO_CONTEST_PICK", "3")
    assert contest.pick_choice() == "3" and contest.PICKS["3"] == 2
    monkeypatch.setenv("HIRO_CONTEST_PICK", "hard")
    assert contest.pick_choice() == "lowest"


def test_shop_tiles_read_price_left_and_recommended():
    f = frame(("おすすめ", 61, 319), ("3,000", 120, 480), ("あと1回", 104, 509),
              ("6,000", 290, 480), ("あと2回", 274, 509), ("あと0回", 444, 509), ("マニー", 190, 1083))  # fmt: skip
    t = shop.tiles(f)
    assert [(d["x"], d["left"], d["price"], d["recommended"]) for d in t] == [
        (104, 1, 3000, True), (274, 2, 6000, False), (444, 0, None, False)
    ]  # fmt: skip
    assert t[0]["y"] == 509 - 110


def test_shop_dialog_name_and_currency():
    f = frame(("交換確認", 100, 425), ("マニーを使用して、以下のアイテムと交換しますか?", 300, 512),
              ("[Wonder Scale]倉本千奈のピース", 380, 584), ("キャンセル", 168, 1143), ("決定", 490, 1140))  # fmt: skip
    d = shop.read_dialog(f)
    assert d == {"name": "[Wonder Scale]倉本千奈のピース", "money": True, "ap": False, "jewel": False}
    assert shop.read_dialog(frame(("デイリー交換所", 100, 40))) is None
    j = shop.read_dialog(frame(("交換確認", 100, 425), ("ジュエルを使用して", 300, 512), ("何か", 300, 584)))
    assert j["money"] is False and j["jewel"] is True


def test_shop_name_matching_tolerates_ocr():
    assert shop.name_wanted("IWonderScalel倉本千奈のピ一ス", ["倉本千奈のピース"])
    assert shop.name_wanted("再挑戦チケット", ["ロジックノート", "再挑戦"])
    assert not shop.name_wanted("ロジックノート(ボーカル", ["再挑戦"])


def test_coin_gacha_count_and_plan(monkeypatch):
    assert coin_gacha.count(frame(("ガシャ回数:4回", 300, 816))) == 4
    assert coin_gacha.count(frame(("ガシャ回数:", 300, 816))) == 0  # SIMD 把「0回」漏读
    assert coin_gacha.count(frame(("フレンドガシャ", 300, 545))) is None
    assert coin_gacha.count(frame(("ガシャ回数:", 300, 816), ("5回", 420, 816))) == 5  # 分成两框（CodeRabbit #198）
    monkeypatch.delenv("HIRO_COIN_GACHA", raising=False)
    assert coin_gacha.plan() == []  # 缺省不抽（花币的事默认关，开局页的日常选项会显式传）
    monkeypatch.setenv("HIRO_COIN_GACHA", "auto")
    assert coin_gacha.plan() == "auto"
    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:2,sense,bogus:3,logic:0")
    assert coin_gacha.plan() == [("friend", 2), ("sense", 1)]
    monkeypatch.setenv("HIRO_COIN_GACHA", "off")
    assert coin_gacha.plan() == []
    t = coin_gacha._gacha_title(frame(("センスガシヤ(毎月1日5時00分更新)", 200, 987)), "sense")
    assert t is not None and t.rect.center.y == 987


def test_shop_ap_dialog_and_options(monkeypatch):
    """体力商店（11-26）：确认框「APを使用して」；HIRO_SHOP_AP 选哪几样；名字翻中文。"""
    from hiro.daily.names import item_name

    d = shop.read_dialog(frame(("交換確認", 100, 425), ("APを使用して、以下のアイテムと交換しますか?", 300, 512),
                               ("再挑戦チケット", 300, 584)))  # fmt: skip
    assert d["ap"] and not d["money"] and not d["jewel"] and d["name"] == "再挑戦チケット"
    monkeypatch.setenv("HIRO_SHOP_AP", "retry, regen,bogus")
    assert shop.ap_wanted() == ["retry", "regen"]
    assert shop.AP_ITEMS["regen"] in "メモリー再生成チケット" and shop.AP_ITEMS["pt"] in "入手サポート強化Ptアップ"
    assert item_name("メモリー再生成チケット") == "回忆再生成券"
    assert item_name("入手ノート数アップ") == "笔记获取数提升"


def test_shop_default_off_and_cost(monkeypatch):
    monkeypatch.delenv("HIRO_SHOP", raising=False)
    assert shop.mode() == "off"  # 缺省不进商店（review：领日常奖励不该默认花マニー）
    f = frame(("交換確認", 100, 425), ("2,017,701", 193, 992), ("→", 387, 997), ("2,014,701", 450, 992))
    assert shop.cost(f) == 3000
    assert shop.cost(frame(("2,017,701", 193, 992))) is None


# ---- #122 review：同一天重跑不再兜底买一批、买不起跳过接着看、超时照样报已买的、长名字、扭蛋上限 ----


class _Dev:
    def __init__(self):
        self.taps = []

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, *a, **k):
        pass


class _Rt:
    def __init__(self):
        self.device = _Dev()
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t


def _shop(monkeypatch, screen, goods, money, *, home_raises=False):
    """screen：商店这一屏；goods：格子 x → (名字, 单价, 一次买几个)；money：有多少金币。"""
    import tempfile
    from pathlib import Path

    from hiro.loop.errors import ResumeBlocked

    rt = _Rt()
    state = {"money": money}
    tmp = Path(tempfile.mkdtemp(prefix="hiro-shop-test-"))
    monkeypatch.setattr(shop, "_price_path", lambda: tmp / shop.PRICE_FILE)  # 不写真实 HIRO_HOME（CodeRabbit #138）
    monkeypatch.setattr(shop, "goto_daily_exchange", lambda rt: True)
    monkeypatch.setattr(shop.common, "frame", lambda rt: screen)
    monkeypatch.setattr(shop, "tap_word", lambda *a, **k: True)

    def dialog(rt, gone=False):
        if gone:
            return screen
        x = rt.device.taps[-1][0]
        name, price, n = goods[x]
        return frame(("交換確認", 100, 425), ("マニーを使用して、以下のアイテムと交換しますか?", 300, 512),
                     (name, 380, 584), (f"{state['money']:,}", 193, 992), (f"{state['money'] - price * n:,}", 450, 992))  # fmt: skip

    def confirm(rt, g):
        x = rt.device.taps[-1][0]
        _, price, n = goods[x]
        if price * n > state["money"]:
            return shop.SHORT
        state["money"] -= price * n
        return shop.BOUGHT

    def home(rt):
        if home_raises == "timeout":
            raise shop.common.TaskTimeout()
        if home_raises:
            raise ResumeBlocked("回首页超时")

    monkeypatch.setattr(shop, "wait_dialog", dialog)
    monkeypatch.setattr(shop, "confirm", confirm)
    monkeypatch.setattr(shop.nav, "goto_home", home)
    return rt


def test_shop_recommended_sold_out_means_done_today(monkeypatch):
    """おすすめ今天已经买光（あと0回）：算今天买过了，不兜底买最便宜的（原来会按剩余次数整批买）。"""
    monkeypatch.setenv("HIRO_SHOP", "recommended")
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    screen = frame(("おすすめ", 61, 319), ("3,000", 120, 480), ("あと0回", 104, 509),
                   ("1,000", 290, 480), ("あと2回", 274, 509))  # fmt: skip
    rt = _shop(monkeypatch, screen, {274: ("ノート", 1000, 2)}, 100000)
    r = shop.run(rt)
    assert rt.device.taps == [] and "今天买过了" in r


def test_shop_fallback_only_buys_exactly_one(monkeypatch):
    """没有おすすめ：最便宜那件确认框里一次要买 2 个（扣 2 件的钱）就不买，写明原因。"""
    monkeypatch.setenv("HIRO_SHOP", "recommended")
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    screen = frame(("1,000", 290, 480), ("あと2回", 274, 509))
    rt = _shop(monkeypatch, screen, {274: ("ノート", 1000, 2)}, 100000)
    r = shop.run(rt)
    assert "一次要买 2 个" in r and "买了" not in r
    screen = frame(("1,000", 290, 480), ("あと1回", 274, 509))
    rt = _shop(monkeypatch, screen, {274: ("ロジックノート", 1000, 1)}, 100000)
    assert "商店买了" in shop.run(rt)


def test_shop_all_skips_unaffordable_and_keeps_going(monkeypatch):
    """all：先碰到买不起的 6,000，后面 3,000 的照样买（CodeRabbit #122）。"""
    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    screen = frame(("6,000", 120, 480), ("あと1回", 104, 509), ("3,000", 290, 480), ("あと1回", 274, 509))
    rt = _shop(monkeypatch, screen, {104: ("高い物", 6000, 1), 274: ("ロジックノート", 3000, 1)}, 5000)
    r = shop.run(rt)
    assert "商店买了" in r and "有的金币不够" in r


def test_shop_timeout_still_reports_bought(monkeypatch):
    """买完回首页超时（ResumeBlocked）：结果里照样写出买了什么（#122 review）。"""
    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    screen = frame(("3,000", 120, 480), ("あと1回", 104, 509))
    rt = _shop(monkeypatch, screen, {104: ("ロジックノート", 3000, 1)}, 5000, home_raises=True)
    import pytest

    with pytest.raises(shop.common.PartialStop) as e:  # 这一项算没做完（CodeRabbit #197），已买的照样报
        shop.run(rt)
    assert "商店买了" in e.value.result and "中途停下" in e.value.result and "回首页超时" in e.value.reason


def test_shop_ap_bought_kept_when_stopped(monkeypatch):
    """体力商店买了一件后停下：结果里照样有这一件（CodeRabbit #197）。"""
    from hiro.loop.errors import ResumeBlocked

    rt = _Rt()
    monkeypatch.setattr(shop, "tap_word", lambda *a, **k: True)
    monkeypatch.setattr(shop.common, "frame", lambda rt: frame(("x", 1, 1)))
    monkeypatch.setattr(shop, "tiles", lambda f: [{"x": 100, "y": 500, "left": 2}, {"x": 300, "y": 500, "left": 2}])
    calls = {"n": 0}

    def dialog(rt, gone=False):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ResumeBlocked("弹窗")
        return frame(("APを使用して", 300, 512))

    monkeypatch.setattr(shop, "wait_dialog", dialog)
    monkeypatch.setattr(shop, "read_dialog", lambda g: {"name": "再挑戦チケット", "ap": True, "jewel": False})
    monkeypatch.setattr(shop, "confirm", lambda rt, g: shop.BOUGHT)
    monkeypatch.setattr(shop, "_on_list", lambda rt: True)  # 关完提示回到了列表
    got: list[str] = []
    import pytest

    with pytest.raises(ResumeBlocked):
        shop.buy_ap(rt, ["retry"], got)
    assert got == ["再挑戦チケット"]


def test_shop_max_buys_counts_this_round_only(monkeypatch):
    """免费刷新后的第二轮：上限按这一轮新买的算，不被第一轮已买的件数挤掉（CodeRabbit #197）。"""
    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    screen = frame(("3,000", 120, 480), ("あと1回", 104, 509))
    rt = _shop(monkeypatch, screen, {104: ("ロジックノート", 3000, 1)}, 10**9)
    bought = ["x"] * shop.MAX_BUYS
    shop._buy_round(rt, "all", [], {}, {}, bought)
    assert len(bought) > shop.MAX_BUYS


def test_shop_items_ignored_when_off(monkeypatch):
    """不买时「另外要买的」不算（只是推荐 / 全部的补充），也不进商店。"""
    monkeypatch.setenv("HIRO_SHOP", "off")
    monkeypatch.setenv("HIRO_SHOP_ITEMS", "再挑戦")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    assert shop.run(_Rt()) == "商店：设置里关了"


def test_shop_dialog_reads_long_name():
    """长名字左端靠左（x1 < 150）也读得到（CodeRabbit #122）。"""
    f = frame(("交換確認", 100, 425), ("マニーを使用して、以下のアイテムと交換しますか?", 300, 512))
    f.__dict__["texts"].append(Text("[Wonder Scale]倉本千奈のピース(とても長い名前)", Box(120, 572, 640, 596)))
    assert shop.read_dialog(f)["name"].startswith("[Wonder Scale]")


def test_coin_gacha_times_capped(monkeypatch):
    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:999,sense:3")
    assert coin_gacha.plan() == [("friend", coin_gacha.MAX_TIMES), ("sense", 3)]


def test_shop_can_skip_by_known_price():
    """按名字买：价格只出现在记下来的、不要的商品上才不点开；没见过的价格、和要的同价的照样点开（11-27：挨个点开太慢）。"""
    prices = {"ロジックノート(ボーカル)": 3000, "ロジックノート(ビジュアル)": 3000, "倉本千奈のピース": 1500}
    wants = ["理性笔记（视觉）"]
    assert shop.can_skip(1500, wants, prices)  # 只有碎片是这个价，不要
    assert not shop.can_skip(3000, wants, prices)  # 同价里有要的
    assert not shop.can_skip(4000, wants, prices)  # 没见过的价
    assert not shop.can_skip(None, wants, prices)
    assert not shop.can_skip(1500, [], prices)


def test_shop_skips_known_unwanted_and_learns_prices(monkeypatch):
    monkeypatch.setenv("HIRO_SHOP", "recommended")
    monkeypatch.setenv("HIRO_SHOP_ITEMS", "再挑戦")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    saved = {}
    monkeypatch.setattr(shop, "save_prices", lambda p: saved.update(p))
    screen = frame(("おすすめ", 61, 319), ("3,000", 120, 480), ("あと1回", 104, 509),
                   ("1,500", 290, 480), ("あと1回", 274, 509), ("2,000", 460, 480), ("あと1回", 444, 509))  # fmt: skip
    goods = {104: ("ロジックノート", 3000, 1), 274: ("倉本千奈のピース", 1500, 1), 444: ("再挑戦チケット", 2000, 1)}
    # 要的再挑戦券还没见过价格：同价可能就是它，碎片那格照样点开（CodeRabbit #122）；看过以后记下价格
    monkeypatch.setattr(shop, "load_prices", lambda: {"倉本千奈のピース": 1500})
    rt = _shop(monkeypatch, screen, goods, 100000)
    r = shop.run(rt)
    assert {104, 274, 444} <= {x for x, _ in rt.device.taps}
    assert ("再挑戦チケット" in r or "再挑战" in r) and saved.get("再挑戦チケット") == 2000
    # 要的都见过价格了：只出现在不要的商品上的价格不点开
    monkeypatch.setattr(shop, "load_prices", lambda: {"倉本千奈のピース": 1500, "再挑戦チケット": 2000})
    rt = _shop(monkeypatch, screen, goods, 100000)
    r = shop.run(rt)
    opened = {x for x, _ in rt.device.taps}
    assert 274 not in opened and {104, 444} <= opened  # 碎片（已知不要的价）没点开
    assert "再挑戦チケット" in r or "再挑战" in r


def test_coin_gacha_find_pool_no_initial_swipe(monkeypatch):
    """刚进扭蛋页就在顶上：看得见这个池就不先往上划（11-27 用户）；看不见才回顶重找。"""
    swipes = []
    rt = _Rt()
    rt.device.swipe = lambda *a, **k: swipes.append(a)
    top = frame(("フレンドコインガシャ", 360, 400))
    monkeypatch.setattr(coin_gacha.common, "frame", lambda rt: top)
    monkeypatch.setattr(coin_gacha, "_gacha_title", lambda f, pool: f.texts[0] if pool == "friend" else None)
    _, title = coin_gacha.find_pool(rt, "friend")
    assert title is not None and swipes == []
    _, title = coin_gacha.find_pool(rt, "logic")
    assert title is None and len(swipes) == 4 + 2 + 4  # 往下找 4 屏、回顶 2 下、再往下 4 屏


# ---- 每日免费刷新（HIRO_SHOP_REFRESH，缺省关）：只在一字不差「リスト更新:1回無料」时点，出确认框点空白、绝不确认 ----


def test_free_refresh_label_exact_only():
    assert shop.free_refresh_label(frame(("リスト更新:1回無料", 561, 163))) is not None
    assert shop.free_refresh_label(frame(("リスト更新：1回無料", 561, 163))) is not None  # 全角冒号
    assert shop.free_refresh_label(frame(("リスト更新", 500, 163), ("1回無料", 630, 163))) is not None  # 拆成两块
    assert shop.free_refresh_label(frame(("リスト更新:1回", 561, 163))) is None
    assert shop.free_refresh_label(frame(("リスト更新:1回無料まで", 561, 163))) is None
    assert shop.free_refresh_label(frame(("リスト更新:50", 561, 163))) is None
    assert shop.free_refresh_label(frame(("1回無料", 630, 163))) is None  # 只有「1回無料」不算
    assert shop.free_refresh_label(frame(("リスト更新:1回無料", 561, 700))) is None  # 不在上方


def _seq_rt(monkeypatch, frames):
    rt = _Rt()
    it = iter(frames)
    last = {"f": frames[0]}

    def nxt(rt):
        last["f"] = next(it, last["f"])
        return last["f"]

    monkeypatch.setattr(shop.common, "frame", nxt)

    def tap_word(rt, f, word, **k):  # 画面上有这个字才点（キャンセル 记成 (0, 1)，别的关闭字样 (0, 2)）
        if not any(word in t.text for t in f.texts):
            return False
        rt.device.tap(0, 1 if word == "キャンセル" else 2)
        return True

    monkeypatch.setattr(shop, "tap_word", tap_word)
    return rt


SHOPLIST = (
    ("デイリー交換所", 140, 55),
    ("3,000", 120, 480),
    ("あと1回", 104, 509),
)  # 兑换所商品列表（关框 / 刷新成了的正面证据）


def test_refresh_dialog_is_dismissed_never_confirmed(monkeypatch):
    label = frame(("リスト更新:1回無料", 561, 163))
    dlg = frame(
        ("リスト更新", 360, 500),
        ("ジュエルを消費して更新しますか", 360, 600),
        ("キャンセル", 168, 1143),
        ("決定", 490, 1140),
    )
    rt = _seq_rt(monkeypatch, [label, dlg, frame(*SHOPLIST)])
    why = shop.refresh_list(rt)
    assert why is not None and "确认框" in why
    assert rt.device.taps == [(561, 163), shop.DISMISS_TAP]  # 点了刷新、点空白，没点「決定」


def test_refresh_dialog_still_up_taps_cancel_not_decide(monkeypatch):
    label = frame(("リスト更新:1回無料", 561, 163))
    dlg = frame(("確認", 360, 500), ("キャンセル", 168, 1143), ("決定", 490, 1140))
    rt = _seq_rt(monkeypatch, [label, dlg, dlg, frame(*SHOPLIST)])
    assert "确认框" in shop.refresh_list(rt)
    assert rt.device.taps == [(561, 163), shop.DISMISS_TAP, (0, 1)]  # 空白没关掉：点キャンセル


def test_refresh_dialog_that_wont_close_stops_shop(monkeypatch):
    """框一直关不掉：整个日常停下（RefreshDialogStuck，shop.run 不接），后面不再点任何东西；从头到尾没点「決定」。"""
    import pytest

    label = frame(("リスト更新:1回無料", 561, 163))
    dlg = frame(("確認", 360, 500), ("キャンセル", 168, 1143), ("決定", 490, 1140))
    rt = _seq_rt(monkeypatch, [label, dlg])
    with pytest.raises(shop.RefreshDialogStuck, match="关不掉"):
        shop.refresh_list(rt)
    assert (490, 1140) not in rt.device.taps and len(rt.device.taps) == 1 + shop.REFRESH_CLOSE_TRIES


def test_refresh_late_dialog_is_caught(monkeypatch):
    """框在点完 1.5 秒以后才弹（前几帧右上角的字还在 / 在过场里）：照样认出来关掉，不当成刷新成功。"""
    label = frame(("リスト更新:1回無料", 561, 163))
    blank = frame(*SHOPLIST)
    dlg = frame(("リストを更新しますか", 360, 600), ("閉じる", 168, 1143), ("更新する", 490, 1140))
    rt = _seq_rt(monkeypatch, [label, label, blank, blank, blank, dlg, blank])
    assert "确认框" in shop.refresh_list(rt)
    assert rt.device.taps == [(561, 163), shop.DISMISS_TAP]  # 完整点击序列：没点「更新する」（CodeRabbit #138）


def test_refresh_success_after_watch_window(monkeypatch):
    label = frame(("リスト更新:1回無料", 561, 163))
    after = frame(("リスト更新:50", 561, 163), *SHOPLIST[1:])
    rt = _seq_rt(monkeypatch, [label, after])
    assert shop.refresh_list(rt) is None and rt.device.taps == [(561, 163)]


def test_refresh_not_offered_does_not_tap(monkeypatch):
    rt = _seq_rt(monkeypatch, [frame(("あと20時間27分で更新", 561, 220))])
    assert shop.refresh_list(rt) is not None and rt.device.taps == []


def test_refresh_then_buy_another_round(monkeypatch):
    monkeypatch.setenv("HIRO_SHOP", "recommended")
    monkeypatch.setenv("HIRO_SHOP_REFRESH", "on")
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    rounds = []
    monkeypatch.setattr(shop, "goto_daily_exchange", lambda rt: True)
    monkeypatch.setattr(shop.nav, "goto_home", lambda rt: None)

    def fake_round(rt, m, names, prices, seen, bought):
        rounds.append(1)
        bought.append(f"ロジックノート{len(rounds)}")
        return {"cands": {}, "rec_seen": True, "money_short": False, "tiles_seen": True}

    monkeypatch.setattr(shop, "_buy_round", fake_round)
    monkeypatch.setattr(shop, "refresh_list", lambda rt: None)
    r = shop.run(_Rt())
    assert len(rounds) == 2 and "免费刷新" in r
    monkeypatch.setattr(shop, "refresh_list", lambda rt: "没看到")
    rounds.clear()
    r = shop.run(_Rt())
    assert len(rounds) == 1 and "免费刷新：没看到" in r
    monkeypatch.setenv("HIRO_SHOP_REFRESH", "off")
    rounds.clear()
    shop.run(_Rt())
    assert len(rounds) == 1


def test_coin_gacha_stop_reports_drawn_but_fails(monkeypatch):
    """抽完关结果时超时：已抽的照样报，这一项算失败（原来记成成功，CodeRabbit #197）。"""
    import pytest

    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:2")
    monkeypatch.setattr(coin_gacha, "goto_coin_gacha", lambda rt: True)

    def draw(rt, pool, times):
        raise coin_gacha.DrawStopped(pool, 2, "关结果超时")

    monkeypatch.setattr(coin_gacha, "draw", draw)
    with pytest.raises(coin_gacha.common.PartialStop) as e:
        coin_gacha.run(_Rt())
    assert "2 次" in e.value.result and "关结果超时" in e.value.reason


def test_shop_task_timeout_still_reports_bought(monkeypatch):
    """自检：单项超时（Deadline 的 TaskTimeout）也照样报已买的，这一项算没做完。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    screen = frame(("3,000", 120, 480), ("あと1回", 104, 509))
    rt = _shop(monkeypatch, screen, {104: ("ロジックノート", 3000, 1)}, 5000, home_raises="timeout")
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(rt)
    assert "商店买了" in e.value.result and e.value.reason == "超时"


# ---- 11-28 审查（CodeRabbit #197 / #198 上 shop.py:310 / 318 / 446、coin_gacha.py:43 / 226）----

DIALOG = frame(("交換確認", 100, 425), ("マニーを使用して、以下のアイテムと交換しますか?", 300, 512),
               ("ロジックノート", 380, 584), ("キャンセル", 200, 1110), ("決定", 520, 1110))  # fmt: skip
LIST = frame(("3,000", 120, 480), ("あと1回", 104, 509))


class _WaitRt(_Rt):
    """wait_until 按给定的帧序列一帧帧看（每帧 0.5 秒），到 timeout 还没满足返回 (False, 最后一帧)。"""

    def __init__(self, frames):
        super().__init__()
        self.frames = list(frames)

    def wait_until(self, cond, timeout, every=0.5):
        f = None
        for _ in range(int(timeout / every)):
            f = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
            if cond(f):
                return True, f
        return False, f


def _word_taps(monkeypatch, module=shop):
    taps = []

    def tap_word(rt, f, word, **k):
        if not any(word in t.text for t in f.texts):
            return False
        taps.append(word)
        return True

    monkeypatch.setattr(module, "tap_word", tap_word)
    return taps


def test_confirm_without_decide_button_is_not_bought(monkeypatch):
    """没读到「決定」（没点）：取消、返回 MISSED，不当买成（原来只等标题消失就算买了）。"""
    taps = _word_taps(monkeypatch)
    no_decide = frame(*[(t.text, t.rect.center.x, t.rect.center.y) for t in DIALOG.texts if t.text != "決定"])
    rt = _WaitRt([LIST, LIST])
    monkeypatch.setattr(shop.common, "frame", lambda rt: LIST)
    assert shop.confirm(rt, no_decide) == shop.MISSED and taps == ["キャンセル"]


def test_dialog_gone_needs_two_frames_with_text():
    """一帧漏读「交換確認」或空帧不算框关了：要连着两帧有字、都没这个框。"""
    blank = frame()
    rt = _WaitRt([DIALOG, blank, LIST, DIALOG] * 3)
    assert shop.wait_dialog(rt, gone=True) is None  # 中间夹着的单帧不算
    rt = _WaitRt([DIALOG, LIST, LIST])
    assert shop.wait_dialog(rt, gone=True) is LIST


def test_cancel_dialog_stuck_stops_everything(monkeypatch):
    """点了キャンセル 框还在：抛 DialogStuck（stop_all），不接着点下一格（会读到旧框、买错东西）。"""
    taps = _word_taps(monkeypatch)
    monkeypatch.setattr(shop.common, "frame", lambda rt: DIALOG)
    import pytest

    with pytest.raises(shop.common.DialogStuck):
        shop.cancel_dialog(_WaitRt([DIALOG]), DIALOG)
    assert taps == ["キャンセル", "キャンセル"]


def test_shop_dialog_stuck_reports_bought_and_stops_all(monkeypatch):
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_REFRESH", raising=False)
    monkeypatch.setattr(shop, "goto_daily_exchange", lambda rt: True)

    def stuck_round(rt, m, names, prices, seen, bought):
        bought.append("ロジックノート")
        raise shop.common.DialogStuck("商店：兑换确认框关不掉")

    monkeypatch.setattr(shop, "_buy_round", stuck_round)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(_Rt())
    assert e.value.stop_all and "商店买了" in e.value.result


def test_shop_refresh_stuck_keeps_bought(monkeypatch):
    """免费刷新后框关不掉（RefreshDialogStuck）：第一轮买的照样报，整个日常停下（原来结果全丢）。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.setenv("HIRO_SHOP_REFRESH", "on")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.setattr(shop, "goto_daily_exchange", lambda rt: True)

    def one_round(rt, m, names, prices, seen, bought):
        bought.append("ロジックノート")
        return {"cands": {}, "rec_seen": True, "money_short": False, "tiles_seen": True}

    def stuck(rt):
        raise shop.RefreshDialogStuck("框关不掉")

    monkeypatch.setattr(shop, "_buy_round", one_round)
    monkeypatch.setattr(shop, "refresh_list", stuck)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(_Rt())
    assert e.value.stop_all and "商店买了" in e.value.result

    def timeout(rt):  # 刷新观察期里超时：确认框可能还开着，也整个停下
        raise shop.common.TaskTimeout()

    monkeypatch.setattr(shop, "refresh_list", timeout)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(_Rt())
    assert e.value.stop_all


def test_shop_bought_is_recorded_before_dismissing_prompt(monkeypatch):
    """框关了（买成了）之后、关买完提示时超时：这一件照样记上（原来 confirm 里关提示，超时就漏记）。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_REFRESH", raising=False)
    rt = _shop(monkeypatch, LIST, {104: ("ロジックノート", 3000, 1)}, 5000)

    def dismiss(rt):
        raise shop.common.TaskTimeout()

    monkeypatch.setattr(shop, "dismiss_bought", dismiss)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(rt)
    assert "商店买了" in e.value.result


def test_shop_no_tiles_is_not_done_today(monkeypatch):
    """一个商品格都没读到：不报「推荐商品今天买过了」，这一项算没做完。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "recommended")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_REFRESH", raising=False)
    rt = _shop(monkeypatch, frame(("デイリー交換所", 360, 40)), {}, 5000)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(rt)
    assert "没读到" in e.value.reason and "今天买过了" not in e.value.result


def test_coin_gacha_plan_rejects_bad_counts(monkeypatch):
    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:-1,sense:abc,logic,anomaly:2")
    assert coin_gacha.plan() == [("logic", 1), ("anomaly", 2)]
    monkeypatch.setenv(
        "HIRO_COIN_GACHA", "friend:,sense:²,logic:8,logic:8"
    )  # 有冒号没次数 / 上标数字不抽；同一个池合起来也不超上限
    assert coin_gacha.plan() == [("logic", coin_gacha.MAX_TIMES)]


def test_amount_needs_a_digit():
    assert shop.amount("3,000") == 3000 and shop.amount(",") is None and shop.amount("") is None
    assert shop.tiles(frame((",", 120, 480), ("あと1回", 104, 509)))[0]["price"] is None
    assert shop.cost(frame((",", 193, 992), ("1,000", 450, 992))) is None


def test_can_skip_only_when_every_wanted_item_has_a_known_price():
    """要买的再挑戦券还没见过价格：同价的格子不跳（可能就是它）；见过以后才按价格跳不要的。"""
    prices = {"かけらA": 1500}
    assert not shop.can_skip(1500, ["再挑戦"], prices)
    prices["再挑戦チケット"] = 2000
    assert shop.can_skip(1500, ["再挑戦"], prices) and not shop.can_skip(2000, ["再挑戦"], prices)


def test_late_dialog_is_cancelled_before_next_tile(monkeypatch):
    """点了格子 6 秒没见到框、之后才弹出来：取消掉，不留给下一格。"""
    taps = _word_taps(monkeypatch)
    monkeypatch.setattr(shop.common, "frame", lambda rt: DIALOG)
    monkeypatch.setattr(shop, "wait_dialog", lambda rt, gone=False: LIST if gone else None)
    monkeypatch.setattr(shop, "_on_list", lambda rt: True)  # 取消后回到了列表
    shop._open["dialog"] = True
    shop._late_dialog(_Rt())
    assert taps == ["キャンセル"] and shop._open["dialog"] is False


def test_late_dialog_unknown_screen_stops(monkeypatch):
    """点了格子没见框、也认不出商品列表（框可能开着只是字没读到）：停下，不去点下一格（CodeRabbit #138）。"""
    import pytest

    monkeypatch.setattr(shop.common, "frame", lambda rt: frame(("…", 360, 600)))
    with pytest.raises(shop.common.DialogStuck):
        shop._late_dialog(_Rt())
    monkeypatch.setattr(shop.common, "frame", lambda rt: LIST)
    shop._late_dialog(_Rt())  # 认得出列表：照常接着点下一格


def test_dialog_not_closed_while_buttons_visible():
    """标题漏读、底下キャンセル / 決定还在：不算关了（CodeRabbit #138）。"""
    no_title = frame(*[(t.text, t.rect.center.x, t.rect.center.y) for t in DIALOG.texts if t.text != "交換確認"])
    assert shop.wait_dialog(_WaitRt([no_title]), gone=True) is None


def test_shop_timeout_while_dialog_open_stops_all(monkeypatch):
    """点开格子、框还没确认关掉时超时：后面的项都不跑（框可能还开着）。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_REFRESH", raising=False)
    monkeypatch.setattr(shop, "goto_daily_exchange", lambda rt: True)

    def opened_then_timeout(rt, m, names, prices, seen, bought):
        shop._open["dialog"] = True
        raise shop.common.TaskTimeout()

    monkeypatch.setattr(shop, "_buy_round", opened_then_timeout)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(_Rt())
    assert e.value.stop_all


def test_coin_gacha_pool_not_found_is_not_coin_short(monkeypatch):
    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:1")
    monkeypatch.setattr(coin_gacha, "goto_coin_gacha", lambda rt: True)
    monkeypatch.setattr(coin_gacha.nav, "goto_home", lambda rt: None)
    monkeypatch.setattr(coin_gacha, "find_pool", lambda rt, pool: (None, None))
    r = coin_gacha.run(_Rt())
    assert "没认出来" in r and "币不够" not in r


def test_coin_gacha_draw_stopped_stops_all(monkeypatch):
    """关抽卡结果页没关成：画面还停在结果页上，后面的项都不跑（CodeRabbit #197 / #198）。"""
    import pytest

    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:2")
    monkeypatch.setattr(coin_gacha, "goto_coin_gacha", lambda rt: True)

    def draw(rt, pool, times):
        raise coin_gacha.DrawStopped(pool, 2, "关结果超时")

    monkeypatch.setattr(coin_gacha, "draw", draw)
    with pytest.raises(coin_gacha.common.PartialStop) as e:
        coin_gacha.run(_Rt())
    assert e.value.stop_all

    def timeout(rt, pool, times):  # 普通超时（没在结果页上）：不必整个停下
        raise coin_gacha.common.TaskTimeout()

    monkeypatch.setattr(coin_gacha, "draw", timeout)
    with pytest.raises(coin_gacha.common.PartialStop) as e:
        coin_gacha.run(_Rt())
    assert not e.value.stop_all


def test_coin_gacha_cancel_must_close_dialog(monkeypatch):
    import pytest

    box = frame(("コインガシャ確認", 360, 300), ("キャンセル", 200, 1110))
    taps = _word_taps(monkeypatch, coin_gacha)
    monkeypatch.setattr(coin_gacha.common, "frame", lambda rt: box)
    with pytest.raises(coin_gacha.common.DialogStuck):
        coin_gacha._cancel(_WaitRt([box]), box)
    assert taps.count("キャンセル") >= 2
    coin_gacha._cancel(_WaitRt([frame(("コインガシャ", 360, 40))]), box)  # 关了：不抛


# ---- CodeRabbit 第三轮（#138 / #197 上看到的 #122 部分）----


def test_shop_missed_decide_is_not_done_today(monkeypatch):
    """推荐商品确认框没读到「決定」：不报「今天买过了」，这一项算没做完。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "recommended")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_REFRESH", raising=False)
    screen = frame(("おすすめ", 61, 319), ("3,000", 120, 480), ("あと1回", 104, 509))
    rt = _shop(monkeypatch, screen, {104: ("ロジックノート", 3000, 1)}, 100000)
    monkeypatch.setattr(shop, "confirm", lambda rt, g: shop.MISSED)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(rt)
    assert "決定" in e.value.reason and "今天买过了" not in e.value.result


def test_refresh_needs_list_after_tap(monkeypatch):
    """点了免费刷新、观察期最后一帧是空的（过场）：没认出刷新后的列表就不算刷成，不买第二轮。"""
    label = frame(("リスト更新:1回無料", 561, 163), ("3,000", 120, 480), ("あと1回", 104, 509))
    rt = _seq_rt(
        monkeypatch, [label, frame(), frame(), frame(), frame(), frame(), frame(), frame(), frame(), frame(), frame()]
    )
    assert shop.refresh_list(rt) is not None


def test_daily_summary_uses_real_gacha_plan_and_contest_pick():
    from hiro.cli.jobs import KINDS

    s = KINDS["daily"].summary({"items": "contest,coin_gacha", "coin_gacha": "friend:8,friend:8", "contest_pick": "3"})
    assert f"友情 {coin_gacha.MAX_TIMES} 次" in s and "感性" not in s
    assert "第 3 个对手" in s
    assert "不抽" in KINDS["daily"].summary({"items": "coin_gacha", "coin_gacha": "friend:abc"})


def test_coin_gacha_explicit_pool_missing_is_partial(monkeypatch):
    """friend:1,sense:1：友情抽到、感性没认出来：抽了的照样报，这一项算没做完（CodeRabbit #197 / #198）。"""
    import pytest

    monkeypatch.setenv("HIRO_COIN_GACHA", "friend:1,sense:1")
    monkeypatch.setattr(coin_gacha, "goto_coin_gacha", lambda rt: True)
    monkeypatch.setattr(coin_gacha.nav, "goto_home", lambda rt: None)

    def draw(rt, pool, times):
        if pool == "sense":
            coin_gacha._state["missed"].append(pool)
            return 0
        return 1

    monkeypatch.setattr(coin_gacha, "draw", draw)
    with pytest.raises(coin_gacha.common.PartialStop) as e:
        coin_gacha.run(_Rt())
    assert "友情 1 次" in e.value.result and "感性" in e.value.reason and not e.value.stop_all


def test_shop_unconfirmed_purchase_is_reported(monkeypatch):
    """点了「決定」后、看到框关掉前被单项超时打断：结果里写这件「没核实」，不悄悄丢掉（CodeRabbit #197）。"""
    import pytest

    monkeypatch.setenv("HIRO_SHOP", "all")
    monkeypatch.delenv("HIRO_SHOP_AP", raising=False)
    monkeypatch.delenv("HIRO_SHOP_ITEMS", raising=False)
    monkeypatch.delenv("HIRO_SHOP_REFRESH", raising=False)
    monkeypatch.setattr(shop, "goto_daily_exchange", lambda rt: True)

    def interrupted(rt, m, names, prices, seen, bought):
        shop._open.update(dialog=True, unsure="ロジックノート(ボーカル)")
        raise shop.common.TaskTimeout()

    monkeypatch.setattr(shop, "_buy_round", interrupted)
    with pytest.raises(shop.common.PartialStop) as e:
        shop.run(_Rt())
    assert "没核实" in e.value.result and e.value.stop_all
