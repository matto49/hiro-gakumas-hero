"""低级失误审计（docs/goals/system-hardening.md §1 第 5 条、里程碑 H7）。

输入一局的 trace 基名（自动带上 -rN 续跑段；同一局开局失败重开的几个基名可以一起传），输出「低级失误报告」：
每类次数、步号（段 + seq）/ 帧路径、估计影响。

    .venv/bin/python -m tools.audit.blunders <run>
    .venv/bin/python -m tools.audit.blunders <run-a> <run-b> --name <game>
    .venv/bin/python -m tools.audit.blunders --baseline          # 第九–十二局基线汇总表

输出 reports/analysis/<名>-blunders.{md,json}；--baseline 另出 reports/analysis/blunders-baseline.md。

数据来源（每段）：
- traces/<段>/steps.jsonl：决策、执行结果（ok / error / taps）、notes（recover:*、读错拦截、未识别画面持续…）、checks、观测；
- reports/live/<段>.log：段怎么结束的（报错停止 / 异常 / 被中断 / 正常结束）；
- 日志里「Decision log: reports/runs/<目录>」指向的录像器 events.jsonl：考试内手牌重建（live_hand_rebuilt / unfixable）；
- reports/live/manual_interventions.jsonl：人工介入手工登记（一行一条 {run, t?, reason, detail?}）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
UNKNOWN_SECS = 30  # 页面认不出超过 N 秒
STUCK_SECS = 60  # 同一页、画面不变、同一动作反复超过 N 秒算空转
STUCK_REPEATS = 3  # 或者连续 N 次
MANUAL_LOG = "reports/live/manual_interventions.jsonl"

# 类别 → 种类 → 中文名。每个种类一个检测器；检测不了的写在 GAPS 里。
KINDS: dict[str, dict[str, str]] = {
    "出牌": {
        "skip_with_playable": "有可打的牌 / 饮料却结束回合",
        "slot_mismatch": "选中的牌画面上不存在（定位失败 / 禁用）",
        "wrong_card": "点到别的牌（考试 / 三选一 / 除外）",
        "repeat_tap": "同一动作重复生效（重复点）",
        "hand_mismatch": "决策用的手牌和画面对不上",
    },
    "识别": {
        "read_error": "读数被不变量拦下",
        "model_mismatch": "读数 / 模型不一致（倍率 vs 考前说明页真值）",
        "unknown_page": f"页面认不出超过 {UNKNOWN_SECS} 秒",
        "result_no_score": "结果页没读到分数",
    },
    "执行": {
        "tap_no_effect": "点击没生效需要重试",
        "exec_failed": "执行失败",
        "dialog_not_closed": "确认框 / 弹窗没关掉",
        "stuck": f"卡在同一页空转（同一动作反复回到原画面 ≥{STUCK_REPEATS} 次或 ≥{STUCK_SECS} 秒）",
        "select_cancel": "出牌前另一张牌停在选中态，先取消",
        "select_retap": "点的牌停在选中态，再点一次",
        "select_not_seen": "出牌后没看到 SELECT 确认",
    },
    "流程": {
        "run_stopped": "运行中断（报错停止 / 异常 / 被杀）",
        "crash_restart": "进程内崩溃自恢复",
        "llm_rescue": "认不出的画面交给 LLM 看图兜底（同类画面多了交识屏补签名）",
        "rec_pause": "录屏中断，对局暂停（录屏恢复后继续）",
        "resume_failed": "开局 / 续跑接不上",
        "manual_intervention": "人工介入",
    },
    "资源": {
        "shop_not_spent": "相談 / 轮间没按决策花 P 点",
        "drink_full_unhandled": "饮料满了没处理好",
        "retry_unused": "该用的再挑戦没用",
        "drink_ledger": "轮间持有饮料和 R1 结束时实际剩余不一致",
    },
}
GAPS = {
    "model_mismatch": "说明页倍率由快速判页在「タップして次へ」出来后读（识屏 2026-11-02 起）；更早的局没有说明页读数，算不出。",
    "wrong_card": "考试出牌只按前后两帧手牌差推断（同回合、无抽牌干扰时可靠）；执行记录没有被点卡框的位置，点错但手牌恰好对得上时测不到。",
    "hand_mismatch": "依赖录像器 live_hand_rebuilt / unfixable（2026-11-01 晚加入）；更早的局只有截图兜底记录，算不出。",
    "shop_not_spent": "相談页没有 P 点读数，只能按「决策要买、执行没成」统计，算不出没花掉的 P 数；价格在候选预览里有才给。",
    "manual_intervention": "人工点击不进 trace：按续跑段（进程被停后人工重开）统计，原因取上一段的结束方式；其他手动操作请登记到 " + MANUAL_LOG + "。",
    "select_not_seen": "三类选中态问题只记在运行日志里（hiro.produce.battle），按日志时刻对到正在执行的步；没有日志的局测不到。",
    "retry_unused": "只认「未过线 → 点了结束培育」；本战排名页没拿第 1 且没用再挑戦的情况需要排名页读数，已按 notes 里的本战排名补判。",
}
BUY_KINDS = {"ShopBuy", "ShopUpgradeCard", "ShopDeleteCard", "ShopRemoveCard", "IntervalBuy", "IntervalUpgrade", "IntervalDelete"}
DIALOG_PAGES = re.compile(r"DIALOG|CONFIRM|PDRINK_MAX")


@dataclass
class Finding:
    category: str
    kind: str
    segment: str
    seq: int | None
    detail: str
    t: float | None = None
    frame: str | None = None  # 相对仓库根的帧路径
    impact: dict[str, Any] = field(default_factory=dict)  # seconds / expected_loss / price …

    @property
    def label(self) -> str:
        return KINDS[self.category][self.kind]


@dataclass
class Segment:
    name: str
    dir: Path
    run: dict
    steps: list[dict]
    log: list[str]
    events: list[dict]  # 录像器事件（可能为空）

    def frame(self, obs: dict | None) -> str | None:
        shot = (obs or {}).get("screenshot")
        if not shot:
            return None
        p = self.dir / shot
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return str(p)


# ------------------------------------------------------------------ 载入
def segments_of(base: str, traces: Path) -> list[Path]:
    segs = [traces / base] + sorted(traces.glob(base + "-r*"), key=lambda p: int(p.name.rsplit("-r", 1)[1]))
    return [s for s in segs if s.is_dir() and re.fullmatch(re.escape(base) + r"(-r\d+)?", s.name)]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def load_segment(seg: Path, root: Path = ROOT) -> Segment:
    run = json.loads((seg / "run.json").read_text(encoding="utf-8")) if (seg / "run.json").exists() else {}
    log_path = root / "reports" / "live" / f"{seg.name}.log"
    log = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.exists() else []
    events: list[dict] = []
    for line in log:
        m = re.search(r"Decision log: (reports/runs/[\w-]+)", line)
        if m:
            events = _read_jsonl(root / m.group(1) / "events.jsonl")
            break
    return Segment(seg.name, seg, run, _read_jsonl(seg / "steps.jsonl"), log, events)


def load_game(bases: list[str], traces: Path = ROOT / "traces", root: Path = ROOT) -> list[Segment]:
    return [load_segment(s, root) for b in bases for s in segments_of(b, traces)]


# ------------------------------------------------------------------ 工具
def _obs_all(step: dict) -> list[dict]:
    return [*(step.get("observations_before") or []), step["observation"], *(step.get("observations_after") or [])]


def _action(step: dict) -> dict:
    return ((step.get("decision") or {}).get("action")) or {}


def _akey(a: dict) -> str:
    return f"{a.get('kind')}{json.dumps(a.get('args') or {}, sort_keys=True, ensure_ascii=False)}"


def _notes(seg: Segment):
    for st in seg.steps:
        for n in st.get("notes") or []:
            yield st, n


def _f(seg: Segment, cat: str, kind: str, st: dict | None, detail: str, **impact) -> Finding:
    obs = st.get("observation") if st else None
    return Finding(cat, kind, seg.name, st.get("seq") if st else None, detail, st.get("t") if st else None, seg.frame(obs),
                   {k: v for k, v in impact.items() if v is not None})  # fmt: skip


# ------------------------------------------------------------------ 出牌
def skip_with_playable(seg: Segment) -> list[Finding]:
    """考试里结束回合（Skip），而候选里有出牌 / 喝饮料且期望分更高，或决策自带「还有可打的牌却跳过」警告。影响 = 期望分差。"""
    out = []
    for st in seg.steps:
        d = st.get("decision") or {}
        if d.get("decider") != "battle" or _action(st).get("kind") != "Skip":
            continue
        cands = d.get("candidates") or []
        skip_exp = next((c.get("expected") for c in cands if (c.get("action") or {}).get("kind") == "Skip"), None)
        better = [c for c in cands if (c.get("action") or {}).get("kind") in ("PlayCard", "UseDrink") and c.get("expected") is not None
                  and skip_exp is not None and c["expected"] > skip_exp]  # fmt: skip
        warned = any("可打的牌却跳过" in w for w in d.get("warnings") or [])
        if not better and not warned:
            continue
        best = max(better, key=lambda c: c["expected"]) if better else None
        loss = round(best["expected"] - skip_exp) if best else None
        what = (best.get("preview") or {}).get("动作") if best else "（无预演）"
        reason = d.get("reason") or ""
        why = ("硬规则：" + h.group(1)) if (h := re.search(r"硬规则：(\w+)", reason)) else \
            "其余动作已被禁用（多为本回合定位失败）" if "唯一合法动作" in reason or best else (d.get("warnings") or [""])[0]
        out.append(_f(seg, "出牌", "skip_with_playable", st, f"结束回合（{d.get('source')}），可选 {what}" + (f"，期望分少 {loss:,}" if loss else "")
                      + f"；{why}", expected_loss=loss))  # fmt: skip
    return out


SLOT_NOTE = re.compile(r"存档与画面对不上：((?:play|drink):\d+)（(.*?)）")


def slot_mismatch(seg: Segment) -> list[Finding]:
    return [_f(seg, "出牌", "slot_mismatch", st, f"{m.group(1)}（{m.group(2)}）画面上找不到，本回合禁用")
            for st, n in _notes(seg) if (m := SLOT_NOTE.search(n))]  # fmt: skip


def _base(name: str | None) -> str:
    return re.sub(r"[+＋]+$|（.*?）$", "", (name or "").strip()).rstrip("+＋")


PLAY_REASON = re.compile(r"play:([^\s，,；]+)")


def wrong_card_exam(seg: Segment) -> list[Finding]:
    """考试：同一回合内相邻两个决策点的手牌差（按牌名、忽略 + 与（定制））。
    - 要打的牌：决策理由里的 play:<名>，没有就取执行记录的画面槽位（决策 args.slot 是存档手牌顺序，不是画面顺序）；
    - 打出后要打的牌没少、别的牌少了 → 点到别的牌；一次少了 2 张以上 → 疑似重复点；
    - 少掉的牌在同一回合后面的帧里又出现 → 是识别漏读（手牌换了布局），不算。"""
    out = []
    battle = [st for st in seg.steps if (st.get("decision") or {}).get("decider") == "battle"]
    for i, (a, b) in enumerate(zip(battle, battle[1:], strict=False)):
        ex = a.get("execution") or {}
        if (ex.get("action") or {}).get("kind") != "PlayCard" or not ex.get("ok"):
            continue
        fa, fb = a["observation"]["fields"], b["observation"]["fields"]
        rem = fa.get("remaining_turns")
        if rem is None or rem != fb.get("remaining_turns") or fb.get("hand") is None or b["seq"] - a["seq"] > 2:
            continue  # 跨回合有抽牌，或下一帧手牌不在稳定布局，不比
        hand = fa.get("hand") or []
        m = PLAY_REASON.search((a.get("decision") or {}).get("reason") or "")
        slot = (ex["action"].get("args") or {}).get("slot")
        want = _base(m.group(1)) if m else (_base(hand[slot].get("name")) if slot is not None and slot < len(hand) else None)
        if not want:
            continue
        before = Counter(_base(h.get("name")) for h in hand if h)
        after = Counter(_base(h.get("name")) for h in fb.get("hand") or [] if h)
        if after - before:
            continue  # 有抽牌 / 生成，差集不可靠
        later = [Counter(_base(h.get("name")) for h in s["observation"]["fields"].get("hand") or [] if h)
                 for s in battle[i + 2:i + 6] if s["observation"]["fields"].get("remaining_turns") == rem]  # fmt: skip
        removed = Counter({k: v for k, v in (before - after).items() if not any(c[k] for c in later)})
        if want not in before:
            continue
        if removed and want not in removed and before - after == removed:
            out.append(_f(seg, "出牌", "wrong_card", a, f"要打 {want}，少掉的是 {'、'.join(removed)}"))
        elif want in removed and sum(removed.values()) >= 2:
            out.append(_f(seg, "出牌", "repeat_tap", a, f"打 {want} 一次，手牌少了 {sum(removed.values())} 张（{'、'.join(removed.elements())}）"))
    return out


def wrong_card_select(seg: Segment) -> list[Finding]:
    """三选一：拿 X 后获得弹窗认出的卡不是 X；除外 X 后下一帧少的不是 X；刷新一次次数少了 2 以上。"""
    out = []
    steps = seg.steps
    for i, st in enumerate(steps):
        d = st.get("decision") or {}
        if d.get("decider") != "select_card" or not (st.get("execution") or {}).get("ok"):
            continue
        act, f = _action(st), st["observation"]["fields"]
        cards = f.get("cards") or f.get("candidates") or []
        later = [o for s in steps[i:i + 3] for o in (_obs_all(s) if s is not st else st.get("observations_after") or [])]
        if act.get("kind") == "ActivateEffect":
            idx = ((act.get("args") or {}).get("pick_indexes") or [None])[0]
            if idx is None or idx >= len(cards):
                continue
            want = cards[idx].get("card_id")
            got = next((o["fields"].get("card_id") for o in later if o["page"]["name"] == "ACQUIRE" and o["fields"].get("card_id")), None)
            if got and want and got != want:
                out.append(_f(seg, "出牌", "wrong_card", st, f"三选一要拿 {cards[idx].get('name')}，获得的是 {got}"))
        elif act.get("kind") == "ExcludeCard":
            idx = (act.get("args") or {}).get("pick_index")
            nxt = next((o for o in later if o["page"]["name"] == "SELECT_CARD" and o["seq"] > st["observation"]["seq"]), None)
            if idx is None or idx >= len(cards) or nxt is None:
                continue
            before = Counter(c.get("card_id") for c in cards)
            after = Counter(c.get("card_id") for c in nxt["fields"].get("cards") or nxt["fields"].get("candidates") or [])
            gone = before - after
            want = cards[idx].get("card_id")
            if after and gone and want not in gone and not (after - before):
                out.append(_f(seg, "出牌", "wrong_card", st, f"要除外 {cards[idx].get('name')}，少掉的是 {'、'.join(gone)}"))
        elif act.get("kind") == "Reroll":
            nxt = next((o for o in later if o["page"]["name"] == "SELECT_CARD" and o["seq"] > st["observation"]["seq"]
                        and o["fields"].get("rerolls_left") is not None), None)  # fmt: skip
            r0 = f.get("rerolls_left")
            if r0 is not None and nxt is not None and r0 - nxt["fields"]["rerolls_left"] >= 2:
                out.append(_f(seg, "出牌", "repeat_tap", st, f"刷新一次，次数 {r0} → {nxt['fields']['rerolls_left']}"))
    return out


def hand_mismatch(seg: Segment) -> list[Finding]:
    out = []
    for e in seg.events:
        if e.get("kind") in ("live_hand_unfixable", "live_hand_rebuilt"):
            fixed = e["kind"] == "live_hand_rebuilt"
            f = Finding("出牌", "hand_mismatch", seg.name, None, f"[{e.get('exam')} 剩 {e.get('remaining')}] 存档手牌与画面不同，"
                        + ("已按画面重建" if fixed else "重建不了，本回合走截图") + f"：{'；'.join(e.get('notes') or [])[:120]}",
                        e.get("t"), None, {"auto_fixed": fixed})  # fmt: skip
            out.append(f)
    return out


# ------------------------------------------------------------------ 识别
MULT_RULES = ("attr_multiplier", "model_permil")  # 倍率对说明页真值：单独计「读数 / 模型不一致」，不重复计进 read_error


def read_error(seg: Segment) -> list[Finding]:
    out, seen = [], set()
    for st in seg.steps:
        for c in st.get("checks") or []:
            if c.get("verdict") != "read_error" or c.get("rule") in MULT_RULES:
                continue
            key = (c.get("seq"), c.get("field"))
            if key in seen:
                continue
            seen.add(key)
            out.append(_f(seg, "识别", "read_error", st, f"{c.get('field')}={c.get('observed')!r}（{c.get('rule')}，参考 {c.get('predicted')!r}）"))
    for st, fld, obs, ref, why in exam_reads(seg):
        if (st.get("seq"), fld) not in seen:
            seen.add((st.get("seq"), fld))
            out.append(_f(seg, "识别", "read_error", st, f"{fld}={obs!r}（{why}，参考 {ref!r}）"))
    return out


def _dropped(a, b) -> bool:
    """a 是 b 掉了几位后的连续片段（1388 → 388、1010 → 10）。"""
    return isinstance(a, int) and isinstance(b, int) and 0 <= a < b and len(str(a)) < len(str(b)) and str(a) in str(b)


EXAM_READ_FIELDS = ("genki", "score", "hp")
DROP_LINE = 1000  # 考试中元気 / 分数一步减少这么多以上，又没有清空 / 减半元気的牌：读错


def exam_reads(seg: Segment) -> list[tuple[dict, str, int, int, str]]:
    """考试 HUD 读数的两类核对（协调 2026-11-04：第二十五局 R1 末回合元気 1,388 读成 388）：
    1. 和存档对比：录像器 battle_decision 的局面来自存档 / 续推（source save / track）时，按时刻和动作对到 trace 那一步，
       HUD 读数相对存档掉位（388 vs 1388）→ 读错。存档只在出牌时写，两边差一点是常态，只认掉位；
    2. 无故减少：同一场考试相邻两步，分数减少 ≥ DROP_LINE（分数不会减），或元気减少 ≥ DROP_LINE 而上一步打的不是清空 / 减半元気的牌。
    返回 [(步, exam.<字段>, 读数, 参考, 规则)]。"""
    out = []
    battle = [st for st in seg.steps if (st.get("decision") or {}).get("decider") == "battle"]
    for ev in seg.events:
        if ev.get("kind") != "battle_decision" or ev.get("source") not in ("save", "track"):
            continue
        act = ((ev.get("contract") or {}).get("action")) or {}
        st = next((s for s in battle if 0 <= (s.get("t") or 0) - ev["t"] < 60), None)
        if st is None or ((st.get("decision") or {}).get("action") or {}) != act:
            continue  # 对不上同一个决策就不比
        f, sv = st["observation"]["fields"], ev.get("state") or {}
        for k in EXAM_READ_FIELDS:
            if _dropped(f.get(k), sv.get(k)):
                out.append((st, f"exam.{k}", f[k], sv[k], "和存档对比：HUD 掉位"))
    for a, b in zip(battle, battle[1:], strict=False):
        fa, fb = a["observation"]["fields"], b["observation"]["fields"]
        ra, rb = fa.get("remaining_turns"), fb.get("remaining_turns")
        la, lb = fa.get("exam_label"), fb.get("exam_label")
        if (la and lb and la != lb) or ra is None or rb is None or rb > ra:
            continue  # 换场 / 再挑戦重开
        if (fa.get("score") or 0) - (fb.get("score") or 0) >= DROP_LINE and fb.get("score") is not None:
            out.append((b, "exam.score", fb["score"], fa["score"], f"分数一步减少 ≥{DROP_LINE}"))
        ga, gb = fa.get("genki"), fb.get("genki")
        if ga is not None and gb is not None and ga - gb >= DROP_LINE:
            slot = (((a.get("execution") or {}).get("action") or {}).get("args") or {}).get("slot")
            card = next((c for c in fa.get("hand") or [] if c and c.get("slot") == slot), None) if (a.get("execution") or {}).get("action", {}).get("kind") == "PlayCard" else None
            tags = (card or {}).get("tags") or {}
            if not (tags.get("clears_genki") or tags.get("halves_genki")):
                out.append((b, "exam.genki", gb, ga, f"元気一步减少 ≥{DROP_LINE}，上一步没有清空 / 减半元気的牌"))
    return out


def model_mismatch(seg: Segment) -> list[Finding]:
    """考试 HUD 倍率（attr_multiplier，read_error）/ 模拟器·finals_context 倍率（model_permil，model_gap）与考前说明页真值差 > 1%。"""
    out, seen = [], set()
    for st in seg.steps:
        for c in st.get("checks") or []:
            if c.get("rule") not in MULT_RULES or c.get("verdict") not in ("read_error", "model_gap"):
                continue
            key = (c.get("seq"), c.get("field"))
            if key in seen:
                continue
            seen.add(key)
            src = "HUD" if c.get("rule") == "attr_multiplier" else "模型"
            out.append(_f(seg, "识别", "model_mismatch", st, f"{src} {c.get('field')}={c.get('observed')!r} vs 说明页 {c.get('predicted')!r}：{c.get('note') or ''}"))
    return out


UNKNOWN_NOTE = re.compile(r"(?:未识别画面持续|画面持续 )(\d+)s(?:：| 认不出（)(.*)")


def unknown_page(seg: Segment, secs: int = UNKNOWN_SECS) -> list[Finding]:
    """「未识别画面持续 Xs」会随时长重复记（31s、62s…）：同一步、同一画面文字只取最长的一次。"""
    best: dict[tuple, tuple[int, dict, str]] = {}
    for st, n in _notes(seg):
        m = UNKNOWN_NOTE.search(n)
        if not m:
            continue
        s, texts = int(m.group(1)), m.group(2).strip(" ）")
        key = (st.get("seq"), texts.split(" / ")[0][:12])
        if s >= secs and (key not in best or s > best[key][0]):
            best[key] = (s, st, texts)
    return [_f(seg, "识别", "unknown_page", st, f"{s}s：{texts[:80]}", seconds=s) for s, st, texts in best.values()]


def result_no_score(seg: Segment) -> list[Finding]:
    """考试结束后（HIF_EXAM_RESULT / 本战排名页）没有一帧读到分数。"""
    out = []
    groups: list[tuple[str, dict, list[dict]]] = []
    for st in seg.steps:
        for o in _obs_all(st):
            page = o["page"]["name"]
            if page == "HIF_EXAM_RESULT" or (page == "HIF_FINALS_RANKING" and o["fields"].get("kind") in ("final", "round")):
                if groups and groups[-1][0] == page and o["seq"] - groups[-1][2][-1]["seq"] <= 10:
                    groups[-1][2].append(o)
                else:
                    groups.append((page, st, [o]))
    for page, st, obs in groups:
        key = "score" if page == "HIF_EXAM_RESULT" else "my_score"
        if all(o["fields"].get(key) is None for o in obs):
            out.append(_f(seg, "识别", "result_no_score", st, f"{page} {len(obs)} 帧都没读到分数"))
    return out


# ------------------------------------------------------------------ 执行
def _later_ok(steps: list[dict], i: int, act: dict) -> bool:
    """同一页里之后有没有再成功执行同一个动作（重试成功）。"""
    page = steps[i]["observation"]["page"]["name"]
    for s2 in steps[i + 1:]:
        if s2["observation"]["page"]["name"] != page:
            return False
        if _akey(_action(s2)) == _akey(act) and (s2.get("execution") or {}).get("ok"):
            return True
    return False


def execution(seg: Segment) -> list[Finding]:
    """执行类与资源类（购买 / 饮料）一起判，一次失败只归一类：
    空转循环里的步 → 只记一条 stuck；购买没成且之后没重试成功 → shop_not_spent；饮料替换 → drink_full_unhandled；
    弹窗 / 确认框 → dialog_not_closed；其他 → exec_failed。recover:tap_no_effect 只记执行最终成功（重试后才生效）的步。"""
    loops = stuck(seg)
    in_loop = {s["seq"] for f in loops for s in f.impact.pop("_steps", [])}
    out = list(loops)
    steps = seg.steps
    drink_fail_seqs: set[int] = set()
    for i, st in enumerate(steps):
        e = st.get("execution") or {}
        act = e.get("action") or _action(st)
        page = e.get("page_before") or st["observation"]["page"]["name"]
        err = str(e.get("error"))[:100]
        if st["seq"] in in_loop:
            continue
        if e and e.get("ok") is False:
            k = act.get("kind")
            if k in BUY_KINDS:
                if not _later_ok(steps, i, act):
                    out.append(_f(seg, "资源", "shop_not_spent", st, f"{page} {_akey(act)} 没买成：{err}", price=_price(st)))
                else:
                    out.append(_f(seg, "执行", "tap_no_effect", st, f"{page} {_akey(act)} 第一次没成（{err}），重试后成功"))
            elif k in ("DiscardDrink", "SkipDrink"):
                drink_fail_seqs.add(st["seq"])
                out.append(_f(seg, "资源", "drink_full_unhandled", st, f"{_akey(act)}：{err}"))
            elif k == "Dialog" or DIALOG_PAGES.search(page or ""):
                out.append(_f(seg, "执行", "dialog_not_closed", st, f"{page} {_akey(act)}：{err}", seconds=e.get("seconds")))
            else:
                out.append(_f(seg, "执行", "exec_failed", st, f"{page} {_akey(act)}：{err}", seconds=e.get("seconds")))
            continue
        for n in st.get("notes") or []:
            if "饮料替换没完成" in n:
                if not any(q in drink_fail_seqs for q in (st["seq"] - 1, st["seq"] - 2)):
                    out.append(_f(seg, "资源", "drink_full_unhandled", st, n.split(":", 2)[-1].strip()[:120]))
            elif n.startswith("recover:tap_no_effect:"):
                out.append(_f(seg, "执行", "tap_no_effect", st, n.split(":", 2)[2].strip()[:120], seconds=e.get("seconds")))
    return out


VOLATILE = {"t", "seq", "screenshot", "timings"}


NOISE = {"score", "margin", "dist", "distance", "confidence", "conf", "votes", "rect", "box"}  # 嵌套项里的匹配置信度 / 坐标：每帧都会抖


def _norm(v, depth: int = 0):
    if isinstance(v, dict):
        return {k: _norm(x, depth + 1) for k, x in v.items() if not (depth > 0 and k in NOISE)}
    if isinstance(v, list):
        return [_norm(x, depth + 1) for x in v]
    return v


def _sig(obs: dict) -> str:
    """画面状态签名：页面 + 读数（去掉嵌套项里的匹配置信度；顶层 score 是考试分数，保留）。"""
    return obs["page"]["name"] + json.dumps(_norm(obs.get("fields") or {}), sort_keys=True, ensure_ascii=False, default=str)


def stuck(seg: Segment, secs: int = STUCK_SECS, repeats: int = STUCK_REPEATS) -> list[Finding]:
    """同一页、决策同一动作的连续步里，画面读数反复回到出现过的状态：动作没有推进。
    「回到出现过的状态」包括读数完全不变（同一动作反复失败），也包括在几个状态间来回切换
    （main13 R2 再挑戦 T3：play:2 在两张牌间切换选中 9 次，体力读数 21 / 25 交替，手牌读不到）。
    回访 ≥ repeats-1 次，或回访过且持续 ≥ secs 秒，记一条；正常出牌每步状态都会变，不会回访。"""
    out = []
    run: list[dict] = []

    def flush():
        sigs = [_sig(x["observation"]) for x in run]
        again = [i for i, g in enumerate(sigs) if g in sigs[:i]]
        if not again:
            return
        lo = min(sigs.index(sigs[i]) for i in again)
        part = run[lo:again[-1] + 1]  # 只报来回的那一段
        revisits = len(part) - len(set(sigs[lo:again[-1] + 1]))
        span = (part[-1].get("t") or 0) - (part[0].get("t") or 0)
        if revisits >= repeats - 1 or (revisits >= 1 and span >= secs):
            n_states = len(part) - revisits
            how = "画面不变" if n_states == 1 else f"在 {n_states} 个画面状态间来回"
            out.append(_f(seg, "执行", "stuck", part[0], f"{part[0]['observation']['page']['name']} {_akey(_action(part[0]))[:80]} × {len(part)}，"
                          f"{how}，{span:.0f}s 无进展", seconds=round(span), repeats=len(part), _steps=list(part)))  # fmt: skip

    for st in seg.steps:
        if not st.get("decision"):
            continue
        o, p = st["observation"], run[-1]["observation"] if run else None
        same = (p is not None and o["page"]["name"] == p["page"]["name"] and _akey(_action(st)) == _akey(_action(run[-1]))
                and o["fields"].get("remaining_turns") == p["fields"].get("remaining_turns"))  # 考试换回合就断开  # fmt: skip
        if same:
            run.append(st)
            continue
        flush()
        run = [st]
    flush()
    return out


LOG_TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3})\]")
SELECT_LOGS = (
    ("select_cancel", re.compile(r"出牌前另一张牌停在选中态")),
    ("select_retap", re.compile(r"卡牌停在选中态（SELECT）→ 再点一次")),
    ("select_not_seen", re.compile(r"出牌 \(\d+,\s*\d+\)：[\d.]+s 内没看到 SELECT")),
)


def _log_time(line: str) -> float | None:
    m = LOG_TS.match(line)
    if not m:
        return None
    import time as _time

    return _time.mktime(_time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000


def _step_at(seg: Segment, t: float | None) -> dict | None:
    """日志时刻正在执行的步：t 之前最后一个步（步的 t 是观测时刻，决策与执行在其后）。"""
    if t is None:
        return None
    prev = None
    for st in seg.steps:
        if (st.get("t") or 0) > t:
            break
        prev = st
    return prev


def selection_log(seg: Segment) -> list[Finding]:
    """考试出牌的选中态问题（只在日志里）：出牌前别的牌停在选中态被取消 / 点了卡停在选中态要再点 / 出牌后没看到 SELECT。"""
    out = []
    for ln in seg.log:
        for kind, pat in SELECT_LOGS:
            if pat.search(ln):
                st = _step_at(seg, _log_time(ln))
                f = _f(seg, "执行", kind, st, ln.split("] ", 3)[-1][:120])
                f.t = _log_time(ln)
                out.append(f)
    return out


# ------------------------------------------------------------------ 流程
def _exception_line(log: list[str]) -> str:
    """最后一个 Traceback 之后第一行不缩进、不是日志行的文本（如 IndexError: list index out of range）。"""
    idx = max(i for i, ln in enumerate(log) if ln.startswith("Traceback (most recent call last)"))
    for ln in log[idx + 1:]:
        if ln and not ln[0].isspace() and not ln.startswith("["):
            return ln[:160]
    return "Traceback"


def _recovered_after_last_traceback(log: list[str]) -> bool:
    idx = max(i for i, ln in enumerate(log) if ln.startswith("Traceback (most recent call last)"))
    return any("开始主循环" in ln for ln in log[idx:])


def _log_crashes(seg: Segment) -> list[Finding]:
    """日志里「主循环异常（第 N 次），3 秒后从当前画面继续」：进程内自动重来（trace 里没有 recover:crash_restart 时才用）。"""
    out = []
    for i, ln in enumerate(seg.log):
        if "主循环异常" in ln:
            nxt = next((x for x in seg.log[i + 1:] if x and not x[0].isspace() and not x.startswith(("[", "Traceback"))), "")
            out.append(Finding("流程", "crash_restart", seg.name, None, f"{ln.split('] ', 3)[-1][:60]} {nxt[:100]}".strip()))
    return out


LOG_UNKNOWN = re.compile(r"画面持续未识别 (\d+)s（\w+），画面文字：(.*)")


def _log_unknown(seg: Segment, secs: int = UNKNOWN_SECS) -> list[Finding]:
    """没有 trace 步的段（卡在认不出的画面直到被停）：从日志的「画面持续未识别 Xs」取最长一次。"""
    hits = [(int(m.group(1)), m.group(2)) for ln in seg.log if (m := LOG_UNKNOWN.search(ln))]
    if not hits:
        return []
    s, texts = max(hits)
    return [Finding("识别", "unknown_page", seg.name, None, f"{s}s：{texts[:80]}", None, None, {"seconds": s})] if s >= secs else []


def end_reason(seg: Segment) -> tuple[str, str]:
    """段的结束方式：finished / stopped（报错停止）/ exception / resume_blocked / killed（日志没有运行报告：进程被停）。"""
    text = "\n".join(seg.log)
    errors = [ln for ln in seg.log if "[ERROR]" in ln and "主循环异常" not in ln]
    if "ResumeBlocked" in text or ("破棄" in text and errors and "破棄" in errors[-1]):
        return "resume_blocked", (errors or ["ResumeBlocked"])[-1].split("] ", 3)[-1][:160]
    if "Traceback (most recent call last)" in text and not _recovered_after_last_traceback(seg.log):
        return "exception", _exception_line(seg.log)
    if errors:
        return "stopped", errors[-1].split("] ", 3)[-1][:160]
    if not any(ln.startswith("# 运行报告") for ln in seg.log):
        return "killed", "日志没有运行报告：进程被停 / 中断"
    return "finished", ""


def _entered(seg: Segment) -> bool:
    return bool(seg.steps) or any("开始主循环" in ln for ln in seg.log)


def flow(segs: list[Segment]) -> list[Finding]:
    out = []
    for i, seg in enumerate(segs):
        crashes = [_f(seg, "流程", "crash_restart", st, n.split(":", 2)[2].strip()[:120]) for st, n in _notes(seg)
                   if n.startswith("recover:crash_restart:")]  # fmt: skip
        out += crashes or _log_crashes(seg)
        out += [_f(seg, "流程", "llm_rescue", st, n.split(":", 2)[2].strip()[:160]) for st, n in _notes(seg)
                if n.startswith("recover:llm_rescue:")]  # fmt: skip
        out += [_f(seg, "流程", "rec_pause", st, n[:160]) for st, n in _notes(seg) if n.startswith(("rec_pause:", "rec_resume:"))]
        if not seg.steps:
            out += _log_unknown(seg)
        reason, detail = end_reason(seg)
        last = seg.steps[-1] if seg.steps else None
        done = bool(last) and any(o["page"]["name"] == "PRODUCE_END" for o in _obs_all(last))
        if not _entered(seg) and reason != "finished":
            out.append(Finding("流程", "resume_failed", seg.name, None, f"{seg.run.get('command')} 没进入主循环：{detail}", seg.run.get("started")))
        elif reason in ("stopped", "exception", "resume_blocked") or (reason == "killed" and i + 1 < len(segs)):
            out.append(_f(seg, "流程", "run_stopped", last, f"{reason}：{detail}" + ("（局已打完，收尾时出错）" if done else ""),
                          after_end=done or None))  # fmt: skip
        if i + 1 < len(segs):
            nxt = segs[i + 1]
            gap = (nxt.run.get("started") or 0) - ((last or {}).get("t") or seg.run.get("started") or 0)
            out.append(Finding("流程", "manual_intervention", nxt.name, None, f"重开（{nxt.run.get('command')}），上一段 {reason}：{detail[:80]}",
                               nxt.run.get("started"), None, {"seconds": round(gap) if gap > 0 else None}))  # fmt: skip
    return out


def manual_log(segs: list[Segment], path: Path = ROOT / MANUAL_LOG) -> list[Finding]:
    names = {s.name for s in segs}
    bases = {re.sub(r"-r\d+$", "", n) for n in names}
    out = []
    for row in _read_jsonl(path):
        run = row.get("run", "")
        if run in names or run in bases:
            out.append(Finding("流程", "manual_intervention", run, row.get("seq"), f"手工登记：{row.get('reason')} {row.get('detail') or ''}".strip(),
                               row.get("t")))  # fmt: skip
    return out


# ------------------------------------------------------------------ 资源（购买、饮料在 execution 里一起判）
def _price(st: dict) -> int | None:
    act = _action(st)
    for c in (st.get("decision") or {}).get("candidates") or []:
        if c.get("action") == act or _akey(c.get("action") or {}) == _akey(act):
            p = c.get("preview") or {}
            for k in ("价格", "price", "P点", "费用"):
                v = p.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
                if isinstance(v, str) and (m := re.search(r"\d+", v)):
                    return int(m.group())
    return None


FINAL_RANK = re.compile(r"本战排名页（final）：第 (\d) 轮，我方第 (\d) 名")
RETRY_DONE = ("使用再挑戦", "到上限", "上限 ")


def retry_unused(segs: list[Segment]) -> list[Finding]:
    """整局看：没过线 / 本战没拿第 1，之后（含后续续跑段）没有「使用再挑戦」，也没有「已到上限」的说明。"""
    timeline: list[tuple[Segment, dict | None, str]] = []
    for seg in segs:
        timeline += [(seg, st, n) for st, n in _notes(seg)] + [(seg, None, ln) for ln in seg.log if "再挑戦" in ln or FINAL_RANK.search(ln)]
    out, flagged = [], set()
    for k, (seg, st, text) in enumerate(timeline):
        rest = [x for _, _, x in timeline[k + 1:]] + [x for _, _, x in timeline[max(0, k - 3):k]]
        handled = any(w in x for x in rest for w in RETRY_DONE)
        m = FINAL_RANK.search(text)
        if "未过线，游戏提示重挑战 / 结束培育" in text and not handled:
            out.append(_f(seg, "资源", "retry_unused", st, text) if st else Finding("资源", "retry_unused", seg.name, None, text))
        elif "没有再挑戦按钮" in text:
            out.append(_f(seg, "资源", "retry_unused", st, text + "（确认是否真的没有次数）"))
        elif m and m.group(2) != "1" and not handled and m.group(1) not in flagged:
            flagged.add(m.group(1))
            out.append(Finding("资源", "retry_unused", seg.name, None, f"本战第 {m.group(1)} 轮最终第 {m.group(2)} 名，之后没有再挑戦记录"))
    return out


SEGMENT_DETECTORS = [skip_with_playable, slot_mismatch, wrong_card_exam, wrong_card_select, hand_mismatch, read_error, model_mismatch, unknown_page,
                     result_no_score, execution, selection_log]  # fmt: skip


def _label_of(st: dict) -> str | None:
    return ((st.get("state") or {}).get("exam_session") or {}).get("label") or st["observation"]["fields"].get("exam_label")


def _drink_ids(drinks) -> Counter:
    return Counter(d.get("drink_id") or d.get("name") for d in drinks or [] if isinstance(d, dict) and (d.get("drink_id") or d.get("name")))


def _drink_name(drink_id: str) -> str:
    try:
        import importlib

        import hiro  # noqa: F401  先装兼容层，再读 KAA 数据库

        drinks = importlib.import_module("kaa.db.drink").Drink.all()
        return next((d.name for d in drinks if d.id == drink_id), drink_id)
    except Exception:  # noqa: BLE001  离线没有 KAA 数据库时用编号
        return drink_id


def interval_drinks(segs: list[Segment]) -> list[Finding]:
    """轮间决策用的持有饮料（state.produce.drinks）比 R1 结束时实际剩下的多：饮料账没跟上 R1 里喝掉的（第二十九局：R1 四瓶喝完，
    轮间仍按开局 4 瓶算「饮料栏满」，一瓶没买，协调 2026-11-04）。
    决策用了纠正后的持有（warnings 有「轮间持有：…不一致，按…」，决策 fix_matto_interval_held）时 produce.drinks 过时也不算失误。
    R1 实际剩余 = 轮间前最后一个 R1 出牌决策点画面上的饮料栏，减去这一步喝掉的那瓶。"""
    out = []
    last_r1: tuple[Segment, dict] | None = None
    reasons = [(st.get("decision") or {}).get("reason") or "" for seg in segs for st in seg.steps
               if (st.get("decision") or {}).get("decider") == "interval"]  # fmt: skip
    # 决策已发现并纠正（fix_matto_interval_held：按画面 / R1 结束剩余取持有，warnings 里报「轮间持有：…不一致，按…」）：不算失误
    corrected = any(w.startswith("轮间持有：") and "按" in w for seg in segs for st in seg.steps
                    if (st.get("decision") or {}).get("decider") == "interval" for w in (st.get("decision") or {}).get("warnings") or [])  # fmt: skip
    for seg in segs:
        for st in seg.steps:
            d = st.get("decision") or {}
            if d.get("decider") == "battle" and _label_of(st) == "r1":
                last_r1 = (seg, st)
            elif d.get("decider") == "interval" and last_r1 is not None:
                rseg, rst = last_r1
                last_r1 = None  # 每次轮间只核对第一步
                f = rst["observation"]["fields"]
                if f.get("drinks") is None:
                    continue  # R1 最后一步饮料栏没读到，不判
                left = _drink_ids(f.get("drinks"))
                a = (rst.get("execution") or {}).get("action") or {}
                if a.get("kind") == "UseDrink":
                    used = next((x for x in f.get("drinks") or [] if isinstance(x, dict) and x.get("slot") == (a.get("args") or {}).get("slot")), None)
                    if used:
                        left -= Counter([used.get("drink_id") or used.get("name")])
                held = Counter(x for x in ((st.get("state") or {}).get("produce") or {}).get("drinks") or [] if x)
                extra = held - left
                if extra and not corrected:
                    cap = ((st.get("state") or {}).get("produce") or {}).get("drink_capacity")
                    full = any("饮料栏满" in r for r in reasons)
                    out.append(_f(seg, "资源", "drink_ledger", st,
                                  f"R1 结束时剩 {sum(left.values())} 瓶（{rseg.name} #{rst.get('seq')}），轮间按持有 {sum(held.values())} 瓶算"
                                  f"{'（判成饮料栏满）' if full else ''}：多算 {'、'.join(map(_drink_name, extra.elements()))}" + (f"；饮料栏 {cap} 格" if cap else ""),
                                  phantom=sum(extra.values())))  # fmt: skip
    return out


def audit(segs: list[Segment], manual_path: Path = ROOT / MANUAL_LOG) -> list[Finding]:
    out = [f for seg in segs for det in SEGMENT_DETECTORS for f in det(seg)]
    out += flow(segs) + retry_unused(segs) + interval_drinks(segs) + manual_log(segs, manual_path)
    order = {s.name: i for i, s in enumerate(segs)}
    return sorted(out, key=lambda f: (order.get(f.segment, 99), f.seq if f.seq is not None else -1))


# ------------------------------------------------------------------ 报告
def summarize(findings: list[Finding]) -> dict[str, dict[str, int]]:
    c = Counter((f.category, f.kind) for f in findings)
    return {cat: {k: c[(cat, k)] for k in kinds} for cat, kinds in KINDS.items()}


def to_json(name: str, segs: list[Segment], findings: list[Finding]) -> dict:
    return {
        "name": name, "segments": [s.name for s in segs], "kinds": KINDS, "gaps": GAPS, "counts": summarize(findings),
        "total": len(findings), "steps": sorted({f"{f.segment}#{f.seq}" for f in findings if f.seq is not None}),
        "findings": [{**asdict(f), "label": f.label} for f in findings],
    }  # fmt: skip


def _impact(f: Finding) -> str:
    i = f.impact
    parts = []
    if i.get("expected_loss"):
        parts.append(f"期望分 -{i['expected_loss']:,}")
    if i.get("seconds"):
        parts.append(f"{i['seconds']:.0f}s")
    if i.get("price"):
        parts.append(f"{i['price']} P")
    if i.get("phantom"):
        parts.append(f"多算 {i['phantom']} 瓶")
    if i.get("auto_fixed"):
        parts.append("已自动纠正")
    return "，".join(parts)


def to_md(data: dict) -> str:
    lines = [f"# 低级失误报告：{data['name']}", "", f"段：{'、'.join(data['segments'])}。共 {data['total']} 条，涉及 {len(data['steps'])} 步。", ""]
    lines += ["| 类别 | 种类 | 次数 | 估计影响 |", "|---|---|---|---|"]
    by_kind: dict[str, list[dict]] = {}
    for f in data["findings"]:
        by_kind.setdefault(f["kind"], []).append(f)
    for cat, kinds in data["kinds"].items():
        for k, label in kinds.items():
            fs = by_kind.get(k, [])
            secs = sum(f["impact"].get("seconds") or 0 for f in fs)
            loss = sum(f["impact"].get("expected_loss") or 0 for f in fs)
            imp = "，".join(x for x in (f"期望分 -{loss:,}" if loss else "", f"{secs:.0f}s" if secs else "") if x)
            lines.append(f"| {cat} | {label} | {len(fs)} | {imp} |")
    lines.append("")
    for cat, kinds in data["kinds"].items():
        for k, label in kinds.items():
            fs = by_kind.get(k, [])
            if not fs:
                continue
            lines += [f"## {cat}：{label}（{len(fs)}）", ""]
            for f in fs:
                where = f"{f['segment']} #{f['seq']}" if f["seq"] is not None else f["segment"]
                imp = _impact(Finding(**{x: f[x] for x in ("category", "kind", "segment", "seq", "detail", "t", "frame", "impact")}))
                lines.append(f"- {where}：{f['detail']}" + (f"（{imp}）" if imp else "") + (f" [帧]({(ROOT / f['frame']).resolve()})" if f.get("frame") else ""))
            lines.append("")
    lines += ["## 检测缺口", ""] + [f"- {data['kinds'][next(c for c in data['kinds'] if k in data['kinds'][c])][k]}：{v}" for k, v in data["gaps"].items()]
    return "\n".join(lines) + "\n"


def write(name: str, segs: list[Segment], findings: list[Finding], out_dir: Path) -> dict:
    data = to_json(name, segs, findings)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}-blunders.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out_dir / f"{name}-blunders.md").write_text(to_md(data), encoding="utf-8")
    return data


# ------------------------------------------------------------------ 基线
PREFIX = os.environ.get("HIRO_TRACE_PREFIX", "")  # trace 基名前缀（tools/play_game 同一个变量）：[<PREFIX>-]<qualify|main><N>-<日期>-<时间>


def games(traces: Path, numbers=range(9, 13), modes=("qualify", "main")) -> dict[str, list[str]]:
    """第 N 局 → 基名列表（同一局开局失败重开的几个基名放一起）。"""
    out: dict[str, list[str]] = {}
    for mode in modes:
        for n in numbers:
            bases = sorted({re.sub(r"-r\d+$", "", p.name) for p in traces.glob(f"{PREFIX + '-' if PREFIX else ''}{mode}{n}-*")})
            if bases:
                out[f"{mode}{n}"] = bases
    return out


def baseline(traces: Path, out_dir: Path) -> str:
    rows = {}
    for name, bases in games(traces).items():
        segs = load_game(bases, traces)
        rows[name] = write(name, segs, audit(segs), out_dir)
    names = list(rows)
    lines = ["# 低级失误基线（第九–十二局）", "", "| 类别 | 种类 | " + " | ".join(names) + " | 合计 |", "|---|---|" + "---|" * (len(names) + 1)]
    for cat, kinds in KINDS.items():
        for k, label in kinds.items():
            vals = [rows[n]["counts"][cat][k] for n in names]
            lines.append(f"| {cat} | {label} | " + " | ".join(map(str, vals)) + f" | {sum(vals)} |")
    tot = [rows[n]["total"] for n in names]
    lines.append("| **合计** | | " + " | ".join(map(str, tot)) + f" | {sum(tot)} |")
    md = "\n".join(lines) + "\n"
    (out_dir / "blunders-baseline.md").write_text(md, encoding="utf-8")
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bases", nargs="*", help="trace 基名（不带 -rN）；同一局的几个基名可一起传")
    ap.add_argument("--name", help="报告名（默认第一个基名）")
    ap.add_argument("--traces", type=Path, default=ROOT / "traces")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "analysis")
    ap.add_argument("--baseline", action="store_true", help="第九–十二局基线汇总")
    args = ap.parse_args()
    if args.baseline:
        print(baseline(args.traces, args.out))
        return
    if not args.bases:
        ap.error("要给 trace 基名，或用 --baseline")
    segs = load_game(args.bases, args.traces)
    name = args.name or args.bases[0]
    data = write(name, segs, audit(segs), args.out)
    print(to_md(data).split("\n## ", 1)[0])
    print(f"→ {args.out / (name + '-blunders.md')}")


if __name__ == "__main__":
    main()
