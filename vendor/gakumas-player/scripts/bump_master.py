"""把模拟器主数据（vertesan/gakumasu-diff）升到新提交：改钉版本，不下载数据。

    python vendor/gakumas-player/scripts/bump_master.py [commit] [--summary 文件]

不给 commit 时取上游默认分支的最新提交；和现在钉的一样就什么都不改（退出码 0，打印「已是最新」）。
升级时一起改这几处（11-25 手动升 d31baf6 → edc305a 的做法）：

- data/master/<新提交>/source-files.json：上游根目录的 *.yaml 清单（GitHub git/trees 接口），删掉旧提交的那份；
- scripts/fetch_master.py 的 DEFAULT_COMMIT；
- data/schedules/hif.json、hajime_nia.json 的 master_commit：引擎发现日程和主数据版本对不上会直接 Unsupported。
  日程内嵌 / 引用的是 SCHEDULE_TABLES 这几张表；它们的 Git blob 新旧一致才改，变了就不改日程、退出码 3，要人核对后手动改；
- docs/hajime-nia-mechanics.md 里写的快照版本。

--summary 把这次升级的说明（新旧提交、增改了哪些表、日程是否跟着改）写成 Markdown，给自动开 PR 用。
设了 GITHUB_TOKEN 时带上它调 GitHub 接口（CI 上避开匿名限流）。只用标准库。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # vendor/gakumas-player
REPO_ROOT = ROOT.parents[1]
UPSTREAM = "vertesan/gakumasu-diff"
FETCH = ROOT / "scripts" / "fetch_master.py"
SCHEDULES = (ROOT / "data" / "schedules" / "hif.json", ROOT / "data" / "schedules" / "hajime_nia.json")
SCHEDULE_TABLES = ("Produce.yaml", "ProduceSetting.yaml", "ProduceStepOpenLesson.yaml")
DOCS = (REPO_ROOT / "docs" / "hajime-nia-mechanics.md",)
_COMMIT = re.compile(r'^DEFAULT_COMMIT = "([0-9a-f]{40})"$', re.M)


def _api(path: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com/{path}", headers={"Accept": "application/vnd.github+json", "User-Agent": "hiro-bump-master"})
    if token := os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def latest_commit() -> str:
    return _api(f"repos/{UPSTREAM}/commits/HEAD")["sha"]


def pinned_commit() -> str:
    m = _COMMIT.search(FETCH.read_text(encoding="utf-8"))
    if m is None:
        raise SystemExit(f"{FETCH} 里找不到 DEFAULT_COMMIT")
    return m.group(1)


def source_files(commit: str, tree: dict | None = None) -> list[dict]:
    """上游这个提交根目录的 *.yaml，格式和 fetch_master.py 读的 source-files.json 一致（按文件名排序）。"""
    tree = tree if tree is not None else _api(f"repos/{UPSTREAM}/git/trees/{commit}")
    if tree.get("truncated"):
        raise SystemExit("git/trees 结果被截断，清单不完整")
    out = [
        {
            "git_blob_sha": e["sha"],
            "name": e["path"],
            "size": e["size"],
            "url": f"https://raw.githubusercontent.com/{UPSTREAM}/{commit}/{e['path']}",
        }
        for e in tree["tree"]
        if e["type"] == "blob" and re.fullmatch(r"[A-Za-z0-9_]+\.yaml", e["path"])
    ]
    return sorted(out, key=lambda x: x["name"])


def dump(files: list[dict]) -> str:
    return json.dumps(files, indent=1) + "\n"


def _snap(commit: str) -> Path:
    return ROOT / "data" / "master" / commit / "source-files.json"


def bump(new: str, old: str, new_files: list[dict], old_files: list[dict]) -> dict:
    """改文件，返回说明用的数据。"""
    old_sha = {f["name"]: f["git_blob_sha"] for f in old_files}
    new_sha = {f["name"]: f["git_blob_sha"] for f in new_files}
    added = sorted(set(new_sha) - set(old_sha))
    removed = sorted(set(old_sha) - set(new_sha))
    changed = sorted(n for n in set(new_sha) & set(old_sha) if new_sha[n] != old_sha[n])
    schedule_changed = [n for n in SCHEDULE_TABLES if old_sha.get(n) != new_sha.get(n)]

    _snap(new).parent.mkdir(parents=True, exist_ok=True)
    _snap(new).write_text(dump(new_files), encoding="utf-8")
    if _snap(old).exists():
        _snap(old).unlink()
    FETCH.write_text(_COMMIT.sub(f'DEFAULT_COMMIT = "{new}"', FETCH.read_text(encoding="utf-8")), encoding="utf-8")
    if not schedule_changed:
        for p in SCHEDULES:
            text = p.read_text(encoding="utf-8")
            if f'"master_commit": "{old}"' not in text:
                raise SystemExit(f"{p.name} 的 master_commit 不是 {old}，先手动对齐")
            p.write_text(text.replace(f'"master_commit": "{old}"', f'"master_commit": "{new}"'), encoding="utf-8")
    for p in DOCS:
        if p.exists():
            p.write_text(p.read_text(encoding="utf-8").replace(f"gakumasu-diff `{old[:7]}`", f"gakumasu-diff `{new[:7]}`"), encoding="utf-8")
    return {"old": old, "new": new, "added": added, "removed": removed, "changed": changed, "schedule_changed": schedule_changed}


def summary(info: dict) -> str:
    old, new = info["old"], info["new"]
    lines = [
        f"上游 [{UPSTREAM}](https://github.com/{UPSTREAM}) 有新提交，模拟器主数据从 `{old[:7]}` 升到 `{new[:7]}`"
        f"（[对比](https://github.com/{UPSTREAM}/compare/{old}...{new})）。",
        "",
        f"- 新增表 {len(info['added'])} 张、改动 {len(info['changed'])} 张、删除 {len(info['removed'])} 张",
    ]
    for label, key in (("新增", "added"), ("删除", "removed")):
        if info[key]:
            lines.append(f"- {label}：" + "、".join(f"`{n.removesuffix('.yaml')}`" for n in info[key]))
    if info["changed"]:
        shown = info["changed"][:40]
        more = f" 等 {len(info['changed'])} 张" if len(info["changed"]) > len(shown) else ""
        lines.append("- 改动：" + "、".join(f"`{n.removesuffix('.yaml')}`" for n in shown) + more)
    if info["schedule_changed"]:
        lines += [
            "",
            "**日程没跟着升**：日程依赖的 " + "、".join(f"`{n}`" for n in info["schedule_changed"])
            + " 变了。核对 `data/schedules/hif.json` 内嵌的 Produce / ProduceSetting 行和开课奖励，"
            "确认无误后把两份日程的 `master_commit` 改成新提交，否则引擎会报 Schedule/master version mismatch。",
        ]
    else:
        lines.append("- 日程依赖的 " + "、".join(f"`{n.removesuffix('.yaml')}`" for n in SCHEDULE_TABLES) + " 没变，两份日程的 `master_commit` 已跟着改")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    out = None
    if "--summary" in argv:
        i = argv.index("--summary")
        out = Path(argv[i + 1])
        args = [a for a in args if a != argv[i + 1]]
    old = pinned_commit()
    new = args[0] if args else latest_commit()
    if new == old:
        print(f"已是最新：{old}")
        return 0
    old_files = source_files(old)
    # 末尾换行不比：d31baf6 那份是手写的、没有末尾换行
    if _snap(old).exists() and _snap(old).read_text(encoding="utf-8").rstrip("\n") != dump(old_files).rstrip("\n"):
        raise SystemExit(f"重新生成的 {old[:7]} 清单和仓库里的不一致：生成格式变了，先修本脚本")
    info = bump(new, old, source_files(new), old_files)
    if out is not None:
        out.write_text(summary(info), encoding="utf-8")
    print(f"已升级：{old[:7]} → {new[:7]}；新增 {len(info['added'])}、改动 {len(info['changed'])}、删除 {len(info['removed'])} 张表")
    if info["schedule_changed"]:
        print("日程依赖的表变了，日程没改：" + "、".join(info["schedule_changed"]), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
