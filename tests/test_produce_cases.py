"""培育决策器的合成用例（examples/cases/produce.json，局面手工构造、按社区攻略判对错），只用规则、不调 LLM。

需要主数据库（python -m hiro.state.master_db 生成的，和原来 KAA 的 game.db 同结构）：环境变量 HIRO_MASTER_DB 指定路径；没有就跳过（CI 默认跳过）。
"""

import json
import os
import sqlite3
from pathlib import Path

from hiro import plugins
from hiro.brain.deciders import ProduceDeciders, action_key
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Decision, GameState

import pytest

CASES = json.loads((Path(__file__).parents[1] / "examples/cases/produce.json").read_text(encoding="utf-8"))


def _usable(p: str | None) -> str | None:
    """文件存在且真有主数据表（空库 / 别的库不算；只读打开，不会顺手建出空文件）。"""
    if not p or not Path(p).is_file():
        return None
    try:
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as db:
            ok = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='IdolCard'").fetchone()
    except sqlite3.Error:
        return None
    return p if ok else None


def _master_path() -> str | None:
    return _usable(os.environ.get("HIRO_MASTER_DB"))


class _MasterOnly:
    """只给主数据的素材源（其余都返回 None）。"""

    def __init__(self, path):
        self.path = path

    def master_db(self):
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture(autouse=True)
def _master(monkeypatch):
    path = _master_path()
    if path is None:
        pytest.skip("没有主数据（设置 HIRO_MASTER_DB 为主数据库的路径，python -m hiro.state.master_db 生成）")
    try:  # 有识别层的默认素材源（带日程）就用它：它的主数据按 HIRO_MASTER_DB 读同一份库
        from hiro.defaults.assets import PublicAssetSource  # noqa: F401

        plugins._registered.pop("asset_source", None)
    except ImportError:
        monkeypatch.setitem(plugins._registered, "asset_source", lambda: _MasterOnly(path))
    plugins._cache.pop("asset_source", None)
    import hiro.state.catalog as cat
    import hiro.state.context as ctx

    for local in (cat._LOCAL, ctx._LOCAL):
        local.__dict__.clear()
    yield
    plugins._cache.pop("asset_source", None)


def _has_schedule() -> bool:
    src = plugins.get("asset_source")
    return bool(src is not None and src.schedule("produce-007"))


NEEDS_SCHEDULE = {"ACTION_SELECT"}  # 选行动要日程（AssetSource.schedule）；只给主数据的素材源没有


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_rules_only(case):
    if case["decider"] in NEEDS_SCHEDULE and not _has_schedule():
        pytest.skip("需要日程数据（AssetSource.schedule），默认素材源还没有")
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(case["decider"], GameState.from_dict(case["state"]))
    assert isinstance(d, Decision) and not d.problems()
    key = action_key(d.action)
    assert key not in case.get("forbid", []), f"选了禁止项 {key}：{d.reason}"
    if "accept" in case:
        assert key in case["accept"], f"选了 {key}，可接受的是 {case['accept']}：{d.reason}"
