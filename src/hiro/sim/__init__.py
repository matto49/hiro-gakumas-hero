# 来源：digital-hiro src/hiro/sim/__init__.py（develop 864e037）
"""离线对局环境：用 gakumas-player 的考试引擎 gakumas_exam（默认 Rust 后端 gakumas_exam_rs，没编译时用 Python 版）
和培育层公式 gakumas_produce 驱动决策层（社区公开 master）。

引擎随仓库在 vendor/gakumas-player（主数据另行下载：python vendor/gakumas-player/scripts/fetch_master.py，setup 脚本会做）；
环境变量 GAKUMAS_PLAYER 可指向别处的引擎目录。
"""

import os
import sys
from pathlib import Path

_CANDIDATES = [
    os.environ.get("GAKUMAS_PLAYER"),
    str(Path(__file__).resolve().parents[3] / "vendor" / "gakumas-player"),
]


def engine_root() -> Path:
    for c in _CANDIDATES:
        root = Path(c).expanduser() if c else None
        if root and (root / "gakumas_exam" / "battle.py").exists() and (root / "gakumas_produce").is_dir():
            return root
    raise RuntimeError("找不到考试引擎 gakumas_exam / gakumas_produce：应在 vendor/gakumas-player，或设置 GAKUMAS_PLAYER")


def master_ready() -> bool:
    """模拟器主数据（catalog.sqlite）已构建。没有时模拟相关决策退回社区规则。"""
    cur = ROOT / "data" / "master" / "current.json"
    if not cur.exists():
        return False
    import json

    return (ROOT / "data" / "master" / json.loads(cur.read_text())["database"]).exists()


ROOT = engine_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "simulator" / "fixtures"
