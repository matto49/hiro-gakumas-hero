"""中断层：每一拍判页之前先过一遍，按优先级（去 KAA 设计稿 §3.4）。

每个中断是 fn(frame, rt) -> str | None：处理了返回一句说明（主循环下一拍重截），不是它管的返回 None；
要停机就抛 ResumeBlocked（维护 / 被踢 / 破弃框 / 认不出按钮的红线框），要回首页续跑就抛 DateChanged / CommErrorToTitle。
rt 是 hiro.runtime.loop.Runtime：rt.device（带危险点击拦截）、rt.screen、rt.sleep。

这里的几种都是带橙黄标题条的弹窗，先看像素（frame.has_dialog，约 20 ms），有弹窗才整帧 OCR；没弹窗的画面这一层不花 OCR 时间。
剧情 SKIP、横屏 LIVE、引导框等非弹窗的中断由页面层接（新主循环迁移时补进来）。
"""

from __future__ import annotations

from collections.abc import Callable

from hiro.loop import comm_error, date_change, skip_settings
from hiro.loop.errors import ResumeBlocked
from hiro.vision.ocr import screen_texts

# 维护进行中 / 别处登录被踢：用户 11-14 定直接停机，不重试、不等待。宁窄勿宽：登录后的「メンテナンスのお知らせ」（预告）
# 不能停，所以只认进行中的说法，或者标题就是「メンテナンス」的弹窗。还没有实机样本，碰到新说法再补（配实机帧当用例）。
STOP_WORDS = ("メンテナンス中", "メンテナンスを実施", "他の端末でログイン", "別の端末でログイン")


TAP_GAP_S = 1.5  # 同一个中断点了同一个框之后，这么久之内框还在就不再点（框淡出要时间：カスタマイズ確認 0.5 秒点了两次「決定」，h10 11-15）
CONFIRM_FADE_S = (
    8.0  # 点过「決定」之后这么久内框还在、按钮读不到，当成框在淡出（DMM 有时 3–4 秒才响应，qualifyz3 11-18）
)


def _recently(rt, key: str, gap: float = TAP_GAP_S) -> bool:
    last = getattr(rt, "_interrupt_tap", None)
    now = rt.clock() if callable(getattr(rt, "clock", None)) else 0.0
    return last is not None and last[0] == key and now - last[1] < gap


def _tapped(rt, key: str) -> None:
    rt._interrupt_tap = (key, rt.clock() if callable(getattr(rt, "clock", None)) else 0.0)


def _cancel(frame):
    return next((t for t in frame.texts if "キャンセル" in t.text), None)


def red_line(frame, rt) -> str | None:
    """危险弹窗（リタイア 确认、培育数据破棄）：只点キャンセル。破棄框点完停机（是否破弃由用户决定）；找不到キャンセル 停机。"""
    if not frame.has_dialog or (d := frame.dialog) is None or not d["danger"]:
        return None
    cancel = _cancel(frame)
    if cancel is None:
        raise ResumeBlocked(f"危险弹窗「{d['title']}」：没读到「キャンセル」，不点，停机")
    rt.device.tap(*cancel.rect.center)
    if d["kind"] == "data_discard":
        raise ResumeBlocked(
            f"游戏提示「{d['title']}」（其他端末有进行中的培育）：已点「キャンセル」，是否破弃由用户决定"
        )
    return f"危险弹窗「{d['title']}」：点キャンセル"


COMM_RETRY_MAX = 10  # 通信エラー 连着点リトライ 这么多次还在就停机报警（11-17 Mac：10 分钟点了 162 次，没上限）
COMM_RETRY_MAX_S = 300.0  # 或者从第一次点起这么久还在
COMM_RESET_S = 60.0  # 这么久没再见到通信エラー 才重新计数（两次之间是读取画面，不能每拍清零）


def _comm_frame(rt, f) -> str | None:
    try:
        return rt.session.trace.save_frame(rt.session.trace.next_seq(), f.img) if rt.session is not None else None
    except Exception:  # noqa: BLE001
        return None


def _comm_title_y(frame) -> int | None:
    """「通信エラー」字样在一条弹窗标题条上（字的那几行橙黄占比 ≥ HEADER_MIN_FRAC、字下方紧接白色正文框）时返回字的中线 y。"""
    from hiro.vision import pages

    import cv2

    for t in frame.texts:
        if not any(h in t.text.replace(" ", "") for h in comm_error.TITLE_HINTS):
            continue
        y1, y2 = int(t.rect.y1), int(t.rect.y2)
        band = frame.img[max(0, y1) : max(y1 + 1, y2), pages.HEADER_X[0] : pages.HEADER_X[1]]
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        frac = float(
            ((hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 30) & (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 190)).mean()
        )
        if frac >= pages.HEADER_MIN_FRAC and any(pages._white_body(frame.img, y) for y in range(y2, y2 + 40, 4)):
            return (y1 + y2) // 2
    return None


def comm(frame, rt) -> str | None:
    """通信エラー：只点リトライ；只能回标题时点閉じる并抛 CommErrorToTitle（hiro.loop.comm_error）。
    点之前确认弹窗确实在：标题条读出来是通信エラー、リトライ 在标题条下面，重截一帧还在同一个位置（11-17：リトライ 的位置 (299,1019)
    在首页上靠近「プロデュース中」）。连着点 COMM_RETRY_MAX 次 / COMM_RETRY_MAX_S 秒还在：存帧、停机报警。"""
    if not frame.has_dialog:
        return None
    e = comm_error.comm_error(frame.boxes)
    if e is None:
        return None
    d = frame.dialog or {}
    if d.get("kind") != "comm_error" and not any(h in (d.get("title") or "") for h in comm_error.TITLE_HINTS):
        # 加载画面整片橙黄时，按钮下方的橙色底也像一条「标题条」，排在真标题条下面被当成最上层弹窗（11-20 账号 2 LOADING 页，
        # E209 没点リトライ，180 s 停机）。这时直接核对「通信エラー」字样本身在不在一条标题条上：字所在的行是橙黄、正下方是白色正文框
        y = _comm_title_y(frame)
        if y is None:
            return None  # 画面上有「通信」字样，但不在弹窗标题条上：不点
        d = {"kind": "comm_error", "y": y}
    if e["kind"] == "retry" and d.get("y") is not None and e["retry"][1] <= d["y"]:
        return None  # 「リトライ」不在这个弹窗里（在标题条上面）
    f2 = rt.screen.shot()  # 重截核对：弹窗还在、按钮没挪
    e2 = comm_error.comm_error(f2.boxes) if f2.has_dialog else None
    if (
        e2 is None
        or e2["kind"] != e["kind"]
        or (e["retry"] and (e2["retry"] is None or abs(e2["retry"][1] - e["retry"][1]) > 20))
    ):
        return "通信エラー：重截时弹窗不在了 / 变了，这一拍不点"
    if e2["kind"] == "retry":
        now = rt.clock() if callable(getattr(rt, "clock", None)) else 0.0
        st = getattr(rt, "_comm_retry", None)  # (第一次, 最近一次, 次数)
        if st is not None and now - st[1] > COMM_RESET_S:
            st = None
        n = (st[2] if st else 0) + 1
        first = st[0] if st else now
        if n > COMM_RETRY_MAX or now - first > COMM_RETRY_MAX_S:
            shot = _comm_frame(rt, f2)
            raise ResumeBlocked(f"通信エラー 点了 {n - 1} 次リトライ（{now - first:.0f}s）还在，停机（帧 {shot}）")
        rt._comm_retry = (first, now, n)
    r = comm_error.handle(f2.boxes, rt.device.tap, sleep=rt.sleep)
    return f"通信エラー：{r}" if r else None


def stop_notice(frame, rt) -> str | None:
    """维护 / 被踢下线：停机，写清原因。"""
    if not frame.has_dialog:
        return None
    title = (frame.dialog or {}).get("title") or ""
    hit = next((t.text for t in frame.texts if any(w in t.text for w in STOP_WORDS)), None)
    if hit is None and title.startswith("メンテナンス") and "お知らせ" not in title:
        hit = title
    if hit is not None:
        raise ResumeBlocked(f"游戏维护 / 被踢下线：「{hit}」，停机（不重试）")
    return None


def date_changed(frame, rt) -> str | None:
    """日期切换 / 数据更新（日本时间凌晨 5 点）：抛 DateChanged，run_hif 回首页从「プロデュース中」续跑。"""
    if frame.has_dialog and date_change.date_change_button(frame.boxes) is not None:
        raise date_change.DateChanged("日付変更")
    return None


def settings(frame, rt) -> str | None:
    """演出スキップ / 早送り / ボイス 等设置框：按用户设置勾选后点決定（hiro.loop.skip_settings）。"""
    if not frame.has_dialog:
        return None

    def ocr(img):
        return [(t.text, t.conf, tuple(t.rect)) for t in screen_texts(img)]

    r = skip_settings.handle(
        frame.boxes, shoot=lambda: rt.screen.shot().img, ocr=ocr, click=rt.device.tap, sleep=rt.sleep
    )
    return f"设置框 {r['kind']}" if r else None


def customize_confirm(frame, rt) -> str | None:
    """P 道具定制三选一选完、点了「受け取る」后游戏弹的「カスタマイズ確認」（「以下のPアイテムにカスタマイズを実行します」）：点右下「決定」。
    选哪个已经由 SELECT_PITEM 的决策定了，这里只是确认（旧主循环由 KAA 自动确认；框上勾了「次回以降表示しない」就不再弹）。
    只认 P 道具定制（正文带「Pアイテム」），特別指導的「カスタマイズ実行確認」由它自己的执行器处理。读不到「決定」停机。"""
    if not frame.has_dialog or (d := frame.dialog) is None or d["kind"] != "customize_confirm":
        return None
    if not any(
        "pアイテム" in t.text.lower() for t in frame.texts
    ):  # RapidOCR 小窗口会读成小写「pアイテム」（qualifyz3 11-18）
        return None
    if _recently(rt, "customize_confirm"):
        return f"P 道具定制确认「{d['title']}」：刚点过「決定」，等框关"
    btn = next(
        (t for t in frame.texts if t.text.strip() == "決定" and t.rect.center.x > 360 and t.rect.center.y > 1000), None
    )
    if btn is None:
        if _recently(rt, "customize_confirm", CONFIRM_FADE_S):
            return f"P 道具定制确认「{d['title']}」：点过「決定」，按钮已读不到，等框关"
        raise ResumeBlocked(f"P 道具定制确认框「{d['title']}」：没读到「決定」，不点，停机")
    rt.device.tap(*btn.rect.center)
    _tapped(rt, "customize_confirm")
    return f"P 道具定制确认「{d['title']}」：点「決定」"


# 教程框（新账号 / 新赛季第一次进 H.I.F 弹，「H.I.F開幕!」「3回の選抜試験を突破しよう」「スター性を獲得しよう」…）：
# 白底大卡片（x 27–693）+ 卡片下面一排页码点（当前页是橙色菱形，y≈1068）+ 底部正中只有一个按钮「次へ」/「閉じる」（y≈1160）。
# 没有橙黄标题条，走不到 has_dialog：先看像素（左右两条白边、页码点那一带白底里有少量橙色），对上了才 OCR 底部按钮。
TUTORIAL_EDGE_WHITE = (
    0.98  # 卡片左右白边（x 32–60 / 662–688，y 160–1100）白色比例：教程框 0.995–1.0；声音设置页 0.976、其他全屏页更低
)
TUTORIAL_DOT_ORANGE = (30, 200)  # 页码点那一带（y 1040–1100）的橙色像素：教程框 81–84；声音设置页的橙色滑条 379
TUTORIAL_BAND_WHITE = 0.97  # 同一带的白色比例：教程框 0.993；声音设置页 0.929
TUTORIAL_BUTTONS = ("次へ", "閉じる")


def _tutorial_card(img) -> bool:
    import cv2
    import numpy as np

    if img is None or img.shape[:2] != (1280, 720):
        return False
    hsv = cv2.cvtColor(img[150:1110], cv2.COLOR_BGR2HSV)

    def white(x1, x2, y1, y2):
        b = hsv[y1 - 150 : y2 - 150, x1:x2]
        return float(((b[..., 1] < 25) & (b[..., 2] > 235)).mean())

    if white(32, 60, 160, 1100) < TUTORIAL_EDGE_WHITE or white(662, 688, 160, 1100) < TUTORIAL_EDGE_WHITE:
        return False
    band = hsv[1040 - 150 : 1100 - 150, 70:650]
    orange = int(
        np.count_nonzero((band[..., 0] >= 5) & (band[..., 0] <= 22) & (band[..., 1] > 120) & (band[..., 2] > 200))
    )
    return (
        TUTORIAL_DOT_ORANGE[0] <= orange <= TUTORIAL_DOT_ORANGE[1] and white(70, 650, 1030, 1100) >= TUTORIAL_BAND_WHITE
    )


def tutorial(frame, rt) -> str | None:
    """教程框：点底部唯一的按钮（次へ / 閉じる）。底部那一带读到的不是正好一个、或者不是这两个字，不点（交给判页 / 看门狗）。"""
    import re

    if not _tutorial_card(frame.img):
        return None
    # 按钮左边的「→」「×」图标会被读成单独的一个框：只数带假名 / 汉字的
    bottom = [
        (w, t)
        for t in frame.texts
        if 1120 <= t.rect.center.y <= 1200 and (w := re.sub(r"[^\u3040-\u30ff\u4e00-\u9fff]", "", t.text))
    ]
    if len(bottom) != 1 or bottom[0][0] not in TUTORIAL_BUTTONS:
        return None
    word, btn = bottom[0]
    if _recently(rt, f"tutorial:{word}"):
        return f"教程框：刚点过「{word}」，等换页"
    rt.device.tap(*btn.rect.center)
    _tapped(rt, f"tutorial:{word}")
    return f"教程框：点「{word}」"


def reroll_confirm(frame, rt) -> str | None:
    """「スキルカード再抽選確認」开着（选卡处理器点了再抽選后进程停了、续跑时框已经在；或在页面之间撞上）：点「再抽選する」；
    框上有ジュエル等消耗字样点キャンセル，读不到キャンセル 停机。「次回以降表示しない」勾着也每次刷新都弹（协调 11-14）。"""
    from hiro.produce.ops import reroll_confirm_action

    if not frame.has_dialog or "再抽選確認" not in ((frame.dialog or {}).get("title") or ""):
        return None
    act = reroll_confirm_action(frame.texts)
    if act is None:
        return None
    kind, hit, cost = act
    if _recently(rt, "reroll_confirm"):
        return "再抽選确认框：刚点过，等框关"
    if kind == "cost":
        if hit is None:
            raise ResumeBlocked(f"再抽選确认框上有消耗字样「{cost}」，又没读到キャンセル：不点，停机")
        rt.device.tap(*hit.rect.center)
        _tapped(rt, "reroll_confirm")
        return f"再抽選确认框有消耗字样「{cost}」：点キャンセル"
    rt.device.tap(*hit.rect.center)
    _tapped(rt, "reroll_confirm")
    return f"再抽選确认框：点「{hit.text}」"


# 成就达成提示条（「手札の多さが武器 / 花海 咲季のPアイドルを合計5種類入手 ✓」）：白色圆角横条盖在画面顶部（y 36–150），
# 开局第 1 步的页头被它挡住认不出（Mac 账号 2 11-16）。几秒后自己消失：不点（点了可能跳到成就页），这一拍当「处理过」等它走。
# 判据：横条上下两段白（x 120–580，y 40–56 / 124–146 白色比例 ≥ 0.9）+ 右边蓝色对勾（x 590–665，y 60–130 蓝像素 ≥ 150）。
# fixtures + Mac 实机帧 699 张里只有两张成就帧命中（517 个蓝像素）。
TOAST_MAX_S = 20.0  # 一直在超过这么久就不再当中断（交回判页 / 看门狗，防真卡住时死等）


def _achievement_toast(img) -> bool:
    import cv2
    import numpy as np

    if img is None or img.shape[:2] != (1280, 720):
        return False
    hsv = cv2.cvtColor(img[:170], cv2.COLOR_BGR2HSV)

    def white(y1, y2):
        b = hsv[y1:y2, 120:580]
        return float(((b[..., 1] < 25) & (b[..., 2] > 235)).mean())

    if white(40, 56) < 0.9 or white(124, 146) < 0.9:
        return False
    chk = hsv[60:130, 590:665]
    return (
        int(np.count_nonzero((chk[..., 0] >= 95) & (chk[..., 0] <= 118) & (chk[..., 1] >= 120) & (chk[..., 2] >= 180)))
        >= 150
    )


def achievement_toast(frame, rt) -> str | None:
    """成就达成提示条盖住页头：不点，等它自己消失（连续 TOAST_MAX_S 秒还在就不再拦）。"""
    now = rt.clock() if callable(getattr(rt, "clock", None)) else 0.0
    if not _achievement_toast(frame.img):
        rt._toast_since = None
        return None
    since = getattr(rt, "_toast_since", None)
    if since is None:
        rt._toast_since = since = now
    if now - since >= TOAST_MAX_S:
        return None
    return "成就提示条盖住页头：等它消失"


# 主页通知（Mac 账号 2 11-17：重启、重新数据连携后续跑，主页一连弹 5 个「親愛度STEP3解放」，每个偶像一个，后面跟「使用期限確認」；
# 判页认不出 → 续跑当成「已在培育内」，182 秒停机）。
# 親愛度解放：全屏模糊背景 + 底部白色圆形 ×（中心 (360,1200)），没有橙黄标题条。先看 × 的像素（圆环白、中心有深色笔画，约 1 ms），
# 再 OCR 确认「親愛度」「解放」——日程表浮层底部也有同样的 ×（fixtures 610 张里只有它和这一张过像素判据）。
AFFECTION_CLOSE = (360, 1200)
USE_LIMIT_CANCEL = (212, 1161)  # 「使用期限確認」只有左下一个「キャンセル」


def _close_button(img) -> bool:
    import cv2
    import numpy as np

    if img is None or img.shape[:2] != (1280, 720):
        return False
    cx, cy = AFFECTION_CLOSE
    yy, xx = np.mgrid[cy - 40 : cy + 40, cx - 40 : cx + 40]
    d = np.hypot(xx - cx, yy - cy)
    hsv = cv2.cvtColor(img[cy - 40 : cy + 40, cx - 40 : cx + 40], cv2.COLOR_BGR2HSV)
    white = (hsv[..., 1] < 30) & (hsv[..., 2] > 230)
    dark = hsv[..., 2] < 110
    return float(white[(d > 22) & (d < 34)].mean()) >= 0.8 and float(dark[d < 16].mean()) >= 0.1


def affection_unlock(frame, rt) -> str | None:
    """主页「親愛度STEPn解放」通知：点底部 ×。"""
    if not _close_button(frame.img):
        return None
    texts = [t.text.replace(" ", "") for t in frame.texts]
    if not (any("親愛度" in t for t in texts) and any("解放" in t for t in texts)):
        return None
    if _recently(rt, "affection_unlock"):
        return "親愛度解放通知：刚点过 ×，等它关掉"
    rt.device.tap(*AFFECTION_CLOSE)
    _tapped(rt, "affection_unlock")
    return "親愛度解放通知：点 ×"


def use_limit(frame, rt) -> str | None:
    """主页「使用期限確認」（使用期限の近い持ち物があります）：点「キャンセル」（只是提示，不去持ち物页）。"""
    if not frame.has_dialog:
        return None
    title = (frame.dialog or {}).get("title") or ""
    if "使用期限" not in title and not any("使用期限の近い" in t.text.replace(" ", "") for t in frame.texts):
        return None
    if _recently(rt, "use_limit"):
        return "使用期限確認：刚点过キャンセル，等它关掉"
    cancel = _cancel(frame)
    rt.device.tap(*(cancel.rect.center if cancel is not None else USE_LIMIT_CANCEL))
    _tapped(rt, "use_limit")
    return "使用期限確認：点キャンセル"


LIVE_SETTING_DECIDE = (500, 1160)  # 「…ライブ設定」框右下「決定」（读不到字时）


def live_setting(frame, rt) -> str | None:
    """N.I.A 第一次进第 2 步时弹的「NEXT IDOL AUDITIONライブ設定」（LIVE 演出用 Campus mode!! 还是 Pアイドル楽曲）：
    默认选项不改，点「決定」（协调 11-17：原来没处理，「第 1 步点次へ」20 秒超时停机）。"""
    if not frame.has_dialog or "ライブ設定" not in ((frame.dialog or {}).get("title") or ""):
        return None
    if _recently(rt, "live_setting"):
        return "ライブ設定：刚点过「決定」，等框关"
    btn = next(
        (t for t in frame.texts if t.text.strip() == "決定" and t.rect.center.x > 360 and t.rect.center.y > 1000), None
    )
    rt.device.tap(*(btn.rect.center if btn is not None else LIVE_SETTING_DECIDE))
    _tapped(rt, "live_setting")
    return "ライブ設定：默认选项，点「決定」"


CONSULT_END_DECIDE = (500, 1160)


def consult_end_confirm(frame, rt) -> str | None:
    """「相談終了確認」（まだ交換可能です。相談を終了しますか？）单独留在画面上（N.I.A 采样 11-17：人手点了「終了」后停在这里，
    DIALOG 没有处理器）：已经按了「終了」，点「決定」结束相談。相談处理器自己结束时会在执行器里点掉，不走到这里。"""
    if not frame.has_dialog or (frame.dialog or {}).get("kind") != "consult_end":
        return None
    if _recently(rt, "consult_end"):
        return "相談終了確認：刚点过「決定」，等框关"
    btn = next(
        (t for t in frame.texts if t.text.strip() == "決定" and t.rect.center.x > 360 and t.rect.center.y > 1000), None
    )
    rt.device.tap(*(btn.rect.center if btn is not None else CONSULT_END_DECIDE))
    _tapped(rt, "consult_end")
    return "相談終了確認：点「決定」"


INTERVAL_END_GO = (500, 1160)


def interval_end_confirm(frame, rt) -> str | None:
    """「インターバル終了確認」（使用できるPポイントが残っています。インターバルを終了して本戦へ進みますか？）留在画面上
    （mainz4 11-18 14:08：轮间还剩 10P，点「終了」后弹出，执行器在等轮间主画面、主循环判 DIALOG 没有处理器，247 秒停机）。
    这个框只在我们自己点了「終了」后出现（轮间决策已定结束），点「本戦へ」。"""
    if not frame.has_dialog or (frame.dialog or {}).get("kind") != "interval_end":
        return None
    if _recently(rt, "interval_end"):
        return "インターバル終了確認：刚点过「本戦へ」，等框关"
    btn = next(
        (t for t in frame.texts if "本戦へ" in t.text and t.rect.center.x > 360 and t.rect.center.y > 1000), None
    )
    rt.device.tap(*(btn.rect.center if btn is not None else INTERVAL_END_GO))
    _tapped(rt, "interval_end")
    return "インターバル終了確認：点「本戦へ」"


DRINK_DETAIL_CANCEL = (
    237,
    1162,
)  # 「Pドリンク詳細」左下「キャンセル」（实拍 tests/fixtures/pages/pdrink-detail-action-select.png）


def drink_detail(frame, rt) -> str | None:
    """「Pドリンク詳細」（捨てる / キャンセル / 使う）留在画面上：只点「キャンセル」，绝不点「捨てる」「使う」（协调 11-18 初 MASTER 20:31：
    剧情 SKIP 的第二下落在选行动页饮料栏上点开了它，盖住选行动页停机）。考试里用饮料由考试执行器自己开关这个框，不经过主循环中断层。
    「キャンセル」读不到时按实拍位置点（左下；「捨てる」在右上、「使う」在右下）。"""
    if not frame.has_dialog:
        return None
    title = (frame.dialog or {}).get("title") or ""
    if "Pドリンク詳細" not in title.replace(" ", "") and not any(
        "Pドリンク詳細" in t.text.replace(" ", "") for t in frame.texts
    ):
        return None
    if _recently(rt, "drink_detail"):
        return "Pドリンク詳細：刚点过キャンセル，等框关"
    cancel = _cancel(frame)
    pos = cancel.rect.center if cancel is not None and cancel.rect.center.x < 360 else DRINK_DETAIL_CANCEL
    rt.device.tap(*pos)
    _tapped(rt, "drink_detail")
    return "Pドリンク詳細：点キャンセル（不丢、不用）"


REST_CONFIRM_REST, REST_CONFIRM_CANCEL = (498, 1159), (236, 1161)


def rest_confirm(frame, rt) -> str | None:
    """「休み確認：休んで体力を回復させますか？」（キャンセル / 休む）单独留在画面上（初 Mac 11-17：续跑时停在这一框，DIALOG 没有处理器，
    181 秒停）。选行动的执行器（action_select._rest）自己会点掉，走到这里说明决策后的确认没在执行器里完成：
    session 记的最近一次决策是选行动「休息」就点「休む」，否则（续跑后不知道、或者选的不是休息）点「キャンセル」回选行动页重新决策。"""
    if not frame.has_dialog or "休み確認" not in str((frame.dialog or {}).get("title") or ""):
        return None
    if _recently(rt, "rest_confirm"):
        return "休み確認：刚点过，等框关"
    last = getattr(getattr(rt, "session", None), "last_decision", None)
    rest = bool(last) and last[0] == "ACTION_SELECT" and (last[1].action.args or {}).get("option") == "rest"
    word, fallback = ("休む", REST_CONFIRM_REST) if rest else ("キャンセル", REST_CONFIRM_CANCEL)
    btn = next((t for t in frame.texts if t.text.replace(" ", "").strip() == word and t.rect.center.y > 1000), None)
    rt.device.tap(*(btn.rect.center if btn is not None else fallback))
    _tapped(rt, "rest_confirm")
    return f"休み確認：{'刚才决策是休息，点「休む」' if rest else '最近的决策不是休息（或续跑后不知道），点「キャンセル」回选行动页重新决策'}"


LESSON_CONFIRM_START, LESSON_CONFIRM_CANCEL = (515, 1160), (236, 1161)


# 决策后游戏再弹的确认框（标题 → 哪些选行动决策算「已经决定了」）：最近一次决策是其中之一就点「開始する」（执行决策器已经做出的选择），
# 否则（含续跑不知道之前选了什么）点「キャンセル」回选行动页重新决策。协调 + 3940a9 11-17 定，不加开关。
DECISION_CONFIRMS = {
    "レッスン開始確認": ("training", "class"),  # 体力が低下しています（初 Mac 11-17 frames/00360）
}
# 一律点「キャンセル」、回选行动页时告诉决策器这一周别再选它（page_fields.declined_options，3940a9 07f1d30 已接）：
# 相談開始確認（Pポイントの所持数が少ないです）说明按游戏实际价 P 不够，进去也只能直接离开，这一周白费（3940a9 11-18，初第二局第 4 周）
DECLINE_CONFIRMS = {"相談開始確認": "shop"}


def _decline(rt, option: str) -> None:
    """记进局面 produce.declined_options = {options, step}：reduce 在同一周的选行动页放进 page_fields，换周丢掉；选行动执行完清掉。"""
    session = getattr(rt, "session", None)
    state = getattr(session, "state", None)
    if state is None:
        return
    prod = dict(state.produce or {})
    cur = prod.get("declined_options") or {}
    opts = sorted({*(cur.get("options") or []), option}) if cur.get("step") == prod.get("step") else [option]
    prod["declined_options"] = {"options": opts, "step": prod.get("step")}
    state.produce = prod


def decision_confirm(frame, rt) -> str | None:
    """「レッスン開始確認」「相談開始確認」这类确认框（キャンセル / 開始する）。只认 DECISION_CONFIRMS / DECLINE_CONFIRMS 里的标题；
    「開始する」不会点在破棄框上（破棄框由 red_line 先处理，标题也不在表里）。"""
    if not frame.has_dialog:
        return None
    title = str((frame.dialog or {}).get("title") or "")
    decline = next(((t, o) for t, o in DECLINE_CONFIRMS.items() if t in title), None)
    if decline is not None:
        if _recently(rt, "decision_confirm"):
            return f"{decline[0]}：刚点过，等框关"
        btn = next((t for t in frame.texts if "キャンセル" in t.text and t.rect.center.y > 1000), None)
        rt.device.tap(*(btn.rect.center if btn is not None else LESSON_CONFIRM_CANCEL))
        _tapped(rt, "decision_confirm")
        _decline(rt, decline[1])
        return f"{decline[0]}：点「キャンセル」，这一周不再选 {decline[1]}（declined_options）"
    hit = next(((t, opts) for t, opts in DECISION_CONFIRMS.items() if t in title), None)
    if hit is None:
        return None
    name, opts = hit
    if _recently(rt, "decision_confirm"):
        return f"{name}：刚点过，等框关"
    last = getattr(getattr(rt, "session", None), "last_decision", None)
    go = bool(last) and last[0] == "ACTION_SELECT" and (last[1].action.args or {}).get("option") in opts
    if go:
        btn = next(
            (t for t in frame.texts if t.text.replace(" ", "").strip() == "開始する" and t.rect.center.y > 1000), None
        )
        pos = btn.rect.center if btn is not None else LESSON_CONFIRM_START
    else:
        btn = next((t for t in frame.texts if "キャンセル" in t.text and t.rect.center.y > 1000), None)
        pos = btn.rect.center if btn is not None else LESSON_CONFIRM_CANCEL
    rt.device.tap(*pos)
    _tapped(rt, "decision_confirm")
    what = "/".join(opts)
    return f"{name}：{f'刚才决策是 {what}，点「開始する」' if go else f'最近的决策不是 {what}（或续跑后不知道），点「キャンセル」回选行动页重新决策'}"


lesson_lowhp_confirm = decision_confirm  # 旧名（ea62356）


TAP_NEXT = "TAPで次へ"
TAP_NEXT_BLOCK = ("キャンセル", "購入", "ジュエル", "破棄", "削除")


def tap_next_dialog(frame, rt) -> str | None:
    """弹窗样式的展示页、底部只有「TAPで次へ」（初结算的 True End 条件页：1 位 + 三个アチーブメント + 「3つのアチーブメントを獲得した
    状態で試験1位」，协调 11-17：判成 DIALOG 没处理器，182 秒停）：点「TAPで次へ」。有キャンセル / 購入 / ジュエル 等字样的框不点。"""
    if not frame.has_dialog:
        return None
    texts = [t.text.replace(" ", "") for t in frame.texts]
    if any(w in t for t in texts for w in TAP_NEXT_BLOCK):
        return None
    btn = next((t for t in frame.texts if TAP_NEXT in t.text.replace(" ", "") and t.rect.center.y > 1000), None)
    if btn is None:
        return None
    if _recently(rt, "tap_next"):
        return "「TAPで次へ」：刚点过，等翻页"
    rt.device.tap(*btn.rect.center)
    _tapped(rt, "tap_next")
    return f"展示页「{(frame.dialog or {}).get('title') or ''}」：点「TAPで次へ」"


LIVE_RESUME = "画面タップで再開"


def live_resume(frame, rt) -> str | None:
    """LIVE 暂停（「画面タップで再開」，初 True End LIVE 在全屏里暂停、退出全屏后竖屏带黑边显示，协调 11-17）：点这行字继续。
    先看版面（竖屏、上下两段黑、中间一条横向画面）再 OCR，平常的帧不多花整屏 OCR。"""
    img = frame.img
    if img is None or img.shape[0] <= img.shape[1]:
        return None
    h = img.shape[0]
    if (
        float(img[: h * 3 // 10].mean()) > 5
        or float(img[h * 7 // 10 :].mean()) > 5
        or float(img[h * 4 // 10 : h * 6 // 10].mean()) < 8
    ):
        return None
    btn = next((t for t in frame.texts if LIVE_RESUME in t.text.replace(" ", "")), None)
    if btn is None:
        return None
    if _recently(rt, "live_resume"):
        return "LIVE 暂停：刚点过「画面タップで再開」，等继续"
    rt.device.tap(*btn.rect.center)
    _tapped(rt, "live_resume")
    return "LIVE 暂停：点「画面タップで再開」"


Interrupt = Callable[[object, object], "str | None"]
DEFAULT: tuple[Interrupt, ...] = (
    red_line,
    comm,
    stop_notice,
    date_changed,
    settings,
    customize_confirm,
    tutorial,
    reroll_confirm,
    achievement_toast,
    affection_unlock,
    use_limit,
    live_setting,
    consult_end_confirm,
    interval_end_confirm,
    drink_detail,
    rest_confirm,
    decision_confirm,
    tap_next_dialog,
    live_resume,
)


def check(frame, rt, interrupts: tuple[Interrupt, ...] = DEFAULT) -> str | None:
    """按顺序过中断，第一个处理了的返回它的说明；都不是返回 None。"""
    for fn in interrupts:
        if (done := fn(frame, rt)) is not None:
            return done
    return None
