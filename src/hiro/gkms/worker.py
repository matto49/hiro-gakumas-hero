# pyright: reportMissingImports=false
# GakumasCLI 是本机装的 GKMS（运行时加进 sys.path），不是本仓库的依赖
"""在 GKMS 自己的 Python 里跑的子进程（spec 4.3）。只调用 GKMS 的函数，不复制它的算法。

    <python> -I -u worker.py <gkms_home>

标准输入每行一个请求 {"id", "op", "args"}，标准输出每行一个回复 {"id", "ok", "result" | "error"}。
GKMS 打到 stdout 的东西会弄坏协议，所以调用期间把 sys.stdout 换成 stderr。
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import traceback

HOME = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, HOME)

_OUT = sys.stdout
_world = None


def _mods():
    import GakumasCLI.produce.hajime_route as HR
    import GakumasCLI.produce.hif_route as R
    import GakumasCLI.produce.nia_produce as N
    import GakumasCLI.produce.nia_route as NR
    import GakumasCLI.produce.pickv as PV

    return HR, R, N, NR, PV


def _world_for(record):
    """GKMS 的 World（主数据模型只建一次），这次只放这一份培育记录。"""
    global _world
    _HR, R, _N, _NR, _PV = _mods()
    if _world is None:
        _world = R.World(produces=[])
    _world.produces = [record]
    return _world


def op_hello(_args):
    _HR, _R, _N, _NR, PV = _mods()
    return {"format": PV.FORMAT, "algo": PV.ALGO, "tau": PV.TAU, "reroll_gain": PV.REROLL_GAIN, "home": HOME}


def op_route(args):
    HR, _R, N, NR, _PV = _mods()
    rec = args["record"]
    world = _world_for(rec)
    pid, k = rec.get("produce_id"), int(args.get("beam") or 8)
    step = args.get("from_step")
    if HR.is_hajime(pid):
        res = HR.plan(world, rec["uuid"], step, k)
    elif N.is_nia(pid):
        res = NR.plan(world, rec["uuid"], step, k, ref=NR.FullRef(rec, world))
    else:
        raise LookupError(f"GKMS 路线规划不管 {pid}")
    return {"plan": res["plan"], "decisions": res["decisions"], "from_step": res["from_step"]}


def _pack(PV, idol, wait_s):
    deadline = time.monotonic() + float(wait_s or 0)
    while True:
        pack, state = PV.pack_for(idol)
        if state != "loading" or time.monotonic() >= deadline:
            return pack, state
        time.sleep(0.5)


def op_pack(args):
    _HR, _R, _N, _NR, PV = _mods()
    pack, state = _pack(PV, args.get("idol"), args.get("wait_s"))
    return {"state": state, "records": (pack or {}).get("records"), "snapshot": (pack or {}).get("snapshot")}


def op_values(args):
    """V：候选 V_{n+1}、牌组每张 V_n、可强化卡 V_1 × u、这组候选的刷新收益。没有包时 state 说明原因、其余为空。"""
    _HR, _R, _N, _NR, PV = _mods()
    pack, state = _pack(PV, args.get("idol"), args.get("wait_s"))
    out = {"state": state, "take": {}, "held": {}, "up": {}, "reroll_gain": None}
    if pack is None:
        return out
    deck = [str(c).split("+")[0] for c in args.get("deck") or []]
    held = {}
    for c in deck:
        held[c] = held.get(c, 0) + 1
    for c in args.get("offer") or []:
        out["take"][c] = PV.v_of(pack, c, held.get(c, 0) + 1)
    for c in set(deck):
        out["held"][c] = PV.v_of(pack, c, max(1, held[c]))
    for c in args.get("upgrade") or []:
        v = PV.v_of(pack, c, max(1, held.get(c, 1)))
        u = ((pack.get("cards") or {}).get(c) or {}).get("u", 0.0)
        out["up"][c] = None if v is None else round(v * u, 4)
    if args.get("offer"):
        out["reroll_gain"] = PV.reroll_gain(pack, args.get("rarity_mode") or "produce-008", held, list(args["offer"]))
    return out


def op_removal(args):
    """pickv.removal_by_v：先トラブル，再按 V_n 从低到高。deck 是 [{card_id, upgrade}]。"""
    _HR, _R, _N, _NR, PV = _mods()
    pack, state = _pack(PV, args.get("idol"), args.get("wait_s"))
    if pack is None:
        return {"state": state, "order": []}
    deck = [
        {"produce_card_id": c["card_id"], "current_upgrade_count": int(c.get("upgrade") or 0)}
        for c in args.get("deck") or []
    ]
    order = PV.removal_by_v(deck, pack, allow_trouble=bool(args.get("allow_trouble", True)), k=10**6)
    return {"state": state, "order": [{"card_id": c["id"], "upgrade": c.get("upgrade")} for c in order]}


def op_crash(_args):  # 测试用
    sys.exit(3)


OPS = {
    "hello": op_hello,
    "route": op_route,
    "pack": op_pack,
    "values": op_values,
    "removal": op_removal,
    "crash": op_crash,
}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            fn = OPS[req["op"]]
            with contextlib.redirect_stdout(sys.stderr):
                res = fn(req.get("args") or {})
            reply = {"id": rid, "ok": True, "result": res}
        except SystemExit:
            raise
        except BaseException as e:  # noqa: BLE001  任何错误都回给调用方，worker 继续活着
            traceback.print_exc(file=sys.stderr)
            reply = {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}
        _OUT.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
        _OUT.flush()


if __name__ == "__main__":
    main()
