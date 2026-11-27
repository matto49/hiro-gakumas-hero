"""续跑时恢复「本局已拿到的卡」（用户规则 3e）：按 trace 里执行成功的事件顺序记账。

加：三选一拿卡（SELECT_CARD ActivateEffect）、相談买卡（CONSULT ShopBuy 货架上的卡）、换卡换入（SKILL_CARD_CHANGE_1 选中、阶段 2 真的换了）、
    回忆带的卡（memory_cards，开局获得）；
减：换卡换出（SKILL_CARD_CHANGE_2 remove_card_id）、删卡事件（SKILL_CARD_REMOVAL card_id）、相談删卡（ShopBuy service=delete，按当时牌组的 card_index）。
读到完整牌组（DECK_LIST，complete 不为 False）时以牌组为准重置，再接着记之后的事件。
只用于「必拿卡齐没齐」这类判断；牌组识别到位时决策器以牌组为准。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path


def _page(step: dict) -> str | None:
    return ((step.get("observation") or {}).get("page") or {}).get("name") or (
        (step.get("state") or {}).get("page") or {}
    ).get("name")


def owned_cards(steps: Iterable[dict], memory_cards: Iterable[str] = ()) -> Counter:
    """返回 card_id → 张数。steps 按时间顺序。"""
    owned: Counter = Counter(memory_cards)
    pending_in: str | None = None
    for st in steps:
        page = _page(st)
        state = st.get("state") or {}
        pf = state.get("page_fields") or {}
        fields = (st.get("observation") or {}).get("fields") or {}
        if page == "DECK_LIST" and fields.get("deck") and fields.get("complete") is not False:
            owned = Counter(c["card_id"] for c in fields["deck"] if c.get("card_id"))
            continue
        act = (st.get("decision") or {}).get("action") or {}
        args = act.get("args") or {}
        if not (st.get("execution") or {}).get("ok"):
            continue
        kind = act.get("kind")
        if page == "SELECT_CARD" and kind == "ActivateEffect":
            cands = pf.get("candidates") or []
            for i in args.get("pick_indexes") or []:
                if 0 <= int(i) < len(cands) and cands[int(i)].get("card_id"):
                    owned[cands[int(i)]["card_id"]] += 1
        elif page == "CONSULT" and kind == "ShopBuy":
            if args.get("service") == "delete":
                deck = (state.get("produce") or {}).get("deck") or []
                i = args.get("card_index")
                if isinstance(i, int) and 0 <= i < len(deck) and deck[i].get("card_id"):
                    owned[deck[i]["card_id"]] -= 1
            elif "index" in args:
                shelf = pf.get("shelf") or []
                i = int(args["index"])
                if 0 <= i < len(shelf) and (shelf[i] or {}).get("card_id"):
                    owned[shelf[i]["card_id"]] += 1
        elif page == "SKILL_CARD_CHANGE_1" and kind == "ChangeCard" and not args.get("cancel"):
            cands = pf.get("candidates") or []
            i = args.get("pick_index")
            pending_in = cands[i].get("card_id") if isinstance(i, int) and 0 <= i < len(cands) else None
        elif page == "SKILL_CARD_CHANGE_2" and kind == "ChangeCard":
            if not args.get("cancel") and args.get("remove_card_id"):
                owned[args["remove_card_id"]] -= 1
                if pending_in:
                    owned[pending_in] += 1
            pending_in = None
        elif page == "SKILL_CARD_REMOVAL" and args.get("card_id"):
            owned[args["card_id"]] -= 1
    return +owned  # 去掉 ≤0 的


def picked_cards(steps: Iterable[dict]) -> Counter:
    """本局三选一领取成功（execution.ok）的卡 card_id → 张数，减去之后删掉的（相談删卡带 card_id、删卡页）。
    刷回忆的删卡正向名单（hiro.brain.farm.junk_cards）：只删这里面的卡，牌组快照（DECK_LIST）不算进来——那里面有借用回忆带进来的卡。"""
    got: Counter = Counter()
    for st in steps:
        page = _page(st)
        act = (st.get("decision") or {}).get("action") or {}
        args = act.get("args") or {}
        if not (st.get("execution") or {}).get("ok"):
            continue
        kind = act.get("kind")
        if page == "SELECT_CARD" and kind == "ActivateEffect":
            cands = ((st.get("state") or {}).get("page_fields") or {}).get("candidates") or []
            for i in args.get("pick_indexes") or []:
                if 0 <= int(i) < len(cands) and cands[int(i)].get("card_id"):
                    got[cands[int(i)]["card_id"]] += 1
        elif args.get("card_id") and (
            (page == "CONSULT" and kind == "ShopBuy" and args.get("service") == "delete")
            or page == "SKILL_CARD_REMOVAL"
        ):
            got[args["card_id"]] -= 1
    return +got


def steps_of(trace_dirs: Iterable[Path]) -> Iterable[dict]:
    for d in trace_dirs:
        f = Path(d) / "steps.jsonl"
        if not f.exists():
            continue
        for line in f.open(encoding="utf-8"):
            try:
                yield json.loads(line)
            except ValueError:
                continue
