"""按场景标真值：两个不同来源的 LLM 各自看整帧、按场景信息项清单读，逐项比对（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-scenarios-design.md §3）。

提示词里只有场景定义，不给识别系统的读数（真值照画面来，不被系统带偏）。
"""

from __future__ import annotations

from tools.ocr_dataset import scenario

import cv2


class LabelError(RuntimeError):
    pass


def ask(llm, img, sid: str, keys: list[str] | None = None) -> dict:
    """一个 LLM 读一帧 → {"items": 归一后的信息项, "buttons": [按钮文字]}。keys：只问、只留这几项。"""
    system, user = scenario.prompt(sid, keys)
    ref = [
        it
        for it in scenario.load()[sid]["items"]
        if it.get("reference") == "status_sheet" and (keys is None or it["key"] in keys)
    ]
    if ref:  # 状态图标上没有字：拼上放大的状态栏和图标对照表
        from tools.ocr_dataset import status_sheet

        if keys is not None and set(keys) <= {it["key"] for it in ref}:
            img = status_sheet.compose(img, ref[0]["region"])  # 只问状态栏：只给拼图
        else:
            sheet_img = status_sheet.compose(img, ref[0]["region"])
            h = sheet_img.shape[0]
            full = cv2.resize(img, (round(img.shape[1] * h / img.shape[0]), h))
            img = cv2.hconcat([full, sheet_img])
        user = status_sheet.NOTE + "\n" + user
    _ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    res = llm.ask_json_image(system, user, buf.tobytes())
    data = res.data if not res.error else None
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise LabelError(f"{getattr(llm, 'name', '?')} 没给出 items：{res.error or (res.raw or '')[:200]}")
    buttons = [scenario._text(b) for b in data.get("buttons") or [] if isinstance(b, (str, int))]
    items = scenario.normalize(sid, data["items"])
    if keys is not None:
        items = {k: v for k, v in items.items() if k in keys}
    return {"items": items, "buttons": [b for b in buttons if b]}


def label_frame(img, sid: str, readers: dict, keys: list[str] | None = None) -> dict:
    """readers：{名字: LLM 适配器}，取前两个比对。→ {truth, item_status, answers, button_labels}。keys：只问这几项。"""
    names = list(readers)
    answers = {n: ask(readers[n], img, sid, keys) for n in names}
    a, b = answers[names[0]], answers[names[1]]
    truth, status = scenario.merge(a["items"], b["items"], sid=sid)
    labels = [x for x in a["buttons"] if x in b["buttons"]]
    return {"truth": truth, "item_status": status, "answers": answers, "button_labels": labels}


def backfill(dataset, set_: str, sid: str, keys: list[str], readers: dict, log=print) -> dict[str, int]:
    """场景定义新加了信息项时，对已有的帧只补问这几项：真值和各方答案里加上这几项，不一致的记 disputed；其余项（包括人定过的）不动。"""
    import json
    from pathlib import Path

    root = Path(dataset) / set_
    frames = disputed = 0
    dropped: list[str] = []
    unanswered: list[str] = []
    for js in sorted(root.glob("*.json")):
        fr = json.loads(js.read_text(encoding="utf-8"))
        if fr.get("schema") != 3 or fr.get("scenario") != sid:
            continue
        try:
            r = label_frame(cv2.imread(str(root / fr["image"])), sid, readers, keys)
        except LabelError as e:
            log(f"{fr['id']} 补问失败：{e}")
            continue
        # 只处理有人答了的项（答案里有这个键，哪怕是显式 null）；两个 LLM 都漏答的项原样保留（真值、状态、复核记录、各方答案）
        answered = [k for k in keys if any(k in a["items"] for a in r["answers"].values())]
        if len(answered) < len(keys):
            unanswered.append(fr["id"])
            log(f"{fr['id']} 补问漏答 {[k for k in keys if k not in answered]}：保留原值")
        if not answered:
            continue
        st = fr.setdefault("item_status", {})
        rv = fr.get("reviewed") or {}
        mine = lambda p, answered=answered: p.split("[")[0].split(".")[0] in answered  # noqa: E731
        if any(mine(p) for p in rv):
            dropped.append(fr["id"])  # 这几项整列重读，旧的人工答案位置可能对不上了：清掉并报告
        for p in [p for p in st if mine(p)]:
            del st[p]
        for p in [p for p in rv if mine(p)]:
            del rv[p]
        for k in answered:
            fr["truth"].pop(k, None)
        fr["truth"].update(r["truth"])
        for k in answered:  # 比对结果里没有这个键（只有一方答了空列表）：用答了的那一方的
            if k not in fr["truth"]:
                fr["truth"][k] = next(a["items"][k] for a in r["answers"].values() if k in a["items"])
        for p, s in r["item_status"].items():
            st[p] = s
            disputed += 1
        for n, a in r["answers"].items():
            fr.setdefault("answers", {}).setdefault(n, {"items": {}}).setdefault("items", {}).update(a["items"])
        js.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
        frames += 1
        log(f"{fr['id']}：补问 {keys} → {r['truth']}")
    return {"frames": frames, "disputed": disputed, "dropped_reviewed": dropped, "unanswered": unanswered}
