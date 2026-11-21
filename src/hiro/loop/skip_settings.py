"""开局的一次性设置框（データ連携 / 重新登录后游戏本地设置被重置，本战点了开始后依次弹出）：按用户既定设置选好再点「決定」。

- 「【プロデュース】演出スキップ設定」（第二十三局）：四个开关全 OFF → 都打开（用户 2026-11-02，与 tools/check_settings.py 同一组）；
- 「コミュ早送り設定」（第二十五局）：选「コミュを早送りする」，「未読コミュでも早送りする」开关打开（用户既定设置）；
- 「ボイス再生確認」（第二十五局）：保持「ボイスありで再生」，点「決定」；
- 「獲得ガイド表示設定」（新账号第一次拿技能卡，选卡三选一中途弹）：选「獲得ガイドを表示しない」，点「決定」。
只动这些选项 / 开关和「決定」，「次回以降表示しない」勾选框保持原样。开关点了几轮还有 OFF：照样点「決定」放行
（只影响剧情速度），记 WARNING。主循环（HiroController._skip_settings_dialog）和考试循环（battle._confirm_dialog）共用。

输入是 OCR 结果：[(text, conf, (x1, y1, x2, y2))]（hiro.produce.ops.ocr_boxes 格式，720×1280 整帧坐标）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

import logging

logger = logging.getLogger(__name__)

# 演出スキップ設定 四个开关的标题行（任一行文字命中即可；开关和标题第一 / 二行同一高度 ±SWITCH_DY）
ITEMS = {
    "support_commu": ("サポートイベントの", "コミュスキップ"),
    "start_commu": ("各プロデュース開始時の", "説明コミュスキップ"),
    "hajime_commu": ("気になる", "様子」コミュスキップ"),
    "affection_movie": ("親愛度コミュ内の", "映像スキップ"),
}
# 标题 → {toggles: 要打开的开关, option: 要选的单选项（(要含的字, 不能含的字)）}。标题按顺序匹配
DIALOGS = {
    "演出スキップ設定": {"toggles": ITEMS, "option": None},
    "早送り設定": {"toggles": {"unread_ff": ("未読コミュでも早送り",)}, "option": ("早送りする", ("未読", "しない"))},
    "ボイス再生確認": {"toggles": {}, "option": ("ボイスありで再生", ("なし",))},
    # 第一次拿到技能卡时（选卡三选一中途）弹：「獲得ガイドを表示する / 表示しない」单选 + 決定。选「表示しない」（协调 11-14，Mac 账号 2）
    "獲得ガイド表示設定": {"toggles": {}, "option": ("獲得ガイドを表示しない", ())},
}
TITLE = "演出スキップ設定"  # 兼容旧调用
SWITCH_X = 480  # 开关在右侧（720 宽：x ≈ 580–640）
SWITCH_DY = 60
ROUNDS = 3


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def _center(b) -> tuple[int, int]:
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


OK_TEXT = "決定"
OK_ICON_JUNK = "く<〈✓✔√レ"  # 「決定」按钮左边的对勾图标常被 OCR 读成一个字符（第三十三局 ボイス再生確認：「く決定」）


def is_ok_text(t: str) -> bool:
    """「決定」按钮的 OCR 文字：「決定」，或前面多一个对勾图标读出来的字符。"""
    n = _norm(t)
    return n == OK_TEXT or (len(n) == len(OK_TEXT) + 1 and n.endswith(OK_TEXT) and n[0] in OK_ICON_JUNK)


def kind_of(texts) -> str | None:
    """是哪个设置框（DIALOGS 的标题键），不是返回 None。"""
    flat = [_norm(t) for t in texts]
    return next((k for k in DIALOGS if any(k in t for t in flat)), None)


def is_skip_settings(texts) -> bool:
    return kind_of(texts) is not None


STORY_TOGGLES = ("support_commu", "start_commu", "hajime_commu", "affection_movie")  # 跳剧情类的演出开关


def desired(stop_story: bool | None = None) -> dict[str, str | None]:
    """各开关要的状态：缺省全 ON。HIRO_UNREAD_STORY=stop 时（hiro.loop.unread_story）：「未読コミュでも早送り」要 OFF
    （开着时未读剧情自己快进过去，不弹确认框，就认不出未读）；跳剧情类的演出开关不动（None = 保持原样）。"""
    if stop_story is None:
        from hiro.loop.unread_story import stopping

        stop_story = stopping()
    if not stop_story:
        return {}
    return {"unread_ff": "OFF", **{k: None for k in STORY_TOGGLES}}


def read(boxes, stop_story: bool | None = None) -> dict | None:
    """不是设置框返回 None；是 → {kind, items: {key: "ON" | "OFF" | None}, off: [(x, y)…]（要点的开关）, option: (x, y) | None, ok}。"""
    kind = kind_of([t for t, _, _ in boxes])
    if kind is None:
        return None
    spec = DIALOGS[kind]
    switches = [(_norm(t), _center(b)) for t, _, b in boxes if _norm(t) in ("ON", "OFF") and b[0] > SWITCH_X]
    items: dict[str, str | None] = {}
    off: list[tuple[int, int]] = []
    want = desired(stop_story)
    for key, needles in spec["toggles"].items():
        ys = [_center(b)[1] for t, _, b in boxes if any(n in _norm(t) for n in needles)]
        hit = None
        for y in ys:
            near = sorted((abs(c[1] - y), s, c) for s, c in switches if abs(c[1] - y) <= SWITCH_DY)
            if near:
                hit = near[0]
                break
        items[key] = hit[1] if hit else None
        goal = want.get(key, "ON")
        if hit and goal is not None and hit[1] != goal and hit[2] not in off:
            off.append(hit[2])  # 要点的开关（名字沿用 off：以前只会把 OFF 点成 ON）
    option = None
    if spec["option"] is not None:
        want, avoid = spec["option"]
        option = next((_center(b) for t, _, b in boxes if want in _norm(t) and not any(a in _norm(t) for a in avoid)), None)
    ok = next((_center(b) for t, _, b in boxes if is_ok_text(t)), None)
    return {"kind": kind, "items": items, "off": off, "option": option, "ok": ok}


def handle(boxes, *, shoot: Callable, ocr: Callable, click: Callable[[int, int], None],
           sleep: Callable[[float], None] = time.sleep, stop_story: bool | None = None) -> dict | None:  # fmt: skip
    """处理设置框：不是返回 None；是 → {kind, items（最后读到的开关状态）, option（选了没有）, clicked（点开的开关数）, ok（点了決定没有）}。"""
    r = read(boxes, stop_story)
    if r is None:
        return None
    kind, picked = r["kind"], False
    if r["option"] is not None:
        click(*r["option"])  # 单选项：点选项文字
        picked = True
        sleep(0.4)
    elif DIALOGS[kind]["option"] is not None:
        logger.warning("%s：没读到要选的「%s」，按当前选项", kind, DIALOGS[kind]["option"][0])
    clicked = 0
    for _ in range(ROUNDS):
        if not r["off"]:
            break
        for x, y in r["off"]:
            click(x, y)
            clicked += 1
            sleep(0.4)
        sleep(0.4)
        img = shoot()
        r2 = read(ocr(img), stop_story) if img is not None else None
        if r2 is None or r2["kind"] != kind:  # 弹窗不见了（不该发生）：交给主循环重判
            return {"kind": kind, "items": r["items"], "option": picked, "clicked": clicked, "ok": False}
        r = r2
    if r["off"] or any(v is None for k, v in r["items"].items() if desired(stop_story).get(k, "ON") is not None):
        logger.warning("%s：还有没打开 / 没读到的开关 %s，照样点「決定」", kind, r["items"])
    if r["ok"] is None:
        logger.warning("%s：没读到「決定」，不点", kind)
        return {"kind": kind, "items": r["items"], "option": picked, "clicked": clicked, "ok": False}
    click(*r["ok"])
    sleep(1.0)
    logger.warning("%s（设置被重置）：选项 %s，拨了 %d 个开关 → 決定；%s", kind, "已选" if picked else "-", clicked, r["items"])
    return {"kind": kind, "items": r["items"], "option": picked, "clicked": clicked, "ok": True}
