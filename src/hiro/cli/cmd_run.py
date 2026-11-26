"""`hiro run …`：开局 / 扫描（交给 hiro 服务跑）、看状态和事件流、暂停 / 接手 / 继续 / 停止。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from hiro.cli import client
from hiro.cli.jobs import KINDS, LIVE, LLM_VARS
from hiro.cli.registry import Arg, CliError, Risk, command

_TERMINAL_WAIT = 0.5


PARAMS = (
    "command",
    "mode",
    "idol",
    "layers",
    "seconds",
    "no_llm",
    "scenario",
    "what",
    "dry_run",
    "fix",
    "main_attr",
    "support_set",
    "memory_set",
    "difficulty",
    "items",
    "chapter",
    "until",
    "final_rank",
    "plugins",
    "sources",
    "only",
    "prepare",
    "prepare_note",
    "prepare_what",
)


def _params(a: dict) -> dict:
    return {k: a[k] for k in PARAMS if a.get(k) not in (None, False)}


def _precheck(a: dict) -> None:
    import sys

    from hiro.cli import jobs
    from hiro.cli.registry import CliError

    if a["kind"] == "fit-window" and jobs.sys.platform != "win32":
        raise CliError("windows_only", "调窗口大小只用于 Windows DMM 版", "Mac 上 PlayCover 的窗口不用调")
    if a["kind"] == "write-formation":
        from hiro.cli import paths
        from hiro.cli.home import hiro_home

        if not paths.plan_path(hiro_home()).is_file():
            raise CliError("no_plan", "还没有编成方案，没东西可写", "先出一份：hiro plan formation --idol <偶像卡编号>")
        if not a.get("dry_run"):
            try:
                plan = json.loads(paths.plan_path(hiro_home()).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                plan = {}
            what = [
                w.strip() for w in (a.get("what") or "support,memory").split(",") if w.strip() in ("support", "memory")
            ]
            _check_plan_sets(plan, what, ((plan.get("idol") or {}).get("idol_card_id") or ""))
        if not a.get("dry_run") and "memory" in (a.get("what") or "support,memory"):
            _check_memory_freshness(paths.plan_path(hiro_home()), hiro_home())
    if a["kind"] in ("hajime", "nia") and (a.get("command") or "new") == "new" and not a.get("idol"):
        raise CliError(
            "bad_args", "开新的一局要指定偶像卡", "加 --idol <偶像卡编号>，例如 i_card-skin-shro-3-007", exit_code=2
        )
    if a["kind"] == "daily" and a.get("items"):
        bad = [i for i in a["items"].split(",") if i and i not in jobs.DAILY_ITEMS]
        if bad:
            raise CliError(
                "bad_args", f"日常里没有这些项：{'、'.join(bad)}", "可选：" + "、".join(jobs.DAILY_ITEMS), exit_code=2
            )
    if a["kind"] == "nia" and a.get("difficulty") == "regular":
        raise CliError("bad_args", "N.I.A 没有 REGULAR，只有 pro / master", "改成 --difficulty pro", exit_code=2)
    _check_formation_plan(a)
    del sys


FORMATION_KINDS = ("hif", "hajime", "nia")  # 开局要走编成步骤（第 2、3 步）的模式


def _check_memory_freshness(plan_path: Path, home: Path) -> None:
    """写回忆编成靠方案里记的库存位置（第几行第几列，按库存扫描时数的）：
    - 回忆扫描之后又打完过培育：每局多一份回忆，列表位置变了（Mac 11-24 16:44：11-23 11:22 扫的库存，
      20:33 那局「初」之后再写，翻了 28 格没找到才停）；
    - 方案比最近一次回忆扫描还早：方案里的位置是旧库存的。
    两种都在写之前说清楚，不进游戏。没有回忆扫描记录（导入的库存）时不判断。"""
    from datetime import datetime

    from hiro.cli.jobs import jobs_dir
    from hiro.cli.store import Store

    db = home / "hiro.db"
    if not db.is_file():
        return
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    st = Store(db)
    # 方案来自分片库存（设计 5.6 / D14）：看方案用到的那几片里最早的一次扫描，不看旧的整表快照
    used = {c["slice"] for c in (plan.get("memory") or {}).get("chosen") or [] if c.get("slice")}
    if used:
        cov = st.slice_coverage()
        times = [cov[k]["scanned_at"] for k in used if k in cov]
        if len(times) < len(used):
            return
        scanned = min(times)
    else:
        history = st.history("memories")
        if not history:
            return
        scanned = history[0]["scanned_at"]
    try:
        scanned_ts = datetime.strptime(scanned, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return
    idol = (plan.get("idol") or {}).get("idol_card_id") or "<偶像卡编号>"
    replan = f"重新出方案：hiro plan formation --idol {idol}；再写编成"
    played = []
    for p in jobs_dir(home).glob("*/job.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if j.get("kind") in FORMATION_KINDS and j.get("state") == "done" and (j.get("ended_at") or 0) > scanned_ts:
            played.append(j)
    if played:
        raise CliError(
            "inventory_stale",
            f"回忆库存是 {scanned} 扫的，之后又打完了 {len(played)} 局培育（每局会多一份回忆，列表位置变了）",
            f"先重扫回忆：hiro run start scan-memories；{replan}",
        )
    if (plan.get("created") or "") < scanned:
        raise CliError(
            "plan_stale",
            f"编成方案是 {plan.get('created') or '?'} 出的，之后回忆库存重扫过（{scanned}），方案里记的位置是旧的",
            replan,
        )


def _check_formation_plan(a: dict) -> None:
    """新开一局（H.I.F 只算选拔）又没指定编成号时，要有这张卡 × 这个模式的开局编成方案，而且已经写进游戏，
    否则任务进了游戏流程才报「开局要编成号」或第 2 / 3 步核对对不上退出（Mac 11-24：方案是给月村手毬的，H.I.F、藤田ことね的「初」都这样白开了一次）。

    2026-11-26 起方案按「偶像卡 × 模式」存（plans/<模式>/<偶像卡>.json）：这张卡在这个模式没有方案就按社区攻略自动出一份
    （编成位号沿用这个模式上一份方案的），再让玩家先「写编成」；有方案就把它设为当前方案（开局核对、写编成都读当前方案）。"""
    from hiro.brain import formation_plan as fp
    from hiro.cli import paths
    from hiro.cli.home import hiro_home

    kind = a["kind"]
    if kind not in FORMATION_KINDS or (a.get("command") or "new") != "new":
        return
    if kind == "hif" and (a.get("mode") or "qualify") == "main":
        return  # 本战不走编成步骤，用的是选拔得到的回忆
    # 支援卡 / 回忆分开看：两样都指定了编成号才不看方案；只给了一样，另一样还要方案里有（CodeRabbit #88）
    explicit = {
        "support": a.get("support_set") or os.environ.get("HIRO_SUPPORT_SET"),
        "memory": a.get("memory_set") or os.environ.get("HIRO_MEMORY_SET"),
    }
    given = {k: bool(v) for k, v in explicit.items()}
    if all(given.values()):
        return
    env_plan = os.environ.get("HIRO_FORMATION_PLAN")
    if env_plan == "off":
        return
    idol = (a.get("idol") or "").replace("i_card-skin-", "i_card-")
    home = hiro_home()
    # 显式指定了别的方案文件：照以前只核对偶像。指向当前方案的（paths.apply_to_process 补进进程环境的缺省值，
    # 常驻服务出过一次方案后就一直在）不算显式
    if env_plan and Path(env_plan).resolve() != paths.plan_path(home).resolve():
        _check_plan_file(Path(env_plan), idol, kind, given)
        return
    todo = [w for w in ("support", "memory") if not given[w]]  # 开局给了编成号的那样直接用游戏里编好的，不用写

    def not_written(plan: dict, name: str, lead: str) -> CliError:
        """方案里没指定的那几样还没写进游戏：只列这几样的编成位号，写编成也只写这几样。"""
        parts = fp.unwritten(plan, kind, todo)
        label = {"support": "支援卡", "memory": "回忆"}
        where = "、".join(f"{label[w]}编成 {(plan.get(w) or {}).get('set')} 号" for w in parts)
        what = "" if len(parts) == 2 else f" --what {parts[0]}"
        return CliError(
            "plan_not_written",
            f"{lead}（{where}），游戏里{'这两个编成位' if len(parts) == 2 else '这个编成位'}还是之前的，要先写进去",
            f"写进游戏：hiro run start write-formation --scenario {kind}{what}；写完再开局",
        )

    found = fp.find_plan(home, idol, kind) if idol else None
    if found is None:
        if not idol:
            _check_plan_file(paths.plan_path(home), idol, kind, given)
            return
        from hiro.cli.cmd_plan import make_plan
        from hiro.cli.registry import CliError as _E

        s_set, m_set = fp.inherited_sets(home, kind)  # 没指定的沿用这个模式上一份方案的位号
        if sv := explicit["support"]:
            s_set = int(sv)
        if mv := explicit["memory"]:
            m_set = int(mv)
        try:
            plan, _path = make_plan(home, idol, kind, difficulty=a.get("difficulty"), support_set=s_set,
                                    memory_set=m_set, source="auto")  # fmt: skip
        except _E as e:
            if e.code == "no_inventory":
                raise CliError(
                    "no_plan",
                    "这张卡在这个模式还没有开局编成方案，也没有支援卡和回忆的库存，出不了方案",
                    "先扫描支援卡和回忆（hiro run start scan-supports / scan-memories），开局时会按社区攻略自动出方案",
                ) from None
            raise
        name = (plan.get("idol") or {}).get("name") or idol
        if _auto_formation() and _prepare_in_job(a, home, plan, kind, todo):
            return
        _check_plan_sets(plan, [w for w in ("support", "memory") if not given[w]], idol, kind,
                         lead=f"已按社区攻略给「{name}」出了这个模式的开局编成方案，")  # fmt: skip
        raise not_written(plan, name, f"已按社区攻略给「{name}」出了这个模式的开局编成方案")
    plan, path = found
    if path != paths.plan_path(home):
        fp.activate(home, plan)  # 开局核对、写编成都读当前方案
    name = (plan.get("idol") or {}).get("name") or idol
    if _auto_formation() and _prepare_in_job(a, home, plan, kind, todo):
        return
    _check_plan_sets(plan, [w for w in ("support", "memory") if not given[w]], idol, kind, lead=f"「{name}」的")
    if fp.needs_write(plan, kind, todo):
        raise not_written(plan, name, f"「{name}」的开局编成方案还没写进游戏")


def _auto_formation() -> bool:
    """D4（11-26）：开局时方案没定编成号 / 没写进游戏，在同一个开局任务里自己挑空白编成位、写好再开局，不再反问玩家。
    D7（11-26 用户本人定）：缺省打开；HIRO_AUTO_FORMATION=0 关掉，回到「报 plan_no_set / plan_not_written，让玩家定编成号、先写编成」。"""
    return os.environ.get("HIRO_AUTO_FORMATION", "1").strip() != "0"


_MODE_NAME = {"hif": "H.I.F", "hajime": "「初」", "nia": "N.I.A"}


def _prepare_in_job(a: dict, home: Path, plan: dict, kind: str, todo: list[str]) -> bool:
    """方案没定编成号或没写进游戏时，改成在开局任务里先准备（a["prepare"]，任务由 hiro.runtime.prepare_start 包一层）：
    - 没定编成号：最近一次读的编成位现状（读完后没写过这个模式的编成）里挑空着的最小号；没读过就进游戏先读再挑；
      读过但都满了，报 formations_full 问玩家覆盖哪一个（不替玩家覆盖已有编成）。
    - 编成号定了、没写进去：任务里先写编成（写完回读核对）。
    什么都不用做时返回 False（照旧往下核对）。"""
    from hiro.brain import formation_plan as fp
    from hiro.cli.store import open_store

    label = {"support": "支援卡", "memory": "回忆"}
    missing = [w for w in todo if (plan.get(w) or {}).get("set") is None]
    steps: list[str] = []
    # 编成号已经定了、还没写进去的那几样（例如支援卡定了号、回忆没定）：只有空位或初星助手写过的位才自动写，
    # 玩家自己的编成照旧让玩家确认覆盖；检查放在保存方案之前，免得留下挑好的另一样编成号（#134 review）
    given = [w for w in fp.unwritten(plan, kind, todo) if w not in missing]
    if missing and not all(_slot_ours_or_blank(home, kind, w, (plan.get(w) or {}).get("set")) for w in given):
        return False
    if missing:
        meta, rows = open_store().latest("formations")
        fresh = [r for r in rows if r.get("mode") == kind] if meta is not None else []
        if meta is not None and fresh and not _wrote_formation_since(meta.get("scanned_at"), kind):
            picked, unread, reused = {}, [], set()
            for w in missing:
                if not any(r.get("kind") == w for r in fresh):
                    # 这一样没读过（开局前准备只读要挑号的那几样，#157 review）：不能当成全满，任务里先读再挑
                    unread.append(w)
                    continue
                blanks = sorted(int(r["set"]) for r in fresh if r.get("kind") == w and r.get("filled") == 0)
                if not blanks and (ours := own_full_slots(home, kind, w, fresh)):
                    picked[w] = ours[0]  # D26：都满了，覆盖初星助手自己写过的号
                    reused.add(w)
                    continue
                if not blanks:
                    raise CliError(
                        "formations_full",
                        f"{_MODE_NAME.get(kind, kind)}的{label[w]}编成位都满了，没有空着的可以放这套编成方案",
                        f"选一个可以覆盖的编成位：hiro plan formation --idol {(plan.get('idol') or {}).get('idol_card_id') or '<偶像卡编号>'} "
                        f"--mode {kind} --support-set <号> --memory-set <号>；或者开局时用 --support-set / --memory-set 直接开游戏里已经编好的",
                    )
                picked[w] = blanks[0]
            for w, n in picked.items():
                plan[w] = dict(plan.get(w) or {}, set=n)
            if picked:
                fp.save_plan(home, plan, kind)
                steps.append(
                    "把编成方案写进"
                    + "、".join(
                        f"{label[w]}编成 {n} 号（{'都满了，覆盖初星助手之前写过的' if w in reused else '空着的'}）"
                        for w, n in picked.items()
                    )
                )
            if unread:
                steps.append(
                    f"先读一遍{_MODE_NAME.get(kind, kind)}的{'、'.join(label[w] for w in unread)}编成位，挑空着的把编成方案写进去"
                )
        else:
            steps.append(f"先读一遍{_MODE_NAME.get(kind, kind)}的编成位，挑空着的把编成方案写进去")
    elif left := fp.unwritten(plan, kind, todo):
        # 编成号是沿用的：只有那个位是空的、或者是初星助手自己写过的，才自动写；玩家自己的编成照旧让玩家确认覆盖（CodeRabbit #95）
        if not all(_slot_ours_or_blank(home, kind, w, (plan.get(w) or {}).get("set")) for w in left):
            return False
        steps.append("把编成方案写进" + "、".join(f"{label[w]}编成 {(plan.get(w) or {}).get('set')} 号" for w in left))
    if not steps:
        return False
    if "memory" in missing or "memory" in fp.unwritten(plan, kind, todo):
        # 要写回忆：库存扫描之后又打过培育、或方案比扫描旧，就在出确认卡之前说清楚（手动写编成那条路同样的检查，CodeRabbit #95）
        from hiro.cli import paths

        _check_memory_freshness(paths.plan_path(home), home)
    a["prepare"] = True
    a["prepare_what"] = ",".join(todo)
    a["prepare_note"] = "；".join(steps) + "（只写空着的或初星助手自己写过的编成位，写完回读核对）"
    return True


def own_full_slots(home: Path, kind: str, what: str, rows: list) -> list[int]:
    """编成位读数 rows 里这个模式的 what 编成号中初星助手写过的那几号（从小到大）。
    D26（11-26 用户本人定）：编成位都满了时直接覆盖这些号，不再停下来问；玩家自己配的照旧不动。"""
    nums = sorted({int(r["set"]) for r in rows if r.get("kind") == what and r.get("mode", kind) == kind})
    return [n for n in nums if slot_written_by_us(home, kind, what, n)]


def slot_written_by_us(home: Path, kind: str, what: str, n) -> bool:
    """这个模式的 what n 号编成位是初星助手写过的（formation-slots.json 记过，或哪份方案的 written 记着这个号）。"""
    from hiro.brain import formation_plan as fp
    from hiro.cli import paths

    if n is None:
        return False
    if int(n) in fp.recorded_slots(home, kind, what):
        return True
    files = list((home / "plans" / kind).glob("*.json")) if (home / "plans" / kind).is_dir() else []
    for f in [*files, paths.plan_path(home)]:
        w = ((fp.load(f) or {}).get("written") or {}).get(kind) or {}
        if what in (w.get("what") or ()) and w.get(f"{what}_set") == n:
            return True
    return False


def _slot_ours_or_blank(home: Path, kind: str, what: str, n) -> bool:
    """这个模式的 what（support / memory）n 号编成位能不能直接写：初星助手写过（哪份方案的 written 记着这个号），
    或者最近一次读的编成位现状（读完没再写过）里它是空的。"""
    from hiro.cli.store import open_store

    if n is None:
        return False
    if slot_written_by_us(home, kind, what, n):
        return True
    meta, rows = open_store().latest("formations")
    if meta is None or _wrote_formation_since(meta.get("scanned_at"), kind):
        return False
    return any(
        r.get("mode") == kind and r.get("kind") == what and r.get("set") == n and r.get("filled") == 0 for r in rows
    )


def _check_plan_file(path: Path, idol: str, kind: str, given: dict) -> None:
    """只看一个方案文件（HIRO_FORMATION_PLAN 指定的，或没给偶像时的当前方案）：有没有、偶像对不对。"""
    hint = (
        f"先给这位偶像出编成方案：hiro plan formation --idol {idol or '<偶像卡编号>'} --mode {kind}；"
        f"再写进游戏：hiro run start write-formation --scenario {kind}；然后再开局。"
        "也可以用 --support-set / --memory-set 指定游戏里已经编好的编成号"
    )
    if not path.is_file():
        raise CliError("no_plan", "还没有开局编成方案，不知道这局用哪套支援卡和回忆", hint)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("no_plan", "开局编成方案读不出来", hint) from None
    want = (plan.get("idol") or {}).get("idol_card_id")
    if idol and want and idol != want:
        name = (plan.get("idol") or {}).get("name") or want
        raise CliError("plan_other_idol", f"现在的编成方案是给「{name}」出的，这局是 {idol}", hint)
    _check_plan_sets(plan, [w for w in ("support", "memory") if not given[w]], idol, kind)


def _check_plan_sets(plan: dict, what, idol: str = "", kind: str = "", lead: str = "") -> None:
    """方案里要用的编成号（支援卡 / 回忆）没定时，在出待确认之前就说清楚（11-26 Mac：方案没带编成号，
    确认开局后才报「new 开局要编成号」）。"""
    from hiro.cli.registry import CliError

    missing = [w for w in what if (plan.get(w) or {}).get("set") is None]
    if not missing:
        return
    names = "、".join({"support": "支援卡", "memory": "回忆"}[w] for w in missing)
    raise CliError(
        "plan_no_set",
        f"{lead}编成方案还没定{names}存进游戏里的哪个编成位，开局时不知道选哪一套",
        f"重新出方案时带上编成号：hiro plan formation --idol {idol or '<偶像卡编号>'}{f' --mode {kind}' if kind else ''} --support-set <号> --memory-set <号>"
        "（哪些编成位空着用 hiro info formations 看，没读过就先 hiro run start scan-formations）；"
        "或者开局时直接加 --support-set / --memory-set",
    )


def _plan_file_used(a: dict) -> Path | None:
    """这个任务要读的开局编成方案文件；任务不读方案（不是开局 / 写编成，或两样编成号都指定了）时返回 None。"""
    from hiro.cli import paths
    from hiro.cli.home import hiro_home

    if a["kind"] == "write-formation":
        return paths.plan_path(hiro_home())
    if a["kind"] not in FORMATION_KINDS or (a.get("command") or "new") != "new":
        return None
    if a["kind"] == "hif" and (a.get("mode") or "qualify") == "main":
        return None
    if (a.get("support_set") or os.environ.get("HIRO_SUPPORT_SET")) and (
        a.get("memory_set") or os.environ.get("HIRO_MEMORY_SET")
    ):
        return None
    env_plan = os.environ.get("HIRO_FORMATION_PLAN")
    if env_plan == "off":
        return None
    return Path(env_plan) if env_plan else paths.plan_path(hiro_home())


def plan_snapshot(a: dict) -> dict | None:
    """出待确认时固定一份编成方案：内容哈希（confirm 时核对）+ 摘要（确认卡按它画）。
    确认卡原来每分钟读一次 `plan show`、执行时任务又读方案文件，期间方案被重新出过就会卡上和实际写的不一致（PR #79 CodeRabbit）。
    方案文件不存在时也算一份快照（哈希为空）：确认时它出现了或没了都能察觉。"""
    import hashlib

    from hiro.cli.cmd_plan import summarize

    path = _plan_file_used(a)
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return {"digest": "", "label": "编成方案", "plan": None}
    try:
        plan = json.loads(raw.decode("utf-8"))
    except ValueError:
        plan = None
    return {
        "digest": hashlib.sha256(raw).hexdigest(),
        "label": "编成方案",
        "plan": summarize(plan) if isinstance(plan, dict) else None,
    }


EXAM_KINDS = ("hif", "hajime", "nia", "tower")  # 有考试、出牌靠算法的任务


def run_access(a: dict) -> str:
    """操作类别（11-25 review：权限档参考 dsh）：read 只看画面；daily 领日常（用户说不用确认）；
    blank 写空白编成位（用户说不用确认）；write 改游戏（コミュ、竞赛编队）；overwrite 会覆盖已有编成；all 用掉培育次数 / 资源（开局、续打、初星課題）。"""
    kind = a["kind"]
    if a.get("command") == "resume" or kind in EXAM_KINDS or kind == "main-task":
        return "all"
    if kind == "check-settings" and a.get("fix"):
        return "write"  # 改游戏里的设置开关（D3）
    if kind.startswith("scan-") or kind in ("check-settings", "selftest", "collect-all"):
        return "read"
    if kind == "daily":
        return "daily"
    if kind in ("write-formation", "contest-unit") and a.get("dry_run"):
        return "read"
    if kind == "write-formation":
        return "blank" if _formation_slots_blank(a) else "overwrite"
    return "write"


def _formation_slots_blank(a: dict) -> bool:
    """要写的编成位（方案里的支援卡 / 回忆编成号）在最近一次读的编成位现状里都是空的。读不到、读的是别的模式都按不空算。"""
    from hiro.cli import paths
    from hiro.cli.home import hiro_home
    from hiro.cli.store import open_store

    try:
        plan = json.loads(paths.plan_path(hiro_home()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    mode = a.get("scenario") or "hif"
    what = [w.strip() for w in (a.get("what") or "support,memory").split(",") if w.strip()]
    want = {(w, (plan.get(w) or {}).get("set")) for w in what}
    if any(n is None for _, n in want):
        return False
    meta, rows = open_store().latest("formations")
    if meta is None or _wrote_formation_since(meta.get("scanned_at"), mode):
        return False  # 读完以后写过编成：读到的「空」已经不准（CodeRabbit #79）
    filled = {(r.get("kind"), r.get("set")): r.get("filled") for r in rows if r.get("mode") == mode}
    return all(filled.get(k) == 0 for k in want)


def _wrote_formation_since(scanned_at: str | None, mode: str) -> bool:
    """编成位现状读完之后，有没有开过写这个模式编成的任务（对照不算；没跑完的也算，可能写了一半）。"""
    from hiro.cli.home import hiro_home
    from hiro.cli.jobs import jobs_dir

    try:
        t0 = time.mktime(time.strptime(scanned_at or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True
    for f in jobs_dir(hiro_home()).glob("*/job.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        p = j.get("params") or {}
        # 写编成任务（对照不算），和带 prepare 的开局任务（任务里先写编成，CodeRabbit #95）都算
        writes = j.get("kind") == "write-formation" and not p.get("dry_run") and (p.get("scenario") or "hif") == mode
        writes = writes or (bool(p.get("prepare")) and j.get("kind") == mode)
        if not writes:
            continue
        if (j.get("started_at") or 0) >= t0:
            return True
    return False


def _summary(a: dict) -> str:
    kind = KINDS.get(a["kind"])
    text = kind.summary(_params(a)) if kind else f"开始任务 {a['kind']}"
    if a["kind"] in EXAM_KINDS and (why := exam_algo_problem()):
        text += f"\n⚠ 考试出牌只能用规则打，分数会明显低：出牌算法没加载上（{why}）"
    return text


_exam_algo_checked: list = []


def exam_algo_problem() -> str | None:
    """任务环境里考试出牌算法（hiro.bench_bridge，缺省 uinet）能不能加载；不能时返回一句原因。
    在任务同样的环境里跑一次 `python -m hiro.bench_bridge check`（不下载），同一进程只查一次。
    Mac 11-24 18:27：测试目录缺主数据，uinet 没加载上，整局考试退回规则，只有日志里一行 WARNING。"""
    if _exam_algo_checked:
        return _exam_algo_checked[0]
    import subprocess

    from hiro.cli import paths
    from hiro.cli.home import hiro_home
    from hiro.cli.jobs import REPO

    env = paths.apply_defaults(os.environ.copy(), hiro_home())
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUTF8"] = "1"
    try:
        r = subprocess.run(
            [sys.executable, "-m", "hiro.bench_bridge", "check"],
            env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        why = None if r.returncode == 0 else _first_reason(r.stdout + r.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        why = f"检查没跑完：{type(e).__name__}"
    _exam_algo_checked.append(why)
    return why


def _first_reason(out: str) -> str:
    """bench_bridge check 的输出里挑写给人看的那句（「缺 Python 模块 …」/「下载失败 …」），没有就第一行。"""
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    pick = next((x for x in lines if x.startswith(("缺 ", "下载失败", "模型校验"))), lines[0] if lines else "原因不明")
    return pick[:160]


@command(
    "run start",
    Risk.ACT,
    "开一个任务（同一时间只能一个）：hif H.I.F 培育、hajime「初」培育、nia N.I.A 培育、tower 偶像之路、daily 日常、main-task 初星課題（关完成弹窗、读 NEXT）、story 看初星コミュ（逐话再生并跳过）、contest-unit 竞赛编队（おまかせ重排、读总合力）、scan-supports / scan-memories 扫描、selftest 自检。"
    "会动游戏，第一次返回待确认，用户同意后才开始。",
    args=(
        Arg("kind", "任务种类", choices=tuple(KINDS)),
        Arg("--command", "hif：new 新开 / resume 接着打进行中的培育", choices=("new", "resume")),
        Arg("--mode", "hif：qualify 选拔 / main 本战", choices=("qualify", "main")),
        Arg("--idol", "hif / hajime / nia：偶像卡编号（如 i_card-skin-shro-3-007）；tower：路线的偶像名（如 篠澤広）"),
        Arg("--main-attr", "hif / hajime / nia：主属性（不给按编成方案）", choices=("Vo", "Da", "Vi")),
        Arg("--support-set", "hif / hajime / nia：用游戏里第几号支援卡编成（不给按编成方案）", type="int"),
        Arg("--memory-set", "hif / hajime / nia：用第几号回忆编成", type="int"),
        Arg("--difficulty", "hajime：regular / pro / master；nia：pro / master", choices=("regular", "pro", "master")),
        Arg(
            "--final-rank",
            "hajime：期末要到第几名（如 1），没到就用再挑戦（免费 / チケット，不花ジュエル）",
            type="int",
        ),
        Arg(
            "--items",
            "daily：只领这几项（逗号分隔：activity_funds、presents、assignment、contest、mission_reward、club_reward）",
        ),
        Arg("--layers", "tower：打几层", type="int"),
        Arg("--chapter", "story：初星コミュ第几章（缺省 1）", type="int"),
        Arg("--until", "story：看到第几话为止（缺省看完所有解锁的新话）", type="int"),
        Arg("--seconds", "selftest：跑几秒", type="int"),
        Arg("--no-llm", "决策只用规则和算法，不调用模型", type="bool"),
        Arg("--scenario", "write-formation：写哪个模式的编成", choices=("hif", "hajime", "nia")),
        Arg("--what", "write-formation：写哪些（support / memory，逗号分隔，缺省都写）"),
        Arg("--only", "collect-all：只做这几步（account,idols,supports,memories,formations，逗号分隔；缺省全做）"),
        Arg("--dry-run", "write-formation：只看差在哪几格，不改；contest-unit：只读总合力，不重排", type="bool"),
        Arg("--fix", "check-settings：快进 / 跳过演出那几项不对的顺手改好（会改游戏设置）", type="bool"),
        Arg(
            "--plugins",
            "这一局换用的插件（覆盖 HIRO_HOME/strategy.json）：决策点=插件 id，逗号分隔，如 produce.ACTION_SELECT=uinet,exam=gnrpa；查已装插件用 hiro plugins list",
        ),
        Arg("--sources", "这一局用的数据来源插件 id，逗号分隔（覆盖 strategy.json 的 sources）"),
        Arg(
            "--prepare",
            "开局前先在同一个任务里挑空白编成位、把编成方案写进去（开局检查按需自动加上，不用手填）",
            type="bool",
        ),
        Arg("--prepare-note", "开局前准备做什么（开局检查填，给确认卡看）"),
        Arg("--prepare-what", "开局前准备哪几样（support / memory，逗号分隔；开局检查按没指定编成号的填）"),
    ),
    confirm_summary=_summary,
    precheck=_precheck,
    access=run_access,
    snapshot=plan_snapshot,
)
def _start(a: dict) -> dict:
    # 对局用哪个模型：跟着发起开局的这个进程的环境（桌面程序按模型设置传；终端里就是自己 shell 的 HIRO_LLM）
    llm_env = {k: os.environ[k] for k in LLM_VARS if os.environ.get(k)}
    return client.data("start", {"kind": a["kind"], "params": _params(a), "llm_env": llm_env})


@command(
    "run status", Risk.READ, "现在在跑什么任务（状态：running / paused / takeover / stopping），以及最近 10 个任务。"
)
def _status(a: dict) -> dict:
    return client.data("status")


@command(
    "run events",
    Risk.READ,
    "读一个任务的事件（每一步的日志和结果）。--since 从第几条开始；--follow 一直跟到任务结束（每条事件一行 JSON）。",
    args=(
        Arg("--job", "任务编号（缺省：当前任务）"),
        Arg("--since", "从第几条事件开始", type="int", default=0),
        Arg("--follow", "一直跟到任务结束", type="bool", agent=False),
    ),
)
def _events(a: dict) -> dict:
    body = {"job": a["job"], "since": a["since"]}
    if not a["follow"]:
        return client.data("events", body)
    since, out = a["since"], None
    while True:
        out = client.data("events", {**body, "since": since})
        for ev in out["events"]:
            sys.stdout.write(json.dumps({"event": ev}, ensure_ascii=False) + "\n")
            since = ev["seq"] + 1
        sys.stdout.flush()
        body["job"] = out["job"]
        if out["state"] not in LIVE + ("stopping",):
            return {"job": out["job"], "state": out["state"], "next": since}
        time.sleep(_TERMINAL_WAIT)


def _control(action: str, help_: str):
    @command(f"run {action}", Risk.READ, help_, args=(Arg("--job", "任务编号（缺省：当前任务）"),))
    def _f(a: dict) -> dict:
        return client.data(action, {"job": a["job"]})

    return _f


_control("pause", "暂停当前任务：主循环停在下一个检查点，不再点击。")
_control("takeover", "用户接手：自动操作停下，用户在游戏里手动点；之后 hiro run resume 从当前画面接着打。")
_control("resume", "继续：从当前画面重新判断在哪一页，接着自动打。")
_control("stop", "停止当前任务（不会放弃培育：游戏停在原地，之后可以 resume 续跑）。")


@command(
    "run report",
    Risk.READ,
    "一局的成绩摘要：步数、决策来源（规则 / 算法 / LLM / 兜底）、LLM 次数、故障恢复次数、每场考试分数。",
    args=(Arg("--job", "任务编号", required=True),),
)
def _report(a: dict) -> dict:
    from hiro.cli import report

    return report.summarize(report.trace_base(report.check_job(a["job"])))


@command(
    "run review",
    Risk.READ,
    "生成一局的复盘页（逐回合出牌、候选、状态曲线，带截图），返回本机文件路径。只给有考试的对局。",
    args=(Arg("--job", "任务编号", required=True),),
)
def _review(a: dict) -> dict:
    from hiro.cli import report

    jid = report.check_job(a["job"])
    base = report.trace_base(jid)  # 续打任务：复盘整局
    if not report.segments(report.traces_dir(), base):
        raise CliError("no_trace", f"任务 {jid} 没有对局记录", "只有对局才有复盘页")
    html = report.build_review_html(base, report.traces_dir())
    from hiro.cli.home import ensure_home

    out = ensure_home() / "jobs" / jid / "review.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"job": jid, "path": str(out), "url": f"review/{jid}.html"}
