#!/usr/bin/env bash
# 一条命令装好实机游玩环境（macOS + PlayCover；Windows 用 scripts/setup.ps1）：.venv（Python 3.10）→ 安装 → OCR 模型
# → 识别用的游戏原图（技能卡 / 偶像卡 / 饮料 → resources/game_data）→ 模拟器主数据 + 主数据库 → 自检。
#
#   scripts/setup.sh            # 第一次装 / 更新后重跑（已有的步骤会跳过）
#   scripts/setup.sh --no-data  # 不下载游戏数据（已用 HIRO_KAA_GAME_DATA / HIRO_MASTER_DB 指向别处时）
#
# 只装开发 / 测试（不玩实机）不需要这个脚本：uv venv && uv pip install -e ".[dev]"。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
DATA=1
for a in "$@"; do
  case "$a" in
    --no-data) DATA=0 ;;
    -h|--help) sed -n 2,7p "$0"; exit 0 ;;
    *) echo "未知参数：$a（可用 --no-data）" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

command -v uv >/dev/null || { echo "需要 uv（https://docs.astral.sh/uv/getting-started/installation/）" >&2; exit 1; }

step "1/6 虚拟环境 .venv（Python 3.10）"
venv_py() { if [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; else echo .venv/bin/python; fi; }  # Windows / 其他
if [ ! -x "$(venv_py)" ]; then
  uv venv --python 3.10 .venv
else
  echo "已有 .venv（$("$(venv_py)" --version)）"
fi
PY="$ROOT/$(venv_py)"

step "2/6 安装本仓库（.[dev,play]）"
uv pip install --python "$PY" -e ".[dev,play]"
# RapidOCR 后端的识别模型：从本仓库 GitHub Release 下载、校验 SHA256（Mac 缺省用 Vision，下载失败不挡安装）
"$PY" tools/assets/rapid_models.py || echo "  ⚠ RapidOCR 模型没下好（网络？）：要用 HIRO_OCR=rapidocr 时再跑 python tools/assets/rapid_models.py"

step "3/6 识别用的游戏原图（本仓库 Release game-images-b9577da：技能卡 / 偶像卡 / 饮料 → resources/game_data，约 125 MB）"
if [ "$DATA" = 0 ]; then
  echo "跳过（--no-data）"
else
  "$PY" tools/assets/game_images.py || echo "  ⚠ 游戏原图没下好（网络？）：卡面 / 偶像 / 饮料识别会退回 OCR；可稍后重跑本脚本"
fi

step "4/6 模拟器主数据 + 主数据库（社区公开 master，vertesan/gakumasu-diff 固定提交 → vendor/gakumas-player/data/master，首次约 216 MB / 2 分钟；再生成 cache/master/game.db）"
if [ "$DATA" = 0 ]; then
  echo "跳过（--no-data）：模拟相关决策（轮间、饮料、考试预演）会退回社区规则"
else
  if "$PY" vendor/gakumas-player/scripts/fetch_master.py; then
    "$PY" -m hiro.state.master_db || echo "  ⚠ 主数据库没生成成功：卡名 / 效果查不到，可稍后重跑本脚本"
  else
    echo "  ⚠ 模拟器主数据没装好（网络？）：可稍后重跑本脚本；没有时主数据查不到、模拟相关决策退回社区规则"
  fi
fi

step "5/6 考试引擎 Rust 后端与考试出牌算法（都可选）"
# 考试引擎的 Rust 后端（可选，快约 8 倍）：有 Rust 工具链就编译装进 .venv；没有就下预编译包（GitHub Release，按源码哈希对版本、校验 sha256）；
# 都不行就用 Python 版（同一套规则，结果相同）
if command -v cargo >/dev/null 2>&1; then
  echo "  编译考试引擎 Rust 后端（vendor/gakumas-player/gakumas_exam_rs，首次约 1 分钟）…"
  uv pip install --python "$PY" -q maturin && VIRTUAL_ENV="$PWD/.venv" "$PY" -m maturin develop --release -q -m vendor/gakumas-player/gakumas_exam_rs/Cargo.toml \
    || echo "  ⚠ Rust 后端没编译成：考试引擎用 Python 版（结果相同，慢一些）"
else
  echo "  没有 Rust 工具链（cargo）：下载预编译的 Rust 后端…"
  "$PY" scripts/engine_rs.py install --python "$PY" \
    || echo "  考试引擎用 Python 版（结果相同，慢一些）；装了 Rust（https://rustup.rs）后重跑本脚本可本地编译"
fi

# 考试出牌：gakumas-bench 的开源算法（缺省 UINet，默认开；HIRO_BENCH_ADVISOR=0 关）。装不上不影响开局：考试出牌退回社区规则
echo "  考试出牌算法（gakumas-bench + UINet 模型 + 主数据导出）…"
if uv pip install --python "$PY" -q -e ".[bench]"; then
  "$PY" -m hiro.bench_bridge setup || echo "  ⚠ UINet 模型 / 主数据没准备好（原因见上面几行）：考试出牌会退回社区规则；修好后重跑本脚本"
else
  echo "  ⚠ gakumas-bench / UINet 没装上：考试出牌会退回社区规则"
fi

step "6/6 自检"
"$PY" - <<'PY'
import sys

import hiro.run_hif  # noqa: F401  入口能导入（策略、控制器、执行器）
from hiro.loop import manifest

m = manifest.collect()
print(f"插件：资产 {m.asset_source}；局面 {m.exam_state_source}；出招 {m.exam_advisor}")
print(f"主数据库：{'有' if m.master_db else '没有'}；随仓库素材包：{m.bundled_assets or '没有（考试效果名读不到，可用 tools/assets/capture_icons.py 补）'}；自建素材：{m.user_assets or '没有'}")
for p in m.problems:
    print(f"  ⚠ {p}")
import os
if sys.platform != "darwin" and not os.environ.get("HIRO_DEVICE"):
    print("  ⚠ 这台机器不是 macOS：开局前设 HIRO_DEVICE（dmm / adb / mumu / leidian），见 AGENTS.md「设备」")
import hiro.sim
print(f"模拟器：引擎 {hiro.sim.ROOT}；主数据 {'已构建' if hiro.sim.master_ready() else '没有（模拟相关决策退回社区规则）'}")
from pathlib import Path
if not (Path("data") / "value_tables" / "drink_scale.json").exists():
    print("  ⚠ 没有 data/value_tables/（价值表随仓库发布）：轮间饮料增益不校准，请用完整源码（git clone 整个仓库，或下载 GitHub 的源码压缩包）")
print("自检通过：入口能导入")
PY

cat <<'MSG'

装好了。开一局（先在游戏里编好支援卡 / 回忆编成）：
  HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> .venv/bin/python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>
离线测试：.venv/bin/python -m pytest -q
MSG
