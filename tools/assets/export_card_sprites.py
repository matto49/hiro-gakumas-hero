"""从你自己安装的游戏客户端导出技能卡小图标要用的界面精灵图（13 张），放进使用者素材目录 sprites/。

随仓库的素材包 assets/pack/sprites 已经带了这些图，一般不用跑；素材包关掉了、或游戏更新后图标样式变了时，用这个工具从你自己的客户端导出。
之后运行 `python -m hiro.vision.card_icon_render build` 生成渲染库（见 docs/formation-plan.md）。

    pip install UnityPy
    python tools/assets/export_card_sprites.py [--client <客户端 Data/data.unity3d>] [--out <素材目录>/sprites]

默认客户端路径是 macOS PlayCover 的安装位置；默认输出 $HIRO_USER_ASSETS/sprites（未设时 resources/user_assets/sprites）。
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from hiro.vision.card_icon_render import SPRITES  # noqa: E402

DEFAULT_CLIENT = (
    Path.home()
    / "Library/Containers/io.playcover.PlayCover/Applications/jp.co.bandainamcoent.BNEI0421.app/Data/data.unity3d"
)
UNITY_VERSION = "6000.0.77f1"  # 客户端资源里的版本号被抹掉时用它


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--client", default=str(DEFAULT_CLIENT))
    ap.add_argument(
        "--out", default=str(Path(os.environ.get("HIRO_USER_ASSETS") or "resources/user_assets") / "sprites")
    )
    args = ap.parse_args(argv)
    try:
        import UnityPy
    except ImportError:
        print("需要 UnityPy：pip install UnityPy", file=sys.stderr)
        return 2
    if not Path(args.client).is_file():
        print(f"找不到客户端文件 {args.client}（用 --client 指定 Data/data.unity3d）", file=sys.stderr)
        return 2
    warnings.filterwarnings("ignore")
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    want, done = set(SPRITES), set()
    for obj in UnityPy.load(args.client).objects:
        if obj.type.name != "Sprite" or obj.peek_name() not in want or obj.peek_name() in done:
            continue
        sprite = obj.read()
        sprite.image.save(out / f"{sprite.m_Name}.png")
        done.add(sprite.m_Name)
    missing = sorted(want - done)
    print(f"导出 {len(done)} 张 → {out}" + (f"；客户端里没找到 {missing}" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
