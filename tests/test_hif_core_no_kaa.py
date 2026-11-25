"""去 KAA 第 3 批：H.I.F 的培育逻辑只剩 HifCore（新主循环的处理器调它），KAA 的策略类壳 HiroHifStrategy 删了，
hiro.produce.hif 里连类型注解用的 KAA 导入都没有。"""

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.synthetic, pytest.mark.play_free]

SRC = Path(__file__).resolve().parents[1] / "src" / "hiro" / "produce" / "hif.py"


def test_hif_source_has_no_kaa_import():
    src = SRC.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import) (kaa|kotonebot)\b", src, re.M)
    assert "def _kaa_strategy" not in src and "class HiroHifStrategy" not in src and "def __getattr__" not in src


def test_hif_core_is_the_only_strategy():
    spec = importlib.util.find_spec("hiro.produce.hif")
    assert spec is not None
    import hiro.produce.hif as hif

    assert hasattr(hif, "HifCore")
    with pytest.raises(AttributeError):
        hif.HiroHifStrategy  # noqa: B018
