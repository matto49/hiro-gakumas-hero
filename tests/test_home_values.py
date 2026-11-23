"""首页顶栏读数：PLv、AP（现有 / 上限）、ジュエル。截图样例在 tests/fixtures/home（不随仓库发布）。"""

from pathlib import Path

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures" / "home"
CASES = {
    "home-now.png": {"plv": 65, "ap": 85, "ap_max": 100, "jewel": 15220},
    "hj-home2.png": {"plv": 65, "ap": 88, "ap_max": 100, "jewel": 15220},
    "hj-home3.png": {"plv": 65, "ap": 80, "ap_max": 100, "jewel": 15220},
}


@pytest.mark.fixtures
@pytest.mark.parametrize("name", sorted(CASES))
def test_reads_home_top_bar(name):
    from hiro.vision.home_values import read_home_values

    got = read_home_values(cv2.imread(str(FIX / name)))
    assert {k: got[k] for k in CASES[name]} == CASES[name]


@pytest.mark.fixtures
def test_popup_over_home_is_not_read():
    from hiro.vision.home_values import read_home_values

    assert read_home_values(cv2.imread(str(FIX / "home-after-link.png"))) is None
