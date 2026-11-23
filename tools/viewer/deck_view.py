"""复盘页「开局牌库」：每场考试（每次挑戦）第一步时的整副牌组（手牌 + 山札 + 弃牌 + 除外）。

数据来源：录像器 reports/runs/<目录>/events.jsonl 里 battle_decision 的 public_info（山札 / 弃牌是公开信息，按名字列出），
目录名从 reports/live/<段>.log 的「Decision log: reports/runs/…」找。手牌用 trace 那一步的手牌。
每张牌按名字查 master：是否一次性（レッスン中1回）、有没有再動（スキルカード使用数追加 / ターン追加）、会不会往山札塞眠気、
是不是初始卡、是不是核心保护卡。查不到 master（引擎不在）时只列名字。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PILES = (("draw", "山札"), ("discard", "弃牌"), ("exile", "除外"))
CUSTOM = "（定制）"


def recorder_events(seg: Path, root: Path = ROOT) -> list[dict]:
    """这一段 trace 对应的录像器考试决策（t、exam、public_info、手牌名）。root 是数据所在的仓库目录（traces 的上一级）。"""
    log = root / "reports" / "live" / f"{seg.name}.log"
    if not log.exists():
        return []
    dirs = re.findall(r"Decision log: (reports/runs/[^；\s]+)", log.read_text(encoding="utf-8", errors="ignore"))
    out = []
    for d in dict.fromkeys(dirs):
        ev = root / d / "events.jsonl"
        if not ev.exists():
            continue
        for line in ev.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "battle_decision":
                continue
            st = r.get("state") or {}
            hand = [c.get("name") if isinstance(c, dict) else c for c in st.get("hand") or []]
            out.append(
                {"t": r.get("t"), "exam": r.get("exam"), "public_info": st.get("public_info") or "", "hand": hand}
            )
    return out


def parse_piles(info: str) -> dict[str, list[str]]:
    piles = {}
    for key, word in PILES:
        m = re.search(rf"{word}\s*(\d+)\s*张[^：]*：([^；]*)", info)
        if m:
            names = [x.strip() for x in m.group(2).split("、") if x.strip()]
            piles[key] = names
    return piles


class Cards:
    """按名字查 master 卡牌属性；缓存。"""

    def __init__(self):
        self.cache: dict[str, dict] = {}
        self.ok = True
        try:
            sys.path.insert(0, str(ROOT / "src"))
            from hiro.brain.cards import description_text
            from hiro.state import knowledge
            from hiro.state.context import catalog

            self.cat, self.desc, self.k = catalog(), description_text, knowledge
        except Exception:
            self.ok = False

    def info(self, name: str) -> dict:
        if name in self.cache:
            return self.cache[name]
        custom = CUSTOM in name
        base = name.replace(CUSTOM, "").strip()
        d = {"name": name, "custom": custom}
        if self.ok and base:
            stem = base.rstrip("+")
            try:
                hit = next((r for r in self.cat.search(stem, 20) if r.get("name") == base), None)
            except Exception:
                hit = None
            if hit:
                cid, up = hit["card_id"], hit["upgrade_count"]
                try:
                    c = self.cat.card(cid, up)
                    text = self.desc(c.get("produceDescriptions")) or ""
                except Exception:
                    c, text = {}, ""
                trouble = "-acc-" in cid or "トラブル" in str(c.get("category"))
                d.update({
                    "id": cid, "upgrade": up, "text": text,
                    "once": "Lost" in str(c.get("playMovePositionType")),
                    "redo": bool(re.search(r"スキルカード使用数追加|ターン追加", text)),
                    "sleep": "眠気" in text and "生成" in text,
                    "basic": str(c.get("isInitialDeckProduceCard")) == "True" or "基本" in base,
                    "core": bool(self.k.is_protected(cid)) if hasattr(self.k, "is_protected") else False,
                    "trouble": trouble, "hp": c.get("stamina"),
                })  # fmt: skip
        self.cache[name] = d
        return d


def _summary(cards: list[dict]) -> dict:
    known = [c for c in cards if "once" in c]
    return {
        "total": len(cards),
        "once": sum(1 for c in known if c["once"] and not c["trouble"]),
        "reusable": sum(1 for c in known if not c["once"] and not c["trouble"]),
        "reusable_noredo": sum(
            1 for c in known if not c["once"] and not c["trouble"] and not c["redo"] and not c["core"]
        ),
        "sleep_gen": sum(1 for c in known if c["sleep"]),
        "trouble": sum(1 for c in known if c["trouble"]),
        "basic": sum(1 for c in known if c["basic"] and not c["trouble"]),
        "unknown": len(cards) - len(known),
    }


def attach_decks(data: dict, segments: list[Path], root: Path | None = None) -> None:
    """给 data["exams"] 里每场考试加 deck（开局牌库）和 deck_diff（和上一场不同 label 考试的差异）。"""
    root = root or (segments[0].parent.parent if segments else ROOT)
    events = [ev for seg in segments for ev in recorder_events(seg, root)]
    if not events:
        return
    cards = Cards()
    prev_by_label: dict[str, Counter] = {}
    for e in data["exams"]:
        first = e["steps"][0]
        t0 = first.get("t")
        cand = [ev for ev in events if ev["t"] is not None and t0 is not None and abs(ev["t"] - t0) < 30]
        if not cand:
            continue
        ev = min(cand, key=lambda ev: abs(ev["t"] - t0))
        piles = parse_piles(ev["public_info"])
        hand = [h["name"] for h in first.get("hand") or [] if h.get("name")] or [h for h in ev["hand"] if h]
        groups = {"hand": hand, **{k: piles.get(k, []) for k, _ in PILES}}
        deck = {k: [cards.info(n) for n in v] for k, v in groups.items()}
        flat = [c for v in deck.values() for c in v]
        e["deck"] = {"groups": deck, "summary": _summary(flat), "turn_rem": first.get("rem"),
                     "drinks": [d["name"] for d in first.get("drinks") or []]}  # fmt: skip
        names = Counter(c["name"].replace(CUSTOM, "") for c in flat)
        ref_label = next((lb for lb in prev_by_label if lb != e["label"]), None) if e["label"] == "r2" else None
        if ref_label:
            ref = prev_by_label[ref_label]
            e["deck_diff"] = {
                "vs": ref_label,
                "added": sorted((names - ref).elements()),
                "removed": sorted((ref - names).elements()),
            }
        prev_by_label.setdefault(e["label"], names)
