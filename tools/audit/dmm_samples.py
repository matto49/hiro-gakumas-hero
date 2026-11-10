"""DMM 实机帧识别回归集：取值规则（tests/test_dmm_regression.py 共用）+ 加样例的命令。

    python -m tools.audit.dmm_samples add <trace 目录> <seq>[,<seq>…] [--note 说明]   # 帧拷进 tests/fixtures/dmm/，按当前识别写期望
    python -m tools.audit.dmm_samples add-file <图片> [--name 文件名] [--note 说明]
    python -m tools.audit.dmm_samples show <文件名>                                     # 打印当前读数，和清单比

期望先按当前识别写进清单（tests/dmm_regression.json），**逐条看图核对**后才算数；读错的项改成正确值（测试随之失败 = 待修的识别问题）。


每帧断言：快速判页（quick_page）、页面（hiro.vision.scene.classify_current）、该页 extract 字段里的「稳定项」：
ID / 数值 / 布尔 / 短枚举。长文本（建议、效果说明、OCR 原文）不断言：Windows 用 RapidOCR、Mac 用 Vision，文字可能合理地不同。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIST = ROOT / "tests" / "dmm_regression.json"
FIX = ROOT / "tests" / "fixtures" / "dmm"

ITEM_KEYS = ("card_id", "upgrade", "drink_id", "pitem_id", "kind", "price", "sold", "dimmed", "recommended", "slot", "row", "col",
             "available", "id", "attr", "value", "count", "selected", "asset", "state", "color", "trouble", "cost_p", "cost_hp")  # fmt: skip
SKIP_KEYS = {"score", "margin", "y", "method", "confidence", "box", "upgrade_red_px", "advice", "text", "effect", "rect", "center", "ms", "my_score_read", "my_score_votes", "error", "raw", "boxes", "art_card_id",
             "name", "names", "title", "message", "detail", "texts", "npc_scores", "entries"}  # fmt: skip


def stable(value, depth: int = 0, key: str = "", item: bool = False):
    """extract 结果 → 只留稳定项（递归）。None 表示这一项不断言。ID（键名 id / asset / *_id）不论长短都留。
    列表里的元素（卡 / 饮料 / 选项）只留 ITEM_KEYS 和数值 / 布尔；具名的子字典（services、params…）整个留（去掉 SKIP_KEYS）。"""
    if isinstance(value, bool) or isinstance(value, int) or value is None:
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, str):
        return value if len(value) <= 16 or key in ("id", "asset") or key.endswith("_id") else None
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in SKIP_KEYS or (item and k not in ITEM_KEYS and not k.endswith("_id") and not isinstance(v, (int, bool))):
                continue
            s = stable(v, depth + 1, k)
            if s is not None and s != {} and s != []:
                out[k] = s
        return out
    if isinstance(value, (list, tuple)):
        return [stable(v, depth + 1, key, item=True) for v in value]
    return None


def diff(want, got, path: str = "") -> list[str]:
    """want 里的每一项都要在 got 里相等（got 多出来的项不管）。"""
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return [f"{path or '.'}: 期望字典，读到 {got!r}"]
        return [d for k, v in want.items() for d in diff(v, got.get(k), f"{path}.{k}")]
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return [f"{path}: 期望 {len(want)} 项，读到 {got!r}"[:300]]
        return [d for i, (a, b) in enumerate(zip(want, got)) for d in diff(a, b, f"{path}[{i}]")]
    return [] if want == got else [f"{path}: 期望 {want!r}，读到 {got!r}"]


def read(path: Path, page: str | None = None) -> dict:
    """一帧 → {quick, quick_fields, tap, scene, fields}。page：这一帧的真实页面（取字段用哪个提取器）；不给就用 scene。"""
    import cv2

    from hiro.context import ensure_offline_context
    from hiro.vision.extract import extract
    from hiro.vision.offline import load
    from hiro.vision.quick import quick_page
    from hiro.vision.scene import classify_current

    ensure_offline_context()
    raw = cv2.imread(str(path))
    img = load(raw)
    q = quick_page(img) or {}
    scene = classify_current().scene
    fields, _ = extract(page or scene, img)
    tap = q.get("tap")
    return {"quick": q.get("page"), "quick_fields": stable(q.get("fields")) or None, "tap": list(tap) if isinstance(tap, (list, tuple)) else tap,
            "scene": scene, "fields": stable(fields) if fields else None}


def _load() -> list[dict]:
    return json.loads(LIST.read_text(encoding="utf-8")) if LIST.exists() else []


def _save(rows: list[dict]) -> None:
    LIST.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def add(src: Path, name: str, note: str, page: str | None = None, assert_scene: bool = True) -> dict:
    FIX.mkdir(parents=True, exist_ok=True)
    dst = FIX / name
    shutil.copyfile(src, dst)
    got = read(dst, page)
    row = {"file": name, "note": note, "page": page or got["scene"], "quick": got["quick"]}
    for k in ("quick_fields", "tap"):
        if got[k] is not None:
            row[k] = got[k]
    if assert_scene:
        row["scene"] = got["scene"]
    if got["fields"]:
        row["fields"] = got["fields"]
    rows = [r for r in _load() if r["file"] != name] + [row]
    _save(sorted(rows, key=lambda r: r["file"]))
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("trace", type=Path)
    a.add_argument("seqs")
    a.add_argument("--note", default="")
    a.add_argument("--page", help="真实页面（scene 认不出 / 交给 KAA 的页面，如 STUDY）")
    a.add_argument("--no-scene", action="store_true", help="不断言 classify_current（这页不归它管）")
    f = sub.add_parser("add-file")
    f.add_argument("image", type=Path)
    f.add_argument("--name")
    f.add_argument("--note", default="")
    f.add_argument("--page", help="真实页面（scene 认不出 / 交给 KAA 的页面，如 STUDY）")
    f.add_argument("--no-scene", action="store_true", help="不断言 classify_current（这页不归它管）")
    g = sub.add_parser("regen", help="按当前识别重写清单里的读数（保留 note / page / 是否断言 scene / xfail_on）；核对过的期望会被覆盖，慎用")
    g.add_argument("names", nargs="*")
    s = sub.add_parser("show")
    s.add_argument("name")
    args = ap.parse_args()
    if args.cmd == "add":
        for seq in args.seqs.split(","):
            src = args.trace / "frames" / f"{int(seq):05d}.jpg"
            row = add(src, f"{args.trace.name}-{int(seq):05d}.jpg", args.note, args.page, not args.no_scene)
            print(row["file"], row["quick"], row["scene"])
    elif args.cmd == "add-file":
        row = add(args.image, args.name or args.image.name, args.note, args.page, not args.no_scene)
        print(row["file"], row["quick"], row["scene"])
    elif args.cmd == "regen":
        rows = _load()
        for r in rows:
            if args.names and r["file"] not in args.names:
                continue
            got = read(FIX / r["file"], r.get("page"))
            r["quick"] = got["quick"]
            for k in ("quick_fields", "tap", "fields"):
                if got[k] is not None:
                    r[k] = got[k]
                else:
                    r.pop(k, None)
            if "scene" in r:
                r["scene"] = got["scene"]
            print(r["file"], r["quick"], r.get("scene"), r.get("page"))
        _save(rows)
    else:
        want = next((r for r in _load() if r["file"] == args.name), None)
        got = read(FIX / args.name, (want or {}).get("page"))
        print(json.dumps(got, ensure_ascii=False, indent=1))
        if want is not None:
            print("和清单的差异：", diff(want.get("fields"), got["fields"], "fields") or "无")


if __name__ == "__main__":
    main()
