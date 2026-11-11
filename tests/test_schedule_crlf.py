"""HIF 日程的社区表按 sha256 核对来源：Windows 上 Git（core.autocrlf=true）把它检出成 CRLF 时也要认得
（Windows 上全新 git clone 的检出跑 DMM 实机，选行动预演报
「Calendar source changed; rebuild explicitly」，3 次 Wait 后停机）。"""

import shutil

import pytest

import hiro.sim  # noqa: F401  把 vendor/gakumas-player 放进 sys.path


@pytest.mark.sim_master
def test_schedule_accepts_crlf_checkout(tmp_path, monkeypatch):
    import gakumas_produce.schedule as sched

    root = sched.ROOT
    fake = tmp_path / "gp"
    (fake / "data" / "schedules").mkdir(parents=True)
    (fake / "data" / "community").mkdir(parents=True)
    shutil.copy(root / "data/schedules/hif.json", fake / "data/schedules/hif.json")
    lf = (root / "data/community/hif-wiki-tables.json").read_bytes()
    assert b"\r\n" not in lf
    (fake / "data/community/hif-wiki-tables.json").write_bytes(lf.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(sched, "ROOT", fake)
    s = sched.HIFSchedule()
    assert s.stage(1)


def test_gitattributes_keeps_engine_data_bytes():
    """vendor/gakumas-player/data 下的数据按二进制检出（不转换行尾），哈希、主数据快照和 Mac 一致。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".gitattributes").read_text(encoding="utf-8")
    assert any(line.split()[:2] == ["vendor/gakumas-player/data/**", "-text"] for line in text.splitlines() if line.strip())
