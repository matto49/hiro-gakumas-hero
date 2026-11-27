"""商店的デイリー交換所（マニー换东西）：日常任务「ショップで買い物」用。KAA tasks/daily/purchase 的思路，按 OCR 文字做（没有 KAA 的模板）。

首页「ショップ」→「デイリー交换所」（マニー页签）→ 按设置逐个点商品，交換確認框里读商品名，要买就「決定」，不要就「キャンセル」。
- HIRO_SHOP：`off`（缺省，不进商店：花マニー的事默认不做，开局页的日常选项会显式传）/ `recommended` = 买标「おすすめ」的，
  一个都没有时买最便宜的一件（让日常任务完成）/ `all` = 能买的都买。
- HIRO_SHOP_REFRESH（缺省 off）：买完用每日 1 次的免费刷新再买一轮。只在右上角一字不差读到「リスト更新:1回無料」时点；
  点了以后出了任何确认框都点框外空白关掉（再不行点キャンセル），绝不确认、不花钻石。
- HIRO_SHOP_ITEMS：另外要买的商品名（逗号分隔，名字里包含这几个字就算，如「倉本千奈のピース,再挑戦」）。
- 只用マニー：交換確認框里必须有「マニーを使用して」，看到「ジュエル」一律キャンセル；「リスト更新」不点。
- HIRO_SHOP_AP（可选，缺省不买）：AP 页签（用体力换）买哪几样，逗号分隔：pt（入手サポート強化Ptアップ）/ note（入手ノート数アップ）
  / retry（再挑戦チケット）/ regen（メモリー再生成チケット）。每样每天 2 次、一次 10 AP，确认框缺省就是能买的最多个数，点「決定」一次买完。
  确认框里必须是「APを使用して」。11-25 DMM 实测：AP 页签只有这 4 样，一行。

11-25 DMM 实测的画面（720x1280）：一屏 4 列 3 行，每格下面「あと N回」（还能买几次），格子上沿左边「おすすめ」；
慢慢往上滑（1.2 秒）才滚得动，一共 18 件；交換確認框：商品名在「マニーを使用して…」下面一行，「キャンセル」(168,1143)、「決定」(490,1140)。
"""

from __future__ import annotations

import logging
import os
import re

from hiro.daily import common
from hiro.daily.names import item_name
from hiro.runtime import nav

logger = logging.getLogger(__name__)

TIMEOUT = 40.0
SWIPE = (450, 850, 450, 350)  # 商品格里往上滑（快滑滚不动）
SWIPE_S = 1.2
MAX_SCREENS = 4
MAX_BUYS = 30


AP_ITEMS = {"pt": "サポート強化Pt", "note": "ノート数", "retry": "再挑戦", "regen": "再生成"}


def ap_wanted() -> list[str]:
    return [k for k in (x.strip().lower() for x in (os.environ.get("HIRO_SHOP_AP") or "").split(",")) if k in AP_ITEMS]


def mode() -> str:
    v = (os.environ.get("HIRO_SHOP") or "off").strip().lower()
    return v if v in ("recommended", "all", "off") else "off"


def refresh_on() -> bool:
    """HIRO_SHOP_REFRESH=on：买完用每日 1 次的免费刷新再买一轮（缺省关）。"""
    return (os.environ.get("HIRO_SHOP_REFRESH") or "off").strip().lower() in ("on", "1", "true")


# 免费刷新：右上角那行字一字不差是「リスト更新:1回無料」才点（11-25 DMM 实测 OCR 读到的就是这行，框 [446,151,676,175]）；
# 用完以后这里会变成别的字（要花钻石等），对不上就不点
REFRESH_FREE = "リスト更新:1回無料"
REFRESH_MAX_Y = 320  # 只认画面上方那条（列表里的商品名不会是这行字，保险起见限位置）
DISMISS_TAP = (360, 60)  # 刷新后万一出了确认框：点框外上方空白关掉（不点「決定」「はい」）
DIALOG_WORDS = (
    "キャンセル",
    "決定",
    "はい",
    "いいえ",
    "ジュエル",
    "確認",
    "OK",
    "購入",
    "消費",
    "閉じる",
    "やめる",
    "更新する",
)
REFRESH_WATCH_S = 4.0  # 点了刷新后连看几秒有没有框（框可能弹得晚）
REFRESH_POLL_S = 0.5
REFRESH_CLOSE_TRIES = 3  # 点空白 1 次 + 点关闭字样 2 次，还在就停下
REFRESH_CLOSE_WORDS = ("キャンセル", "いいえ", "閉じる", "やめる")  # 只点这些，绝不点「決定」「はい」「OK」


def _norm_refresh(text: str) -> str:
    from hiro.vision.textnorm import fold

    return fold(text).replace(" ", "").replace("　", "").replace("：", ":")


def free_refresh_label(f):
    """右上角一字不差的「リスト更新:1回無料」（OCR 拆成「リスト更新」「1回無料」两块、在同一行也算）；没有返回 None。"""
    top = [t for t in f.texts if t.rect.center.y < REFRESH_MAX_Y]
    for t in top:
        if _norm_refresh(t.text) == REFRESH_FREE:
            return t
    for t in top:
        if _norm_refresh(t.text) == "1回無料" and any(
            _norm_refresh(o.text) in ("リスト更新", "リスト更新:")
            and abs(o.rect.center.y - t.rect.center.y) < 20
            and o.rect.center.x < t.rect.center.x
            for o in top
        ):
            return t
    return None


def _dialog_up(f) -> bool:
    """画面中间出了框（交換確認或任何带キャンセル / 決定 / はい / ジュエル 等字样的）。"""
    if read_dialog(f) is not None:
        return True
    return any(w in t.text for t in f.texts if 300 < t.rect.center.y < 1200 for w in DIALOG_WORDS)


def refresh_list(rt) -> str | None:
    """用每日免费刷新换一批商品：刷新了返回 None，没刷新返回原因。
    只在读到一字不差的「リスト更新:1回無料」时点；点了以后出了任何确认框都点框外空白关掉、绝不确认（11-27 用户：一定要谨慎，绝不买）。"""
    for _ in range(MAX_SCREENS):
        rt.device.swipe(SWIPE[0], SWIPE[3], SWIPE[2], SWIPE[1], duration=SWIPE_S)  # 回到顶，那行字在上面
    rt.sleep(1.0)
    label = free_refresh_label(common.frame(rt))
    if label is None:
        return "没看到「リスト更新:1回無料」（今天的免费刷新用过了，或没认出），不刷新"
    logger.info("商店：点免费刷新（%s）", label.text)
    rt.device.tap(*label.rect.center)
    # 点完以后连看 REFRESH_WATCH_S 秒（确认框可能弹得晚、第一帧可能在过场里）：出了框就关掉、绝不确认；
    # 关不掉就整个日常停下（RefreshDialogStuck），后面一下都不再点（#122 自检）
    end = rt.clock() + REFRESH_WATCH_S
    g = None
    while True:
        rt.sleep(REFRESH_POLL_S)
        g = common.frame(rt)
        if _dialog_up(g):
            _close_refresh_dialog(rt, g)
            return "点免费刷新出了确认框，没刷新（关掉了，没花东西）"
        if rt.clock() >= end:
            break
    if free_refresh_label(g) is not None:
        return "点了免费刷新但列表没变"
    # 要有刷新成了的正面证据：最后看到的是商品列表（读得到格子）。空帧 / 过场不算（CodeRabbit #138 / #197）
    if not (g.texts and tiles(g)):
        g = common.frame(rt)
        if _dialog_up(g):  # 补拍这一帧出了框：同样关掉、不刷新（CodeRabbit #198）
            _close_refresh_dialog(rt, g)
            return "点免费刷新出了确认框，没刷新（关掉了，没花东西）"
        if free_refresh_label(g) is not None:
            return "点了免费刷新但列表没变"
        if not tiles(g):
            return "点了免费刷新，但没认出刷新后的商品列表，不再买第二轮"
    return None


def _closed_twice(rt, g) -> bool:
    """框关了：这一帧和再下一帧都有字、都没框（一帧空 OCR 不算关了，CodeRabbit #122）。"""
    if not g.texts or _dialog_up(g):
        return False
    rt.sleep(0.5)
    h = common.frame(rt)
    # 还要认得出兑换所的商品列表（只看「框的字没了」不够：漏读一帧框的字也会算关了，CodeRabbit #138）
    return bool(h.texts) and not _dialog_up(h) and bool(tiles(h))


class RefreshDialogStuck(Exception):
    """免费刷新后弹出的框关不掉：整个日常停下（stop_all），后面的项一下都不点，交给人。"""

    stop_all = True


def _close_refresh_dialog(rt, g) -> None:
    """刷新后弹出的框：先点框外空白，再点キャンセル / いいえ / 閉じる；每次都重新看，关不掉就停下。绝不点「決定」「はい」「OK」。"""
    logger.warning("商店：点了免费刷新出了确认框，关掉、不刷新：%s", " ".join(t.text for t in g.texts)[:160])
    for i in range(REFRESH_CLOSE_TRIES):
        if i == 0:
            rt.device.tap(*DISMISS_TAP)
        else:
            for w in REFRESH_CLOSE_WORDS:
                if tap_word(rt, g, w, y_min=600):
                    break
            else:
                rt.device.tap(*DISMISS_TAP)
        rt.sleep(1.0)
        g = common.frame(rt)
        if _closed_twice(rt, g):
            return
    raise RefreshDialogStuck("商店：免费刷新后弹出的框关不掉，日常停下（没点确认，没花东西），请手动关掉")


def wanted_names() -> list[str]:
    return [x.strip() for x in (os.environ.get("HIRO_SHOP_ITEMS") or "").split(",") if x.strip()]


def _norm(s: str) -> str:
    """商品名比较用：fold（全角半角等）+ 长音和「一」不分、去掉空白和括号。OCR 常把「ピース」读成「ピ一ス」。"""
    from hiro.vision.textnorm import fold

    s = fold(s).replace("一", "ー").replace("-", "ー")
    return re.sub(r"[\s\[\]【】()（）|Il]", "", s)


def name_wanted(name: str, wants: list[str]) -> bool:
    """wants 里的名字中日文都行：和读到的日文名比，也和它的中文译名（names.item_name）比。"""
    n, z = _norm(name), _norm(item_name(name))
    return any(_norm(w) and (_norm(w) in n or _norm(w) in z) for w in wants)


PRICE_FILE = "shop_prices.json"  # HIRO_HOME 下：点开看过的商品「名字 → 单价」


def _price_path():
    from hiro.cli.home import hiro_home

    return hiro_home() / PRICE_FILE


def load_prices() -> dict[str, int]:
    """记下来的商品单价（点开过确认框的才有）。读不到就当没有。"""
    import json

    try:
        d = json.loads(_price_path().read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in d.items()}
    except Exception:  # noqa: BLE001  没有 / 坏了：当没记过
        return {}


def save_prices(prices: dict[str, int]) -> None:
    import json

    try:
        p = _price_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(prices, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def can_skip(price: int | None, wants: list[str], prices: dict[str, int]) -> bool:
    """按名字买时，这个没标おすすめ的格子能不能不点开（11-27 用户：挨个点开看名字太慢，205 秒）。
    格子上只有图标和价格、没有名字：只在这个价格**只**出现在记下来的、不要的商品上时跳过；没见过的价格、和要的商品同价的照样点开。
    只会少点开，买之前照旧在确认框里核对名字；万一新出的、要的商品正好和不要的同价，会漏买（不会多买）。"""
    if price is None or not wants:
        return False
    # 要买的每一样都见过价格才跳（CodeRabbit #122：没见过的新商品可能正好和不要的同价，按价格跳会漏买）
    if not all(any(name_wanted(n, [w]) for n in prices) for w in wants):
        return False
    names = [n for n, p in prices.items() if p == price]
    return bool(names) and not any(name_wanted(n, wants) for n in names)


def text_at(f, word: str, *, y_min: int = 0, y_max: int = 1280, x_min: int = 0, x_max: int = 720):
    for t in f.texts:
        x, y = t.rect.center
        if word in t.text and y_min <= y <= y_max and x_min <= x <= x_max:
            return t
    return None


def amount(text: str) -> int | None:
    """「3,000」→ 3000；只读到逗号 / 不是金额 None（原来「,」会走到 int("") 抛错，CodeRabbit #122）。"""
    t = text.strip()
    return int(t.replace(",", "")) if re.fullmatch(r"\d[\d,]*", t) else None


def tiles(f) -> list[dict]:
    """这一屏的商品格：{x, y（点哪里）, left（还能买几次）, price, recommended}。按「あと N回」定位格子。"""
    out = []
    for t in f.texts:
        m = re.search(r"あと\s*(\d+)\s*回", t.text)
        if not m:
            continue
        cx, cy = t.rect.center
        if cy > 1060:  # 页签 / 底栏
            continue
        price = None
        for p in f.texts:
            px, py = p.rect.center
            if abs(px - cx) < 75 and 0 < cy - py < 50 and (v := amount(p.text)) is not None:
                price = v
        rec = any(
            "おすすめ" in r.text and abs(r.rect.center.x - cx) < 85 and 150 < cy - r.rect.center.y < 230
            for r in f.texts
        )
        out.append({"x": cx, "y": cy - 110, "left": int(m.group(1)), "price": price, "recommended": rec})
    return sorted(out, key=lambda d: (d["y"] // 50, d["x"]))


def read_dialog(f) -> dict | None:
    """交換確認框：{name, money（用マニー）, jewel（有ジュエル字样）}；不是这个框 None。"""
    if text_at(f, "交換確認") is None:
        return None
    head = text_at(f, "を使用して")
    name = None
    if head is not None:
        # 名字在说明行下面一行；用文字中心判断（长名字左端会靠左，CodeRabbit #122）
        below = [t for t in f.texts if 20 < t.rect.center.y - head.rect.center.y < 110 and 150 < t.rect.center.x < 650]
        if below:
            name = min(below, key=lambda t: t.rect.center.y).text.strip()
    texts = " ".join(t.text for t in f.texts)
    return {
        "name": name,
        "money": head is not None and "マニー" in head.text,
        "ap": head is not None and "AP" in head.text.upper(),
        "jewel": "ジュエル" in texts,
    }


def goto_daily_exchange(rt) -> bool:
    """首页 → ショップ → デイリー交換所（マニー页签）。到了返回 True。"""
    nav.goto_home(rt)

    def step(f):
        if text_at(f, "デイリー交換所", y_max=80) is not None:
            if (tab := text_at(f, "マニー", y_min=1050, y_max=1120)) is not None:
                rt.device.tap(*tab.rect.center)  # マニー 页签（不碰 AP）
                rt.sleep(0.8)
            return True
        if (t := text_at(f, "デイリー交換所", y_min=850)) is not None and text_at(f, "ショップ", y_max=80) is not None:
            rt.device.tap(*t.rect.center)
            rt.sleep(2)
            return None
        if nav.at_home(f) and (t := text_at(f, "ショップ", y_min=940, y_max=1010, x_min=500)) is not None:
            rt.device.tap(t.rect.center.x + 40, t.rect.center.y - 50)  # 「ショップ」字上面的图标
            rt.sleep(2.5)
        return None

    return bool(rt.drive(step, TIMEOUT, "进每日兑换所", every=0.8))


def wait_dialog(rt, gone: bool = False):
    """等交換確認框出来（gone=False）或关掉（gone=True），6 秒；等到返回那一帧，没等到 None。
    关掉要连着两帧有字、都没有这个框才算（一帧漏读「交換確認」或空帧不算关了，CodeRabbit #197 / #198）。"""
    if not gone:
        ok, f = rt.wait_until(lambda f: read_dialog(f) is not None, 6.0, every=0.5)
        return f if ok else None
    streak = [0]

    def closed(f) -> bool:
        # 底下还读得到キャンセル / 決定（框的按钮）的帧不算关了：只是标题漏读（CodeRabbit #138）
        buttons = any(w in t.text for t in f.texts if t.rect.center.y >= 1080 for w in ("キャンセル", "決定"))
        streak[0] = streak[0] + 1 if f.texts and read_dialog(f) is None and not buttons else 0
        return streak[0] >= 2

    ok, f = rt.wait_until(closed, 6.0, every=0.5)
    return f if ok else None


def tap_word(rt, f, word: str, **kw) -> bool:
    if (t := text_at(f, word, **kw)) is not None:
        rt.device.tap(*t.rect.center)
        return True
    return False


_open: dict = {
    "dialog": False,
    "unsure": None,
}  # 点了格子到确认框确认关掉之间：这时超时不能接着跑下一项（框可能还开着，CodeRabbit #122）


def _late_dialog(rt) -> None:
    """点了格子 6 秒没见到框：框可能弹得晚。再看一眼，有框（或框的按钮）就取消；都没有还要认得出商品列表，
    认不出就停下（框可能开着、只是字没读到，别去点下一格买错东西，CodeRabbit #122 / #138）。"""
    f = common.frame(rt)
    if read_dialog(f) is not None or text_at(f, "キャンセル", y_min=1080) is not None:
        cancel_dialog(rt, f)
    elif not _on_list(rt):
        raise common.DialogStuck("商店：点了商品以后认不出画面（不像商品列表），日常停下，请看一下游戏")
    _open["dialog"] = False


def _on_list(rt) -> bool:
    """现在是不是能点的商品列表：没有框、读得到格子。一帧不行再看一帧。"""
    for _ in range(2):
        f = common.frame(rt)
        if read_dialog(f) is None and text_at(f, "キャンセル", y_min=1080) is None and tiles(f):
            return True
        rt.sleep(0.5)
    return False


def cancel_dialog(rt, g) -> None:
    """交換確認框点「キャンセル」，并确认框真的关了。没读到按钮 / 点了还在：抛 DialogStuck（整个日常停下）——
    框还开着就去点下一格，读到的是这个旧框，会把不要的东西买下（CodeRabbit #197 / #198）。"""
    tap_word(rt, g, "キャンセル", y_min=1080)
    if wait_dialog(rt, gone=True) is None:
        tap_word(rt, common.frame(rt), "キャンセル", y_min=1080)  # 再点一次（第一次可能落在过场里）
        if wait_dialog(rt, gone=True) is None:
            raise common.DialogStuck("商店：兑换确认框关不掉，日常停下（没确认，没花东西），请手动关掉")
    if not _on_list(rt):  # 框的字没了还不够：要回到能点的商品列表（CodeRabbit #138）
        raise common.DialogStuck("商店：取消后没回到商品列表，日常停下（没确认，没花东西），请看一下游戏")
    _open["dialog"] = False


BOUGHT, SHORT, MISSED = "bought", "short", "missed"  # confirm 的三种结果：买成了 / 买不起 / 没读到「決定」没点


def confirm(rt, g) -> str:
    """在交換確認框点「決定」：框关了（买成了）返回 BOUGHT；6 秒还在（按钮灰 = 不够）点キャンセル 返回 SHORT；
    没读到「決定」（没点）就取消、返回 MISSED，不当买成（CodeRabbit #197 / #198）。原来只等 1.5 秒，框关得慢就当成不够（review）。
    买完的提示框不在这里关：调用方先记账、再 dismiss_bought（关提示时超时也不漏记已买的，CodeRabbit #198）。"""
    if not tap_word(rt, g, "決定", y_min=1080):
        logger.warning("兑换确认框没读到「決定」，不买")
        cancel_dialog(rt, g)
        return MISSED
    # 点了「決定」到看到框关掉之间：这时被打断（单项超时等），这件可能已经买了，结果里写「没核实」（CodeRabbit #197）
    _open["unsure"] = (read_dialog(g) or {}).get("name") or "?"
    if wait_dialog(rt, gone=True) is None:
        cancel_dialog(rt, common.frame(rt))  # 取消成了才清「没核实」：取消途中被打断，这件仍可能买了（CodeRabbit #122）
        _open["unsure"] = None
        return SHORT
    _open["dialog"] = False
    _open["unsure"] = None
    return BOUGHT


def dismiss_bought(rt) -> None:
    """关掉买完的提示框（閉じる / OK）。关的时候超时：提示可能还开着，后面的项都不跑（_open，CodeRabbit #197）。"""
    _open["dialog"] = True
    for _ in range(3):
        h = common.frame(rt)
        if not (tap_word(rt, h, "閉じる", y_min=900) or tap_word(rt, h, "OK", y_min=900)):
            break
        rt.sleep(0.8)
    if not _on_list(rt):  # 提示没关掉：后面的点击会被挡住，停下（已买的调用方已经记了，CodeRabbit #138）
        raise common.DialogStuck("商店：买完的提示关不掉，日常停下（已买的照样报），请看一下游戏")
    _open["dialog"] = False


def cost(f) -> int | None:
    """交換確認框最下面「持有 → 换完后」那一行算出的花费（前 − 后）；读不到 None。"""
    nums = []
    for t in f.texts:
        if 940 < t.rect.center.y < 1060 and (v := amount(t.text)) is not None:
            nums.append((t.rect.center.x, v))
    if len(nums) < 2:
        return None
    nums.sort()
    return nums[0][1] - nums[-1][1]


def buy_ap(rt, keys: list[str], got: list[str] | None = None) -> list[str]:
    """在デイリー交換所切到 AP 页签，买 keys 对应的几样（按确认框里的名字认），买到的名字追加进 got 并返回。
    got 由调用方传入：中途停下（ResumeBlocked）时已经买到的照样在里面（CodeRabbit #197）。"""
    got = [] if got is None else got
    f = common.frame(rt)
    if not tap_word(rt, f, "AP", y_min=1050, y_max=1120, x_min=400):
        from hiro.loop.errors import ResumeBlocked

        # 没认出页签不是「体力不够 / 今天买过了」：说清楚停在哪（CodeRabbit #198）
        raise ResumeBlocked("没认出体力商店（AP）页签，体力商店没买，可以再试一次")
    rt.sleep(1.5)
    for tile in tiles(common.frame(rt)):
        if tile["left"] <= 0:
            continue
        _open["dialog"] = True
        rt.device.tap(tile["x"], tile["y"])
        g = wait_dialog(rt)
        if g is None:
            _late_dialog(rt)
            continue
        d = read_dialog(g)
        name = (d or {}).get("name") or ""
        want = d is not None and d["ap"] and not d["jewel"] and any(AP_ITEMS[k] in name for k in keys)
        if not want:
            cancel_dialog(rt, g)
            continue
        r = confirm(rt, g)  # 缺省数量就是能买的最多
        if r == SHORT:  # 没买成 = 体力不够
            break
        if r == MISSED:
            continue
        got.append(name)
        logger.info("AP 商店买了：%s", name)
        dismiss_bought(rt)
    return got


def _fallback(rt, cands: list[dict]) -> tuple[str | None, str | None]:
    """没有おすすめ（今天也没卖过）时买最便宜的一件，让「ショップで買い物」完成：返回 (买到的名字, 没买的原因)。
    滑动距离不固定，回到记下的坐标可能是别的格子（review）：回到顶后逐屏按价格找。确认框缺省是能买的最多个（あと N回），
    只在框里实际扣的マニー正好是一件的价时「決定」（#122 review：原来按剩余次数整批买），所以优先挑只剩 1 次的。"""
    pick = min(cands, key=lambda t: (t["left"] != 1, t["price"]))
    price = pick["price"]
    for _ in range(MAX_SCREENS):
        rt.device.swipe(SWIPE[0], SWIPE[3], SWIPE[2], SWIPE[1], duration=SWIPE_S)  # 回到顶
    rt.sleep(1.0)
    for _ in range(MAX_SCREENS + 1):
        tile = next(
            (t for t in tiles(common.frame(rt)) if t["price"] == price and t["left"] == pick["left"] and t["left"] > 0),
            None,
        )
        if tile is not None:
            _open["dialog"] = True
            rt.device.tap(tile["x"], tile["y"])
            g = wait_dialog(rt)
            if g is None:
                _late_dialog(rt)
                return None, "最便宜那件的确认框没出来"
            d = read_dialog(g)
            c = cost(g)
            if d is not None and d["money"] and not d["jewel"] and c == price:
                r = confirm(rt, g)
                if r == BOUGHT:
                    return d["name"] or "?", None
                return None, ("金币不够" if r == SHORT else "确认框没读到「決定」，没买")
            logger.info("最便宜那件对不上（价 %s，框里扣 %s），不买", price, c)
            cancel_dialog(rt, g)
            if c is not None and price > 0 and c > price:
                return None, f"最便宜那件一次要买 {c // price} 个，没买"
            return None, "最便宜那件的价钱对不上，没买"
        rt.device.swipe(*SWIPE, duration=SWIPE_S)
        rt.sleep(1.0)
    return None, "回头没找到最便宜那件"


def _buy_round(
    rt, m: str, names: list[str], prices: dict[str, int], seen_prices: dict[str, int], bought: list[str]
) -> dict:
    """从当前列表的顶上往下买一轮。返回 {cands（兜底候选）, rec_seen（看到过おすすめ）, money_short（有一件买不起）}；买到的追加进 bought。"""
    skip: set[tuple] = set()  # 看过框、决定不买（或买不起）的格子（屏号, x, y）：重读这一屏时不再点开
    cands: dict[tuple, dict] = {}  # recommended 模式：没标おすすめ、还能买的格子（兜底挑最便宜的）
    rec_seen = False  # 看到过おすすめ（含今天已经买光的）：有就不兜底，今天已经算买过了（#122 review）
    money_short = False  # 有一件金币不够（跳过它接着看别的，CodeRabbit #122）
    missed = False  # 有一件要买的没读到「決定」、没买（不能报成「今天买过了」，CodeRabbit #138）
    tiles_seen = False  # 一个商品格都没读到（页面没加载 / 读不到字）：不能当成「今天买过了」
    screens = 0
    last_sig = None
    start = len(bought)  # 上限按这一轮新买的算：免费刷新后的第二轮不被第一轮的件数挤掉（CodeRabbit #197）
    while screens < MAX_SCREENS and len(bought) - start < MAX_BUYS:
        f = common.frame(rt)
        sig = tuple((t.text, t.rect.center.y // 20) for t in f.texts if t.rect.center.y < 1060)
        if sig == last_sig:
            break  # 滑不动了：到底
        last_sig = sig
        acted = False
        for tile in tiles(f):
            tiles_seen = True
            key = (screens, tile["x"] // 20, tile["y"] // 20)
            rec_seen = rec_seen or tile["recommended"]
            if tile["left"] <= 0 or key in skip:
                continue
            if m == "recommended" and not tile["recommended"]:
                if tile["price"] is not None:
                    cands[key] = tile  # 最低价兜底和指定名字无关（CodeRabbit #122）
                if not names or can_skip(tile["price"], names, prices):
                    continue
            _open["dialog"] = True
            rt.device.tap(tile["x"], tile["y"])
            g = wait_dialog(rt)
            if g is None:
                _late_dialog(rt)
                continue
            d = read_dialog(g)
            name = (d or {}).get("name") or "?"
            if name != "?" and tile["price"] is not None and d is not None and d["money"]:
                seen_prices[name] = tile["price"]
            want = (
                d is not None
                and d["money"]
                and not d["jewel"]
                and (m == "all" or tile["recommended"] or name_wanted(name, names))
            )
            skip.add(key)
            if not want:
                if d is not None and d["jewel"]:
                    logger.warning("兑换确认框里有钻石字样，不买：%s", name)
                cancel_dialog(rt, g)
                continue
            r = confirm(rt, g)
            if r == SHORT:  # 决定点了框不关：マニー不够（按钮灰）。跳过这件，接着看便宜的
                money_short = True
                continue
            if r == MISSED:  # 没读到「決定」、已取消：这格不再点开
                missed = True
                continue
            bought.append(name)  # 先记账再关提示（关提示时超时也不漏记，CodeRabbit #198）
            logger.info("商店买了：%s", name)
            dismiss_bought(rt)
            acted = True
            break  # 画面变了，重新读这一屏
        if acted:
            last_sig = None
            continue
        rt.device.swipe(*SWIPE, duration=SWIPE_S)
        rt.sleep(1.0)
        screens += 1
    return {
        "cands": cands,
        "rec_seen": rec_seen,
        "money_short": money_short,
        "tiles_seen": tiles_seen,
        "missed": missed,
    }


def run(rt) -> str:
    """HIRO_SHOP / HIRO_SHOP_ITEMS / HIRO_SHOP_AP / HIRO_SHOP_REFRESH 见文件头。中途超时（ResumeBlocked）时照样报出已经买到的（#122 review）。"""
    from hiro.loop.errors import ResumeBlocked

    m = mode()
    ap_keys = ap_wanted()
    if m == "off" and not ap_keys:
        return "商店：设置里关了"
    names = wanted_names() if m != "off" else []  # 「另外要买的」是推荐 / 全部的补充：不买时不算（#122 review）
    prices = load_prices() if names else {}
    seen_prices = dict(prices)
    bought: list[str] = []
    ap_got: list[str] = []
    money_short = False
    note: str | None = None
    refresh_note: str | None = None  # 开了免费刷新但没刷成的原因
    refreshed = False
    stopped: str | None = None
    stop_all = False  # 停在可能还开着框的地方（刷新确认框 / 兑换框关不掉）：后面的项都不跑
    missed = False
    in_refresh = False  # 点免费刷新到确认没框之间
    tiles_seen = True
    _open.update(dialog=False, unsure=None)
    try:
        if not goto_daily_exchange(rt):
            return "商店：没进到每日兑换所"
        rec_seen = False
        cands: dict[tuple, dict] = {}

        def fallback() -> None:
            nonlocal note
            if m == "recommended" and not bought and not rec_seen and cands and not money_short:
                got, note = _fallback(rt, list(cands.values()))
                if got:
                    bought.append(got)
                    dismiss_bought(rt)  # 和普通购买一样先记账再关提示（不然挡住后面的 AP 页签，CodeRabbit #122）

        if m != "off":
            r = _buy_round(rt, m, names, prices, seen_prices, bought)
            rec_seen, cands, money_short, tiles_seen = r["rec_seen"], r["cands"], r["money_short"], r["tiles_seen"]
            missed = r.get("missed", False)
            if not tiles_seen:
                raise ResumeBlocked("一个商品都没读到（商店页没加载出来，或读不到画面上的字）")
            if refresh_on():
                fallback()  # 这一轮的兜底先做（刷新会换掉候选，CodeRabbit #138）
                cands = {} if bought else cands
                in_refresh = True
                why = None if money_short else refresh_list(rt)
                in_refresh = False
                if money_short:
                    refresh_note = "金币不够，没用免费刷新"
                elif why:
                    refresh_note = why
                else:
                    refreshed = True
                    r = _buy_round(rt, m, names, prices, seen_prices, bought)
                    rec_seen, cands = rec_seen or r["rec_seen"], r["cands"]
                    money_short = money_short or r["money_short"]
                    missed = missed or r.get("missed", False)
                    if not r["tiles_seen"]:  # 刷新后一个格子都没读到：第二轮没做成（CodeRabbit #197）
                        raise ResumeBlocked("免费刷新后一个商品都没读到（第二轮没买）")
        if missed:  # 有一件要买的没读到「決定」：已买的照样报，这一项算没做完（CodeRabbit #122 / #138）
            raise ResumeBlocked("有要买的商品确认框里没读到「決定」，没买，可以再试一次")
        fallback()
        if ap_keys:
            buy_ap(rt, ap_keys, ap_got)
        nav.goto_home(rt)
    except (ResumeBlocked, common.TaskTimeout, RefreshDialogStuck, common.DialogStuck) as e:
        # 单项超时（Deadline）也照样报已买的（自检）；框关不掉 / 刷新确认框还可能开着时整个日常停下，已买的照样报（CodeRabbit #197）
        stopped = str(e) or "超时"
        stop_all = in_refresh or _open["dialog"] or getattr(e, "stop_all", False)
        logger.warning("商店中途停下：%s", stopped)
    if names and seen_prices != prices:
        save_prices(seen_prices)
    parts = []
    if bought:
        parts.append(
            "商店买了：" + "、".join(item_name(b) for b in bought) + ("（有的金币不够，没买）" if money_short else "")
        )
    elif money_short:
        parts.append("商店：金币不够")
    elif note:
        parts.append("商店：没有推荐商品，" + note)
    elif m != "off" and stopped is None:
        parts.append("商店：没有要买的（推荐商品今天买过了）" if m == "recommended" else "商店：没有要买的")
    if refreshed:
        parts.append("商店用了免费刷新")
    elif refresh_note:
        parts.append("免费刷新：" + refresh_note)
    if ap_keys:
        parts.append(
            "体力商店买了：" + "、".join(item_name(b) for b in ap_got)
            if ap_got
            else ("体力商店：没买到（体力不够或今天买过了）" if stopped is None else "体力商店：没买")
        )
    if stopped is not None:
        if _open.get("unsure"):
            parts.append(f"点了「決定」后被打断、没核实买没买：{item_name(_open['unsure'])}（到游戏里看一下）")
        parts.append(f"商店中途停下：{stopped}")
        raise common.PartialStop(
            "；".join(parts), stopped, stop_all=stop_all
        )  # 已买的照样报，这一项算没做完（CodeRabbit #197）
    return "；".join(parts)
