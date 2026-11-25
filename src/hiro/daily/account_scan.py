"""读首页数值和プロデューサーID（初星助手「读首页数值」任务 scan-account）。

首页顶栏读 PLv / AP / ジュエル（hiro.vision.home_values），再点右下菜单 →「プロフィール」，读「P-ID XXXXXXXX」和名字，
最后回首页。信息库按プロデューサーID 分账号（hiro.cli.store），读到新的 ID 就切过去（11-25：Mac 在账号 2 / 3 之间切换后，
信息库还是上一个账号的库存）。

プロフィール页（720x1280，11-25 账号 2 实机 OCR）：左上标题「プロフィール」；「P-ID VRRL6GVW」(43,543)–(236,564)
（有时 ID 单独成框，在同一行右边）；名字在 P-ID 下面、「PLv 65」上面那一行的左半边。
"""

from __future__ import annotations

import logging
import re

from hiro.daily import common
from hiro.runtime import nav
from hiro.vision.home_values import read_home_values

logger = logging.getLogger(__name__)

MENU_TAP = (652, 1212)  # 首页右下「≡」菜单
TIMEOUT = 30.0
ID_RE = re.compile(r"\b([A-Z0-9]{8})\b")
NAME_X_MAX = 400

Box = tuple[str, float, tuple[int, int, int, int]]


def _cy(b: Box) -> float:
    return (b[2][1] + b[2][3]) / 2


def parse_profile(boxes: list[Box]) -> dict | None:
    """プロフィール页 → {producer_id, name}；不是这一页（找不到 P-ID）返回 None。"""
    label = next((b for b in boxes if re.search(r"P-?ID", b[0].replace(" ", ""))), None)
    if label is None:
        return None
    pid = None
    row = sorted((b for b in boxes if abs(_cy(b) - _cy(label)) < 15 and b[2][0] >= label[2][0]), key=lambda b: b[2][0])
    for b in row:
        if m := ID_RE.search(re.sub(r"P-?ID", "", b[0].replace(" ", "").upper())):
            pid = m.group(1)
            break
    if pid is None:
        return None
    plv = next((b for b in boxes if b[0].replace(" ", "").upper().startswith("PLV") and _cy(b) > _cy(label)), None)
    below = [b for b in boxes if _cy(b) > _cy(label) + 30 and (plv is None or _cy(b) < _cy(plv) - 10)
             and b[2][0] < NAME_X_MAX]  # fmt: skip
    name = min(below, key=_cy)[0].strip() if below else None
    return {"producer_id": pid, "name": name}


def _menu_profile(boxes: list[Box]) -> tuple[int, int] | None:
    """菜单页（「プロフィール」+「名刺」）里「プロフィール」的中心。"""
    if not any("名刺" in b[0] for b in boxes):
        return None
    b = next((b for b in boxes if b[0].strip().endswith("プロフィール")), None)
    if b is None:
        return None
    x1, y1, x2, y2 = b[2]
    return (x1 + x2) // 2, (y1 + y2) // 2


def run(rt) -> dict:
    """首页读数值 → 菜单 → プロフィール 读 ID → 回首页。返回 {plv, ap, ap_max, jewel, producer_id, name}。"""
    nav.goto_home(rt)
    vals = read_home_values(common.frame(rt).img) or {}
    if not vals:
        logger.warning("首页数值没读出来（弹窗挡住？），只读プロデューサーID")
    rt.device.tap(*MENU_TAP)
    rt.sleep(1.5)

    def step(f):
        if (p := parse_profile(f.boxes)) is not None:
            return p
        if (pos := _menu_profile(f.boxes)) is not None:
            rt.device.tap(*pos)
            rt.sleep(1.5)
        return None

    prof = rt.drive(step, TIMEOUT, "进プロフィール读プロデューサーID", every=0.5)
    logger.info("账号：プロデューサーID %s（%s）", prof["producer_id"], prof["name"] or "?")
    nav.goto_home(rt)
    return {**{k: vals.get(k) for k in ("plv", "ap", "ap_max", "jewel")}, **prof}


def main(argv: list[str] | None = None) -> int:
    """命令行（初星助手的「读首页数值」任务）：python -m hiro.daily.account_scan --out out.json。"""
    import argparse
    import json
    import time
    from pathlib import Path

    ap = argparse.ArgumentParser(description="读首页 PLv / AP / ジュエル 和プロフィール里的プロデューサーID")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.daily import Popup, make_runtime
    from hiro.devices import open_device

    dev = open_device()
    dev.connect()
    rt = make_runtime(dev, popup=Popup())
    result = run(rt)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps({"scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), **result}, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
