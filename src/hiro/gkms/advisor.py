"""GKMS 育成插件（spec 4.5）：初的选行动走 GKMS 路线规划，选卡类页面走 pickv 的 V。

GKMS 在子进程里算（hiro.gkms.client）；算不了、对不上、出错时返回 None，这一步交回内置决策器（插件闸门记「不管」）。
为什么不管写进 trace 目录的 gkms.jsonl。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from hiro.contracts import Action, ActionKind, Decision
from hiro.gkms import state_map
from hiro.gkms.client import Client, GkmsError
from hiro.state.modes import HIF, family

logger = logging.getLogger(__name__)

VERSION = "0.1"
CARD_PAGES = frozenset({"SELECT_CARD", "SKILL_CARD_CHANGE_1", "SKILL_CARD_CHANGE_2", "SKILL_CARD_REMOVAL", "SKILL_CARD_ENHANCE"})
PACK_WAIT_S = 10.0  # 第一次用到某张偶像卡的选卡包时，在 worker 里最多等这么久下载
CALL_TIMEOUT_S = 25.0


def _cid(c: Any) -> str | None:
    if isinstance(c, dict):
        return c.get("card_id")
    return getattr(c, "card_id", None)


class GkmsProduceAdvisor:
    id = "gkms"
    version = VERSION
    contracts = "1"
    pages = frozenset({"ACTION_SELECT"}) | CARD_PAGES

    def __init__(self, client: Any = None) -> None:
        if client is None:
            from hiro.gkms import locate

            inst = locate.find()
            if inst is None:
                raise RuntimeError(locate.problem() or "没找到 GKMS")
            client = Client(inst)
            self.version = f"{VERSION}+{inst.version}" if inst.version else VERSION
        self.client = client
        self.context: dict = {}
        self._hello: dict | None = None
        self._log: Path | None = None

    # ------------------------------------------------------------------ 生命周期
    def begin(self, context: dict) -> None:
        self.context = dict(context or {})
        td = self.context.get("trace_dir")
        self._log = Path(td) / "gkms.jsonl" if td else None

    def end(self, result: dict | None) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

    def hello(self) -> dict:
        if self._hello is None:
            self._hello = self.client.call("hello")
        return self._hello

    def _note(self, page: str, **rec: Any) -> None:
        if self._log is None:
            return
        try:
            with self._log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "page": page, **rec}, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _defer(self, page: str, why: str) -> None:
        logger.info("GKMS 不管 %s：%s", page, why)
        self._note(page, deferred=why)
        return None

    # ------------------------------------------------------------------ 入口
    def decide(self, page: str, state: Any, *, n_options: int | None, kw: dict) -> Decision | None:
        try:
            if page == "ACTION_SELECT":
                return self._action_select(state)
            fn = {"SELECT_CARD": self._select_card, "SKILL_CARD_CHANGE_1": self._change_pick, "SKILL_CARD_CHANGE_2": self._change_remove,
                  "SKILL_CARD_REMOVAL": self._remove, "SKILL_CARD_ENHANCE": self._enhance}.get(page)  # fmt: skip
            return fn(state) if fn else None
        except GkmsError as e:
            return self._defer(page, str(e))

    # ------------------------------------------------------------------ 选行动
    def _action_select(self, state: Any) -> Decision | None:
        rec, why = state_map.record(state)
        if rec is None:
            return self._defer("ACTION_SELECT", why or "拼不出 GKMS 局面")
        if not rec.get("idol_card_id"):
            rec["idol_card_id"] = self.context.get("idol")
        res = self.client.call("route", record=rec, from_step=rec["from_step"], timeout=CALL_TIMEOUT_S)
        step = rec["from_step"]
        choice = dict((int(s), c) for s, c in res["plan"]["route"]).get(step)
        opts = (state.page_fields or {}).get("options") or []
        i = state_map.option_for(choice, opts) if choice else None
        if i is None:
            return self._defer("ACTION_SELECT", f"GKMS 计划本周选 {choice}，画面选项里没有")
        o = opts[i]
        dec = next((d for d in res.get("decisions") or [] if d.get("step") == step), None)
        others = "、".join(f"{x['choice']} {x['vs_best']:+g}" for x in (dec or {}).get("options") or [] if x.get("vs_best") not in (None, 0))
        end = res["plan"].get("end") or {}
        reason = (f"GKMS 路线：第 {step} 周选 {choice}（评分 {res['plan'].get('rating')}"
                  + (f"；其余比最优差：{others}" if others else "")
                  + f"；计划终局 Vo {end.get('vocal')} / Da {end.get('dance')} / Vi {end.get('visual')}）")  # fmt: skip
        self._note("ACTION_SELECT", step=step, choice=choice, option=i, rating=res["plan"].get("rating"), route=res["plan"]["route"])
        return Decision(Action.produce_next(o["option"], attr=o.get("attr"), sp=o.get("sp")), reason, "plan", 0.8,
                        decider="gkms_route")  # fmt: skip

    # ------------------------------------------------------------------ 选卡类
    def _idol(self, state: Any) -> str | None:
        return (state.produce or {}).get("idol_card_id") or self.context.get("idol")

    def _pack_label(self, state: Any) -> str:
        return "pickv" if family((state.produce or {}).get("mode")) == HIF else "pickv（H.I.F 的包）"

    def _deck(self, state: Any, page_cards: list | None = None) -> list[str]:
        deck = [c for c in ((state.produce or {}).get("deck") or []) if _cid(c)]
        if not deck and page_cards:
            deck = [c for c in page_cards if _cid(c)]
        return [_cid(c) for c in deck]

    def _values(self, state: Any, *, offer=(), upgrade=(), page_cards=None) -> dict | None:
        mode = (state.produce or {}).get("mode")
        r = self.client.call("values", idol=self._idol(state), deck=self._deck(state, page_cards), offer=list(offer), upgrade=list(upgrade),
                             rarity_mode=mode if family(mode) == HIF else "produce-008", wait_s=PACK_WAIT_S, timeout=CALL_TIMEOUT_S)  # fmt: skip
        if r.get("state") != "ok":
            return None
        return r

    def _best_take(self, page: str, state: Any, cands: list[dict]) -> tuple[int, dict, float, dict] | None:
        known = [(i, c) for i, c in enumerate(cands) if c.get("card_id")]
        if not known:
            return self._defer(page, "候选都没认出")
        r = self._values(state, offer=[c["card_id"] for _, c in known])
        if r is None:
            return self._defer(page, f"选卡包不可用（偶像卡 {self._idol(state)}）")
        scored = [(r["take"].get(c["card_id"]), i, c) for i, c in known]
        scored = [(v, i, c) for v, i, c in scored if v is not None]
        if not scored:
            return self._defer(page, "候选都不在选卡包里")
        v, i, c = max(scored, key=lambda x: (x[0], -x[1]))
        return i, c, v, r

    def _select_card(self, state: Any) -> Decision | None:
        pf = state.page_fields or {}
        cands = list(pf.get("candidates") or [])
        got = self._best_take("SELECT_CARD", state, cands)
        if got is None:
            return None
        i, c, v, r = got
        lab = self._pack_label(state)
        gain, thr = r.get("reroll_gain"), self.hello().get("reroll_gain", 0.1)
        left = pf.get("rerolls_left")
        listing = "、".join(f"{x.get('card_id')} {r['take'].get(x.get('card_id'))}" for x in cands if x.get("card_id"))
        if gain is not None and gain > thr and isinstance(left, int) and left > 0:
            self._note("SELECT_CARD", reroll=True, gain=gain, take=r["take"])
            return Decision(Action(ActionKind.REROLL), f"{lab}：刷新（新候选的期望最好 V 比现在高 {gain:g} > {thr:g}；现在 {listing}）", "plan", 0.7,
                            decider="gkms_pickv")  # fmt: skip
        self._note("SELECT_CARD", pick=i, card=c["card_id"], v=v, take=r["take"])
        return Decision(Action.activate_effect(i), f"{lab}：拿 {c['card_id']}（V {v:g}；候选 {listing}）", "plan", 0.8, decider="gkms_pickv")

    def _change_pick(self, state: Any) -> Decision | None:
        cands = list((state.page_fields or {}).get("candidates") or [])
        got = self._best_take("SKILL_CARD_CHANGE_1", state, cands)
        if got is None:
            return None
        i, c, v, _r = got
        self._note("SKILL_CARD_CHANGE_1", pick=i, card=c["card_id"], v=v)
        return Decision(Action(ActionKind.CHANGE_CARD, {"stage": 1, "pick_index": c.get("index", i)}),
                        f"{self._pack_label(state)}：换入 {c['card_id']}（V {v:g}）", "plan", 0.8, decider="gkms_pickv")  # fmt: skip

    def _removal_pick(self, page: str, state: Any, cards: list[dict], *, allow_trouble: bool) -> dict | None:
        """按 pickv.removal_by_v 的顺序，挑第一张画面上认得出、没定制过的卡。"""
        deck = [{"card_id": _cid(c), "upgrade": (c.get("upgrade") if isinstance(c, dict) else getattr(c, "upgrade", 0)) or 0}
                for c in ((state.produce or {}).get("deck") or cards) if _cid(c)]  # fmt: skip
        r = self.client.call("removal", idol=self._idol(state), deck=deck, allow_trouble=allow_trouble, wait_s=PACK_WAIT_S, timeout=CALL_TIMEOUT_S)
        if r.get("state") != "ok":
            self._defer(page, f"选卡包不可用（偶像卡 {self._idol(state)}）")
            return {"defer": True}
        usable = [c for c in cards if c.get("card_id") and not c.get("customized")]
        for want in r.get("order") or []:
            for c in usable:
                if c["card_id"] == want["card_id"] and (want.get("upgrade") is None or int(c.get("upgrade") or 0) == int(want["upgrade"] or 0)):
                    return c
        return None

    def _change_remove(self, state: Any) -> Decision | None:
        cards = list((state.page_fields or {}).get("cards") or [])
        if not cards:
            return self._defer("SKILL_CARD_CHANGE_2", "牌组卡格没读到")
        c = self._removal_pick("SKILL_CARD_CHANGE_2", state, cards, allow_trouble=False)
        if c is None or c.get("defer"):
            return None if c else self._defer("SKILL_CARD_CHANGE_2", "pickv 的换出顺序里没有画面上能选的卡")
        self._note("SKILL_CARD_CHANGE_2", card=c["card_id"], index=c.get("index"))
        return Decision(Action(ActionKind.CHANGE_CARD, {"stage": 2, "remove_card_id": c["card_id"], "remove_index": c.get("index"),
                                                        "remove_upgrade": c.get("upgrade") or 0}),
                        f"{self._pack_label(state)}：换出 {c['card_id']}（V 最低）", "plan", 0.8, decider="gkms_pickv")  # fmt: skip

    def _remove(self, state: Any) -> Decision | None:
        cards = list((state.page_fields or {}).get("cards") or [])
        if not cards:
            return self._defer("SKILL_CARD_REMOVAL", "删卡页卡格没读到")
        c = self._removal_pick("SKILL_CARD_REMOVAL", state, cards, allow_trouble=True)
        if c is not None and c.get("defer"):
            return None
        if c is None:
            self._note("SKILL_CARD_REMOVAL", cancel=True)
            return Decision(Action(ActionKind.REMOVE_CARD, {"cancel": True}), f"{self._pack_label(state)}：没有能删的卡（都定制过或没认出），取消",
                            "plan", 0.6, decider="gkms_pickv")  # fmt: skip
        self._note("SKILL_CARD_REMOVAL", card=c["card_id"], index=c.get("index"))
        return Decision(Action(ActionKind.REMOVE_CARD, {"card_id": c["card_id"], "index": c.get("index"), "upgrade": c.get("upgrade") or 0}),
                        f"{self._pack_label(state)}：删 {c['card_id']}（先删トラブル，再删 V 最低）", "plan", 0.8, decider="gkms_pickv")  # fmt: skip

    def _enhance(self, state: Any) -> Decision | None:
        cards = [c for c in (state.page_fields or {}).get("cards") or [] if c.get("card_id") and not c.get("upgrade")]
        if not cards:
            return self._defer("SKILL_CARD_ENHANCE", "没有可强化的卡格")
        ids = list(dict.fromkeys(c["card_id"] for c in cards))
        r = self._values(state, upgrade=ids, page_cards=(state.page_fields or {}).get("cards"))
        if r is None:
            return self._defer("SKILL_CARD_ENHANCE", f"选卡包不可用（偶像卡 {self._idol(state)}）")
        scored = [(r["up"].get(c["card_id"]), c) for c in cards]
        scored = [(v, c) for v, c in scored if v is not None]
        if not scored:
            return self._defer("SKILL_CARD_ENHANCE", "可强化的卡都不在选卡包里")
        v, c = max(scored, key=lambda x: x[0])
        self._note("SKILL_CARD_ENHANCE", card=c["card_id"], v=v)
        return Decision(Action(ActionKind.ENHANCE_CARD, {"card_id": c["card_id"], "index": c.get("index"), "upgrade": 0}),
                        f"{self._pack_label(state)}：强化 {c['card_id']}（V × 强化率 {v:g}）", "plan", 0.8, decider="gkms_pickv")  # fmt: skip
