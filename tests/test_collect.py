"""hiro.collect：采集循环走 hiro.tool_device + hiro.vision.classify，不要 kotonebot context / KAA 判页（用假设备跑一小段）。"""

import json
import socket

from hiro import collect

import numpy as np


class FakeDevice:
    name = "fake"

    def __init__(self):
        self.n = 0

    def screenshot(self):
        self.n += 1
        img = np.zeros((1280, 720, 3), np.uint8)
        img[:, : (self.n * 97) % 720] = 255  # 每张都不一样：触发保存
        return img


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_collect_loop_saves_frames_with_own_classifier(tmp_path, monkeypatch):
    dev = FakeDevice()
    monkeypatch.setattr(collect, "open_tool_device", lambda: dev)
    monkeypatch.setattr(collect, "check_stale", lambda: None)
    out = tmp_path / "ds"
    monkeypatch.setattr("sys.argv", ["collect", "--minutes", "0.01", "--interval", "0.05", "--min-gap", "0",
                                     "--threshold", "0", "--dir", str(out), "--port", str(_free_port())])  # fmt: skip
    collect.main()
    rows = [json.loads(line) for line in (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and dev.n >= len(rows)
    for r in rows:
        assert r["scene"] and r["page_source"] and r["classify_ms"] >= 0 and (out / r["file"]).exists()


def test_select_card_fields_are_skipped_not_read_through_kaa():
    assert "SELECT_CARD" in collect.NEEDS_KAA
