"""新主循环里不带弹窗标题条的场景（设计稿 §2.2、§3.4 的「非弹窗中断」）：剧情逐句播放点 SKIP、横屏 LIVE 曲目页点开始、
重新登录后的一次性设置框。判据和点法照搬旧主循环 hiro.loop.controller（_skip_commu、_live_tick、_first_run_settings），
不依赖 KAA。都只点 OCR 认出来的字 / 固定的安全位置，点之前重新截图核对。

    handled = commu_skip(rt, frame) or live_start(rt, frame) or first_run_settings(rt, frame)
"""

from __future__ import annotations

import logging

from hiro.vision.ocr import screen_texts

logger = logging.getLogger(__name__)

LIVE_START_TEXT = "TAPTOSTART"
LIVE_INFO_TEXTS = ("作詞", "作曲", "編曲")
# 一次性设置框（标题, 要选的项, 确认按钮）：q13 重新登录后「サポートイベント設定」；q14 剧情点 SKIP 后「スキップ確認」
FIRST_RUN_SETTINGS = (("サポートイベント設定", "強化済みで獲得する", "決定"), ("スキップ確認", None, "決定"))


TAP_NEXT = "TAPで次へ"
TAP_NEXT_MIN_Y = 1000  # 只认画面底部那行提示
TAP_NEXT_GAP_S = 1.5  # 点过这么久之内不再点（翻页要时间，第二下会落到下一页上）


def tap_next(rt, frame) -> bool:
    """画面底部有「TAPで次へ」、别的字里没有按钮 / 购买 / 危险字样（hiro.loop.advance.BLOCK_WORDS，「次へ」本身除外）：点这行字推进
    （协调 11-18 初 MASTER 最終后的「1位」证书页：白色证书框、没有弹窗标题条、后面压着暗掉的剧情底栏，判 unknown；
    连点被 BLOCK_WORDS 里的「次へ」拦住，180 秒停机）。点之前重新截图核对这行字还在。"""
    from hiro.loop.advance import BLOCK_WORDS

    btn = next(
        (t for t in frame.texts if TAP_NEXT in t.text.replace(" ", "") and t.rect.center.y > TAP_NEXT_MIN_Y), None
    )
    if btn is None:
        return False
    others = [t.text.replace(" ", "") for t in frame.texts if t is not btn]
    if (w := next((w for w in BLOCK_WORDS if w != "次へ" and any(w in o for o in others)), None)) is not None:
        logger.info("「TAPで次へ」页上有「%s」：不点", w)
        return False
    now = rt.clock() if callable(getattr(rt, "clock", None)) else None
    last = getattr(rt, "_tap_next_at", None)
    if now is not None and last is not None and now - last < TAP_NEXT_GAP_S:
        return True  # 刚点过，等翻页（算处理了，不交给后面的连点）
    if not any(
        TAP_NEXT in b.text.replace(" ", "") and b.rect.center.y > TAP_NEXT_MIN_Y
        for b in screen_texts(rt.screen.shot().img)
    ):
        return False
    rt.device.tap(*btn.rect.center)
    rt._tap_next_at = now
    logger.info("底部「TAPで次へ」：点 %s", tuple(btn.rect.center))
    return True


def live_start_tap(img, texts) -> tuple[int, int] | None:
    """横屏曲目页：认到「TAP TO START」点它；只认到曲目信息（作詞 / 作曲 / 編曲）点下方中间；其余 None。texts 是 ocr.Text 列表。"""
    for t in texts:
        if LIVE_START_TEXT in t.text.replace(" ", "").upper():
            return t.rect.center
    if any(k in t.text for t in texts for k in LIVE_INFO_TEXTS):
        h, w = img.shape[:2]
        return w // 2, int(h * 0.85)
    return None


def live_start(rt, frame) -> bool:
    """横屏（LIVE）帧：曲目页就点开始；其余横屏帧不点（演出中）。返回是否点了。"""
    img = frame.img
    if img is None or img.shape[1] <= img.shape[0]:
        return False
    from hiro.runtime import live_mode

    def items(f) -> list:
        return [(t.text, tuple(t.rect.center)) for t in f.texts]

    def shoot():
        f = rt.screen.shot()
        return f.img.shape[1], f.img.shape[0], items(f)

    # 曲目页点开始 / HIRO_LIVE=watch 开始前停下等人 / pick·auto 点 SKIP（hiro.runtime.live_mode，用户 11-21）
    done = live_mode.driver().step(img.shape[1], img.shape[0], items(frame), lambda x, y: rt.device.tap(x, y), shoot)
    return done is not None and done != "watched"


def commu_skip(rt, frame) -> bool:
    """培育剧情逐句播放（底栏有「早送り」+ ログ / AUTO / SKIP）：点认出来的 SKIP（左下、y > 1120）。点之前重新截图核对。"""
    texts = [t.text for t in frame.texts]
    if not any("早送" in t for t in texts) or not any(
        "ログ" in t or "AUT" in t.upper() or "SKI" in t.upper() for t in texts
    ):
        return False
    fresh = screen_texts(rt.screen.shot().img)
    if not any("早送" in b.text for b in fresh):
        return False
    btn = next((b for b in fresh if "SKI" in b.text.upper() and b.rect.center.y > 1120 and b.rect.center.x < 360), None)
    if btn is None:
        return False
    rt.device.tap(*btn.rect.center)
    logger.info("培育剧情：点 SKIP")
    return True


def unread_confirm(rt) -> bool:
    """剧情点 SKIP 后弹的「スキップ確認：未読のコミュです」（HIRO_UNREAD_STORY=stop）：交给 unread_story.handle——
    点キャンセル、停下等人（看完自动继续 / 删等人文件 = 跳过 / 剧情框文字 60 秒没变就关游戏，抛 UnreadStoryTimeout 停机）。
    SKIP 按钮本身照常点（scenes.commu_skip）：读过的剧情不弹这个框，直接跳。"""
    from hiro.loop.skip_settings import is_ok_text
    from hiro.runtime import unread_story

    def tap(word: str):
        def go():
            boxes = screen_texts(rt.screen.shot().img)
            btn = next((b for b in boxes if b.text.strip() == word or (word == "決定" and is_ok_text(b.text))), None)
            if btn is None:
                raise RuntimeError(f"未读剧情确认框上没找到「{word}」")
            rt.device.tap(*btn.rect.center)
            rt.sleep(0.8)

        return go

    got = unread_story.handle(cancel=tap("キャンセル"), confirm=tap("決定"), texts=lambda: unread_story.read_screen(rt.screen.shot().img),
                              snap=lambda: rt.screen.shot().img, sleep=rt.sleep)  # fmt: skip
    logger.warning("未读剧情确认框：%s", got)
    if rt.session is not None:
        rt.session.recover("dialog", f"未读剧情：{got}")
    return True


def first_run_settings(rt, frame) -> bool:
    """重新登录后游戏重置本地设置弹的一次性设置框：选推荐项（有的话）→ 点「決定」。"""
    from hiro.loop.skip_settings import is_ok_text

    texts = [t.text for t in frame.texts]
    hit = next((x for x in FIRST_RUN_SETTINGS if any(x[0] in t for t in texts)), None)
    if hit is None:
        return False
    from hiro.runtime import unread_story

    if hit[0] == "スキップ確認" and unread_story.stopping():
        return unread_confirm(rt)  # HIRO_UNREAD_STORY=stop：未读剧情的确认框不点決定，停下等人（review #13）
    boxes = screen_texts(rt.screen.shot().img)
    opt = next((b for b in boxes if hit[1] in b.text), None) if hit[1] else None
    if opt is not None:
        rt.device.tap(*opt.rect.center)
        rt.sleep(0.5)
    btn = next((b for b in boxes if b.text.strip() == hit[2] or (hit[2] == "決定" and is_ok_text(b.text))), None)
    if btn is None:
        return False
    rt.device.tap(*btn.rect.center)
    logger.warning("一次性设置「%s」：选「%s」→「%s」", hit[0], hit[1], hit[2])
    if rt.session is not None:
        rt.session.recover("dialog", f"一次性设置「{hit[0]}」选「{hit[1]}」")
    return True
