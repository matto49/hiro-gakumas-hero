"""契约基类：dataclass ↔ JSON、按类型注解生成 JSON schema、校验。

所有模块间交换的数据都是这里的 Contract 子类。规则：
- 字段只增不改名；每个对象带 version（契约版本），变更记入 docs/contracts.md；
- to_dict/to_json 输出全部字段，None 原样保留（None = 没读到，不是 0）；
- from_dict 遇到未知字段报错（防止改名后静默丢数据），缺失字段用默认值；
- validate() 按自动生成的 schema 校验（不依赖 jsonschema 包，支持的子集见 check()）。
"""

import dataclasses
import json
import types
import typing
from enum import Enum
from typing import Any, ClassVar, get_args, get_origin, get_type_hints

VERSION = "1.3"  # 契约版本：破坏性变更才升主版本；加字段升次版本


class SchemaError(ValueError):
    pass


# ---------------------------------------------------------------- schema 生成
_PRIMITIVE = {int: "integer", float: "number", str: "string", bool: "boolean", type(None): "null"}


def _type_schema(tp) -> dict:
    if tp is Any or tp is object:
        return {}
    if tp in _PRIMITIVE:
        return {"type": _PRIMITIVE[tp]}
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = get_args(tp)
        non_null = [a for a in args if a is not type(None)]
        nullable = len(non_null) < len(args)
        if len(non_null) == 1:
            s = dict(_type_schema(non_null[0]))
            if nullable:
                t = s.get("type")
                if t is None:
                    return {"anyOf": [s, {"type": "null"}]}
                s["type"] = (t if isinstance(t, list) else [t]) + ["null"]
            return s
        return {"anyOf": [_type_schema(a) for a in args]}
    if origin in (list, tuple):
        args = get_args(tp)
        item = _type_schema(args[0]) if args and args[0] is not Ellipsis else {}
        return {"type": "array", "items": item}
    if origin is dict:
        args = get_args(tp)
        return {"type": "object", "additionalProperties": _type_schema(args[1]) if len(args) == 2 else {}}
    if isinstance(tp, type) and issubclass(tp, Enum):
        return {"type": "string", "enum": [e.value for e in tp]}
    if dataclasses.is_dataclass(tp):
        return {k: v for k, v in schema_for(tp).items() if k != "$schema"}
    if tp in (dict,):
        return {"type": "object"}
    if tp in (list, tuple):
        return {"type": "array"}
    raise TypeError(f"不支持的注解类型：{tp!r}")


def schema_for(cls) -> dict:
    """按 dataclass 的类型注解生成 JSON schema（draft 2020-12 子集）。所有字段都 required，不允许未知字段。"""
    hints = get_type_hints(cls)
    props = {}
    for f in dataclasses.fields(cls):
        s = _type_schema(hints[f.name])
        if f.metadata.get("doc"):
            s = {**s, "description": f.metadata["doc"]}
        if f.metadata.get("enum"):
            s = {**s, "enum": list(f.metadata["enum"])}
        props[f.name] = s
    out = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": cls.__name__,
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }
    if cls.__doc__:
        out["description"] = cls.__doc__.strip().splitlines()[0]
    return out


# ---------------------------------------------------------------- 校验
_JSON_TYPES = {
    "null": lambda v: v is None,
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def check(value, schema: dict, path: str = "$") -> list[str]:
    """返回不符合之处（空列表 = 通过）。支持 type / enum / properties / required / additionalProperties / items / anyOf。"""
    errs: list[str] = []
    if "anyOf" in schema:
        if not any(not check(value, s, path) for s in schema["anyOf"]):
            errs.append(f"{path}: 不匹配 anyOf 的任何分支")
        return errs
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_JSON_TYPES[x](value) for x in ts):
            return [f"{path}: 期望 {'/'.join(ts)}，得到 {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} 不在 {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: 缺少字段 {k}")
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errs += check(v, props[k], f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: 未知字段 {k}")
            elif isinstance(extra, dict):
                errs += check(v, extra, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += check(v, schema["items"], f"{path}[{i}]")
    return errs


# ---------------------------------------------------------------- 基类
def _to_jsonable(v):
    if isinstance(v, Enum):
        return v.value
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _to_jsonable(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, dict):
        return {str(k): _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, float) and v != v:  # NaN 不是合法 JSON
        return None
    return v


def _from_jsonable(tp, v, path: str):
    if v is None:
        return None
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return _from_jsonable(args[0], v, path)
        for a in args:  # 多分支：挑第一个能构造的
            try:
                return _from_jsonable(a, v, path)
            except (SchemaError, TypeError, ValueError):
                continue
        raise SchemaError(f"{path}: 无法按 {tp} 解析 {v!r}")
    if origin in (list, tuple):
        args = get_args(tp)
        item = args[0] if args else Any
        return [_from_jsonable(item, x, f"{path}[{i}]") for i, x in enumerate(v)]
    if origin is dict:
        args = get_args(tp)
        val_tp = args[1] if len(args) == 2 else Any
        return {k: _from_jsonable(val_tp, x, f"{path}.{k}") for k, x in v.items()}
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(v)
    if dataclasses.is_dataclass(tp):
        return build(tp, v, path)
    return v


def build(cls, d: dict, path: str = "$"):
    """dict → dataclass（递归处理嵌套 dataclass / list / dict / Enum）。未知字段报错，缺失字段用默认值。"""
    if not isinstance(d, dict):
        raise SchemaError(f"{path}: 期望对象，得到 {type(d).__name__}")
    hints = get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(d) - names
    if unknown:
        raise SchemaError(
            f"{path}: {cls.__name__} 未知字段 {sorted(unknown)}（契约字段只增不改名，改名须同步 observe.py 与测试）"
        )
    kwargs = {k: _from_jsonable(hints[k], v, f"{path}.{k}") for k, v in d.items()}
    return cls(**kwargs)


class Contract:
    """所有契约对象的公共方法。子类必须是 dataclass，并带 version 字段。"""

    _schema_cache: ClassVar[dict] = {}

    @classmethod
    def schema(cls) -> dict:
        if cls not in Contract._schema_cache:
            Contract._schema_cache[cls] = schema_for(cls)
        return Contract._schema_cache[cls]

    def to_dict(self) -> dict:
        return _to_jsonable(self)

    def to_json(self, **kw) -> str:
        kw.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_dict(cls, d: dict):
        return build(cls, d)

    @classmethod
    def from_json(cls, s: str | bytes):
        return cls.from_dict(json.loads(s))

    def problems(self) -> list[str]:
        return check(self.to_dict(), self.schema())

    def validate(self) -> "Contract":
        errs = self.problems()
        if errs:
            raise SchemaError(f"{type(self).__name__} 不符合契约：" + "；".join(errs[:10]))
        return self
