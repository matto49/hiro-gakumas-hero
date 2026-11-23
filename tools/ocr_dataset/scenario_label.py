"""按场景标真值：两个不同来源的 LLM 各自看整帧、按场景信息项清单读，逐项比对（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-scenarios-design.md §3）。

提示词里只有场景定义，不给识别系统的读数（真值照画面来，不被系统带偏）。
"""

from __future__ import annotations

import cv2

from tools.ocr_dataset import scenario


class LabelError(RuntimeError):
    pass


def ask(llm, img, sid: str) -> dict:
    """一个 LLM 读一帧 → {"items": 归一后的信息项, "buttons": [按钮文字]}。"""
    system, user = scenario.prompt(sid)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    res = llm.ask_json_image(system, user, buf.tobytes())
    data = res.data if not res.error else None
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise LabelError(f"{getattr(llm, 'name', '?')} 没给出 items：{res.error or (res.raw or '')[:200]}")
    buttons = [scenario._text(b) for b in data.get("buttons") or [] if isinstance(b, (str, int))]
    return {"items": scenario.normalize(sid, data["items"]), "buttons": [b for b in buttons if b]}


def label_frame(img, sid: str, readers: dict) -> dict:
    """readers：{名字: LLM 适配器}，取前两个比对。→ {truth, item_status, answers, button_labels}。"""
    names = list(readers)
    answers = {n: ask(readers[n], img, sid) for n in names}
    a, b = answers[names[0]], answers[names[1]]
    truth, status = scenario.merge(a["items"], b["items"])
    labels = [x for x in a["buttons"] if x in b["buttons"]]
    return {"truth": truth, "item_status": status, "answers": answers, "button_labels": labels}
