"""偶像之路的本机缓存（只给偶像之路同编成重试用；H.I.F 不读种子 / 不读存档的规矩不变）。

两类文件，目录 HIRO_TOWER_CACHE（缺省 ~/.cache/hiro/tower）：

- `<tower_id>-<layer>.json`：编成页读到的 loadout（gkms.exam_loadout.v1 + formation_signature）。「同じ編成で再挑戦」不经过编成页，
  同一进程重试、或从结果页 / 名次页 / 再挑戦确认页 / 考试中起跑时读这一份设 HIRO_TOWER_LOADOUT；经过编成页就覆盖（协调 11-14）。
- `<tower_id>-<layer>-<签名>.json`：这一场实际抽到的牌（按抽到的顺序，含开局手牌）和对手分数（8a3a1e7 11-14 定的格式）：
  `{"tower_id", "layer", "formation_signature", "draws": [{"id": "p_card-…", "u": 1}, …], "rivals": [105438, …]}`。
  抽牌从考试每一步的手牌变化推出来，只记编成牌组里的牌（生成卡跳过、计数 skipped_generated）；有一步说不清
  （认不出的卡、同 id 消失又出现分不清是哪张）就只写到那之前，不猜。

编成签名：主 / サブ1 / サブ2 回忆的 id 读不到，用偶像卡 id + 编成页三张回忆的评价值（「12075」「10773」「9978」，按主、サブ1、サブ2）；
评价值读不全退回偶像卡 id + 三属性值。都读不到不写签名。
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


def cache_dir() -> Path:
    return Path(os.environ.get("HIRO_TOWER_CACHE") or Path.home() / ".cache" / "hiro" / "tower")


def signature(idol_card_id: str | None, ratings: list | None, params: list | None) -> str | None:
    """编成签名：偶像卡 id + 三张回忆评价值（读不全退回三属性值）；偶像卡 id 读不到返回 None。"""
    if not idol_card_id:
        return None
    nums = ratings if ratings and len(ratings) == 3 else params if params and len(params) == 3 else None
    return f"{idol_card_id}.{'-'.join(str(int(v)) for v in nums)}" if nums else None


def loadout_path(tower_id: str, layer: int) -> Path:
    return cache_dir() / f"{tower_id}-{layer}.json"


def save_loadout(lo: dict) -> Path | None:
    """loadout 存一份到缓存（tower_id / layer 缺一个就不存）。"""
    if not lo.get("tower_id") or not lo.get("layer"):
        return None
    p = loadout_path(lo["tower_id"], lo["layer"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lo, ensure_ascii=False), encoding="utf-8")
    return p


def load_loadout(tower_id: str | None, layer: int | None) -> dict | None:
    """按 tower_id + layer 读缓存的 loadout；layer 不知道（从考试中 / 名次页起跑）时取这条路线最近写的一份。"""
    if not tower_id:
        return None
    if layer:
        p = loadout_path(tower_id, layer)
    else:
        cands = [p for p in cache_dir().glob(f"{tower_id}-*.json") if p.stem[len(tower_id) + 1 :].isdigit()]
        p = max(cands, key=lambda q: q.stat().st_mtime, default=None)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("偶像之路 loadout 缓存 %s 读不了：%s", p, e)
        return None


def draws_path(tower_id: str, layer: int, sig: str) -> Path:
    return cache_dir() / f"{tower_id}-{layer}-{sig}.json"


def save_draws(
    tower_id: str, layer: int, sig: str, draws: list[dict], rivals: list[int], skipped_generated: int = 0
) -> Path:
    p = draws_path(tower_id, layer, sig)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"tower_id": tower_id, "layer": layer, "formation_signature": sig, "draws": draws, "rivals": rivals,
           "skipped_generated": skipped_generated}  # fmt: skip
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 抽牌推断
def _hand(fields: dict, deck: dict[str, set[int]]) -> list[tuple[str, int]] | None:
    """一步的手牌 [(卡 id, 强化)]（按槽位从左到右）。有认不出的卡返回 None；强化没读到（None）时按牌组补
    （牌组里这张只有一种强化才补；生成卡当 0），补不了也返回 None。"""
    hand = fields.get("hand")
    if hand is None:
        return None
    out = []
    for c in sorted(hand, key=lambda c: c.get("slot", 0)):
        if not c.get("card_id"):
            return None
        u = c.get("upgrade")
        if u is None:
            us = deck.get(c["card_id"], {0})
            if len(us) != 1:
                return None
            u = next(iter(us))
        out.append((c["card_id"], int(u)))
    return out


def infer_draws(steps: list[dict], deck_ids: dict[str, set[int]] | None) -> tuple[list[dict], int, str | None]:
    """考试各步（{"remaining_turns", "hand": [...], "played": 槽位或 None, "unrecognized": 认不出的卡数}，按时间顺序）→
    (抽到的编成牌组里的牌 [{"id", "u"}]，跳过的生成卡张数，停在哪儿的原因；全程说得清时原因是 None)。

    规则（8a3a1e7 11-14 定）：
    - draws 只记编成牌组里的牌（deck_ids：卡 id → 牌组里的强化值，编成页读到的 + 2 张基本卡），按抽到的先后排。不在牌组里的（眠気等生成卡：主数据里
      生成效果只往山札顶 / 随机位置 / 弃牌堆塞，不直接进手牌）跳过、计数，继续往下推——原有牌之间的先后不受影响。
    - 第一步的整手牌是开局手牌，全算抽到（槽位从左到右）。
    - 回合变了（残りターン 变小）：上回合剩下的手牌进弃牌堆，新手牌整手算抽到。
    - 同一回合内：上一步的手牌去掉打出的那张，多出来的算抽到（卡效果抽牌）；少了的（消耗 / 弃牌效果）不管。
      同一张卡原地强化（同 id、u 变了、位置没变）当同一张，不算抽牌也不算消失。
    - 认错的更正：刚抽到时读成不在牌组里的卡（当生成卡跳过了），同一回合后面同一位置读成牌组里的卡——是第一帧认错
      （r1 T1：トークタイム+ 先读成準備運動+，8a3a1e7 11-14）。补回到当初跳过的位置，跳过数减一。
    - 说不清就停，只留前面的：认不出的卡（或强化没读到、牌组里这张又不止一种强化）、残りターン 读不到 / 变大、同 id 消失又出现而分不清是哪张。
    - 牌组不知道（编成页第 2 页没读到）时分不出生成卡：不记。
    """
    if not deck_ids:
        return [], 0, "编成牌组没读到，分不出生成卡"
    draws: list[dict] = []
    skipped = 0
    prev: list[tuple[str, int]] | None = None
    prev_turn = None
    ghosts: list[list] = []  # 这一回合跳过的、还在手里的非牌组卡：[手牌里的位置, 卡, 当初在 draws 里该在的下标]
    for i, st in enumerate(steps):
        hand = None if st.get("unrecognized") else _hand(st, deck_ids)
        if hand is None:
            return draws, skipped, f"第 {i} 步手牌有认不出的卡（或强化读不出）"
        turn = st.get("remaining_turns")
        if turn is None:
            return draws, skipped, f"第 {i} 步残りターン读不到"
        if prev is None or turn < prev_turn:
            added, ghosts = list(enumerate(hand)), []
        elif turn > prev_turn:
            return draws, skipped, f"第 {i} 步残りターン变大（{prev_turn} → {turn}）"
        else:
            expect = list(prev)
            slot = steps[i - 1].get("played")
            if slot is not None:
                if not 0 <= slot < len(prev):
                    return draws, skipped, f"第 {i - 1} 步打出的槽位 {slot} 不在手牌里"
                del expect[slot]
                for g in ghosts:
                    g[0] = -1 if g[0] == slot else g[0] - (g[0] > slot)  # 打出的是它就不在手里了；右边的左移一格
            for g in [g for g in ghosts if 0 <= g[0] < min(len(expect), len(hand))]:
                k = g[0]
                if (
                    expect[k] == g[1] and hand[k] != g[1] and hand[k][0] in deck_ids
                ):  # 同一位置换成了牌组里的卡：第一帧认错
                    draws.insert(g[2], {"id": hand[k][0], "u": hand[k][1]})
                    skipped -= 1
                    for h in ghosts:
                        h[2] += h is not g and h[2] >= g[2]
                    expect[k] = hand[k]
                    g[0] = -1
            ghosts = [g for g in ghosts if g[0] >= 0]
            extra = Counter(hand) - Counter(expect)
            gone = Counter(expect) - Counter(hand)
            for cid in {c for c, _ in extra} & {c for c, _ in gone}:
                ge = [c for c in gone.elements() if c[0] == cid]
                xe = [c for c in extra.elements() if c[0] == cid]
                # 原地强化：这个 id 正好消失一张、出现一张，位置没变
                pos_old = [k for k, c in enumerate(expect) if c == ge[0]] if len(ge) == 1 else []
                pos_new = [k for k, c in enumerate(hand) if c == xe[0]] if len(xe) == 1 else []
                if len(ge) == len(xe) == 1 and len(pos_old) == len(pos_new) == 1 and pos_old == pos_new:
                    extra[xe[0]] -= 1
                    gone[ge[0]] -= 1
                    continue
                return draws, skipped, f"第 {i} 步 {cid} 消失又出现，分不清是哪张"
            left = Counter(+extra)
            added = []
            for k in range(len(hand) - 1, -1, -1):  # 同样的卡有几张时，新抽的在右边
                if left[hand[k]] > 0:
                    added.append((k, hand[k]))
                    left[hand[k]] -= 1
            added.reverse()  # 多出来的按槽位从左到右
        for pos, (cid, u) in added:
            if cid in deck_ids:
                draws.append({"id": cid, "u": u})
            else:
                skipped += 1  # 生成卡（眠気等）：不记，继续；同一回合后面同一位置读成牌组里的卡就补回来
                ghosts.append([pos, (cid, u), len(draws)])
        prev, prev_turn = hand, turn
    return draws, skipped, None


def exam_steps(steps_jsonl: Path, label: str, after_seq: int = 0) -> list[dict]:
    """trace 的 steps.jsonl 里这一场考试（exam_label 对得上、seq 在开考之后）的各步 → infer_draws 的输入。"""
    out = []
    if not steps_jsonl.exists():
        return out
    for line in steps_jsonl.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        f = (r.get("observation") or {}).get("fields") or {}
        if (r.get("seq") or 0) <= after_seq or f.get("exam_label") not in (label, None) or f.get("hand") is None:
            continue
        act = (r.get("decision") or {}).get("action") or {}
        ok = (r.get("execution") or {}).get("ok", True)
        played = (act.get("args") or {}).get("slot") if act.get("kind") == "PlayCard" and ok else None
        out.append({"remaining_turns": f.get("remaining_turns"), "hand": f.get("hand"), "played": played,
                    "unrecognized": len(f.get("unrecognized_cards") or [])})  # fmt: skip
    return out


# ---------------------------------------------------------------- 每层挑战次数（跨进程）
DAY_RESET_HOUR = 5  # 游戏每天日本时间凌晨 5 点换日（日付変更；北京时间 4 点）。协调 11-17 实机：北京 04:05 弹「日付変更」，登录奖励写「04:59まで」
JST = __import__("datetime").timezone(
    __import__("datetime").timedelta(hours=9), "JST"
)  # 日本没有夏令时：固定 UTC+9（不用 zoneinfo，Windows 上要另装 tzdata）


def game_day(now=None) -> str:
    """游戏日：按日本时间，凌晨 5 点前算前一天（原来按本机时区 4 点，在北京时间的 Mac 上碰巧对，换到别的时区的机器就错）。
    now：带时区的时刻（换成日本时间再算）；不带时区的按日本时间理解；缺省现在。"""
    from datetime import datetime, timedelta

    t = now if now is not None else datetime.now(JST)
    if t.tzinfo is not None:
        t = t.astimezone(JST)
    return (t - timedelta(hours=DAY_RESET_HOUR)).strftime("%Y-%m-%d")


def progress_path(tower_id: str) -> Path:
    return cache_dir() / f"{tower_id}-progress.json"


def save_progress(run) -> Path | None:
    """TowerRun 的 tries / best / cleared / gave_up_three 落盘，标上游戏日（协调 11-17：只记在进程里，重启就从 0 算，手毬 16 层实际打了 3 次、
    日志每次都写 1）。"""
    if not getattr(run, "tower_id", None):
        return None
    p = progress_path(run.tower_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"game_day": game_day(), "tries": {str(k): v for k, v in run.tries.items()}, "best": {str(k): v for k, v in run.best.items()},
           "cleared": sorted(run.cleared), "gave_up_three": list(run.gave_up_three)}  # fmt: skip
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return p


def load_progress(run) -> bool:
    """同一游戏日的记录读回 TowerRun（别的日子的不读：每天的次数重新算）。返回读到没有。"""
    p = progress_path(run.tower_id) if getattr(run, "tower_id", None) else None
    if p is None or not p.exists():
        return False
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("偶像之路次数记录 %s 读不了：%s", p, e)
        return False
    if rec.get("game_day") != game_day():
        return False
    run.tries.update({int(k): int(v) for k, v in (rec.get("tries") or {}).items()})
    run.best.update({int(k): int(v) for k, v in (rec.get("best") or {}).items()})
    run.cleared.update(int(x) for x in rec.get("cleared") or [])
    run.gave_up_three.extend(int(x) for x in rec.get("gave_up_three") or [] if int(x) not in run.gave_up_three)
    return True


def reset_progress(tower_id: str) -> None:
    p = progress_path(tower_id)
    if p.exists():
        p.unlink()
