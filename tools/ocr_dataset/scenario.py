"""场景定义：加载、标注提示词、LLM 答案归一、两方比对（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-scenarios-design.md §2–§4）。

场景是靶子：每一屏玩家要知道什么（信息项）、能点什么（按钮），只看画面定、不看识别代码。定义在同目录的 scenarios.json。
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from functools import cache
from pathlib import Path

from tools.ocr_dataset import truth as truth_mod

TYPES = ("int", "text", "enum", "bool", "list")
DEF = Path(__file__).with_name("scenarios.json")


@cache
def load() -> dict[str, dict]:
    return {k: v for k, v in json.loads(DEF.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def for_page(page: str | None) -> str | None:
    """系统的页面名 → 场景 id（只用来给截图分组；真值照画面标）。"""
    return next((sid for sid, d in load().items() if page in d.get("pages", [])), None)


# ---------- 提示词 ----------

SYSTEM = (
    "你在给学園アイドルマスター（学マス）的游戏截图做标注。按给定的信息项清单，只写画面上实际看得到的内容：不推测、不补全、不按游戏规则推算；"
    "看不到或看不清的项写 null。数字只写数字（去掉逗号、%、单位）。文字照抄画面（日文原样）。列表按说明里的顺序。"
    '只回复一个 JSON 对象：{"items": {每个信息项的 key: 值}, "buttons": [画面上能看到的按钮文字]}。'
)


def _describe(it: dict, indent: str = "") -> str:
    t = it["type"]
    if t == "enum":
        t = "取值之一：" + " / ".join(it["values"])
    elif t == "list" and "fields" in it:
        t = "列表，每个元素是对象"
    elif t == "list":
        t = f"列表，元素是 {it.get('item', 'text')}"
    line = f"{indent}- {it['key']}（{t}）：{it['desc']}"
    if "fields" in it:
        line += "\n" + "\n".join(_describe(f, indent + "    ") for f in it["fields"])
    return line


def prompt(sid: str, keys: list[str] | None = None) -> tuple[str, str]:
    """keys：只问这几项（补问新加的项时用）。"""
    d = load()[sid]
    items = [it for it in d["items"] if keys is None or it["key"] in keys]
    user = (f"场景：{d['name']}\n信息项：\n" + "\n".join(_describe(it) for it in items)
            + f"\n按钮：列出画面上看得到的按钮文字，尤其注意这些：{' / '.join(d.get('buttons', [])) or '无'}")  # fmt: skip
    return SYSTEM, user


# ---------- 归一 ----------


def _int(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", unicodedata.normalize("NFKC", str(v)).replace(",", "").replace(" ", ""))
    return int(m.group()) if m else None


def _text(v):
    if v is None:
        return None
    return unicodedata.normalize("NFKC", str(v)).replace(" ", "").replace("　", "").strip() or None


def _bool(v):
    if isinstance(v, bool) or v is None:
        return v
    s = str(v).strip().lower()
    return (
        True
        if s in ("true", "yes", "1", "是", "有")
        else False
        if s in ("false", "no", "0", "否", "无", "没有")
        else None
    )


def _value(spec: dict, v):
    t = spec["type"]
    if t == "int":
        n = _int(v)
        return abs(n) if n is not None and spec.get("abs") else n
    if t == "text":
        return _text(v)
    if t == "bool":
        return _bool(v)
    if t == "enum":
        if v is None:
            return None
        s = str(v).strip().lower()
        return s if s in spec["values"] else "?" + str(v).strip()
    if t == "list":
        if v is None:
            return None
        items = v if isinstance(v, list) else [v]
        if "fields" in spec:
            return [
                {f["key"]: _value(f, x.get(f["key"])) for f in spec["fields"]} if isinstance(x, dict) else {}
                for x in items
            ]
        return [_value({"type": spec.get("item", "text")}, x) for x in items]
    return v


def normalize(sid: str, raw: dict) -> dict:
    """LLM 的 items → 按场景定义的类型归一；没定义的 key 丢掉，没给的项不补。"""
    out = {}
    for it in load()[sid]["items"]:
        if it["key"] in (raw or {}):
            out[it["key"]] = _value(it, raw[it["key"]])
    return out


# ---------- 两方比对 ----------


def _set(obj, path: str, value) -> None:
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = obj
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        nxt = parts[i + 1] if not last else None
        if p.startswith("["):
            k = int(p[1:-1])
            while len(cur) <= k:
                cur.append({} if nxt and not nxt.startswith("[") else [] if nxt else None)
            if last:
                cur[k] = value
            else:
                cur = cur[k]
        else:
            if last:
                cur[p] = value
            else:
                cur = cur.setdefault(p, [] if nxt.startswith("[") else {})


def spec_for(sid: str, path: str) -> dict | None:
    """信息项路径（hand[0].cost、hp、actions[休む].hp_change）→ 场景定义里这一项 / 子项的类型说明。"""
    parts = re.sub(r"\[[^\]]*\]", "", path).split(".")
    item = next((it for it in load()[sid]["items"] if it["key"] == parts[0]), None)
    if item is None or len(parts) == 1:
        if item is not None and item["type"] == "list" and "fields" not in item and "[" in path:
            return {"type": item.get("item", "text")}
        return item
    return next((f for f in item.get("fields", []) if f["key"] == parts[1]), None)


def _blocked(obj, path: str) -> bool:
    """往 obj 的 path 写一个叶子会不会撞上形状冲突：路径中间碰到 None / 标量 / 类型不对的容器，或者要写的位置上已经是容器。"""
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = obj
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        if p.startswith("["):
            if not isinstance(cur, list):
                return True
            k = int(p[1:-1])
            if k >= len(cur):
                return False  # 后面补新元素，不会冲突
            if last:
                return isinstance(cur[k], (dict, list))
            cur = cur[k]
        else:
            if not isinstance(cur, dict):
                return True
            if p not in cur:
                return False
            if last:
                return isinstance(cur[p], (dict, list))
            cur = cur[p]
    return False


def _bad_enum(sid: str | None, path: str, value) -> bool:
    """normalize 把不在取值表里的枚举写成「?原文」；只有场景定义里这一项是 enum 才算（文字项本身可以以 ? 开头）。"""
    if sid is None or not isinstance(value, str) or not value.startswith("?"):
        return False
    spec = spec_for(sid, path)
    return bool(spec and spec.get("type") == "enum")


def merge(a: dict, b: dict, sid: str | None = None) -> tuple[dict, dict]:
    """两方答案 → (真值, 待定项)。逐个叶子比：一致 → 定下；不一致或只有一方有 → disputed（真值先填第一方的读法）。
    形状冲突（一方是 null / 另一方是列表等）保留第一方的值、记 disputed，不往 None 里写子路径。
    给了 sid 时，两方都答了同一个非法枚举（「?原文」）也记 disputed：这个值不能当成已定的真值。"""
    fa, fb = truth_mod.flatten(a), truth_mod.flatten(b)
    out = copy.deepcopy(a)
    status = {}
    for p in list(fa) + [p for p in fb if p not in fa]:
        if p in fa and p in fb and fa[p] == fb[p] and not _bad_enum(sid, p, fa[p]):
            continue
        status[p] = "disputed"
        if p not in fa and not _blocked(out, p):
            _set(out, p, fb[p])
    return out, status
