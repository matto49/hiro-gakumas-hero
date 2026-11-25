"""OCR 测试集的标注格式与数据仓库校验（规格 §5 / §5.1 / §7）。

只用 Python 标准库：这个文件原样拷进数据仓库当 tools/validate.py，CI 用 `python tools/validate.py .` 校验，不依赖主仓库。

一帧 = 一张图（core/ 或 hard/ 下）+ 同名 .json 标注：
    {schema, id, image, sha256, source{run, seq, contributor, captured}, platform{kind, native, stored, device, note}, page,
     boxes: [{id, box: [x1, y1, x2, y2], text, status, answers{…}, tags{field, type}, ignore}]}
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

SCHEMA_VERSION = 1  # 旧的框级标注（第一批 302 帧），第 3 步重建后停用
SCHEMA_FIELDS = 2  # 字段真值（规格 2026-11-23-recognition-dataset-fields-design.md §4），已被 v3 取代
SCHEMA_SCENARIO = 3  # 场景真值（规格 2026-11-23-recognition-dataset-scenarios-design.md §4）
SETS = ("core", "hard")
NATIVE_SUFFIX = ".native.png"  # 帧旁边可选的原生分辨率截图 <id>.native.png
IMAGE_EXT = (".png", ".jpg", ".jpeg")
STATUSES = ("agreed", "resolved", "disputed", "ignored")
TYPES = ("button", "title", "name", "number", "body", "label", "other")
PLATFORMS = ("dmm", "dmm540", "dmm720", "dmm1080", "mumu", "mac", "adb", "other")


def frame_id(platform: str, run: str, seq: int) -> str:
    """<平台>-<来源运行>-<seq 五位>。"""
    return f"{platform}-{run}-{int(seq):05d}"


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _size(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and all(_is_int(x) and x > 0 for x in v)


def validate_frame(d) -> list[str]:
    """一帧标注的格式问题（空列表 = 合格）。"""
    if not isinstance(d, dict):
        return ["标注不是 JSON 对象"]
    errs: list[str] = []
    if not _is_int(d.get("schema")) or d["schema"] not in (
        SCHEMA_VERSION,
        SCHEMA_FIELDS,
        SCHEMA_SCENARIO,
    ):  # true / 3.0 不算版本号
        errs.append(
            f"schema 应为 {SCHEMA_SCENARIO}（场景真值）、{SCHEMA_FIELDS}（字段真值）或 {SCHEMA_VERSION}（旧的框级标注）"
        )
    for k in ("id", "image", "sha256", "page"):
        if not isinstance(d.get(k), str) or not d.get(k):
            errs.append(f"缺 {k}（非空字符串）")
    src = d.get("source")
    if not isinstance(src, dict) or not isinstance(src.get("run"), str) or not _is_int(src.get("seq")):
        errs.append("source 要有 run（字符串）和 seq（整数）")
    plat = d.get("platform")
    if not isinstance(plat, dict) or plat.get("kind") not in PLATFORMS:
        errs.append(f"platform.kind 应为 {PLATFORMS} 之一")
    else:
        if not _size(plat.get("stored")):
            errs.append("platform.stored 应为 [宽, 高]")
        if plat.get("native") is not None and not _size(plat.get("native")):
            errs.append("platform.native 应为 [宽, 高] 或 null")
    if d.get("schema") == SCHEMA_FIELDS:
        return errs + _validate_truth(d)
    if d.get("schema") == SCHEMA_SCENARIO:
        return errs + _validate_scenario(d)
    boxes = d.get("boxes")
    if not isinstance(boxes, list):
        return errs + ["缺 boxes（列表）"]
    seen = set()
    for i, b in enumerate(boxes):
        where = f"boxes[{i}]"
        if not isinstance(b, dict):
            errs.append(f"{where} 不是对象")
            continue
        bid = b.get("id")
        if not _is_int(bid) or bid in seen:
            errs.append(f"{where}.id 应为不重复的整数")
        if _is_int(bid):  # 只记整数：[] 之类不可哈希，加进去会抛 TypeError，后面的诊断就都没了
            seen.add(bid)
        box = b.get("box")
        if not (
            isinstance(box, list)
            and len(box) == 4
            and all(_is_int(x) and x >= 0 for x in box)
            and box[0] < box[2]
            and box[1] < box[3]
        ):
            errs.append(f"{where}.box 应为 [x1, y1, x2, y2]（非负整数，x1<x2、y1<y2）")
        if b.get("status") not in STATUSES:
            errs.append(f"{where}.status 应为 {STATUSES} 之一")
        if not isinstance(b.get("text"), str) and b.get("status") in ("agreed", "resolved"):
            errs.append(f"{where}.text：agreed / resolved 的框要有正确文字")
        if not isinstance(b.get("answers"), dict):
            errs.append(f"{where}.answers 应为对象")
        tags = b.get("tags")
        if not isinstance(tags, dict) or tags.get("type") not in TYPES:
            errs.append(f"{where}.tags.type 应为 {TYPES} 之一")
        if not isinstance(b.get("ignore"), bool):
            errs.append(f"{where}.ignore 应为 true / false")
    return errs


def _validate_truth(d: dict) -> list[str]:
    """schema 2：truth 里至少有 fields / quick_fields / checks 之一；field_status 的值是 STATUSES 之一。"""
    errs = []
    t = d.get("truth")
    if not isinstance(t, dict) or not any(
        isinstance(t.get(k), dict) and t.get(k) for k in ("fields", "quick_fields", "checks")
    ):
        errs.append("truth 要有 fields / quick_fields / checks 至少一项（非空对象）")
    st = d.get("field_status", {})
    if not isinstance(st, dict) or any(v not in STATUSES for v in st.values()):
        errs.append(f"field_status 应为 {{字段路径: {STATUSES} 之一}}")
    return errs


def _scenario_ids() -> set[str] | None:
    """同目录的 scenarios.json 里的场景名（数据仓库里 tools/validate.py 旁边也放一份）；没有就不查场景名。"""
    p = Path(__file__).with_name("scenarios.json")
    if not p.is_file():
        return None
    return {k for k in json.loads(p.read_text(encoding="utf-8")) if not k.startswith("_")}


def _validate_scenario(d: dict) -> list[str]:
    errs = []
    ids = _scenario_ids()
    if not isinstance(d.get("scenario"), str) or (ids is not None and d["scenario"] not in ids):
        errs.append(f"scenario 应为场景定义里的名字（{sorted(ids) if ids else '?'}）")
    if not isinstance(d.get("truth"), dict):
        errs.append("truth 应为对象")
    st = d.get("item_status", {})
    if not isinstance(st, dict) or any(v not in STATUSES for v in st.values()):
        errs.append(f"item_status 应为 {{信息项路径: {STATUSES} 之一}}")
    buttons = d.get("buttons")
    if buttons is not None and not isinstance(buttons, list):
        return [*errs, "buttons 应为列表（没有按钮就省略或写 []）"]
    for i, b in enumerate(buttons or []):
        box = b.get("box") if isinstance(b, dict) else "x"
        if (
            not isinstance(b, dict)
            or not isinstance(b.get("label"), str)
            or not (box is None or (isinstance(box, list) and len(box) == 4 and all(_is_int(v) for v in box)))
        ):
            errs.append(f"buttons[{i}] 应为 {{label: 文字, box: [x1, y1, x2, y2] 或 null}}")
    return errs


def validate_dir(root: str | Path) -> list[str]:
    """整个数据仓库的问题：每帧格式、图片和标注一一对应、sha256、id 与文件名一致、跨目录不重复。"""
    root = Path(root)
    if not root.is_dir():
        return [f"数据集目录不存在：{root}"]
    errs: list[str] = []
    ids: dict[str, str] = {}
    shas: dict[str, str] = {}
    for s in SETS:
        d = root / s
        if not d.is_dir():
            continue
        for img in sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT):
            if img.name.endswith(NATIVE_SUFFIX):  # 原生分辨率截图，跟着同 id 的帧
                if not (d / (img.name[: -len(NATIVE_SUFFIX)] + ".json")).exists():
                    errs.append(f"{s}/{img.name}：原生截图没有对应的帧标注")
            elif not img.with_suffix(".json").exists():
                errs.append(f"{s}/{img.name}：没有同名标注 .json")
        for js in sorted(d.glob("*.json")):
            rel = f"{s}/{js.name}"
            try:
                fr = json.loads(js.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                errs.append(f"{rel}：读不了（{e}）")
                continue
            errs += [f"{rel}：{e}" for e in validate_frame(fr)]
            if not isinstance(fr, dict):
                continue
            if fr.get("id") != js.stem:
                errs.append(f"{rel}：id {fr.get('id')!r} 和文件名不一致")
            name = str(fr.get("image") or "")
            img = d / name
            if (  # 主图必须是本分区里和标注同名的图：指到别的目录的图，哈希对得上也不行（贡献者拿到的数据集会缺主图）
                Path(name).name != name
                or Path(name).stem != js.stem
                or Path(name).suffix.lower() not in IMAGE_EXT
                or name.endswith(NATIVE_SUFFIX)
            ):
                errs.append(f"{rel}：图片 {name!r} 应是本目录里和标注同名的图（{js.stem}.jpg / .png）")
            elif not img.is_file():
                errs.append(f"{rel}：图片 {fr.get('image')} 不存在")
            elif sha256(img) != fr.get("sha256"):
                errs.append(f"{rel}：sha256 和图片对不上")
            fid, sha = str(fr.get("id")), str(fr.get("sha256"))
            if fid in ids:
                errs.append(f"{rel}：id 重复（已在 {ids[fid]}）")
            elif sha in shas:
                errs.append(f"{rel}：图片内容重复（同 {shas[sha]}）")
            ids.setdefault(fid, rel)
            shas.setdefault(sha, rel)
    return errs


SKELETON = Path(__file__).with_name("skeleton")


def init(root: str | Path) -> Path:
    """生成数据仓库骨架（已存在的文件不覆盖）。"""
    root = Path(root)
    for s in (*SETS, "inbox", "lexicon", "reports", "schema", "tools", ".github/workflows"):
        (root / s).mkdir(parents=True, exist_ok=True)
    files = {
        "README.md": SKELETON / "README.md",
        "CONTRIBUTING.md": SKELETON / "CONTRIBUTING.md",
        ".gitattributes": SKELETON / "gitattributes",
        ".gitignore": SKELETON / "gitignore",
        ".github/workflows/validate.yml": SKELETON / "validate.yml",
        "schema/frame.schema.json": Path(__file__).with_name("frame.schema.json"),
        "tools/validate.py": Path(__file__),
        "tools/scenarios.json": Path(__file__).with_name("scenarios.json"),
    }
    for rel, src in files.items():
        dst = root / rel
        if not dst.exists():
            shutil.copyfile(src, dst)
    if not (root / "disputed.json").exists():
        (root / "disputed.json").write_text("[]\n", encoding="utf-8")
    return root


def main(argv: list[str] | None = None) -> int:
    """python validate.py <数据仓库目录>：打印问题，有问题退出码 1。"""
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0] if argv else ".")
    errs = validate_dir(root)
    for e in errs:
        print(e)
    print(f"{root}：{len(errs)} 个问题")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
