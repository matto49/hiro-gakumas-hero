"""vendor/gakumas-player/scripts/bump_master.py：不联网，假的上游清单，在临时目录里改文件。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "vendor" / "gakumas-player" / "scripts" / "bump_master.py"
OLD, NEW = "a" * 40, "b" * 40


def _load():
    spec = importlib.util.spec_from_file_location("bump_master", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(**sha: str) -> dict:
    return {"truncated": False, "tree": [{"type": "blob", "path": n, "sha": s, "size": 1} for n, s in sha.items()]
            + [{"type": "tree", "path": "sub", "sha": "x"}, {"type": "blob", "path": "README.md", "sha": "y", "size": 1}]}  # fmt: skip


@pytest.fixture
def repo(tmp_path, monkeypatch):
    b = _load()
    root = tmp_path / "vendor" / "gakumas-player"
    (root / "scripts").mkdir(parents=True)
    (root / "data" / "schedules").mkdir(parents=True)
    (root / "data" / "master" / OLD).mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    fetch = root / "scripts" / "fetch_master.py"
    fetch.write_text(f'X = 1\nDEFAULT_COMMIT = "{OLD}"\nREPO = "r"\n', encoding="utf-8")
    schedules = tuple(root / "data" / "schedules" / n for n in ("hif.json", "hajime_nia.json"))
    for p in schedules:
        p.write_text(json.dumps({"schema_version": 1, "master_commit": OLD, "modes": {}}, indent=2), encoding="utf-8")
    doc = tmp_path / "docs" / "hajime-nia-mechanics.md"
    doc.write_text(f"快照是 gakumasu-diff `{OLD[:7]}`。\n", encoding="utf-8")
    monkeypatch.setattr(b, "ROOT", root)
    monkeypatch.setattr(b, "FETCH", fetch)
    monkeypatch.setattr(b, "SCHEDULES", schedules)
    monkeypatch.setattr(b, "DOCS", (doc,))
    return b, root, fetch, schedules, doc


def test_source_files_only_root_yaml_sorted():
    b = _load()
    files = b.source_files(NEW, _tree(**{"Zeta.yaml": "2", "Alpha.yaml": "1"}))
    assert [f["name"] for f in files] == ["Alpha.yaml", "Zeta.yaml"]
    assert files[0] == {"git_blob_sha": "1", "name": "Alpha.yaml", "size": 1,
                        "url": f"https://raw.githubusercontent.com/vertesan/gakumasu-diff/{NEW}/Alpha.yaml"}  # fmt: skip


def test_bump_updates_pin_schedules_docs_and_manifest(repo):
    b, root, fetch, schedules, doc = repo
    same = dict.fromkeys(b.SCHEDULE_TABLES, "s")
    old = b.source_files(OLD, _tree(**same, **{"IdolCard.yaml": "1"}))
    new = b.source_files(NEW, _tree(**same, **{"IdolCard.yaml": "2", "NewTable.yaml": "3"}))
    (root / "data" / "master" / OLD / "source-files.json").write_text(b.dump(old), encoding="utf-8")

    info = b.bump(NEW, OLD, new, old)

    assert f'DEFAULT_COMMIT = "{NEW}"' in fetch.read_text(encoding="utf-8")
    assert all(json.loads(p.read_text(encoding="utf-8"))["master_commit"] == NEW for p in schedules)
    assert f"`{NEW[:7]}`" in doc.read_text(encoding="utf-8")
    assert json.loads((root / "data" / "master" / NEW / "source-files.json").read_text(encoding="utf-8")) == new
    assert not (root / "data" / "master" / OLD / "source-files.json").exists()
    assert info["added"] == ["NewTable.yaml"] and info["changed"] == ["IdolCard.yaml"] and not info["schedule_changed"]
    assert "已跟着改" in b.summary(info)


def test_bump_leaves_schedules_when_their_tables_change(repo):
    b, _, fetch, schedules, _ = repo
    old = b.source_files(OLD, _tree(**dict.fromkeys(b.SCHEDULE_TABLES, "s")))
    new = b.source_files(NEW, _tree(**{n: ("t" if n == "Produce.yaml" else "s") for n in b.SCHEDULE_TABLES}))

    info = b.bump(NEW, OLD, new, old)

    assert f'DEFAULT_COMMIT = "{NEW}"' in fetch.read_text(encoding="utf-8")
    assert all(json.loads(p.read_text(encoding="utf-8"))["master_commit"] == OLD for p in schedules)
    assert info["schedule_changed"] == ["Produce.yaml"]
    assert "日程没跟着升" in b.summary(info)
