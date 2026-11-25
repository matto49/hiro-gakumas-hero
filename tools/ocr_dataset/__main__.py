"""python -m tools.ocr_dataset <子命令>。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tools.ocr_dataset import schema


def dataset_root(arg: str | None = None) -> Path:
    return Path(
        arg or os.environ.get("HIRO_OCR_DATASET") or Path(__file__).resolve().parents[2].parent / "hiro-ocr-dataset"
    )


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
    p.add_argument(
        "--fill-types", action="store_true", help="对剩下的帧做文字检测，按文字类型补足每类 30 个框（Mac，慢）"
    )
    p = sub.add_parser("label", help="AI 标注 inbox 里的帧，移进 core/（Mac：要 Apple Vision）")
    p.add_argument("inbox", nargs="+", help="inbox/<贡献者>/<日期> 目录")
    p.add_argument("--dataset")
    p.add_argument("--set", default="core", choices=schema.SETS)
    p.add_argument("--llm", help="LLM 适配器（缺省 HIRO_LLM，再缺省 claude:opus）")
    p.add_argument("--workers", type=int, default=2, help="同时标几帧（LLM 调用并行）")
    p.add_argument("--limit", type=int)
    p.add_argument("--llm2", default="codex", help="第二个 LLM（只读待定框，和第一个不同源）；none 不跑")
    p = sub.add_parser("adjudicate", help="第二个 LLM 读一遍还待定的框，三家两家一致就定下")
    p.add_argument("--dataset")
    p.add_argument("--llm2", default="codex")
    p.add_argument("--workers", type=int, default=2)
    p = sub.add_parser("reask", help="重新问一遍 LLM（缺省只问拼图超过一张的帧），之后再跑 adjudicate")
    p.add_argument("--dataset")
    p.add_argument("--llm", default=None, help="缺省 HIRO_LLM，再缺省 claude:opus")
    p.add_argument("--all", action="store_true", help="所有帧都重问")
    p.add_argument("--workers", type=int, default=2)
    p = sub.add_parser(
        "import-regression",
        help="把 tests/dmm_regression.json 的回归帧转成字段真值帧写进数据仓库（旧框级帧挪到 legacy-v1/）",
    )
    p.add_argument("--dataset")
    p = sub.add_parser("eval-fields", help="字段评测：每个后端跑实机分页读取，和字段真值逐字段比")
    p.add_argument("--dataset")
    p.add_argument("--backend", default="vision,rapidocr")
    p.add_argument("--set", default="core", choices=schema.SETS)
    p.add_argument("--name", default="fields")
    p = sub.add_parser("redecide", help="规则改了以后用已存的答案重新定 status（不重新识别）")
    p.add_argument("--dataset")
    p = sub.add_parser("spot-check", help="从 agreed 框里随机抽查，生成本地核对页")
    p.add_argument("--dataset")
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--seed", type=int)
    p = sub.add_parser("review", help="待定框核对页；--apply 把导出的答案写回")
    p.add_argument("--dataset")
    p.add_argument("--apply", metavar="ANSWERS_JSON", help="review 页或抽查页导出的 JSON")
    p = sub.add_parser("eval", help="评测 OCR 后端，报告写到 reports/")
    p.add_argument(
        "--backend",
        default="rapidocr",
        help="逗号分隔：vision / rapidocr / winocr / ppocr5 / simd（装了 PR #9 时）/ rapidocr+charset",
    )
    p.add_argument("--set", default="core", choices=schema.SETS)
    p.add_argument("--tags", default="", help="platform=dmm540,page=CONSULT")
    p.add_argument("--compare", help="上一份报告的 JSON")
    p.add_argument("--name", default="eval", help="报告文件名后缀")
    p.add_argument("--dataset")
    a = ap.parse_args(argv)
    if a.cmd == "eval":
        from tools.ocr_dataset import eval as ev

        tags = dict(kv.split("=", 1) for kv in a.tags.split(",") if "=" in kv)
        md, js = ev.main(
            dataset_root(a.dataset),
            [b.strip() for b in a.backend.split(",") if b.strip()],
            a.set,
            tags,
            a.compare,
            a.name,
        )
        print(md.read_text(encoding="utf-8").split("## 按")[0])
        print(f"→ {md}\n→ {js}")
        return 0
    if a.cmd == "review":
        from tools.ocr_dataset import review

        root = dataset_root(a.dataset)
        if a.apply:
            st = review.apply(root, a.apply)
            left = json.loads((root / "disputed.json").read_text(encoding="utf-8"))
            print(
                f"写回：resolved {st['resolved']}，ignored {st['ignored']}，打回待定 {st['disputed']}，没找到 {st['missing']}；剩余待定 {len(left)}"
            )
        else:
            print(review.build(root))
        return 0
    if a.cmd == "label":
        from tools.ocr_dataset import label

        root = dataset_root(a.dataset)
        readers = label.RealReaders(a.llm or os.environ.get("HIRO_LLM") or "claude:opus")
        readers2 = label.RealReaders(a.llm2) if a.llm2 != "none" else None
        if (
            readers2 is not None and readers2.name == readers.name
        ):  # 同源的第二轮不是独立意见：decide 会把两次相同的答案当一致
            print(
                f"--llm2 {a.llm2!r} 和第一个 LLM 是同一个来源（{readers.name}），第二轮不算独立意见：换一个，或写 --llm2 none",
                file=sys.stderr,
            )
            return 2
        total = 0
        for d in a.inbox:
            total += label.run(root, d, readers=readers, set_=a.set, workers=a.workers, limit=a.limit)
        if readers2 is not None:
            st = label.adjudicate(root, readers2, workers=a.workers)
            print(f"第二个 LLM（{a.llm2}）看了 {st['frames']} 帧的待定框，改判 {st['changed']} 个")
        disputed = json.loads((root / "disputed.json").read_text(encoding="utf-8"))
        print(f"标完 {total} 帧；待定框 {len(disputed)} 个（disputed.json），用 review 处理")
        return 0
    if a.cmd == "adjudicate":
        from tools.ocr_dataset import label

        st = label.adjudicate(dataset_root(a.dataset), label.RealReaders(a.llm2), workers=a.workers)
        print(f"看了 {st['frames']} 帧，改判 {st['changed']} 个；剩余待定 {st['disputed']} 个")
        return 0
    if a.cmd == "import-regression":
        from tools.ocr_dataset import regression

        st = regression.import_all(dataset_root(a.dataset))
        print(f"导入 {st['imported']} 帧（缺图 {st['missing']}），旧框级帧挪走 {st['legacy_moved']} 帧 → legacy-v1/")
        if st["conflicts"]:
            print(
                f"有 {st['conflicts']} 帧跳过：同名图片内容不同，或同 ID 已有场景真值（v3）；没写任何真值，先查清楚再导入"
            )
        return 0
    if a.cmd == "eval-fields":
        from tools.ocr_dataset import fields_eval

        md, js = fields_eval.main(
            dataset_root(a.dataset), [b.strip() for b in a.backend.split(",") if b.strip()], a.set, a.name
        )
        print(md.read_text(encoding="utf-8").split("## 按页面")[0])
        print(f"→ {md}\n→ {js}")
        return 0
    if a.cmd == "reask":
        from tools.ocr_dataset import label

        st = label.reask(dataset_root(a.dataset), label.RealReaders(a.llm or os.environ.get("HIRO_LLM") or "claude:opus"),
                         multi_sheet_only=not a.all, workers=a.workers)  # fmt: skip
        print(f"重问 {st['frames']} 帧，改判 {st['changed']} 个；剩余待定 {st['disputed']} 个（接着跑 adjudicate）")
        return 0
    if a.cmd == "redecide":
        from tools.ocr_dataset import label

        st = label.redecide(dataset_root(a.dataset))
        print(f"改了 {st['changed']} 个框；剩余待定 {st['disputed']} 个")
        return 0
    if a.cmd == "spot-check":
        from tools.ocr_dataset import label

        print(label.spot_check(dataset_root(a.dataset), n=a.n, seed=a.seed))
        return 0
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
            counter = None
            if a.fill_types:
                from tools.ocr_dataset import label

                counter = label.type_counter(label.RealReaders())
            sel = pick.select(
                cands, n=a.n or pick.N_CELL, n_dmm540=a.n_dmm540 or pick.N_CELL_DMM540, known=known, counter=counter
            )
        print(f"读到 {len(cands)} 帧（去重后），挑中 {len(sel)}")
        print(pick.summary(sel))
        if not a.dry_run and sel:
            skipped: list = []
            print(f"→ {pick.write_inbox(sel, root, a.contributor, notes, skipped=skipped)}")
            for fid, why in skipped:
                print(f"  跳过 {fid}：{why}")
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
