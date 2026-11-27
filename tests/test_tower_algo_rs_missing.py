"""N45（G3 第 3 轮 11-26 Windows rc.6）：Rust 后端没编译成时，sys.path 上的 vendor/gakumas-player 让 import 拿到 Rust 源码目录
（命名空间包，没有 __file__），原来报成「是旧版本，没有 search / plan_many」，其实是没装。"""

import sys
import types

import pytest


def test_namespace_package_reported_as_not_installed(monkeypatch):
    from hiro.search import tower_algo

    ns = types.ModuleType("gakumas_exam_rs")
    ns.__path__ = ["vendor/gakumas-player/gakumas_exam_rs"]  # 命名空间包：有 __path__，没有 __file__
    monkeypatch.setitem(sys.modules, "gakumas_exam_rs", ns)
    cls = next(v for v in vars(tower_algo).values() if isinstance(v, type) and "sees_seed" in vars(v))
    with pytest.raises(RuntimeError, match="没装 Rust 后端"):
        cls()


def test_old_wheel_still_reported_as_old(monkeypatch):
    from hiro.search import tower_algo

    old = types.ModuleType("gakumas_exam_rs")
    old.__file__ = "site-packages/gakumas_exam_rs/__init__.py"
    monkeypatch.setitem(sys.modules, "gakumas_exam_rs", old)
    cls = next(v for v in vars(tower_algo).values() if isinstance(v, type) and "sees_seed" in vars(v))
    with pytest.raises(RuntimeError, match="是旧版本"):
        cls()
