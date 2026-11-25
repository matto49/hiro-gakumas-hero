"""tools.ocr_dataset.pick：从 trace / 图片目录挑帧（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.1）。"""

import json

from tools.ocr_dataset import pick, schema

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def _img(seed: int, w=720, h=1280):
    """不同 seed 画面差异大（缩略图差远超 SCREEN_DIFF）；同 seed 完全相同。"""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    for _ in range(12):
        x, y = int(rng.integers(0, w - 200)), int(rng.integers(0, h - 200))
        img[y : y + 200, x : x + 200] = rng.integers(40, 255, 3)
    return img


def _trace(root, name, pages, seeds, run=None):
    d = root / name
    (d / "frames").mkdir(parents=True)
    with (d / "steps.jsonl").open("w", encoding="utf-8") as f:
        for i, (page, seed) in enumerate(zip(pages, seeds), 1):
            cv2.imwrite(str(d / f"frames/{i:05d}.jpg"), _img(seed))
            obs = {"seq": i, "page": {"name": page}, "screenshot": f"frames/{i:05d}.jpg"}
            f.write(json.dumps({"seq": i, "observation": obs}) + "\n")
    (d / "run.json").write_text(json.dumps(run or {"started": 1794653270.0}))
    return d


def test_guess_platform_prefers_run_json_then_name():
    dmm = {"platform": {"device": "dmm", "native": [540, 960]}}
    assert pick.guess_platform("x", dmm) == "dmm540"
    assert pick.guess_platform("x", {"platform": {"device": "dmm", "native": [1080, 1920]}}) == "dmm1080"
    assert pick.guess_platform("x", {"platform": {"device": "dmm", "native": None}}) == "dmm"
    assert pick.guess_platform("x", {"platform": {"device": "mumu", "native": [720, 1280]}}) == "mumu"
    assert pick.guess_platform("x", {"platform": {"device": "maatools", "native": [1179, 2556]}}) == "mac"
    # 老 trace 没有 platform：按运行名猜，老 DMM 一律 dmm（尺寸未知）
    assert pick.guess_platform("maindmm1m-20261110-205150", {"started": 1}) == "dmm"
    assert pick.guess_platform("hif-mac-p9", {}) == "mac"
    assert pick.guess_platform("20261114-184750", {}) == "other"


def test_collect_reads_pages_and_drops_repeated_frames(tmp_path):
    # 第 2 帧和第 1 帧一模一样（连续重复，去掉）；第 4 帧和第 1 帧相同但不连续：内容哈希相同，也去掉
    d = _trace(tmp_path, "maindmm1-20261110-192152", ["home", "home", "shop", "home"], [1, 1, 2, 1])
    cands = pick.collect([d])
    assert [(c.seq, c.page) for c in cands] == [(1, "home"), (3, "shop")]
    c = cands[0]
    assert c.run == "maindmm1-20261110-192152" and c.platform == "dmm"
    assert c.sha == schema.sha256(d / "frames/00001.jpg")


def test_collect_skips_landscape_and_blank_frames(tmp_path):
    d = _trace(tmp_path, "hif-mac-p1", ["home", "live", "loading"], [1, 2, 3])
    cv2.imwrite(str(d / "frames/00002.jpg"), _img(2, w=1280, h=720))  # 横屏 LIVE
    cv2.imwrite(str(d / "frames/00003.jpg"), np.zeros((1280, 720, 3), np.uint8))  # 全黑
    assert [c.seq for c in pick.collect([d])] == [1]


def test_collect_image_dir_uses_classifier(tmp_path):
    d = tmp_path / "fixtures"
    d.mkdir()
    cv2.imwrite(str(d / "maindmm1m-20261110-205150-00051.jpg"), _img(5))
    cv2.imwrite(str(d / "start-confirm-dmm1.png"), _img(6))
    cands = pick.collect([d], classify=lambda img: "action_select")
    got = sorted((c.run, c.seq, c.page, c.platform) for c in cands)
    assert got == [
        ("maindmm1m-20261110-205150", 51, "action_select", "dmm"),
        ("start-confirm-dmm1", 0, "action_select", "dmm"),
    ]


def test_platform_override(tmp_path):
    d = _trace(tmp_path, "hajime-20261118-001902", ["home"], [1])
    assert [c.platform for c in pick.collect([d], platform="dmm")] == ["dmm"]


def _cands(platform, page, n, run="r"):
    return [
        pick.Candidate(path=None, run=f"{run}{i % 2}", seq=i, platform=platform, page=page, sha=f"{platform}{page}{i}")
        for i in range(n)
    ]


def test_select_caps_each_cell_and_widens_dmm540():
    cands = _cands("dmm", "home", 10) + _cands("dmm540", "home", 10) + _cands("mac", "shop", 2)
    sel = pick.select(cands)
    count = {}
    for c in sel:
        count[(c.platform, c.page)] = count.get((c.platform, c.page), 0) + 1
    assert count == {("dmm", "home"): 3, ("dmm540", "home"): 5, ("mac", "shop"): 2}
    # 一格里尽量跨运行、跨时间分开取，不是取前 N 张
    assert {c.run for c in sel if c.platform == "dmm"} == {"r0", "r1"}


def test_select_fills_text_types_from_leftovers():
    cands = _cands("dmm", "home", 10)
    # 只有 seq 7 / 9 这两帧带 number 类型的框
    counter = lambda c: {"button": 2, **({"number": 20} if c.seq in (7, 9) else {})}  # noqa: E731
    sel = pick.select(cands, n=2, k=30, counter=counter)
    seqs = {c.seq for c in sel}
    assert {7, 9} <= seqs and len(sel) <= 2 + 2 + 15  # 补 number 的两帧；button 补到 30 为止


def test_select_skips_known_hashes():
    cands = _cands("dmm", "home", 3)
    assert [c.seq for c in pick.select(cands, known={cands[0].sha})] == [1, 2]


def test_write_inbox_copies_images_and_meta(tmp_path):
    d = _trace(tmp_path, "maindmm1-20261110-192152", ["home", "shop"], [1, 2],
               run={"started": 1794653270.0, "platform": {"device": "dmm", "screenshot": "windows_background", "ocr": "rapidocr", "native": [540, 960]}})  # fmt: skip
    sel = pick.collect([d])
    out = pick.write_inbox(
        sel, tmp_path / "ds", "matto", {"maindmm1-20261110-192152:2": "編 认成 綿"}, date="2026-11-23"
    )
    assert out == tmp_path / "ds" / "inbox" / "matto" / "2026-11-23"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["contributor"] == "matto" and len(meta["frames"]) == 2
    f0, f1 = meta["frames"]
    assert f0["id"] == "dmm540-maindmm1-20261110-192152-00001" and (out / f0["image"]).is_file()
    assert f0["sha256"] == schema.sha256(out / f0["image"])
    assert f0["source"] == {
        "run": "maindmm1-20261110-192152",
        "seq": 1,
        "contributor": "matto",
        "captured": "2026-11-14",
    }
    assert f0["platform"] == {
        "kind": "dmm540",
        "native": [540, 960],
        "stored": [720, 1280],
        "device": "windows_background",
        "ocr": "rapidocr",
    }
    assert f0["page"] == "home" and f0["note"] is None
    assert f1["note"] == "編 认成 綿"


def test_write_inbox_copies_native_png_when_present(tmp_path):
    d = _trace(tmp_path, "maindmm1-20261110-192152", ["home"], [1])
    cv2.imwrite(str(d / "frames/00001.native.png"), _img(1, w=540, h=960))
    out = pick.write_inbox(pick.collect([d]), tmp_path / "ds", "matto", {}, date="2026-11-23")
    f0 = json.loads((out / "meta.json").read_text(encoding="utf-8"))["frames"][0]
    assert f0["native_image"] == f0["id"] + ".native.png" and (out / f0["native_image"]).is_file()
    assert f0["platform"]["native"] == [540, 960]


def test_collect_skips_loading_pages(tmp_path):
    d = _trace(tmp_path, "hif-mac-p1", ["home", "LOADING", "loading"], [1, 2, 3])
    assert [c.seq for c in pick.collect([d])] == [1]


def test_guess_platform_checks_mumu_and_mac_before_dmm():
    assert pick.guess_platform("mumu-windows-20261120-101010", {}) == "mumu"
    assert pick.guess_platform("hif-mac-win2", {}) == "mac"


def test_platform_override_does_not_beat_recorded_device(tmp_path):
    d = _trace(
        tmp_path,
        "z1-20261121-000000",
        ["home"],
        [1],
        run={"started": 1, "platform": {"device": "mumu", "native": [720, 1280]}},
    )
    assert [c.platform for c in pick.collect([d], platform="dmm")] == ["mumu"]


def test_skipped_loading_page_does_not_make_next_frame_a_duplicate(tmp_path):
    """加载页本身不是纯色时，以前先记进去重状态再因 loading 跳过：紧跟着的相似有效帧被当成重复丢了（PR #104）。"""
    d = _trace(tmp_path, "hif-mac-p1", ["LOADING", "home"], [1, 1])  # 同一画面，第一张被页面过滤
    assert [c.seq for c in pick.collect([d])] == [2]


def test_write_inbox_skips_same_id_with_different_content_from_another_day(tmp_path):
    """同一 platform / run / seq 在别的日子的 inbox 或已标注的 core 里有了、内容不同：不能写进今天的 inbox
    （它会被当成已标注，从 meta.json 里悄悄删掉，PR #104）。要跳过并报告，不是静默丢。"""
    d1 = _trace(tmp_path, "maindmm1-20261110-192152", ["home"], [1])
    d2 = _trace(tmp_path, "other", ["home"], [2], run=None)
    sel1 = pick.collect([d1])
    ds = tmp_path / "ds"
    pick.write_inbox(sel1, ds, "matto", {}, date="2026-11-23")
    # 另一天又采到同 run / seq、画面不同的帧
    c2 = pick.collect([d2])[0]
    c2.run, c2.seq, c2.platform = sel1[0].run, sel1[0].seq, sel1[0].platform
    skipped: list = []
    out = pick.write_inbox([c2], ds, "matto", {}, date="2026-11-24", skipped=skipped)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["frames"] == [] and len(skipped) == 1 and skipped[0][0] == schema.frame_id(c2.platform, c2.run, c2.seq)
