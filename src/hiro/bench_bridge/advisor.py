"""ExamAdvisor：考试出牌交给 gakumas-bench 的开源算法（缺省 UINet），出错时这一步退回规则。

用户 2026-11-12 决定：考试出牌主要交给 bench 算法，不再调 LLM；默认开。
- 算法：HIRO_BENCH_ALGO，写法同 bench（`uinet`、`uinet:threads=2`、`my.py:Cls`），缺省 uinet。只能用只看画面的算法
  （sees_seed / privileged 的拒绝加载；要 GKMS「世界」的算法拿不到世界，会在决策时出错并退回规则）。
- 关掉：HIRO_BENCH_ADVISOR=0（回到 hiro.defaults.advisor:RuleLLMAdvisor 的行为，含 LLM）；或 HIRO_EXAM_ADVISOR 换别的插件。
- 局面：缺省用画面读数 + 本场跟踪（hiro.bench_bridge.screen）。HIRO_BENCH_OBS=engine 时改用模拟器跟踪的引擎局面
  （hiro.sim.screen_source 挂在 state.sim_game 上），画面能看到的再用画面覆盖（_overlay_screen）。
- 依赖（可选，setup 默认装）：gkbench、gkms-uinet、onnxruntime；UINet 模型按固定提交下载并校验 sha256，
  主数据从引擎的 catalog（我们下载的 gakumasu-diff 快照）导出 9 张表。缺任何一样：开局日志写明原因，整场用规则。
- 退回规则时不调 LLM（RuleLLMAdvisor 去掉 LLM）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hiro.contracts import Action, Candidate, Decision, Preview

logger = logging.getLogger(__name__)
uiobs_log = logging.getLogger("hiro.bench_bridge.uiobs")  # 每步一行 JSON，量大：不想要时把这个 logger 调到 WARNING

UINET_COMMIT = "1201b841828d9874b604f7d48f7b00ddf3b71d95"  # yuzeis/GKMSUINet Ver1.1-Hingebungsvoll（MIT）
UINET_MODEL_URL = f"https://raw.githubusercontent.com/yuzeis/GKMSUINet/{UINET_COMMIT}/model/gkms_uinet.onnx"
UINET_MODEL_SHA256 = "a8addb1a221100dd251b7846cc3ba3898ec3698c9d3b9b35768d5965472ebd5a"
UINET_TABLES = (
    "ProduceCard",
    "ProduceCardCustomize",
    "ProduceCardGrowEffect",
    "ProduceCardSearch",
    "ProduceExamEffect",
    "ProduceExamTrigger",
    "ProduceExamStatusEnchant",
    "ProduceDrink",
    "ProduceDrinkEffect",
)
DEFAULT_ALGO = "uinet"
DECIDE_BUDGET_S = float(os.environ.get("HIRO_BENCH_BUDGET_S", "5"))
# 给算法的局面从哪来：screen（缺省，画面读数 + 本场跟踪）/ engine（模拟器跟踪的引擎局面，画面能看到的再用画面覆盖）。
# 2026-11-12 dmmt1 R1 21.6 万后离线重放 dmmw1：跟踪大多步按画面重建，重建丢了打出的卡挂上的附魔；合成开局还带着
# 模板那局的回忆 / P 道具附魔、山札里有模拟器自己随机的强化。引擎局面反而不如画面跟踪，先默认用画面。
OBS_SOURCE = os.environ.get("HIRO_BENCH_OBS", "screen").strip().lower()


def enabled() -> bool:
    return os.environ.get("HIRO_BENCH_ADVISOR", "1").strip().lower() not in ("0", "false", "off", "no")


def cache_dir() -> Path:
    return Path(os.environ.get("HIRO_BENCH_CACHE") or Path.home() / ".cache" / "hiro" / "bench").expanduser()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ort_reads_ir10() -> bool:
    import onnxruntime

    parts = [int(x) for x in onnxruntime.__version__.split(".")[:2] if x.isdigit()]
    return tuple(parts) >= (1, 18)


def _convert(src: Path, dst: Path) -> None:
    """原模型 → opset 18（convert_model.py），在 uv 的隔离环境里跑（要 onnx，不装进 hiro 环境）。"""
    import shutil
    import subprocess

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("onnxruntime < 1.18 读不了 UINet 原模型，转换要用 uv（没找到 uv）")
    script = Path(__file__).with_name("convert_model.py")
    tmp = dst.with_suffix(".part")
    r = subprocess.run([uv, "run", "--isolated", "--no-project", "--with", "onnx", "--with", "numpy", "python", str(script), str(src), str(tmp)],
                       capture_output=True, text=True, timeout=600)  # fmt: skip
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"UINet 模型转换失败：{(r.stderr or r.stdout).strip()[-400:]}")
    tmp.replace(dst)


def ensure_model(download: bool = True) -> Path:
    """UINet 模型：GKMS_UINET_MODEL 指定的文件；否则缓存目录里按固定提交下载的那份（校验 sha256）。
    onnxruntime < 1.18（KAA 钉在 1.14）读不了原模型：用转换成 opset 18 的那份（第一次用时转换并缓存）。"""
    env = os.environ.get("GKMS_UINET_MODEL")
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"GKMS_UINET_MODEL 指向的文件不存在：{p}")
        return p
    orig = _download_model(download)
    if _ort_reads_ir10():
        return orig
    conv = orig.with_name(orig.stem + "-opset18.onnx")
    if not conv.is_file():
        if not download:
            raise FileNotFoundError(f"没有转换好的 UINet 模型（{conv}）；运行 python -m hiro.bench_bridge setup")
        _convert(orig, conv)
    return conv


def _download_model(download: bool) -> Path:
    p = cache_dir() / f"gkms_uinet-{UINET_COMMIT[:12]}.onnx"
    if p.is_file() and _sha256(p) == UINET_MODEL_SHA256:
        return p
    if not download:
        raise FileNotFoundError(f"没有 UINet 模型（{p}）；运行 python -m hiro.bench_bridge setup 下载")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    logger.info("下载 UINet 模型 %s", UINET_MODEL_URL)
    with urllib.request.urlopen(UINET_MODEL_URL, timeout=120) as r, tmp.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    got = _sha256(tmp)
    if got != UINET_MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"UINet 模型校验失败：sha256 {got[:12]}… ≠ {UINET_MODEL_SHA256[:12]}…")
    tmp.replace(p)
    return p


def _engine_on_path() -> None:
    """把考试引擎（vendor/gakumas-player 里的 gakumas_exam）放进 sys.path：hiro.sim 导入时做这件事。
    实机开局时它早就导入了；`python -m hiro.bench_bridge setup / check` 单独跑时没有，gkbench 导入 gakumas_exam 就失败
    （Windows 上 setup 报 No module named 'gakumas_exam'，模型和主数据都没准备）。"""
    import hiro.sim  # noqa: F401


def ensure_master() -> Path:
    """UINet 读的 9 张主数据表：从考试引擎的 catalog（gakumasu-diff 快照）导出 jsonl，按快照提交号缓存。"""
    env = os.environ.get("GKBENCH_UINET_MASTER")
    if env:
        return Path(env).expanduser()
    _engine_on_path()
    from gakumas_exam.catalog import Catalog

    cat = Catalog()
    try:
        out = cache_dir() / f"uinet-master-{cat.info['commit'][:12]}"
        if all((out / f"{t}.jsonl").is_file() for t in UINET_TABLES):
            return out
        tmp = out.with_name(out.name + ".part")
        tmp.mkdir(parents=True, exist_ok=True)
        for t in UINET_TABLES:
            with (tmp / f"{t}.jsonl").open("w", encoding="utf-8") as f:
                for row in cat.rows(t):
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if out.exists():
            import shutil

            shutil.rmtree(out)
        tmp.replace(out)
        return out
    finally:
        cat.db.close()


def algo_spec() -> str:
    return (os.environ.get("HIRO_BENCH_ALGO") or DEFAULT_ALGO).strip()


# 偶像之路（tower-* 场次）的出牌算法：用户 2026-11-14 定「偶像之塔这里我们用 gnrpa 去搜索吧，这一块就不要强制性使用 uinet」。
# HIRO_TOWER_ALGO 写法同 HIRO_BENCH_ALGO（包.模块:类名[:参数]，bench Algorithm 接口：begin(ctx) / decide(ctx) → Decision）；
# 缺省 TOWER_DEFAULT_ALGO = Rust GNRPA（hiro.search.tower_algo）。加载失败（Rust 后端没装 / 没有 search、plan_many）时用 H.I.F
# 那个算法（uinet）兜底，每步决策的理由里写明「兜底」和原因。
TOWER_DEFAULT_ALGO = "hiro.search.tower_algo:TowerGnrpa"


def tower_algo_spec() -> str:
    return (os.environ.get("HIRO_TOWER_ALGO") or TOWER_DEFAULT_ALGO).strip()


EXAM_DEFAULT_ALGO = "hiro.search.exam_algo:ExamGnrpa"


def exam_search_algo_spec(label: str = "") -> str:
    """初 / N.I.A 场次的出牌算法。HIRO_EXAM_SEARCH_ALGO 设了就用它（两个家族都管）；没设时：
    - 初的中間 / 最終考试（hajime-mid1 / hajime-final）和课程（hajime-lesson-*）缺省走 Rust GNRPA（EXAM_DEFAULT_ALGO，目标用按场次的
      stage_objective）——协调 11-17 / 11-18 定，依据 bench：初考试 30 题 × 2 种子（中期 uinet 63% → 94%）、课程 33 题 × 2 种子
      （69% → 93%，满分 34 → 51 局）；HIRO_EXAM_SEARCH_HAJIME=0 两样都退回 uinet；
    - N.I.A 试镜（nia-mid1 / mid2 / final）：HIRO_EXAM_SEARCH_NIA 打开才走 GNRPA（按场次目标 + 对手分缩放，护栏和倍率画面实读同初）；
      缺省关（协调 11-18：bench MASTER 过线 78% → 90–97%，但初的 GNRPA 第一次上实机就因倍率 0 打成 227，N.I.A 要等实机验过再开）。"""
    spec = (os.environ.get("HIRO_EXAM_SEARCH_ALGO") or "").strip()
    if spec:
        return spec
    base = str(label or "").split("#", 1)[0].lower()
    if base.startswith("hajime-") and _hajime_key(base) in hajime_search_rounds():
        return EXAM_DEFAULT_ALGO
    if base.startswith("nia-") and base[len("nia-") :] in nia_search_rounds():
        return EXAM_DEFAULT_ALGO
    if base in hif_qualify_search_rounds():
        return EXAM_DEFAULT_ALGO
    return ""


# H.I.F 选拔考试（qualify-1/2/3）→ 主数据的场次（produce-007 的 AuditionMid1 / Mid2 / Final）
HIF_QUALIFY_ROUNDS = {"qualify-1": "mid1", "qualify-2": "mid2", "qualify-3": "final"}


def hif_qualify_search_rounds() -> set[str]:
    """HIRO_EXAM_SEARCH_HIF：H.I.F 选拔考试也走 GNRPA（同初 / N.I.A：中转层按培育记账现拼编成，引擎按 battle config 开局）。缺省关；
    final = 只有选拔第 3 场（用户 11-24：先只在最后一场试）；all = 三场都搜；也可以写场次号 1,3。本战 R1 / R2 不归它管（SearchAdvisor）。"""
    v = (os.environ.get("HIRO_EXAM_SEARCH_HIF") or "").strip().lower()
    if v in ("", "0", "off", "false", "no"):
        return set()
    if v == "all":
        return set(HIF_QUALIFY_ROUNDS)
    if v == "final":
        return {"qualify-3"}
    return {f"qualify-{n.strip()}" for n in v.split(",") if n.strip() in ("1", "2", "3")}


def searched_label(label: str | None) -> bool:
    """这一场走「现拼编成 + 搜索」的家族：初 / N.I.A，或开关打开的 H.I.F 选拔场次。"""
    base = str(label or "").split("#", 1)[0].lower()
    return base.startswith(("hajime", "nia")) or base in hif_qualify_search_rounds()


def round_key(label: str | None) -> str:
    """标签 → 主数据场次（audition_row 的 round_key）：hajime-final → final，nia-mid2#audition-3 → mid2，qualify-3 → final。"""
    base = str(label or "").split("#", 1)[0].lower()
    return HIF_QUALIFY_ROUNDS.get(base) or base.partition("-")[2]


HAJIME_ROUNDS = ("mid1", "final", "lesson")  # 初：中間試験、最終試験、练习课
NIA_ROUNDS = ("mid1", "mid2", "final")  # N.I.A：三场试镜


def round_set(var: str, keys: tuple[str, ...], default_all: bool) -> set[str]:
    """按场次的搜索开关：1 / on / all = 全部，0 / off / none = 都不搜，也可以写场次（逗号分隔，如 mid1,final）；没设按 default_all。
    11-27 用户：开局页按场次选「快速 / 搜索」。"""
    v = (os.environ.get(var) or "").strip().lower()
    if not v:
        return set(keys) if default_all else set()
    if v in ("1", "on", "true", "yes", "all"):
        return set(keys)
    if v in ("0", "off", "false", "no", "none"):
        return set()
    picked = {x.strip() for x in v.split(",") if x.strip() in keys}
    if (
        not picked
    ):  # 一个场次都认不出（如「2」「yes,」）：按原来的开关含义，照缺省（初全搜、N.I.A 不搜），不悄悄变成全关
        logging.getLogger(__name__).warning("%s=%r 认不出场次，按缺省", var, v)
        return set(keys) if default_all else set()
    return picked


def _hajime_key(base: str) -> str:
    """hajime-mid1 → mid1，hajime-final → final，hajime-lesson-vo-sp → lesson。"""
    rest = base[len("hajime-") :]
    return "lesson" if rest.startswith("lesson") else rest


def nia_search_rounds() -> set[str]:
    """HIRO_EXAM_SEARCH_NIA：N.I.A 哪几场试镜走搜索，缺省都不搜（协调定）。"""
    return round_set("HIRO_EXAM_SEARCH_NIA", NIA_ROUNDS, False)


def hajime_search_rounds() -> set[str]:
    """HIRO_EXAM_SEARCH_HAJIME：初的哪几场考试 / 课程走搜索，缺省都搜（协调定）。"""
    return round_set("HIRO_EXAM_SEARCH_HAJIME", HAJIME_ROUNDS, True)


def nia_search_on() -> bool:
    """有没有哪场 N.I.A 试镜走搜索（旧接口）。"""
    return bool(nia_search_rounds())


def hajime_search_on() -> bool:
    """有没有哪场初的考试 / 课程走搜索（旧接口）。"""
    return bool(hajime_search_rounds())


def load_algo(spec: str | None = None, *, download: bool = True):
    """按名字加载 bench 算法。uinet 自动带上我们的主数据和模型（spec 里写了 master= / model= 就用写的）。"""
    _engine_on_path()
    from gkbench.algos import make

    spec = spec or algo_spec()
    name, _, rest = spec.partition(":")
    if name == "uinet":
        extra = []
        if "master=" not in rest:
            extra.append(f"master={ensure_master()}")
        if "model=" not in rest:
            extra.append(f"model={ensure_model(download)}")
        spec = name + ":" + ",".join(x for x in [rest, *extra] if x)
    try:
        algo = make(spec)
    except SystemExit as e:  # bench 的 make 用 SystemExit 报参数错
        raise ValueError(str(e)) from None
    if getattr(algo, "sees_seed", False) or getattr(algo, "privileged", False):
        raise ValueError(f"{spec} 要看种子 / 真实局面，实战不能用")
    return algo


def _plan_number(idol: str | None) -> int | None:
    if not idol:
        return None
    try:
        from gakumas_exam.catalog import Catalog

        cat = Catalog()
        try:
            row = cat.one("IdolCard", idol.replace("i_card-skin-", "i_card-"))  # skin id（续跑 / 开局参数）也认
        finally:
            cat.db.close()
        plan = (row or {}).get("planType") or ""
        return {
            "ProducePlanType_Common": 1,
            "ProducePlanType_Plan1": 2,
            "ProducePlanType_Plan2": 3,
            "ProducePlanType_Plan3": 4,
        }.get(plan)
    except Exception:  # noqa: BLE001
        return None


def _customize_ids():
    """(card_id, 强化次数) → 主数据的可定制列表（超过最高等级取最高）。"""
    from gakumas_exam.catalog import Catalog

    cat = Catalog()
    cache: dict = {}

    def get(card_id: str, u: int) -> list:
        k = (card_id, u)
        if k not in cache:
            ids = []
            for v in range(int(u), -1, -1):
                try:
                    row = cat.one("ProduceCard", card_id, v)
                except Exception:  # noqa: BLE001  主数据没有这一级 / 这张卡：再往下找，都没有就当没有定制
                    row = None
                if row is not None:
                    ids = row.get("produceCardCustomizeIds") or []
                    break
            cache[k] = ids
        return cache[k]

    return get


def _overlay_screen(obs: dict, screen: dict, state) -> dict:
    """引擎局面转出的 UIObs 上，画面能看到的一律换成画面的。模拟器合成开局只按画面粗对齐（dmmw1 R1 离线重放）：
    回合属性是模板的占位顺序；手牌强化被模拟器自己的随机支援强化抬高（画面 + 引擎 +++）；やる気 / 好印象 / 分数有偏差。
    引擎只补画面看不到的：山札 / 弃牌 / 除外 / 保留的内容、附魔、其余状态。"""
    for k in ("turn", "limit", "types", "permil"):
        obs["exam"][k] = screen["exam"][k]
    if [c["id"] for c in obs["hand"]] == [c["id"] for c in screen["hand"]]:
        obs["hand"] = [dict(c, u=h["u"], p=h["p"]) for c, h in zip(obs["hand"], screen["hand"])]
    else:
        obs["hand"] = screen["hand"]  # 手牌顺序 / 内容和画面对不上：动作要落到画面，用画面的
    obs["drinks"], obs["legal"] = screen["drinks"], screen["legal"]
    for k, attr in (("score", "score"), ("stamina", "hp"), ("max_stamina", "max_hp"), ("block", "genki")):
        if getattr(state, attr, None) is not None:
            obs["num"][k] = screen["num"][k]
    screen_kinds = {}
    for k, attr in (("aggressive", "motivation"), ("review", "impression")):
        if getattr(state, attr, None) is not None:
            screen_kinds[k] = int(getattr(state, attr))
    if screen_kinds:
        obs["status"] = [x for x in obs["status"] if x[0] not in screen_kinds] + [
            [k, float(v), -1] for k, v in screen_kinds.items() if v
        ]
    return obs


def _round_defaults(label: str | None, deck: list, max_hp: int | None = None) -> tuple[dict, dict | None]:
    """画面还没读到时的倍率（R1 记下的 / 参考值），和本战回合属性配额（模拟器合成模板那场的配额，本战每局相同：
    dmmw1、dmmt2 的 R1 都是 Vo4 / Da3 / Vi2、末三回合 Vi → Da → Vo）。选拔考试没有模板，配额给 None。
    max_hp：开考时从培育页读到的最大体力；未知时不合成模板（配额给 None，未知回合按流向填）。"""
    base = str(label or "").split("#", 1)[0].lower()
    permil, alloc = {}, None
    if base.startswith(
        ("tower", "hajime", "nia")
    ):  # 偶像之路 / 初 / N.I.A：倍率由编成定（编成给了就用编成的），没有 H.I.F 的参考值
        return permil, alloc
    try:
        if base in ("r1", "r2"):
            from hiro.state.finals_context import CONTEXT
            from hiro.state.knowledge import FINALS_REFERENCE_PERMIL

            permil = {k: v for k, v in (CONTEXT.get("permil") or {}).items() if v} or dict(FINALS_REFERENCE_PERMIL)
        else:
            from hiro.sim.resource import REFERENCE_PERMIL

            permil = dict(REFERENCE_PERMIL)
    except Exception as e:  # noqa: BLE001
        logger.info("参考倍率不可用：%s: %s", type(e).__name__, e)
    if base in ("r1", "r2") and deck and max_hp:
        try:
            from hiro.sim import interval as iv
            from hiro.sim import resource as rv

            setup, _ = rv.setup_for(base, deck=list(deck), drinks=[], hp=max_hp, max_hp=max_hp, permil=permil or None)
            alloc = (getattr(iv.bare_exam(setup), "meta", None) or {}).get("allocation")
        except Exception as e:  # noqa: BLE001  没有模拟器 / 主数据：未知回合按流向填
            logger.info("本战回合配额不可用：%s: %s", type(e).__name__, e)
    return permil, alloc


def items_from_enchants(ench) -> list[str]:
    """UIObs 的附魔列表 → 持有的 P 道具 id：P 道具的考试附魔是 enchant-p_item_effect_<道具号>-encNN（道具 pitem_<道具号>）。
    这份列表是 ScreenTracker 记的：偶像固有道具开场按主数据放进去（Enchants.idol_items = IdolCard.beforeProduceItemId），
    其余道具画面认出来才有。初 PRO 第一局 / H.I.F mainz4（11-18）：P 道具账本是空的，拼的编成里连固有道具 選びとった熱 都没有，
    T7 打えいえいおー 画面上道具发动（追加行动、やる気+N），引擎里没有，后面整段对不上。"""
    out: list[str] = []
    for e in ench or ():
        eid = str(e[0] if isinstance(e, (list, tuple)) else e)
        m = re.match(r"^enchant-p_item_effect_(\d{2}-\d-\d{3}-\d)-enc\d+$", eid)
        if m and (iid := f"pitem_{m.group(1)}") not in out:
            out.append(iid)
    return out


def stage_round(family: str, stage: str | None) -> str | None:
    """面板过线条件原文（exam.pass_line.stage）→ 场次：初「…中間試験突破」mid1 /「…最終試験突破」final；N.I.A「1次 / 2次 / 最終
    オーディション」mid1 / mid2 / final（N.I.A 面板还没有实拍帧，按关键词认，全角数字 / 汉数字都认）。认不出 None。"""
    t = str(stage or "")
    if not t:
        return None
    if family == "hajime":
        return "mid1" if "中間" in t else "final" if "最終" in t else None
    if family == "nia":
        if "最終" in t:
            return "final"
        if any(k in t for k in ("2次", "２次", "二次")):
            return "mid2"
        if any(k in t for k in ("1次", "１次", "一次")):
            return "mid1"
    return None


class _Waiting(Exception):
    """实机拼编成还在等（倍率没读到）：这一步用 H.I.F 的算法，不算搜索开局失败。"""


def _tag_number(label: str | None, key: str) -> int | None:
    """标签 tag 里的编号：nia-mid1#audition-2 → 2（试镜档位，平台 11-17 约定）；hajime-lesson-vo-sp#lesson-3 → 3（同种同属性第几节课）。"""
    for tag in str(label or "").split("#")[1:]:
        k, _, v = tag.partition("-")
        if k == key and v.isdigit():
            return int(v)
    return None


def _audition_number(label: str | None) -> int | None:
    return _tag_number(label, "audition")


def _exam_loadout(ctx: dict) -> dict | None:
    """初 / N.I.A 这一场的编成：context 的 exam_loadout，或 HIRO_EXAM_LOADOUT 指的 JSON 文件（bench 题存档或编成本身）。没有就 None。"""
    if isinstance(ctx.get("exam_loadout"), dict):
        return ctx["exam_loadout"]
    path = os.environ.get("HIRO_EXAM_LOADOUT")
    if not path:
        return None
    try:
        doc = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("HIRO_EXAM_LOADOUT=%s 读不了：%s", path, e)
        return None
    return doc.get("loadout", doc)


def _tower_loadout(ctx: dict) -> dict | None:
    """偶像之路这一场的编成（gkms.exam_loadout.v1）：context 的 tower_loadout，或 HIRO_TOWER_LOADOUT 指的 JSON 文件
    （编成决策写出来、驱动设好）。没有就 None（只靠画面）。"""
    if isinstance(ctx.get("tower_loadout"), dict):
        return ctx["tower_loadout"]
    path = os.environ.get("HIRO_TOWER_LOADOUT")
    if not path:
        return None
    try:
        doc = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("HIRO_TOWER_LOADOUT=%s 读不了：%s", path, e)
        return None
    return doc.get("loadout", doc)  # 题目存档（带 loadout）或编成本身都行


def tower_cache_dir() -> Path:
    return Path(os.environ.get("HIRO_TOWER_CACHE") or Path.home() / ".cache" / "hiro" / "tower").expanduser()


def known_draws(loadout: dict | None) -> list | None:
    """偶像之路同一编成重试时，上一次挑战在画面上看到的抽牌顺序（用户 2026-11-14 定：可以交给搜索；只限偶像之路同一编成）。
    记录由执行层写在 <HIRO_TOWER_CACHE>/<tower_id>-<layer>-<编成签名>.json：
    {"tower_id", "layer", "formation_signature", "draws": [{"id", "u"}, …（按抽到的先后，跨回合）]}。
    编成签名（主 / サブ1 / サブ2 回忆按顺序）由编成页写进 loadout 的 formation_signature；对不上、没记录或格式不对返回 None。"""
    lo = loadout or {}
    sig, tower, layer = lo.get("formation_signature"), lo.get("tower_id"), lo.get("layer")
    if not (sig and tower and layer):
        return None
    path = tower_cache_dir() / f"{tower}-{int(layer)}-{sig}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        doc.get("formation_signature") != sig
        or doc.get("tower_id") != tower
        or int(doc.get("layer") or 0) != int(layer)
    ):
        logger.warning("偶像之路抽牌记录 %s 和本场编成对不上，不用", path)
        return None
    draws = [
        {"id": d["id"], "u": int(d.get("u") or 0)}
        for d in doc.get("draws") or ()
        if isinstance(d, dict) and d.get("id")
    ]
    return draws or None


def _engine_state(game) -> dict | None:
    """SimGame → 引擎导出局面（Python / Rust 后端同一格式）。"""
    env = getattr(game, "env", None)
    if env is None:
        return None
    rx = getattr(env, "rx", None)
    if rx is not None:
        return json.loads(rx.export_json())
    ex = getattr(env, "ex", None)
    if ex is not None:
        from gakumas_exam.native import export_state

        return export_state(ex)
    return None


class BenchAdvisor:
    """考试每一步：局面 → UIObs → bench 算法 → hiro 动作；不行就这一步用规则（不调 LLM）。"""

    def __init__(self, profile=None, llm=None):
        from hiro.brain.battle import BattleBrain
        from hiro.defaults.advisor import RuleLLMAdvisor

        if enabled():
            # 开着 bench：退回规则的那一步不调 LLM（传 False 免得 RuleLLMAdvisor 去读 HIRO_LLM 建客户端）
            self.rules = RuleLLMAdvisor(profile, llm=False)
            self.rules.brain = BattleBrain(profile, None)
        else:
            self.rules = RuleLLMAdvisor(profile, llm)  # 关掉 bench：就是原来的默认出招（规则 + LLM）
        self.profile = profile
        self.algo = None  # H.I.F 的算法（HIRO_BENCH_ALGO，缺省 uinet）；也是偶像之路的兜底
        self.round_algo = None  # 这一场用的算法（偶像之路场次可能是 HIRO_TOWER_ALGO）
        self.round_name = ""  # 这一场算法的名字（写进决策理由）
        self.round_note = ""  # 兜底说明：偶像之路没用上指定算法时写原因
        self._tower_algos: dict = {}  # spec → 算法或加载失败的说明（同一进程多场只载一次）
        self.tower_loadout: dict | None = None
        self.exam_loadout: dict | None = None
        self._session = None
        self._number: int | None = None
        self._live_built = False
        self._history: list = []
        self._calc_pm: dict = {}  # 按三属性粗算的倍率（只当比例用）
        self._seen_pm: dict = {}  # 这一场画面实读到的倍率（每属性最后一次非 0 的读数）
        self._live_wait: str | None = None  # 实机拼编成在等什么（倍率没读到）：这一步用 uinet、下一步再拼
        self.known_draws: list | None = None
        self.load_error: str | None = None
        self.exam: dict = {}
        self.label: str | None = None
        self.tracker = None
        self.began = False
        self._hif_began = False
        self.rng = random.Random(0)
        self.stats = {"bench": 0, "rules": 0}
        self.deck: list = []
        self._cz = None
        if not enabled():
            self.load_error = "HIRO_BENCH_ADVISOR=0"
        else:
            try:
                self.algo = load_algo()
                logger.info("考试出牌：bench 算法 %s", algo_spec())
            except Exception as e:  # noqa: BLE001  缺依赖 / 模型下载失败：整场用规则
                self.load_error = f"{type(e).__name__}: {e}"
                logger.warning("bench 算法 %s 加载失败，考试出牌用规则（不调 LLM）：%s", algo_spec(), self.load_error)

    # ---------------------------------------------------------------- 生命周期
    def on_round_start(self, round_no: int, context: dict[str, Any]) -> None:
        from hiro.bench_bridge.screen import ScreenTracker, exam_fields
        from hiro.brain.state import profile_for

        ctx = context or {}
        self.rules.on_round_start(round_no, ctx)
        if enabled():
            self.rules.brain.llm = None
        idol = ctx.get("idol") or (self.profile.idol_id if self.profile else None)
        if idol and (p := profile_for(idol)) is not None:
            self.profile = p
        self.label = ctx.get("exam")
        deck = [
            (c["card_id"], int(c.get("upgrade") or 0)) if isinstance(c, dict) else tuple(c[:2])
            for c in ctx.get("deck") or ()
        ]
        self.deck = [c for c in deck if c and c[0]]
        trend = None
        try:
            from hiro.state.knowledge import idol_trend

            trend = tuple(idol_trend(idol)[1]) if idol else None
        except Exception:  # noqa: BLE001
            trend = None
        self._session = ctx.get("session")
        self.exam = exam_fields(self.label, idol=idol, plan=_plan_number(idol), produce=self._live_mode())
        self.max_hp = ctx.get("max_hp")
        self.exam["permil_default"], self.exam["allocation"] = _round_defaults(self.label, self.deck, self.max_hp)
        if self.max_hp and not self.exam.get("max_stamina"):
            self.exam["max_stamina"] = int(self.max_hp)  # 画面帧没给最大体力时（screen.from_screen 的退路）用开考读数
        self._history: list = []  # 这一场执行过的 (UIObs, 动作)：编成晚拼时交给搜索补放（ExamGnrpa._replay）
        self.tower_loadout = None
        self.known_draws = None
        if str(self.label or "").lower().startswith("tower") and (lo := _tower_loadout(ctx)) is not None:
            from hiro.bench_bridge.screen import tower_context

            self.tower_loadout = lo  # 搜索类算法（GNRPA）要整份编成：ctx.loadout
            self.known_draws = known_draws(lo)
            if self.known_draws:
                logger.info(
                    "[%s] 同一编成重试：上一次看到的抽牌顺序 %d 张交给算法（ctx.known_draws）",
                    self.label,
                    len(self.known_draws),
                )
            tc = tower_context(lo)
            self.exam.update({k: tc[k] for k in ("turns", "permil_fixed", "max_stamina") if tc[k]})
            self.exam["idol"] = tc["idol"] or self.exam.get("idol")
            if not self.deck and tc["deck"]:
                self.deck = list(tc["deck"])
            logger.info("[%s] 偶像之路编成：%d 回合，倍率 %s，最大体力 %s，开局牌组 %d 张", self.label, tc["turns"] or 0,
                        tc["permil_fixed"], tc["max_stamina"], len(self.deck))  # fmt: skip
        self._session = ctx.get("session")
        self._number = number = _audition_number(self.label)
        self._live_built = False
        lo = _exam_loadout(ctx) if str(self.label or "").lower().startswith(("hajime", "nia")) else None
        if lo is not None and number and not lo.get("audition_number"):
            lo = {**lo, "audition_number": number}  # 平台在标签里带的档位（nia-mid1#audition-2）
        self.exam_loadout = lo
        if lo is None and number and str(self.label or "").lower().startswith(("hajime", "nia")):
            from hiro.bench_bridge.screen import audition_context

            try:
                ac = audition_context(self.label, idol, number)
            except Exception as e:  # noqa: BLE001  查不到：只靠画面
                logger.warning("[%s] 档位 %s 查不到审查基准：%s: %s", self.label, number, type(e).__name__, e)
            else:
                self.exam["crit"], self.exam["permil_default"], self.exam["allocation"] = ac["crit"], {}, None
                if ac["turns"]:
                    self.exam["turns"] = ac["turns"]
        self._calc_pm, self._seen_pm, self._live_wait = {}, {}, None
        self._label_note = None
        self._round_checked = str(self.label or "").split("#", 1)[0].lower() not in (
            "hajime-mid1",
            "hajime-final",
            "nia-mid1",
            "nia-mid2",
            "nia-final",
        )
        base = str(self.label or "").split("#", 1)[0].lower()
        if lo is None and base.startswith(("hajime-", "nia-")) and "-lesson" not in base:
            self._audition_defaults(idol, number)
        elif lo is None and base in hif_qualify_search_rounds():
            # H.I.F 的 idol 常是皮肤 id（--idol i_card-skin-…），主数据试镜行按偶像卡 id 查
            card = self.profile.idol_id if self.profile is not None else idol
            self.exam["idol"] = card
            self._audition_defaults(card, None)
        if lo is not None:
            from hiro.bench_bridge.screen import exam_context, exam_fields

            try:
                xc = exam_context(lo)
            except Exception as e:  # noqa: BLE001  查不到这场试镜：只靠画面
                logger.warning("[%s] 编成换算失败，只靠画面读数：%s: %s", self.label, type(e).__name__, e)
            else:
                idol = xc["idol"] or idol
                self.exam = exam_fields(
                    self.label, idol=idol, plan=_plan_number(idol), crit=xc["crit"], produce=xc["produce"]
                )
                self.exam["permil_default"], self.exam["allocation"] = {}, None
                self.exam.update({k: xc[k] for k in ("turns", "permil_fixed", "max_stamina") if xc[k]})
                if not self.deck and xc["deck"]:
                    self.deck = list(xc["deck"])
                logger.info("[%s] 编成：%s 回合，审查基准 %s，倍率 %s，最大体力 %s，开局牌组 %d 张", self.label, xc["turns"], xc["crit"],
                            xc["permil_fixed"], xc["max_stamina"], len(self.deck))  # fmt: skip
        ench = items = None
        if self.algo is not None:
            try:
                from hiro.bench_bridge.enchants import Enchants

                ench = self._ench = getattr(self, "_ench", None) or Enchants()
                items = ench.idol_items(idol)
            except Exception as e:  # noqa: BLE001  没有主数据：持续效果不记
                logger.info("持续效果记账不可用：%s: %s", type(e).__name__, e)
        self.tracker = ScreenTracker(self.deck, trend, enchants=ench, items=items)
        self._pick_algo()
        self.began = False
        self._hif_began = False  # 搜索算法某步搜不了时临时用 H.I.F 的算法：它这一场 begin 过没有
        self.rng = random.Random(hash((self.label, round_no)) & 0xFFFFFFFF)
        self.stats = {"bench": 0, "rules": 0}
        if self.algo is None:
            logger.info("[%s] 考试出牌用规则：%s", self.label, self.load_error)

    def _pick_algo(self) -> None:
        """这一场用哪个算法：偶像之路用 HIRO_TOWER_ALGO（加载失败或没配置就用 H.I.F 的算法兜底，写明原因），其余用 H.I.F 的。"""
        self.round_algo, self.round_name, self.round_note = self.algo, algo_spec().split(":")[0], ""
        label = str(self.label or "").lower()
        if not enabled():
            return
        if label.startswith(("hajime", "nia")) or searched_label(label):
            # 初 / N.I.A：搜索（ExamGnrpa 自己按 UIObs 对齐引擎局面，不要 screen_source 跟踪）。初的考试和课程缺省开，
            # N.I.A 缺省关、HIRO_EXAM_SEARCH_NIA 打开（exam_search_algo_spec）
            spec = exam_search_algo_spec(label)
            if not spec:
                return
            what = "H.I.F 选拔" if label.split("#", 1)[0] in HIF_QUALIFY_ROUNDS else "初 / N.I.A"
        elif label.startswith("tower"):
            spec, what = tower_algo_spec(), "偶像之路"
        else:
            return
        if not spec:
            self.round_note = "偶像之路指定算法（GNRPA）还没接上，HIRO_TOWER_ALGO 未设"
        else:
            got = self._tower_algos.get(spec)
            if got is None:
                try:
                    got = load_algo(spec)
                except Exception as e:  # noqa: BLE001  缺依赖 / 写错：兜底
                    got = f"{type(e).__name__}: {e}"
                self._tower_algos[spec] = got
            if isinstance(got, str):
                self.round_note = f"{what}指定算法 {spec} 加载失败（{got}）"
            else:
                self.round_algo, self.round_name = got, spec.split(":")[-1] if ":" in spec else spec
                logger.info("[%s] %s出牌：%s", self.label, what, spec)
                return
        logger.warning("[%s] %s，用 %s 兜底", self.label, self.round_note, self.round_name)

    def _live_exam_loadout(self, obs: dict) -> dict | None:
        """实机初 / N.I.A：没有 HIRO_EXAM_LOADOUT 时，在这场第一次决策时拼编成（协调 11-17 定：中转层自己拼）。
        牌组 = 执行层给的培育牌组账本；P 道具 / 支援卡 / 回忆附魔 = hiro.search.loadout.run_loadout（培育记账 + 开局编成方案）；
        倍率 / 体力 = 这一帧画面（倍率只读到当前属性时按编成的 permil_fixed，没有就是画面补齐的值）；
        场次 + 档位 = 标签（nia-mid2#audition-3）→ 主数据的试镜行（exam、audition：搜索按场次的目标要用）。拼不出返回 None。"""
        import os

        from hiro.search.exam_env import ExamUnsupported, audition_row

        base = str(self.label or "").split("#", 1)[0].lower()
        rnd = round_key(self.label)
        idol, produce = self.exam.get("idol"), self.exam.get("produce")
        if not idol or not self.deck:
            logger.info("[%s] 拼不了编成（偶像 %s、牌组 %d 张）", self.label, idol, len(self.deck))
            return None
        num = obs.get("num") or {}
        lo = {"produce_id": produce, "round": rnd, "idol_card_id": idol,
              "produce_cards": [f"{c}+{u}" if u else c for c, u in self.deck],
              "starting_stamina": int(num.get("stamina") or 0) or None, "max_stamina": int(num.get("max_stamina") or 0) or None,
              "drink_ids": list(obs.get("drinks") or [])}  # fmt: skip
        if rnd.startswith("lesson"):
            got = self._live_lesson(base, obs)
            if got is None:
                return None
            lo.update(round="lesson", lesson=got)
            row = {"number": None}
        else:
            try:
                row = audition_row(idol, produce, rnd, self._number)
            except ExamUnsupported as e:
                logger.warning("[%s] 拼编成时查不到试镜行：%s", self.label, e)
                return None
            pm, why = self._exam_permils()
            if pm is None:
                # 倍率不全不能交给搜索（引擎会以为出牌不加分，初第二局中間 227 分）：这一步用 uinet，下一步画面读到了再拼
                self._live_wait = why
                logger.info("[%s] 倍率还不够拼编成（%s），这一步用 uinet", self.label, why)
                return None
            lo.update(score_bonus_permils=[int(pm.get(a) or 0) for a in ("Vo", "Da", "Vi")],
                      exam={"produce_exam_battle_config_id": row["produceExamBattleConfigId"],
                            "produce_exam_gimmick_effect_group_id": row.get("produceExamGimmickEffectGroupId") or None,
                            "produce_exam_battle_npc_group_id": row["produceExamBattleNpcGroupId"]},
                      audition={k: row.get(k) for k in ("rankThreshold", "baseScore", "forceEndScore", "voteCount", "isStaticNpcScore", "number")})  # fmt: skip
        try:
            from hiro.search.loadout import run_loadout

            produce_state = (
                (getattr(getattr(self._session, "state", None), "produce", None) or {})
                if self._session is not None
                else {}
            )
            plan = None
            if os.environ.get("HIRO_FORMATION_PLAN", "").lower() != "off":
                try:
                    from hiro.brain.formation_plan import load

                    plan = load(os.environ.get("HIRO_FORMATION_PLAN") or None)
                except Exception as e:  # noqa: BLE001
                    logger.info("开局编成方案读不了：%s", e)
            run, notes = run_loadout(produce_state, plan)
            # 回忆附魔（run["skills"]）只认 H.I.F 回忆技能（触发 p_trigger-start_audition-for_hif_memory，只在 H.I.F 考试生效），
            # 初 / N.I.A 不给；N.I.A 回忆能力（「1次オーディション開始時、30%の確率で…」）还没解析，先按没有（3940a9 11-17）
            items = list(run["items"])
            seen = [i for i in items_from_enchants(obs.get("ench")) if i not in items]
            if seen:  # 账本没记的 P 道具：UIObs 附魔列表里有它的附魔（p_item_effect_<id>-encNN）就补上
                logger.info("[%s] 拼编成：P 道具按 UIObs 附魔补 %s（账本没有）", self.label, seen)
                items += seen
            lo.update(produce_item_ids=items, supports=list(run["supports"]), skills=[])
            if notes:
                logger.info("[%s] 拼编成：%s", self.label, "；".join(notes))
        except Exception as e:  # noqa: BLE001  道具 / 支援卡拼不出：按没有搜
            logger.info(
                "[%s] 拼编成时 run_loadout 失败（按没有道具 / 支援卡）：%s: %s", self.label, type(e).__name__, e
            )
        if lo.get("audition"):
            self._npc_permil(lo)
        if not lo["max_stamina"]:
            # 考试画面只显示当前体力。广以外的偶像构筑表没有 max_hp（11-19 ことね：max_stamina=None，ExamGnrpa 开局 int(None) 整场退回 uinet）：
            # 用培育局面里选行动页读到的「体力 35/36」的上限；再没有就用开考时的体力（不会比上限大）
            ps = (
                (getattr(getattr(self._session, "state", None), "produce", None) or {})
                if self._session is not None
                else {}
            )
            got = int(ps.get("max_stamina") or 0) or None
            src = "培育局面"
            if got is None and lo.get("starting_stamina"):
                got, src = int(lo["starting_stamina"]), "开考时的体力"
            if got:
                lo["max_stamina"] = got
                logger.info("[%s] 拼编成：考试画面没有最大体力，用%s的 %d", self.label, src, got)
            else:
                logger.info("[%s] 拼编成：最大体力没读到，照样交给算法", self.label)
        logger.info("[%s] 现拼编成：牌组 %d 张、P 道具 %d、支援卡 %d、档位 %s", self.label, len(lo["produce_cards"]),
                    len(lo.get("produce_item_ids") or ()), len(lo.get("supports") or ()), row.get("number"))  # fmt: skip
        return lo

    def _live_mode(self) -> str | None:
        """实机这一局的培育 id（state.produce.mode）：和标签同一家族（初 / N.I.A）才用。初 PRO 第一局（协调 11-18）：exam_fields 按标签
        缺省 hajime → produce-001，局面其实是 produce-002，battle config / 封顶（REGULAR 900、PRO 1700）全按 REGULAR 取。"""
        ps = (
            (getattr(getattr(self._session, "state", None), "produce", None) or {}) if self._session is not None else {}
        )
        ps = ps if isinstance(ps, dict) else getattr(ps, "__dict__", {})
        mode = ps.get("mode")
        fam = str(self.label or "").split("#", 1)[0].split("-", 1)[0].lower()
        if not mode or fam not in ("hajime", "nia"):
            return None
        try:
            from hiro.state.modes import family

            return mode if family(mode) == fam else None
        except Exception:  # noqa: BLE001
            return None

    def _check_round(self, obs: dict, state: Any = None, memory: Any = None) -> None:
        """初的中間 / 最終、N.I.A 的 1 次 / 2 次 / 最終：标签和画面对不上时以画面为准（协调 11-18：初 PRO 最終续跑后被标成 hajime-mid1，目标按中間取了 1700 封顶、
        只拿第 2）。依据（先面板、后回合数）：审查日程面板过线条件的原文（exam.pass_line.stage：「…中間試験突破」/「…最終試験突破」，
        平台写）；不按有没有分数线判——初 MASTER 中間也只看名次「3位以上で中間試験突破」，485eb03 因此把中間误判成最終（协调 11-18）。
        没有原文就比回合总数（画面剩余回合推）和这个培育两场 battle config 的回合数（按 produce 查主数据，初 中間 9 / 最終 11）。
        改了就换标签、重查这场的审查基准 / 回合数 / 粗算倍率，日志和每步理由写明。"""
        from hiro.bench_bridge import tower as tw
        from hiro.bench_bridge.screen import exam_fields
        from hiro.search.exam_env import audition_row

        tag = str(self.label or "")
        base, _, rest = tag.partition("#")
        fam, _, rnd = base.lower().partition("-")
        ex = getattr(getattr(self._session, "state", None), "exam", None) if self._session is not None else None
        pl = (ex.get("pass_line") if isinstance(ex, dict) else getattr(ex, "pass_line", None)) or None
        stage = str((pl or {}).get("stage") or (pl or {}).get("text") or "") if isinstance(pl, dict) else ""
        got, why = stage_round(fam, stage), f"面板过线条件「{stage}」"
        if got is None and fam == "nia":
            return  # N.I.A：只认面板原文（三场回合数不一定分得开，不按回合数猜）
        if got is None:
            # 总回合数按画面的剩余回合推（UIObs 的 limit 可能已经按标签那场的 battle config 固定了）
            remaining = getattr(state, "remaining_turns", None)
            limit = int(getattr(memory, "turn", 1) or 1) + int(remaining) - 1 if remaining else 0
            turns = {}
            for r in ("mid1", "final"):
                try:
                    row = audition_row(self.exam.get("idol"), self.exam.get("produce"), r, 1)
                    turns[r] = int((tw.master().battle_config(row["produceExamBattleConfigId"]) or {}).get("turn") or 0)
                except Exception as e:  # noqa: BLE001  查不到：这一场不按回合数判
                    logger.info("[%s] 核场次时查不到 %s 的回合数：%s: %s", self.label, r, type(e).__name__, e)
            hit = [r for r, t in turns.items() if t and t == limit]
            if len(hit) == 1 and limit > 1:
                got, why = hit[0], f"回合数 {limit}（{turns}）"
        if got is None or got == rnd:
            return
        old = self.label
        self.label = f"{fam}-{got}" + (f"#{rest}" if rest else "")
        keep = {k: self.exam.get(k) for k in ("idol", "produce", "plan")}
        self.exam = {
            **self.exam,
            **exam_fields(self.label, idol=keep["idol"], plan=keep["plan"], produce=keep["produce"]),
        }
        self.exam.pop("turns", None)
        self.exam["crit"] = [0, 0, 0]
        self._calc_pm = {}
        self._audition_defaults(keep["idol"], self._number)
        self._label_note = f"场次按画面改成 {got}（标签 {old}；{why}）"
        logger.warning("[%s] 标签 %s 和画面对不上，%s", self.label, old, self._label_note)

    def _turn_types(self) -> list | None:
        """平台读到的审查日程（考试局面 exam.turn_types：["Vo","Da",…]，下标 0 = 第 1 回合）→ [1 / 2 / 3 / None]；没有 None。"""
        ex = getattr(getattr(self._session, "state", None), "exam", None) if self._session is not None else None
        tt = ex.get("turn_types") if isinstance(ex, dict) else getattr(ex, "turn_types", None)
        if not tt:
            return None
        m = {"Vo": 1, "Da": 2, "Vi": 3, 1: 1, 2: 2, 3: 3}
        return [m.get(t) for t in tt]

    def _fix_obs(self, obs: dict) -> None:
        """初 / N.I.A 的 UIObs：回合属性按审查日程（读到的回合）；倍率按 _exam_permils（画面真值那几项 + 按比例推，不再把当前回合那一项
        填给其余两项，初 PRO 第一局 T2 起读成 [6440, 6440, 6440]、真值 Vo 644% / Da 680% / Vi 428%）。uinet 和搜索看到的都是它。"""
        ex = obs.get("exam") or {}
        known = self._turn_types()
        if known and ex.get("types"):
            ex["types"] = [known[i] if i < len(known) and known[i] else t for i, t in enumerate(ex["types"])]
        if ex.get("types") and not self.exam.get("lesson"):
            pm, _ = self._exam_permils(quiet=True)
            if pm:
                ex["permil"] = [int(pm[a]) for a in ("Vo", "Da", "Vi")]

    def _npc_permil(self, lo: dict) -> None:
        """对手分增减（AuditionNpcEnhancePermil）：P 道具账本 + チャレンジP道具（produce.challenge_items，MASTER / LEGEND 带进来的）
        + N.I.Aキー（平台看到它就记 produce.nia_easy_key，账本里不一定有）→ lo["npc_enhance_permil"]（ExamGnrpa 按它缩放对手分）。
        チャレンジP道具的发动次数账本里没有，按 1 次估（偏低，exam_env.npc_enhance_permil 会列出来）。"""
        from hiro.search.exam_env import NIA_EASY_KEY, npc_enhance_permil

        ps = (
            (getattr(getattr(self._session, "state", None), "produce", None) or {}) if self._session is not None else {}
        )
        ps = ps if isinstance(ps, dict) else getattr(ps, "__dict__", {})
        items = list(lo.get("produce_item_ids") or []) + [
            i for i in ps.get("challenge_items") or () if i not in (lo.get("produce_item_ids") or ())
        ]
        if ps.get("nia_easy_key") and NIA_EASY_KEY not in items:
            items.append(NIA_EASY_KEY)
        try:
            permil, unsure = npc_enhance_permil(items)
        except Exception as e:  # noqa: BLE001  没有主数据：不缩放
            logger.info("[%s] 对手分增减算不了（按 0）：%s: %s", self.label, type(e).__name__, e)
            return
        if permil:
            lo["npc_enhance_permil"] = permil
            logger.info(
                "[%s] 对手分增减 %+d‰%s",
                self.label,
                permil,
                f"（{len(unsure)} 件チャレンジP道具发动次数按 1 次估）" if unsure else "",
            )

    def _exam_permils(self, *, quiet: bool = False) -> tuple[dict | None, str]:
        """拼编成用的三属性倍率 → ({Vo, Da, Vi} 或 None, 说明)。编成给了 permil_fixed 就用；否则画面实读的那几项（0 = 画面没给，初的考试
        常见）为准，没读到的按 实读 / 粗算 的比例（几项取中位数）从粗算推；没有粗算时要三项都读到。一项都没读到 → None（等下一步）。"""
        if self.exam.get("permil_fixed"):
            return dict(self.exam["permil_fixed"]), "编成给的"
        seen = {a: int(v) for a, v in (self._seen_pm or {}).items() if v and int(v) > 0}
        calc = self._calc_pm or {}
        if not seen:
            return None, "画面还没读到倍率"
        if len(seen) == 3:
            return seen, "画面三项都读到了"
        if str(self.label or "").split("#", 1)[0].lower() in HIF_QUALIFY_ROUNDS:
            # H.I.F 的倍率含回忆 / 亲爱度等加成，实读和粗算的比例三项差很多（11-19 选拔第 3 场：画面 [26000, 23000, 11610]、
            # 粗算 [4990, 6390, 3150]，比例 5.2 / 3.6 / 3.7），按比例推会差 40%：要三项都读到（画面一般第一步就全有）
            return None, f"H.I.F 倍率要三项都读到（现在 {sorted(seen)}）"
        if not all(calc.get(a) for a in ("Vo", "Da", "Vi")):
            return None, f"画面只读到 {sorted(seen)}、三属性没记账推不出其余"
        rs = sorted(seen[a] / calc[a] for a in seen)
        f = rs[len(rs) // 2]
        pm = {a: seen.get(a) or int(math.ceil(calc[a] * f / 10.0) * 10) for a in ("Vo", "Da", "Vi")}
        if not quiet:
            logger.info("[%s] 倍率：画面读到 %s，其余按比例 %.3f 从粗算 %s 推 → %s", self.label, seen, f, calc, pm)
        return pm, f"画面 {sorted(seen)} + 按比例 {f:.3f} 推"

    def _audition_defaults(self, idol: str | None, number: int | None) -> None:
        """实机初 / N.I.A 试镜、没有编成时：按主数据的试镜行补审查基准 / 回合数；按培育三属性（选行动页读的 produce.params）和 battle config
        粗算三属性倍率（同客户端 CalcParameterBonusPermils 的曲线部分，hiro.bench_bridge.tower.score_bonus_permils）。
        粗算只当比例用：服务器倍率还乘了亲爱度加成（p_dearness_skill-…-audition_parameter_bonus_multiple，+5%–+50%，账号相关），记账的三属性
        也可能滞后（初第二局中間：粗算 [3060, 2710, 1990]、画面实读 [5910, 4740, 3470]）。拼编成时画面读到的那几项为准，没读到的按
        实读 / 粗算 的比例推（_exam_permils）。UIObs 不用它（uinet 看到的和以前一样）。"""
        from hiro.bench_bridge import tower as tw
        from hiro.search.exam_env import audition_row

        rnd = round_key(self.label)
        ps = (
            (getattr(getattr(self._session, "state", None), "produce", None) or {}) if self._session is not None else {}
        )
        ps = ps if isinstance(ps, dict) else getattr(ps, "__dict__", {})
        produce = ps.get("mode") or self.exam.get("produce")
        try:
            row = audition_row(idol, produce, rnd, number)
        except Exception as e:  # noqa: BLE001  查不到：只靠画面
            logger.warning("[%s] 查不到试镜行（%s %s 档位 %s），审查基准 / 倍率只靠画面：%s: %s", self.label, produce, rnd, number,
                           type(e).__name__, e)  # fmt: skip
            return
        cfg = tw.master().battle_config(row["produceExamBattleConfigId"]) or {}
        if cfg and not any(self.exam.get("crit") or ()):
            self.exam["crit"] = [int(cfg[k]) for k in ("vocal", "dance", "visual")]
        if cfg.get("turn") and not self.exam.get("turns"):
            self.exam["turns"] = int(cfg["turn"])
        params = ps.get("params") or {}
        if all(params.get(a) for a in ("Vo", "Da", "Vi")):
            try:
                pm = tw.score_bonus_permils(
                    row["produceExamBattleConfigId"], [int(params[a]) for a in ("Vo", "Da", "Vi")]
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] 按三属性算倍率失败：%s: %s", self.label, type(e).__name__, e)
            else:
                self._calc_pm = dict(zip(("Vo", "Da", "Vi"), pm))
                logger.info(
                    "[%s] 倍率按三属性 %s 粗算：%s（只当比例用：画面读到的那项为准，其余按比例推）",
                    self.label,
                    params,
                    pm,
                )
                return
        logger.warning("[%s] 培育三属性没记账（%s），倍率要等画面三项都读到", self.label, params)

    def _lesson_screen(self) -> dict | None:
        """平台的课程画面读数（state.exam_session.lesson：turns_total / clear_target / perfect_target / level_ids / …，平台 11-18）。"""
        es = getattr(getattr(self._session, "state", None), "exam_session", None) if self._session is not None else None
        got = getattr(es, "lesson", None) if es is not None else None
        if got is not None and not isinstance(got, dict):
            got = getattr(got, "__dict__", None) or (got.model_dump() if hasattr(got, "model_dump") else None)
        return dict(got) if got else None

    def _live_lesson(self, base: str, obs: dict) -> dict | None:
        """实机课程（hajime-lesson-<vo|da|vi>[-sp|-hard]）→ exam_env 的 lesson 写法（协调 11-18）：回合数、合格线用平台的课程画面读数，
        满分线读到就用、没读到按合格线反查的档位（exam_env.lesson_from_screen）。档位按日程位置定、不是同种同属性第几次（平台核两局实机），
        所以不按次数查主数据。画面读数不全：不拼编成，这一场退回 uinet。"""
        from hiro.search.exam_env import ExamUnsupported, lesson_from_screen

        parts = base.split("-")  # hajime / lesson / vo / sp
        attr = parts[2] if len(parts) > 2 else None
        kind = parts[3] if len(parts) > 3 else "normal"
        screen = self._lesson_screen()
        if attr not in ("vo", "da", "vi") or not screen:
            logger.warning("[%s] 课程标签没有属性或没有课程画面读数（%s），这一场退回 uinet", self.label, screen)
            return None
        try:
            got, notes = lesson_from_screen(self.exam.get("produce"), self.exam.get("idol"), kind, attr, screen)
        except ExamUnsupported as e:
            logger.warning("[%s] 拼课程编成失败，这一场退回 uinet：%s", self.label, e)
            return None
        logger.info("[%s] 课程：%d 回合、合格 %s、满分 %s%s", self.label, got["limit_turn"], got["clear_score"], got["perfect_score"],
                    ("；" + "；".join(notes)) if notes else "")  # fmt: skip
        return got

    def _refresh_lesson(self) -> None:
        """课程中途画面读到了满分线（过了合格线才出现 PERFECTまで）：更新编成里的满分线（ExamGnrpa 每步按它更新目标）。"""
        lo = self.exam_loadout
        if (
            not lo
            or not lo.get("lesson")
            or lo["lesson"].get("source") != "screen"
            or lo["lesson"].get("perfect_score") is not None
        ):
            return
        perfect = (self._lesson_screen() or {}).get("perfect_target")
        if perfect:
            lo["lesson"] = {**lo["lesson"], "perfect_score": int(perfect)}
            logger.info("[%s] 课程满分线画面读到：%s", self.label, perfect)

    def not_executed(self, action, why: str) -> None:
        """执行层说上一个决策没生效（hiro.exec.exam._not_executed）：这一步的历史记录作废，搜索类算法把引擎退回出牌前（undo_last）。"""
        if getattr(self, "_appended", False) and getattr(self, "_history", None):
            self._history.pop()
            self._appended = False
        algo = self.round_algo if self.round_algo is not None else self.algo
        undo = getattr(algo, "undo_last", None)
        if undo is not None:
            undo()
        logger.info("[%s] 上一个决策 %s 没生效（%s）：搜索局面退回", self.label, action, why)

    def on_round_end(self, result: dict[str, Any] | None) -> None:
        logger.info("[%s] 考试出牌统计：bench %d 步，规则 %d 步", self.label, self.stats["bench"], self.stats["rules"])
        self.rules.on_round_end(result)

    # ---------------------------------------------------------------- 决策
    def _obs(self, state, memory, legal: list[Action]) -> tuple[dict, list, str]:
        """UIObs、饮料下标 → 画面槽位、来源（engine / screen）。"""
        from hiro.bench_bridge.screen import from_screen
        from hiro.bench_bridge.uiobs import from_engine

        keys = [a.legal_key() for a in legal]
        self.tracker.observe(state, memory)
        game = getattr(state, "sim_game", None) if OBS_SOURCE == "engine" else None
        if game is not None:
            try:
                eng = _engine_state(game)
                if eng is not None:
                    screen_obs, slot_of = from_screen(state, memory, keys, self.tracker, self.exam)
                    if self._cz is None:
                        self._cz = _customize_ids()
                    obs = from_engine(eng, self.exam, [], customize_ids=self._cz, played=self.tracker.played)
                    return _overlay_screen(obs, screen_obs, state), slot_of, "engine"
            except Exception as e:  # noqa: BLE001
                logger.info("引擎局面转 UIObs 失败，改用画面读数：%s: %s", type(e).__name__, e)
        obs, slot_of = from_screen(state, memory, keys, self.tracker, self.exam)
        return obs, slot_of, "screen"

    def _ctx(self, obs: dict):
        def worlds(k):
            raise RuntimeError("实战没有 GKMS 世界（ctx.worlds 不可用）")

        meta = {
            "produce_id": self.exam.get("produce"),
            "step_type": self.exam.get("step"),
            "plan_type": self.exam.get("plan"),
            "idol_card_id": self.exam.get("idol"),
        }
        full = self.tower_loadout or self.exam_loadout  # 搜索类算法要整份编成（偶像之路 / 初 / N.I.A）
        loadout = dict(full) if full else {"idol_card_id": self.exam.get("idol")}
        if self.exam_loadout is not None and (known := self._turn_types()):
            loadout["turn_types"] = known  # 审查日程（ExamGnrpa 按它排回合属性）
        return SimpleNamespace(obs=obs, budget=DECIDE_BUDGET_S, loadout=loadout, meta=meta, known_draws=self.known_draws,
                               rng=self.rng, worlds=worlds, true_session=None, seed=None,
                               history=list(getattr(self, "_history", ())))  # fmt: skip

    def _fallback(self, state, memory, previews, legal, why: str) -> Decision:
        self.stats["rules"] += 1
        out = self.rules.decide(state, memory, previews, legal)
        out.warnings.append(f"bench 算法这一步没用上，改用规则：{why}")
        return out

    def decide(self, state: Any, memory: Any, previews: list[Preview] | None, legal: list[Action]) -> Decision:
        self._appended = False  # 这一步的 (UIObs, 动作) 记进了 _history 没有（not_executed 撤销用）
        algo = self.round_algo if self.round_algo is not None else self.algo
        if algo is None:
            self.stats["rules"] += 1
            return self.rules.decide(state, memory, previews, legal)
        t0 = time.monotonic()
        try:
            obs, slot_of, origin = self._obs(state, memory, legal)
            self._seen_pm.update({k: v for k, v in (getattr(state, "attr_permils", None) or {}).items() if v})
            if not getattr(self, "_round_checked", True):
                self._round_checked = True
                before = self.label
                self._check_round(obs, state, memory)
                if self.label != before and not self.began:
                    # 场次按画面改了：这一场的算法按新场次重选（#197 review：开局页按场次选的快速 / 搜索会选反）
                    self._pick_algo()
                    algo = self.round_algo if self.round_algo is not None else algo
            if str(self.label or "").lower().startswith(("hajime", "nia")):
                self._fix_obs(obs)
            # 每步给算法的 UIObs 记进运行日志（复盘用：实机局能直接看出 UINet 看到的局面和画面差在哪）
            uiobs_log.info(
                "[%s T%s %s] %s",
                self.label,
                getattr(memory, "turn", None),
                origin,
                json.dumps(obs, ensure_ascii=False, separators=(",", ":")),
            )
            if self.exam_loadout is None and not self._live_built and searched_label(self.label):
                self._live_wait = None
                self.exam_loadout = self._live_exam_loadout(obs)
                self._live_built = self._live_wait is None  # 在等倍率：下一步再拼
            self._refresh_lesson()
            ctx = self._ctx(obs)
            step_note = ""
            wait = (
                self._live_wait
                if (self.exam_loadout is None and algo is not self.algo and self.algo is not None)
                else None
            )
            try:
                if wait:
                    raise _Waiting(wait)
                if not self.began:
                    algo.begin(ctx)
                    self.began = True
                d = algo.decide(ctx)
            except Exception as e:
                if algo is self.algo or self.algo is None:
                    raise
                # 指定的搜索算法（偶像之路 / 初 / N.I.A）这一步搜不了：这一步用 H.I.F 的算法（uinet），理由里写明原因。
                # 开局就失败（拼不出编成等）的，这一场剩下的步都用 uinet，不每步重试。
                why = f"{type(e).__name__}: {e}"
                if not isinstance(e, _Waiting):
                    logger.warning(
                        "[%s] %s 这一步搜不了，用 %s：%s", self.label, self.round_name, algo_spec().split(":")[0], why
                    )
                if isinstance(e, _Waiting):
                    step_note = f"{self.round_name} 等画面读到倍率（{e}）"
                elif not self.began or getattr(
                    e, "whole_round", False
                ):  # 开局失败 / 搜索自己判定这一场的模型不可信：整场 uinet
                    what = "开局失败" if not self.began else "不可信"
                    self.round_algo, self.round_note = self.algo, f"{self.round_name} {what}（{why}）"
                    self.round_name = algo_spec().split(":")[0]
                else:
                    step_note = f"{self.round_name} 这一步搜不了（{why}）"
                if not self._hif_began:
                    self.algo.begin(ctx)
                    self._hif_began = True
                d = self.algo.decide(ctx)
            act = tuple(d.action)
        except Exception as e:  # noqa: BLE001
            logger.warning("bench 算法出错，这一步用规则：%s: %s", type(e).__name__, e)
            return self._fallback(state, memory, previews, legal, f"{type(e).__name__}: {e}")
        if list(act) not in obs["legal"]:
            return self._fallback(
                state, memory, previews, legal, f"算法给的动作 {list(act)} 不在合法动作 {obs['legal']} 里"
            )
        if act[0] == "play":
            action = Action.play_card(int(act[1]))
            card = next((c for c in state.hand if c.slot == int(act[1])), None)
            what = card.name if card else f"手牌 {act[1]}"
            if card is not None:
                self.tracker.played_card(card)
        elif act[0] == "drink":
            slot = slot_of[int(act[1])]
            action = Action.use_drink(slot)
            dv = next((x for x in state.drinks if x.slot == slot), None)
            what = dv.name if dv is not None else f"饮料 {slot}"
            if dv is not None:
                self.tracker.drank(getattr(dv, "drink_id", None))
        else:
            action = Action.skip()
            what = "结束回合"
        keys = {a.legal_key() for a in legal}
        if action.legal_key() not in keys:
            return self._fallback(state, memory, previews, legal, f"{action.legal_key()} 不在 hiro 的合法动作里")
        self.stats["bench"] += 1
        if searched_label(self.label):
            self._history.append((copy.deepcopy(obs), list(act)))
            self._appended = True
        meta = dict(getattr(d, "meta", None) or {})
        cands = []
        for a, p in (meta.get("top") or [])[:3]:
            try:
                k = a[0]
                ca = (
                    Action.play_card(int(a[1]))
                    if k == "play"
                    else Action.use_drink(slot_of[int(a[1])])
                    if k == "drink"
                    else Action.skip()
                )
                cands.append(Candidate(ca, note=f"概率 {p}"))
            except Exception:  # noqa: BLE001
                continue
        q = meta.get("score_quantiles")
        name = self.round_name or algo_spec().split(":")[0]
        reason = f"{name}（{'引擎局面' if origin == 'engine' else '画面读数'}）：{what}"
        if self.round_note or step_note:
            note = step_note or self.round_note
            if step_note:
                name = algo_spec().split(":")[0]
            reason = f"{name}（兜底：{note}；{'引擎局面' if origin == 'engine' else '画面读数'}）：{what}"
        if q:
            reason += f"；终局分预测中位 {q.get('50%')}"
        if getattr(self, "_label_note", None):
            reason += f"；{self._label_note}"
        return Decision(
            action=action,
            reason=reason,
            source="bench",
            confidence=None,
            candidates=cands,
            seconds=time.monotonic() - t0,
            decider="battle",
        )
