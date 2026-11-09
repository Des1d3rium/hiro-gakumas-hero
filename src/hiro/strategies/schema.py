"""策略档案的 JSON Schema 校验（只实现档案用到的子集，不依赖 jsonschema 包）。

支持：type、enum、required、properties、additionalProperties（false 或子 schema）、patternProperties、items、
$ref（只认本文件内 #/$defs/…）、oneOf、minLength、minimum、maximum、pattern、minItems。
schema 文件在 hiro/strategies/schemas/，前端也按它们生成表单。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """name：memory-profile / idol-profile / buttons。"""
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _is(value, type_name: str) -> bool:
    if type_name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[type_name])


def errors(value, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return [f"{path}: 不支持的 $ref {ref}"]
        node = root
        for part in ref[2:].split("/"):
            node = node[part]
        return errors(value, node, root, path)
    out: list[str] = []
    if "oneOf" in schema:
        matched = [s for s in schema["oneOf"] if not errors(value, s, root, path)]
        if len(matched) != 1:
            out.append(f"{path}: 应符合其中一种写法（{len(matched)} 种符合）")
        return out
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_is(value, t) for t in types):
            return [f"{path}: 应为 {'/'.join(types)}，实际 {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} 不在 {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: 不能为空")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: {value!r} 不符合 {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {value} > {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: 至少 {schema['minItems']} 项")
        if "items" in schema:
            for i, item in enumerate(value):
                out += errors(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}: 缺少 {key}")
        props = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            sub = f"{path}.{key}"
            if key in props:
                out += errors(item, props[key], root, sub)
                continue
            pat = next((s for p, s in patterns.items() if re.search(p, key)), None)
            if pat is not None:
                out += errors(item, pat, root, sub)
            elif extra is False:
                out.append(f"{sub}: 不认识的项")
            elif isinstance(extra, dict):
                out += errors(item, extra, root, sub)
    return out


def check(value, name: str) -> list[str]:
    return errors(value, load(name))
