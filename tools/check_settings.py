"""开局前检查游戏设置（用户 2026-11-02：重新登录后这些设置会被重置；先只报警，不自动改）。

检查「メニュー → 設定 → プロデュース」里的 5 项是否 ON：
  未読コミュでも早送りする、サポートイベントのコミュスキップ、各プロデュース開始時の説明コミュスキップ、
  定期公演『初』の「気になる様子」コミュスキップ、親愛度コミュ内の映像スキップ。
只读：从首页打开菜单 → 設定 → プロデュース 页签，读开关，存截图，回首页。不点任何开关；不在首页就跳过（什么都不点）。

    python tools/check_settings.py    # 打印每项状态；有 OFF / 读不到时退出码 1
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU, HOME = (653, 1213), (63, 1213)  # 「設定」和「プロデュース」页签按 OCR 找
WANT = {
    "未読早送り": ("未読コミュでも早送り",),
    "支援事件剧情跳过": ("サポートイベントの", "コミュスキップ"),
    "开始说明剧情跳过": ("説明コミュスキップ",),
    "定期公演剧情跳过": ("様子」コミュスキップ", "気になる"),
    "親愛度映像跳过": ("映像スキップ",),
}


def read_toggles(boxes) -> dict[str, str | None]:
    """boxes = [(text, conf, (x1, y1, x2, y2))]。每项按标题行找同一高度（±45px）右侧的 ON / OFF。"""
    switches = [(t, (b[1] + b[3]) // 2) for t, _, b in boxes if t.strip() in ("ON", "OFF") and b[0] > 480]
    out: dict[str, str | None] = {}
    for key, needles in WANT.items():
        ys = [(b[1] + b[3]) // 2 for t, _, b in boxes if any(n in t.replace(" ", "") for n in needles)]
        state = None
        for y in ys:
            near = [s for s, sy in switches if abs(sy - y) <= 45]
            if near:
                state = near[0]
                break
        out[key] = state
    return out


TAB_JUNK = "+＋✦◆◇★☆・*•·⚙"  # 页签之间的星形分隔、标题前的齿轮图标常被 OCR 并进文字（A5：「+ プロデュース」）


def _find(boxes, text: str, *, y_min: int = 0, y_max: int = 1280):
    """OCR 框里文字是 text（去空格、去掉前面并进来的分隔符 / 图标）、中心 y 在范围内的那个框的中心。"""
    for t, _, b in boxes:
        cy = (b[1] + b[3]) // 2
        if t.replace(" ", "").replace("　", "").lstrip(TAB_JUNK) == text and y_min <= cy <= y_max:
            return (b[0] + b[2]) // 2, cy
    return None


NAV_LABELS = ("ホーム", "コミ", "アイドル", "コンテスト", "シャ")  # 首页底栏；RapidOCR「コミユ」、Vision「カシャ」也算


def is_home(boxes) -> bool:
    """首页：底栏页签（ホーム / コミュ / アイドル / コンテスト / ガシャ）认出 ≥3 个，加上中间的「プロデュース」大按钮。
    本战开始确认页、菜单、设置页都不算（开始确认页左下角 (63,1213) 是「ホーム」按钮，盲点会弹「ホーム移動確認」）。"""
    nav = {n for t, _, b in boxes if (b[1] + b[3]) // 2 >= 1080 for n in NAV_LABELS if n in t.replace(" ", "")}
    produce = _find(boxes, "プロデュース", y_min=940, y_max=1060) is not None
    return len(nav) >= 3 and produce


def is_menu(boxes) -> tuple[int, int] | None:
    """菜单页：下半部宫格里的「設定」（另有「データ連携」等）。返回「設定」的位置。"""
    if not any("データ連携" in t or "キャッシュ" in t for t, _, _ in boxes):
        return None
    return _find(boxes, "設定", y_min=800, y_max=1000)


def is_settings(boxes) -> tuple[int, int] | None:
    """设置页：顶部标题「設定」+ 底部页签「基本設定 / プロデュース」。返回「プロデュース」页签的位置。"""
    if _find(boxes, "設定", y_max=120) is None or _find(boxes, "基本設定", y_min=1000) is None:
        return None
    return _find(boxes, "プロデュース", y_min=1000)


def _wait(d, ocr, pred, timeout: float, poll: float = 1.0):
    t0 = time.monotonic()
    while True:
        img = d.screenshot()
        hit = pred(ocr(img)) if img is not None else None
        if hit is not None or time.monotonic() - t0 >= timeout:
            return hit
        time.sleep(poll)


def verdict(found: dict[str, str | None]) -> str:
    """一句话结论：全 ON / 被重置（有 OFF，开局会弹首次设置框）/ 没打开设置页（全读不到）/ 部分读不到。"""
    off = [k for k, v in found.items() if k in WANT and v == "OFF"]
    missing = [k for k, v in found.items() if k in WANT and v is None]
    if found.get("_skipped"):
        return f"跳过：{found['_skipped']}，没点任何东西"
    if not off and not missing:
        return "ok：5 项都 ON"
    if off:
        return f"设置被重置：{len(off)} 项 OFF（{'、'.join(off)}），开局会依次弹首次设置框，run_hif 按既定选项处理"
    if len(missing) == len(found):
        return "读不到：设置页没打开（不代表被重置），见截图"
    return f"部分读不到：{'、'.join(missing)}"


def check(save_dir: Path | None = None) -> dict[str, str | None]:
    """只读：菜单 → 設定 → プロデュース 页签，每一步按 OCR 找到再点、确认到了再走下一步（第三十三局：
    按固定坐标点「設定」没进去，两张截图都还是菜单，读成全 None，被当成「设置被重置」）。"""
    sys.path.insert(0, str(ROOT / "src"))
    from hiro.tool_device import open_tool_device
    from hiro.vision.ocr import ocr_boxes as _ocr

    import cv2

    def ocr_boxes(img):
        return _ocr(img, scale=1.5)  # 和 hiro.produce.ops.ocr_boxes 一样（这里不导入操作层，启动快）

    d = open_tool_device()
    found: dict[str, str | None] = dict.fromkeys(WANT)

    def save(img, name):
        if save_dir is not None and img is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(save_dir / f"{name}.png"), img)

    img = d.screenshot()
    if img is None or not is_home(ocr_boxes(img)):
        # 不在首页（本战开始确认页、培育中、弹窗……）：不点。-MainOnly 从本战开始确认页起时，以前在这里点 MENU 没反应后
        # 又盲点 HOME (63,1213)，正好是开始确认页的「ホーム」，弹出 ホーム移動確認，接着读回忆数值被挡住、本战停下（DMM 实机）
        save(img, "check-not-home")
        return {**found, "_skipped": "不在首页"}
    d.tap(*MENU)
    pos = _wait(d, ocr_boxes, is_menu, 8.0)
    if pos is None:
        save(d.screenshot(), "check-menu-missing")  # 菜单没打开：画面多半没变，不盲点 HOME
        return found
    for _ in range(3):  # 菜单展开动画里点可能不生效：没进设置页就再点一次
        d.tap(*pos)
        tab = _wait(d, ocr_boxes, is_settings, 6.0)
        if tab is not None:
            break
    else:
        save(d.screenshot(), "check-settings-missing")
        d.tap(*HOME)
        time.sleep(3.0)
        return found
    d.tap(*tab)
    _wait(d, ocr_boxes, lambda b: True if any("【プロデュース】" in t for t, _, _ in b) else None, 6.0)
    for i in range(2):
        img = d.screenshot()
        save(img, f"check-{i}")
        for k, v in read_toggles(ocr_boxes(img)).items():
            found[k] = found[k] or v
        d.swipe(360, 900, 360, 450, duration=0.8)
        time.sleep(1.5)
    d.tap(*HOME)
    time.sleep(3.0)
    return found


def main(argv: list[str] | None = None) -> int:
    """打印每项状态；--json <文件> 另写一份给控制台入库（{items, verdict, ok}）。有 OFF / 读不到时退出码 1。"""
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    res = check(ROOT / "reports/live/settings")
    for k, v in res.items():
        print(f"{k}: {v}")
    print(verdict(res))
    ok = all(v == "ON" for v in res.values())
    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        items = {k: v for k, v in res.items() if not k.startswith("_")}
        out.write_text(
            json.dumps({"items": items, "verdict": verdict(res), "ok": ok}, ensure_ascii=False), encoding="utf-8"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
