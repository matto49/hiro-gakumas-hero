#!/usr/bin/env bash
# 一条命令装好实机游玩环境（macOS + PlayCover；Windows 用 scripts/setup.ps1）：KAA 子模块 → .venv（Python 3.10）→ 安装 → 生成 KAA 的 R.py
# → 下载 KAA 公开游戏数据（主数据 game.db + 卡图，resources/game_data）→ 模拟器主数据 → 自检。
#
#   scripts/setup.sh            # 第一次装 / 更新后重跑（已有的步骤会跳过）
#   scripts/setup.sh --regen    # 强制重新生成 kaa/tasks/R.py（KAA 子模块换版本后）
#   scripts/setup.sh --no-data  # 不下载游戏数据（已用 HIRO_KAA_GAME_DATA 指向别处时）
#
# 只装开发 / 测试（不玩实机）不需要这个脚本：uv venv && uv pip install -e ".[dev]"。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
REGEN=0
DATA=1
for a in "$@"; do
  case "$a" in
    --regen) REGEN=1 ;;
    --no-data) DATA=0 ;;
    -h|--help) sed -n 2,10p "$0"; exit 0 ;;
    *) echo "未知参数：$a（可用 --regen / --no-data）" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

command -v uv >/dev/null || { echo "需要 uv（https://docs.astral.sh/uv/getting-started/installation/）" >&2; exit 1; }
command -v git >/dev/null || { echo "需要 git" >&2; exit 1; }

step "1/7 KAA 子模块 vendor/kaa"
git submodule update --init vendor/kaa

step "2/7 虚拟环境 .venv（KAA 要求 Python 3.10）"
venv_py() { if [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; else echo .venv/bin/python; fi; }  # Windows / 其他
if [ ! -x "$(venv_py)" ]; then
  uv venv --python 3.10 .venv
else
  echo "已有 .venv（$("$(venv_py)" --version)）"
fi
PY="$ROOT/$(venv_py)"

step "3/7 安装本仓库与 KAA（.[dev,play]）"
uv pip install --python "$PY" -e ".[dev,play]"

step "4/7 生成 KAA 的 kaa/tasks/R.py（KAA 的生成文件，子模块里没有）"
if [ "$REGEN" = 1 ] || [ ! -f vendor/kaa/kaa/tasks/R.py ]; then
  # 要在 vendor/kaa 下跑（脚本用相对路径）；macOS 上先 import hiro（hiro.compat 补 ctypes.WinDLL，否则 KAA 导入失败）
  (cd vendor/kaa && "$PY" -c "import hiro, runpy, sys; sys.argv = ['make_resources.py']; runpy.run_path('tools/make_resources.py', run_name='__main__')" > "$ROOT/.setup-make-resources.log" 2>&1) \
    || { echo "生成 R.py 失败，见 .setup-make-resources.log" >&2; exit 1; }
  echo "已生成 vendor/kaa/kaa/tasks/R.py（日志 .setup-make-resources.log）"
else
  echo "已有 vendor/kaa/kaa/tasks/R.py（KAA 换版本后用 --regen 重新生成）"
fi

step "5/7 KAA 公开游戏数据（game.db + 卡图 → resources/game_data，首次约 2–3 分钟，含卡图索引）"
if [ "$DATA" = 0 ]; then
  echo "跳过（--no-data）"
else
  "$PY" - <<'PY'
import hiro  # noqa: F401  macOS 兼容层
from kaa.game_data.updater import GameDataUpdater, apply_pending, has_usable_baseline

u = GameDataUpdater()
r = u.check_only()  # 和 KAA 图形界面同一套：kotonebot/kaa-game-data 最新 release
missing = r is not None and (r.needs_db or any(r.category_missing.values()))
if r is None or not (r.needs_update or missing):
    print("游戏数据已是最新" if has_usable_baseline() else "没查到可下载的游戏数据（网络？）")
else:
    shown = {}

    def progress(name, cur, total):
        step = total // 4 if total else 0
        if total and (cur == total or cur - shown.get(name, 0) >= step):
            shown[name] = cur
            print(f"  {name} {cur * 100 // total}%", flush=True)

    u.download_to_staging(r, file_progress_cb=progress)
    print("已下载并应用" if apply_pending() else "下载完成，但没能应用（见上面的日志）")
if not has_usable_baseline():
    raise SystemExit("游戏数据不可用：可稍后重跑本脚本，或用 HIRO_KAA_GAME_DATA 指向已有的 game_data 目录")
PY
fi

step "6/7 模拟器主数据（社区公开 master，vertesan/gakumasu-diff 固定提交 → vendor/gakumas-player/data/master，首次约 216 MB / 2 分钟）"
if [ "$DATA" = 0 ]; then
  echo "跳过（--no-data）：模拟相关决策（轮间、饮料、考试预演）会退回社区规则"
else
  "$PY" vendor/gakumas-player/scripts/fetch_master.py || echo "  ⚠ 模拟器主数据没装好（网络？）：可稍后重跑本脚本；没有时模拟相关决策退回社区规则"
fi
# 考试引擎的 Rust 后端（可选，快约 8 倍）：有 Rust 工具链就编译装进 .venv；没有就用 Python 版（同一套规则，结果相同）
if command -v cargo >/dev/null 2>&1; then
  echo "  编译考试引擎 Rust 后端（vendor/gakumas-player/gakumas_exam_rs，首次约 1 分钟）…"
  uv pip install --python "$PY" -q maturin && VIRTUAL_ENV="$PWD/.venv" "$PY" -m maturin develop --release -q -m vendor/gakumas-player/gakumas_exam_rs/Cargo.toml \
    || echo "  ⚠ Rust 后端没编译成：考试引擎用 Python 版（结果相同，慢一些）"
else
  echo "  没有 Rust 工具链（cargo）：考试引擎用 Python 版（结果相同，慢一些）；装了 Rust（https://rustup.rs）后重跑本脚本可提速"
fi

step "7/7 自检"
"$PY" - <<'PY'
import sys

import hiro  # noqa: F401  兼容层
from kaa.tasks import R  # noqa: F401  生成文件可用

import hiro.run_hif  # noqa: F401  入口能导入（策略、控制器、KAA 补丁、执行器）
from hiro.loop import manifest

m = manifest.collect()
print(f"插件：资产 {m.asset_source}；局面 {m.exam_state_source}；出招 {m.exam_advisor}")
print(f"主数据 game.db：{'有' if m.master_db else '没有'}；随仓库素材包：{m.bundled_assets or '没有（考试效果名读不到，可用 tools/assets/capture_icons.py 补）'}；自建素材：{m.user_assets or '没有'}")
for p in m.problems:
    print(f"  ⚠ {p}")
import os
if sys.platform != "darwin" and not os.environ.get("HIRO_DEVICE"):
    print("  ⚠ 这台机器不是 macOS：开局前设 HIRO_DEVICE（adb / mumu / leidian / dmm），见 AGENTS.md「设备」。"
          "这些后端走 KAA 的设备工厂，作者没有实机验证过")
import hiro.sim
print(f"模拟器：引擎 {hiro.sim.ROOT}；主数据 {'已构建' if hiro.sim.master_ready() else '没有（模拟相关决策退回社区规则）'}")
from pathlib import Path
if not (Path("data") / "value_tables" / "drink_scale.json").exists():
    print("  ⚠ 没有 data/value_tables/（价值表随仓库发布）：轮间饮料增益不校准，请用 git 拿完整仓库")
print("自检通过：入口和 KAA 都能导入")
PY

cat <<'MSG'

装好了。开一局（先在游戏里编好支援卡 / 回忆编成）：
  HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> .venv/bin/python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>
离线测试：.venv/bin/python -m pytest -q
MSG
