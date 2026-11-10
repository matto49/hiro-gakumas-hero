# 来源：digital-hiro tools/play_game.py（c3fc27f）；开源版：偶像 / 主属性改成参数，trace 名不带内部前缀
"""从首页开一整局（选拔 → 本战），或只打本战。选拔结束后不等人工，直接开本战。

    python tools/play_game.py --idol <偶像卡 skin id> [--main-attr Da] [--tag 1]           # 整局：选拔 + 本战
    python tools/play_game.py --main-only [--memory 1670,1828,1011] --idol <…>              # 只打本战（用已有的选拔回忆）
    python tools/play_game.py … -- --no-llm                                                # -- 之后的参数原样交给 run_hif

流程：
1. run_hif new --mode qualify（选拔，trace qualify<tag>-<时间>）；选拔进程非 0 退出就停下（不自动续跑：续跑要先看原因）。
2. 选拔结束停在「開始確認」页：OCR 读选拔回忆的 Vo / Da / Vi 三个数（页面中部那一排，多帧投票），作为 --expect-memory。
3. run_hif new --mode main --expect-memory <三个数>（本战，trace main<tag>-<时间>）；回忆对不上 run_hif 不开局。
4. 每段结束打印耗时；整局结束打印总耗时。日志在 reports/live/<trace>.log。
只读屏 + 起子进程；所有游戏内操作仍由 run_hif 做（含危险弹窗保护）。读屏（设置检查、读回忆）也在短命子进程里做，
包装进程不导入识别层（不常驻 KAA / OCR 的内存）。
开局前先做设置检查（tools/check_settings.py：只报警，不改）；设了 HIRO_PAUSE_FLAG 且旗标在时先等它撤掉。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
PY = sys.executable
PARAM_BAND = (470, 520)  # 开始确认页 Vo / Da / Vi 数字那一排的 y 范围（720x1280；第十六局实机帧 y≈495）


def parse_memory(boxes) -> tuple[int, int, int] | None:
    """开始确认页：取 PARAM_BAND 里从左到右三个 3–4 位数。boxes = [(text, conf, (x1, y1, x2, y2))]。"""
    nums = []
    for t, _, (x1, y1, x2, y2) in boxes:
        cy = (y1 + y2) // 2
        if PARAM_BAND[0] <= cy <= PARAM_BAND[1] and x1 > 330:
            m = re.fullmatch(r"\D*(\d{3,4})\D*", t.replace(",", "").replace(" ", ""))
            if m:
                nums.append(((x1 + x2) // 2, int(m.group(1))))
    nums.sort()
    return tuple(n for _, n in nums[:3]) if len(nums) >= 3 else None


ICON_SCAN = 48  # 属性图标在数字框左端的最大宽度（720x1280）


def icon_right_edge(img, box) -> int | None:
    """数字框左端的属性图标（粉 / 蓝 / 橙的实心圆，高饱和高亮）右缘 x；左端没有图标返回 None。
    DMM 版 RapidOCR 把图标读成数字：1459 → 91459、865 → 89865（图标占一位或两位，只看文字分不出）。"""
    import cv2
    import numpy as np

    x1, y1, x2, y2 = (int(v) for v in box)
    roi = img[max(0, y1):y2, max(0, x1):min(x2, x1 + ICON_SCAN)]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    colored = ((hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 150)).sum(axis=0) >= max(4, roi.shape[0] // 4)
    cols = np.flatnonzero(colored)
    if cols.size < 8:  # 图标约 30 像素宽，零星几列是数字描边 / 背景
        return None
    return x1 + int(cols.max()) + 1


def parse_memory_img(img, boxes, ocr) -> tuple[int, int, int] | None:
    """parse_memory 加一步：数字框左端有属性图标就把图标裁掉，只放大 OCR 数字部分。ocr(img) → [(text, conf, box)]。"""
    import cv2

    fixed = []
    for t, c, (x1, y1, x2, y2) in boxes:
        cy = (y1 + y2) // 2
        if PARAM_BAND[0] <= cy <= PARAM_BAND[1] and x1 > 330:
            edge = icon_right_edge(img, (x1, y1, x2, y2))
            if edge is not None and x2 - edge > 20:
                crop = cv2.resize(img[int(y1):int(y2), edge + 1:int(x2)], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                digits = "".join(re.sub(r"\D", "", tt) for tt, _, _ in ocr(crop))
                if 3 <= len(digits) <= 4:
                    t = digits
        fixed.append((t, c, (x1, y1, x2, y2)))
    return parse_memory(fixed)


def read_memory() -> tuple[int, int, int] | None:
    sys.path.insert(0, str(ROOT / "src"))
    import hiro  # noqa: F401
    from hiro.device import connect
    from hiro.produce.ops import ocr_boxes

    d = connect()
    votes: list[tuple[int, int, int]] = []
    for _ in range(8):
        img = d.screenshot()
        boxes = ocr_boxes(img)
        if any("開始確認" in t or "本戦へ" in t for t, _, _ in boxes):
            m = parse_memory_img(img, boxes, ocr_boxes)
            if m:
                votes.append(m)
        if len(votes) >= 3:
            break
        time.sleep(1.0)
    return vote_memory(votes)


def vote_memory(votes: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    """多帧逐位投票：每个属性取出现最多的读数，且至少 2 帧一致才算数（第十八局单帧把 1434 读成 1454，核对失败）。"""
    from collections import Counter

    if len(votes) < 2:
        return None
    out = []
    for i in range(3):
        val, n = Counter(v[i] for v in votes).most_common(1)[0]
        if n < 2:
            return None
        out.append(val)
    return tuple(out)


def _prefix() -> str:
    """trace 名前缀（HIRO_TRACE_PREFIX，例如 lead35 → lead35-qualify34-…）；不设就没有前缀。tools/audit/blunders 按同一个变量找段。"""
    import os

    p = os.environ.get("HIRO_TRACE_PREFIX", "").strip("-")
    return f"{p}-" if p else ""


def run_cmd(mode: str, trace: str, common: list[str], extra: list[str]) -> list[str]:
    cmd = [PY, "-m", "hiro.run_hif", "new", "--mode", mode, *common, *extra, "--trace", trace]
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        cmd = ["caffeinate", "-d", *cmd]  # 长任务别让显示器睡眠
    return cmd


def child_env() -> dict:
    """子进程环境：PYTHONPATH 把仓库 src 放最前，保留调用方原有的路径（插件，如 HIRO_EXAM_ADVISOR 指的模块）。
    以前直接覆盖成 src，GKMS 第四局本战考试导入 hiro_gkms_advisor 失败、连崩 5 次退出。"""
    import os

    old = os.environ.get("PYTHONPATH", "")
    return {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), *([old] if old else [])])}


def run(mode: str, trace: str, common: list[str], extra: list[str]) -> int:
    log = ROOT / "reports/live" / f"{trace}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    cur = ROOT / ".temp/current_run"  # 当前在跑的 trace 名：录屏 / 剪辑等外部流程按它对齐（主仓库同款）
    cur.parent.mkdir(parents=True, exist_ok=True)
    cur.write_text(trace)
    with open(log, "w") as f:
        code = subprocess.call(run_cmd(mode, trace, common, extra), cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                               env=child_env())  # fmt: skip
    with open(log, "a") as f:
        f.write(f"EXIT {code}\n")
    print(f"{mode} {trace} exit={code} {(time.monotonic() - t0) / 60:.1f} min（日志 {log}）", flush=True)
    return code


def _probe(kind: str) -> dict:
    """在短命子进程里做要识别层的读屏（设置检查 / 读选拔回忆），结果是一行 JSON。
    包装进程本身不导入 KAA / OCR：它要一直挂着等 run_hif，导入一次就常驻约 2.5 GB（Windows 第十九局，和模拟器 + MuMu 一起把内存吃满）。"""
    import json
    import os

    try:
        out = subprocess.run([PY, str(Path(__file__).resolve()), "--probe", kind], cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=180, env=child_env())  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"error": "读屏子进程超时"}
    line = next((ln for ln in reversed(out.stdout.splitlines()) if ln.startswith("{")), None)
    if line is None:
        return {"error": f"读屏子进程没有结果（exit {out.returncode}）：{(out.stderr or '')[-300:]}"}
    return json.loads(line)


def _probe_main(kind: str) -> int:
    """--probe 子进程：做一次读屏，打印一行 JSON。"""
    import json

    sys.path.insert(0, str(ROOT / "src"))
    try:
        if kind == "memory":
            res = {"memory": read_memory()}
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from check_settings import check, verdict

            r = check(ROOT / "reports/live/settings")
            res = {"settings": r if isinstance(r, (dict, list, str, int, float, type(None))) else str(r), "verdict": verdict(r)}
    except Exception as e:  # noqa: BLE001
        res = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(res, ensure_ascii=False, default=str), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--probe"] and len(argv) == 2:
        return _probe_main(argv[1])
    passthrough = argv[argv.index("--") + 1 :] if "--" in argv else []
    argv = argv[: argv.index("--")] if "--" in argv else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--idol", required=True, help="偶像卡 skin id（如 i_card-skin-shro-3-007）")
    ap.add_argument("--main-attr", choices=["Vo", "Da", "Vi"], default=None, help="主属性（按支援卡编成定）")
    ap.add_argument("--tag", default="", help="trace 名里的局号（qualify<tag>-<时间> / main<tag>-<时间>）")
    ap.add_argument("--main-only", "--skip-qualify", dest="main_only", action="store_true", help="只打本战（用已有的选拔回忆）")
    ap.add_argument("--memory", default="", help="本战要核对的选拔回忆 Vo,Da,Vi；不给就在开始确认页读")
    ap.add_argument("--no-settings-check", action="store_true", help="跳过开局前设置检查")
    args = ap.parse_args(argv)
    common = ["--idol", args.idol, *(["--main-attr", args.main_attr] if args.main_attr else []), *passthrough]
    t0 = time.monotonic()
    # 设备操作前：外部暂停旗标在就等。也放子进程里：导入 hiro 包会连带 cv2 / kotonebot，Windows 上包装进程因此常驻约 500 MB（第二十局）
    import os

    subprocess.call([PY, "-c", "from hiro.loop import pause_flag; pause_flag.wait_until_recording()"], cwd=ROOT,
                    env=child_env())  # fmt: skip
    if not args.no_settings_check:
        # 开局前检查设置（重新登录后会被重置；只报警，不改，不阻断）。只打本战也要查（第二十三局）
        r = _probe("settings")
        if r.get("error"):
            print(f"设置检查失败（{r['error']}），继续开局", flush=True)
        else:
            v = str(r.get("verdict"))
            print(f"设置检查：{r.get('settings')}  {'' if v.startswith('ok') else '⚠ '}{v}（只报警）", flush=True)
    if not args.main_only:
        q = f"{_prefix()}qualify{args.tag}-{datetime.now():%Y%m%d-%H%M%S}"
        if run("qualify", q, common, []) != 0:
            print("选拔进程非 0 退出，停下（先看日志再决定续跑：python -m hiro.run_hif resume --mode qualify）", flush=True)
            return 3
    if args.memory:
        mem = tuple(int(x) for x in args.memory.split(","))
    else:
        r = _probe("memory")
        mem = tuple(r["memory"]) if r.get("memory") else None
        if r.get("error"):
            print(f"读选拔回忆出错：{r['error']}", flush=True)
    if not mem:
        print("开始确认页读不出选拔回忆数值，停下", flush=True)
        return 3
    print(f"选拔回忆 {mem}", flush=True)
    m = f"{_prefix()}main{args.tag}-{datetime.now():%Y%m%d-%H%M%S}"
    code = run("main", m, common, ["--expect-memory", ",".join(map(str, mem))])
    print(f"整局 {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
