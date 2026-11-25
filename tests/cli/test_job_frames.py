"""驾驶舱的实时画面：初 / N.I.A 的帧存在任务目录 frames/（frames.jsonl 一行一张），步骤事件出来之前（开局三步）也要能看到画面。
11-23 Mac 手毬：开局第 2 步已经存了 78 张，驾驶舱还是「等第一张画面」。"""

import json

from hiro.cli.jobs import read_new_frames


def test_read_new_frames_returns_latest_complete_line(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    for name in ("00001-a.jpg", "00002-b.jpg"):
        (d / name).write_bytes(b"x")
    idx = d / "frames.jsonl"
    idx.write_text(
        json.dumps({"seq": 1, "file": "00001-a.jpg", "page": "opening"})
        + "\n"
        + json.dumps({"seq": 2, "file": "00002-b.jpg", "page": "tap-1-2"})
        + "\n"
        + '{"seq": 3, "file": "00003',  # 半行：下次再读
        encoding="utf-8",
    )
    pos, rec = read_new_frames(idx, 0)
    assert rec["file"] == "00002-b.jpg" and pos < idx.stat().st_size
    pos2, rec2 = read_new_frames(idx, pos)
    assert rec2 is None and pos2 == pos  # 没有新的完整行


def test_read_new_frames_missing_file(tmp_path):
    assert read_new_frames(tmp_path / "nope.jsonl", 0) == (0, None)
