"""用真实系统去校验场景真值（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-scenarios-design.md §5）。

适配器：真实系统这一屏的读数（hiro.vision.extract.extract 的原始字段）→ 场景信息项的形状。系统根本不产出的项就不给，打分时算「没读」。
打分：每个真值项三种结果——对 / 错 / 没读；真值是 null（画面上没有）且系统也没给的不计；待定 / 忽略的项不计。
"""

from __future__ import annotations

import math

from tools.ocr_dataset import scenario

# ---------- 适配器 ----------


def _strip_plus(name):
    return name.rstrip("+＋") if isinstance(name, str) else name


# 主数据 ExamSetting：examStaminaConsumptionDownPermil 500、examStaminaConsumptionAddPermil 1000
_STAMINA_DOWN, _STAMINA_ADD = 500, 1000


def _shown_cost(base: int, statuses: list[dict]) -> int:
    """卡上显示的体力消耗 = 引擎 calculate_damage 的换算（gakumas_exam_rs/src/exam.rs）：×(1-減少)、×(1+増加)、向上取整、再减「消費体力削減」。

    系统记卡面原始消耗、另外读状态交给引擎换算；画面显示换算后的数，所以打分时按同一规则换算后再比。"""
    names = {s.get("name") for s in statuses}
    x = float(base)
    if "消費体力減少" in names:
        x *= (1000 - _STAMINA_DOWN) / 1000
    if "消費体力増加" in names:
        x *= (1000 + _STAMINA_ADD) / 1000
    v = math.ceil(x - 1e-9)
    fix = sum(s.get("value") or 0 for s in statuses if s.get("name") == "消費体力削減")
    return max(0, v - fix) if fix >= 1 else v


def _status(s: dict) -> dict:
    """系统的状态槽 → 场景项：enchant（卡牌 / 道具持续效果）= card_effect，名字不计；单位是「%」的数记在 pct，同一格上面一行的回合（turns）记在 value。"""
    pct = s.get("unit") == "%"
    return {"kind": "card_effect" if s.get("kind") == "enchant" else "status", "name": None if s.get("kind") == "enchant" else s.get("name"),
            "value": s.get("turns") if pct else s.get("value"), "pct": s.get("value") if pct else None}  # fmt: skip


def _exam(f: dict) -> dict:
    scores = list(f.get("npc_scores") or [])
    if f.get("my_score") is not None and f.get("my_rank_index"):  # 选拔和本战的顶部排名条都包含自己的分数
        scores.insert(max(0, int(f["my_rank_index"]) - 1), f["my_score"])
    hand = []
    for h in sorted(f.get("hand") or [], key=lambda h: h.get("slot", 0)):
        sts = f.get("statuses") or []
        if h.get("genki_cost") is not None:
            ct, c = "green_heart", _shown_cost(h["genki_cost"], sts)
        elif h.get("hp_cost") is not None:
            ct, c = "red_heart", _shown_cost(h["hp_cost"], sts)
        elif h.get("extra_cost") is not None:
            ct, c = "buff", h["extra_cost"]
        else:
            ct, c = "none", None
        hand.append(
            {
                "name": _strip_plus(h.get("name")),
                "plus": bool(h.get("plus") or h.get("upgrade")),
                "cost_type": ct,
                "cost": c,
            }
        )
    preview = next((h["badge_score"] for h in f.get("hand") or [] if h.get("badge_score") is not None), None)
    return {
        "turns_left": f.get("remaining_turns"), "exam_attr": f.get("attr"), "multiplier_pct": f.get("multiplier_pct"),
        "top_scores": scores, "breakthrough_rank": f.get("rank_line"), "breakthrough_total": f.get("participants"),
        "hp": f.get("hp"), "genki": f.get("genki"), "hand": hand, "selected_preview": preview,
        "statuses": [_status(s) for s in sorted(f.get("statuses") or [], key=lambda s: s.get("slot", 0))],
        "deck_left": f.get("deck_count"),
        "drinks_count": sum(1 for s in f.get("drink_slots") or [] if s.get("state") == "drink") if f.get("drink_slots") is not None else None,
    }  # fmt: skip


def _hud(f: dict) -> dict:
    return {
        "hp": f.get("stamina"),
        "hp_max": f.get("max_stamina"),
        "p_points": f.get("produce_point", f.get("p_points")),
    }


def _consult(f: dict) -> dict:
    sv = f.get("services") or {}
    return {**_hud(f),
            "shelf": [{"kind": s.get("kind"), "price": s.get("price"), "purchased": s.get("purchased")} for s in sorted(f.get("shelf") or [], key=lambda s: s.get("index", 0))],
            "upgrade_price": (sv.get("upgrade") or {}).get("price"), "upgrade_used": (sv.get("upgrade") or {}).get("used"),
            "delete_price": (sv.get("delete") or {}).get("price"), "delete_used": (sv.get("delete") or {}).get("used")}  # fmt: skip


def _interval(f: dict) -> dict:
    iv = f.get("interval") or {}
    items = (
        [("card", x) for x in iv.get("cards") or []]
        + [("card", x) for x in iv.get("changes") or []]
        + [("drink", x) for x in iv.get("drinks") or []]
    )
    return {**_hud(f),
            "shelf": [{"kind": k, "price": x.get("price"), "purchased": x.get("purchased")} for k, x in sorted(items, key=lambda kx: kx[1].get("index", 0))],
            "upgrade_price": (iv.get("upgrade") or {}).get("price"), "upgrade_left": (iv.get("upgrade") or {}).get("left"),
            "customize_left": (iv.get("customize") or {}).get("left"), "refresh_price": (iv.get("refresh") or {}).get("price")}  # fmt: skip


def _action(f: dict) -> dict:
    tri = lambda d: [d.get(k) for k in ("vo", "da", "vi")] if isinstance(d, dict) else None  # noqa: E731
    growth = tri(f.get("growth_pct"))
    acts = []
    for o in f.get("options") or []:
        ch = (
            o.get("hp_gain")
            if o.get("hp_gain") is not None
            else (-o["hp_cost"] if o.get("hp_cost") is not None else None)
        )
        acts.append({"label": o.get("label"), "hp_change": ch})
    return {"exam_name": f.get("exam"), "days_left": f.get("days_left"), **_hud(f),
            "params": tri(f.get("params")), "param_caps": tri(f.get("param_caps")),
            "growth_pct": [f"{v:g}%" if isinstance(v, (int, float)) else v for v in growth] if growth else None,
            "star": f.get("star"), "actions": acts, "drinks_count": len(f["drinks"]) if f.get("drinks") is not None else None}  # fmt: skip


ADAPTERS = {"exam": _exam, "consult": _consult, "round_interval": _interval, "action_select": _action}


def adapt(sid: str, fields: dict | None) -> dict:
    return ADAPTERS[sid](fields or {})


# ---------- 打分 ----------


def _keyed_lists(sid: str) -> set[str]:
    """元素带 label 的列表按 label 对齐（行动的顺序各家写法不一，按名字配对）。"""
    return {it["key"] for it in scenario.load()[sid]["items"] if any(f["key"] == "label" for f in it.get("fields", []))}


def _flat(sid: str, d: dict, alias: dict | None = None) -> dict:
    """叶子路径 → 值。按 label 对齐的列表路径是 actions[休む].hp_change；alias 给了就顺便记下每条路径对应的下标写法
    （actions[0].hp_change）：标注端的 item_status 用的是下标路径。"""
    keyed = _keyed_lists(sid)
    out = {}

    def walk(v, p, q):
        if isinstance(v, dict) and v:
            for k, x in v.items():
                walk(x, f"{p}.{k}" if p else k, f"{q}.{k}" if q else k)
        elif isinstance(v, list) and v:
            for i, x in enumerate(v):
                if p in keyed:
                    lab = (x or {}).get("label") if isinstance(x, dict) else None
                    walk(x, f"{p}[{lab}]", f"{q}[{i}]")
                else:
                    walk(x, f"{p}[{i}]", f"{q}[{i}]")
        elif p:
            out[p] = v
            if alias is not None:
                alias[p] = q

    walk(d, "", "")
    return out


SKIP = ("disputed", "ignored")


def _field_flags(sid: str) -> dict[str, set[str]]:
    """场景定义里标了 prefix_ok（画面会截断的名字）/ derivable（画面可能被遮住、系统可从主数据补）的叶子路径（下标换成 []）。"""
    out = {"prefix_ok": set(), "derivable": set(), "unscored": set()}
    for it in scenario.load()[sid]["items"]:
        for f in it.get("fields", []):
            for flag in out:
                if f.get(flag):
                    out[flag].add(f"{it['key']}[].{f['key']}")
    return out


def _canon(sid: str, d: dict) -> dict:
    """打分前两边都过一遍的等价规则：考试手牌消耗为 0 和「没有消耗」对决策是一回事（系统把 0 消耗记成没有消耗）。"""
    if sid == "exam" and "extra_turns" in d:  # 剩余回合 = 圆环大数字 + 下面的「+N」额外回合（系统读的就是合计）
        extra = d.get("extra_turns") or 0
        d = {k: v for k, v in d.items() if k != "extra_turns"}
        if d.get("turns_left") is not None:
            d["turns_left"] = d["turns_left"] + extra
    if sid == "exam" and isinstance(d.get("hand"), list):
        d = {
            **d,
            "hand": [
                ({**h, "cost": None, "cost_type": "none"} if isinstance(h, dict) and h.get("cost") == 0 else h)
                for h in d["hand"]
            ],
        }
    return d


def score_frame(sid: str, truth: dict, got: dict, item_status: dict | None = None) -> list[dict]:
    st = item_status or {}
    alias: dict[str, str] = {}
    ft = _flat(sid, _canon(sid, truth), alias)
    fg = _flat(sid, _canon(sid, scenario.normalize(sid, got or {})))
    flags = _field_flags(sid)
    # 打分时 turns_left = 圆环大数字 + 额外回合：额外回合待定 / 忽略，合出来的值就不可信，不计
    sum_pending = sid == "exam" and "extra_turns" in truth and st.get("extra_turns") in SKIP
    rows = []
    for p, w in ft.items():
        if st.get(p) in SKIP or st.get(alias.get(p)) in SKIP or (sum_pending and p == "turns_left"):
            continue
        g = fg.get(p)
        leaf = _norm_item(p)
        if leaf in flags["unscored"]:
            continue  # 用户定的「不识别也没事」：真值留着，不计分
        if (w is None or w == "none") and leaf in flags["derivable"]:
            continue  # 画面上被遮住 / 看不到，系统按主数据补出来的不算错
        if w is None:
            if g is None:
                continue
            res = "错"
        elif g is None:
            res = "没读"
        elif g == w:
            res = "对"
        elif (
            leaf in flags["prefix_ok"] and isinstance(w, str) and isinstance(g, str) and len(w) >= 2 and g.startswith(w)
        ):
            res = "对"  # 卡名在画面上被截断（手牌多时只显示前几个字），系统给的全名以画面上的部分开头就算对
        else:
            res = "错"
        rows.append({"path": p, "want": w, "got": g, "result": res})
    return rows


# ---------- 按钮 ----------


def score_buttons(buttons: list[dict], texts: list[tuple]) -> list[dict]:
    """系统找按钮的通常做法：整帧文字里找包含按钮文字的那段，点它的中心。中心落在真值框里 → 对；找到但不在框里 → 错；找不到 → 没读。
    真值没有位置（box 为 null）的按钮不计。"""
    from tools.ocr_dataset.lexicon import norm

    rows = []
    for b in buttons or []:
        if not b.get("box"):
            continue
        x1, y1, x2, y2 = b["box"]
        # 包含按钮文字的段里取最贴近的（最短）：「削除」不能落到说明句「…強化や削除ができます」上
        cands = [(t, box) for t, box in texts if norm(b["label"]) in norm(t)]
        hit = min(cands, key=lambda tb: len(norm(tb[0]))) if cands else None
        if hit is None:
            res, got = "没读", None
        else:
            bx = hit[1]
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            res, got = ("对" if x1 <= cx <= x2 and y1 <= cy <= y2 else "错"), hit[0]
        rows.append({"path": f"button:{b['label']}", "want": b["label"], "got": got, "result": res})
    return rows


# ---------- 整批 ----------


def _importance(sid: str, path: str) -> str:
    if path.startswith("button:"):
        return "高"
    key = path.split("[")[0].split(".")[0]
    return next((it.get("importance", "?") for it in scenario.load()[sid]["items"] if it["key"] == key), "?")


def _norm_item(path: str) -> str:
    import re

    return re.sub(r"\[[^\]]*\]", "[]", path)


class _Acc(dict):
    def add(self, res: str):
        self["n"] = self.get("n", 0) + 1
        self[res] = self.get(res, 0) + 1


def evaluate(frames: list[dict], reads: dict[str, dict], backend: str = "?", seconds: dict | None = None) -> dict:
    """reads：{帧 id: {"page": 系统判的页, "fields": extract 原始字段, "texts": [(文字, 框)]}}。"""
    total, page = _Acc(), _Acc()
    groups: dict[str, dict[str, _Acc]] = {"scenario": {}, "item": {}, "importance": {}, "platform": {}}
    errors = []
    for fr in frames:
        r = reads.get(fr["id"])
        if r is None or fr.get("excluded"):  # excluded：整帧不计分（写明原因，如弹窗盖住背景、截图其实不是这个场景）
            continue
        sid = fr["scenario"]
        page.add("对" if r.get("page") in scenario.load()[sid].get("pages", []) else "错")
        rows = score_frame(sid, fr.get("truth") or {}, adapt(sid, r.get("fields")), fr.get("item_status"))
        rows += score_buttons(fr.get("buttons"), r.get("texts") or [])
        for x in rows:
            total.add(x["result"])
            for g, k in (("scenario", sid), ("item", f"{sid} {_norm_item(x['path'])}"), ("importance", _importance(sid, x["path"])),
                         ("platform", (fr.get("platform") or {}).get("kind") or "?")):  # fmt: skip
                groups[g].setdefault(k, _Acc()).add(x["result"])
            if x["result"] != "对":
                errors.append(
                    {
                        "frame": fr["id"],
                        "scenario": sid,
                        "system_page": r.get("page"),
                        "importance": _importance(sid, x["path"]),
                        **x,
                    }
                )
    errors.sort(
        key=lambda e: ({"高": 0, "中": 1, "低": 2}.get(e["importance"], 3), e["scenario"], e["path"], e["frame"])
    )
    import numpy as np

    times = [seconds[f["id"]] for f in frames if seconds and f["id"] in seconds]
    warm = times[1:] if len(times) > 1 else times
    return {"backend": backend, "frames": sum(1 for f in frames if f["id"] in reads and not f.get("excluded")), "groups": {"all": dict(total), "page": dict(page),
            **{g: {k: dict(v) for k, v in sorted(d.items())} for g, d in groups.items()}}, "errors": errors,
            "timing": {"p50": float(np.percentile(warm, 50)) if warm else None, "p90": float(np.percentile(warm, 90)) if warm else None}}  # fmt: skip


def _rate(d: dict | None) -> str:
    if not d or not d.get("n"):
        return "—"
    return (
        f"{d.get('对', 0) / d['n'] * 100:.1f}%（对 {d.get('对', 0)} / 错 {d.get('错', 0)} / 没读 {d.get('没读', 0)}）"
    )


def _sec(v) -> str:
    return "—" if v is None else f"{v:.2f}s"


def report_md(results: list[dict], title: str = "场景校验") -> str:
    names = [r["backend"] for r in results]
    head, sep = "| " + " | ".join(names) + " |", "|---" * len(names) + "|"
    r0 = results[0]
    lines = [f"# {title}", "", f"{r0['frames']} 帧，计分 {r0['groups']['all'].get('n', 0)} 项（信息项 + 按钮）。真值照画面标（两个 LLM 一致或人定），"
             "系统原样跑（自己判页、自己读）。对 = 读对；错 = 读错；没读 = 系统没产出这一项（覆盖缺口）。", ""]  # fmt: skip

    def table(title, group, label, keys=None):
        keys = keys if keys is not None else sorted({k for r in results for k in r["groups"][group]})
        if keys:
            lines.extend(["", f"## {title}", "", f"| {label} " + head, "|---" + sep])
            lines.extend(
                f"| {k} | " + " | ".join(_rate(r["groups"][group].get(k)) for r in results) + " |" for k in keys
            )

    lines.extend(["## 总表", "", "| 后端 | 全部 | 判页 | 高重要度 | P50 | P90 |", "|---|---|---|---|---|---|"])
    for r in results:
        t = r["timing"]
        lines.append(f"| {r['backend']} | {_rate(r['groups']['all'])} | {_rate(r['groups']['page'])} | {_rate(r['groups']['importance'].get('高'))}"
                     f" | {_sec(t['p50'])} | {_sec(t['p90'])} |")  # fmt: skip
    table("按场景", "scenario", "场景")
    table("按重要度", "importance", "重要度")
    unread = sorted({k for r in results for k, v in r["groups"]["item"].items() if v.get("没读")})
    table("系统没读的信息项（覆盖缺口）", "item", "信息项", keys=unread)
    bad = sorted({k for r in results for k, v in r["groups"]["item"].items() if v.get("错")})
    table("有读错的信息项", "item", "信息项", keys=bad)
    for r in results:
        if r["errors"]:
            lines.extend(
                [
                    "",
                    f"## 错例：{r['backend']}（{len(r['errors'])} 个，高重要度在前）",
                    "",
                    "| 帧 | 场景 | 系统判页 | 项 | 结果 | 真值 | 系统读到 |",
                    "|---|---|---|---|---|---|---|",
                ]
            )
            for e in r["errors"][:120]:
                lines.append(
                    f"| {e['frame']} | {e['scenario']} | {e['system_page']} | {e['path']} | {e['result']} | {e['want']} | {e['got']} |"
                )
    return "\n".join(lines) + "\n"


def read_system(img_path, backend: str) -> dict:
    """真实系统原样读一帧：切 OCR 后端 → 判页（快速判页 + 完整判页链）→ 按判出的页 extract → 整帧文字（找按钮用）。"""
    from hiro.vision import hif, ocr
    from hiro.vision.classify import classify
    from hiro.vision.extract import extract
    from hiro.vision.offline import load

    import cv2

    if backend not in ocr.ENGINES or backend == "auto":
        raise ValueError(f"后端 {backend!r} 不认识")
    ocr._engine = backend
    hif.EXAM_CACHE.clear()
    img = load(cv2.imread(str(img_path)))
    page = classify(img).name
    try:
        fields = extract(page, img)[0] if page and page != "unknown" else None
    except Exception:  # noqa: BLE001  这一页没有提取器 / 提取崩了：当作没读
        fields = None
    texts = [(t.text, tuple(t.rect)) for t in ocr.screen_texts(img)]
    return {"page": page, "fields": fields, "texts": texts}
