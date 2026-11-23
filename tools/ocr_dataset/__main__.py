"""python -m tools.ocr_dataset <子命令>。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools.ocr_dataset import schema


def dataset_root(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("HIRO_OCR_DATASET") or Path(__file__).resolve().parents[2].parent / "hiro-ocr-dataset")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ocr_dataset", description="OCR 测试集工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="生成数据仓库骨架")
    p.add_argument("dir", nargs="?")
    p = sub.add_parser("validate", help="校验数据仓库")
    p.add_argument("dir", nargs="?")
    p = sub.add_parser("lexicon", help="生成游戏词表 / 字表（缺省写到数据仓库的 lexicon/）")
    p.add_argument("dir", nargs="?")
    p = sub.add_parser("pick", help="从 trace 目录 / 图片目录挑帧，打包到 inbox/")
    p.add_argument("paths", nargs="+", help="trace 目录（含 frames/）或图片目录")
    p.add_argument("--contributor", required=True)
    p.add_argument("--note", action="append", default=[], help="<seq>=<说明> 或 <运行名>:<seq>=<说明>：这帧哪里认错了")
    p.add_argument("--platform", help="强制平台标签（老 trace 名字里看不出平台时用）")
    p.add_argument("--dataset", help="数据仓库位置（缺省 HIRO_OCR_DATASET 或 ../hiro-ocr-dataset）")
    p.add_argument("--n", type=int, default=None, help="每格最多几张（缺省 3）")
    p.add_argument("--n-dmm540", type=int, default=None, help="DMM 540 每格最多几张（缺省 5）")
    p.add_argument("--all", action="store_true", help="不分层抽样，去重后全收（做进阶集用）")
    p.add_argument("--dry-run", action="store_true", help="只打印挑中的分布，不写 inbox")
    a = ap.parse_args(argv)
    if a.cmd == "pick":
        from tools.ocr_dataset import pick

        if a.platform and a.platform not in schema.PLATFORMS:
            ap.error(f"--platform 应为 {schema.PLATFORMS} 之一")
        notes = {}
        for item in a.note:
            key, sep, text = item.partition("=")
            if not sep:
                ap.error(f"--note 写成 <seq>=<说明>：{item!r}")
            notes[key.strip()] = text.strip()
        root = dataset_root(a.dataset)
        cands = pick.collect(a.paths, platform=a.platform)
        known = pick.known_hashes(root)
        if a.all:
            sel = [c for c in cands if c.sha not in known]
        else:
            sel = pick.select(cands, n=a.n or pick.N_CELL, n_dmm540=a.n_dmm540 or pick.N_CELL_DMM540, known=known)
        print(f"读到 {len(cands)} 帧（去重后），挑中 {len(sel)}")
        print(pick.summary(sel))
        if not a.dry_run and sel:
            print(f"→ {pick.write_inbox(sel, root, a.contributor, notes)}")
        return 0
    if a.cmd == "init":
        root = schema.init(dataset_root(a.dir))
        print(f"骨架已生成：{root}")
        return 0
    if a.cmd == "validate":
        return schema.main([str(dataset_root(a.dir))])
    if a.cmd == "lexicon":
        from tools.ocr_dataset import lexicon

        out = Path(a.dir) if a.dir else dataset_root() / "lexicon"
        lex = lexicon.build()
        lexicon.save(lex, out)
        print(f"词表 {len(lex.words)} 个、字表 {len(lex.charset)} 字（主数据 {lex.version[:12]}）→ {out}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
