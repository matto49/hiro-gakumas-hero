"""下载识别用的游戏原图到 resources/game_data：技能卡 skill_cards/、偶像卡 idol_cards/、P 饮料 drinks/，校验 SHA256。

usage: python tools/assets/game_images.py [--out resources/game_data]

原来由 KAA 的游戏数据更新器从 kotonebot/kaa-game-data 下载（kaa.game_data.updater）。识别一直按 data-b9577da 这一版调，
这一版上游已经下架，新版是重新导出的图，像素有细微差别，换卡对话框里有帧会认错（2026-11-25 实测 edc305a）。
所以去 KAA 后由本仓库 GitHub Release game-images-b9577da 托管这份逐字节相同的副本（每个目录一个 zip，文件在包的根上，
见 Release 里的 NOTICE.txt），版本和 SHA256 钉在下面；换版本要先用识别回归验过。
主数据（game.db）不从这里取，由 python -m hiro.state.master_db 从模拟器主数据生成。
已经是这个版本、三个目录都在就不重复下载。HIRO_KAA_GAME_DATA 指向别的目录时识别从那里读，这里的 --out 也要指过去。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "https://github.com/matto49/hiro-gakumas-hero/releases/download"
TAG = "game-images-b9577da"  # kaa-game-data data-b9577da85ec296d192d7a659422e85f07f74242a 的副本
FILES = {  # 目录名（= 发布包名 <名>.zip，文件都在包的根上）→ SHA256
    "skill_cards": "8aa818c2ea809bb8813cf9c50a496244d132274e9ddee2603f157acba4a7de8f",
    "idol_cards": "3f0458baeae217309ba75726e14a3f123ac78a6670a91190501bf7eb9f2a393d",
    "drinks": "ef0a28b9894078952c75276d3d0c8b73ef214ac2e8a5d8541ddf652176893c22",
}
VERSION_FILE = "images-version.txt"
KAA_VERSION = "b9577da85ec296d192d7a659422e85f07f74242a"  # 原来 KAA 更新器下的同一版：它在目录里写 version.txt


def url(cat: str) -> str:
    return f"{REPO}/{TAG}/{cat}.zip"


def _current(out: Path) -> bool:
    if not all((out / c).is_dir() and any((out / c).iterdir()) for c in FILES):
        return False
    v, kaa = out / VERSION_FILE, out / "version.txt"
    if v.is_file() and v.read_text(encoding="utf-8").strip() == TAG:
        return True
    if kaa.is_file() and kaa.read_text(encoding="utf-8").strip() == KAA_VERSION:  # 以前装的：图就是这一版，不重下
        v.write_text(TAG + "\n", encoding="utf-8")
        return True
    return False


def fetch(out: Path) -> list[str]:
    """把三类图下到 out/<类>/，返回这次下了哪些（按名字排序）；已是 TAG 版本返回空。

    先全部下完、校验通过再替换目录：任何一个校验不对就退出，已有的图不动。"""
    out = Path(out)
    if _current(out):
        return []
    blobs = {}
    for cat, sha in sorted(FILES.items()):
        print("下载", url(cat), flush=True)
        with urllib.request.urlopen(url(cat), timeout=600) as r:
            data = r.read()
        if (h := hashlib.sha256(data).hexdigest()) != sha:
            sys.exit(f"{cat}.zip 校验不对：{h}（应为 {sha}）")
        blobs[cat] = data
    out.mkdir(parents=True, exist_ok=True)
    for cat, data in blobs.items():
        tmp = out / f"{cat}.part"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                name = Path(info.filename).name  # 包里只有根上的图片；去掉路径防止解压到目录外
                if info.is_dir() or not name:
                    continue
                (tmp / name).write_bytes(z.read(info))
        shutil.rmtree(out / cat, ignore_errors=True)
        tmp.replace(out / cat)
    (out / VERSION_FILE).write_text(TAG + "\n", encoding="utf-8")
    return sorted(blobs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "resources" / "game_data"))
    args = ap.parse_args()
    got = fetch(Path(args.out))
    print("已下载：" + "、".join(got) if got else f"游戏图已是 {TAG}")


if __name__ == "__main__":
    main()
