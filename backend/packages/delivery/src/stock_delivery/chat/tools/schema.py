"""轻量 JSON Schema 子集校验（P0 不引入 jsonschema 依赖）。

支持 type/properties/required/additionalProperties/enum/const/pattern/
minLength/maxLength/minimum/maximum/items/anyOf/oneOf。外部 Provider 的完整
JSON Schema 校验后续可替换为 jsonschema，接口保持不变。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate_args(schema: Mapping[str, Any], args: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _validate(schema, args, "$", errors)
    return errors


def _validate(schema: Mapping[str, Any], value: Any, path: str, errors: list[str]) -> None:
    if "$ref" in schema or "$dynamicRef" in schema:
        errors.append(f"{path}: 不支持远程/动态 $ref")
        return
    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, name) for name in expected_types):
            errors.append(f"{path}: 期望 {expected_types}，实际 {type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 必须等于 {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: 不在枚举 {enum!r} 中")
    if isinstance(value, str):
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{path}: 不匹配 pattern {pattern!r}")
            except re.error:
                errors.append(f"{path}: 非法 pattern {pattern!r}")
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: 长度小于 {schema['minLength']}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: 长度大于 {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: 小于 minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: 大于 maximum {schema['maximum']}")
    if isinstance(value, Mapping):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: 缺少必填字段")
        properties = schema.get("properties") or {}
        if isinstance(properties, Mapping):
            for key, item_schema in properties.items():
                if key in value and isinstance(item_schema, Mapping):
                    _validate(item_schema, value[key], f"{path}.{key}", errors)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: 不允许的字段")
        elif isinstance(additional, Mapping):
            for key, item in value.items():
                if key not in properties:
                    _validate(additional, item, f"{path}.{key}", errors)
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _validate(items, item, f"{path}[{index}]", errors)
    any_of = schema.get("anyOf")
    if (
        isinstance(any_of, list)
        and any_of
        and not any(_matches(item, value) for item in any_of if isinstance(item, Mapping))
    ):
        errors.append(f"{path}: 不满足 anyOf")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(1 for item in one_of if isinstance(item, Mapping) and _matches(item, value))
        if matches != 1:
            errors.append(f"{path}: oneOf 命中 {matches} 个分支")


def _matches(schema: Mapping[str, Any], value: Any) -> bool:
    local: list[str] = []
    _validate(schema, value, "$", local)
    return not local


def _is_type(value: Any, name: str) -> bool:
    expected = _JSON_TYPES.get(name)
    if expected is None:
        return True
    if name in {"integer", "number"} and isinstance(value, bool):
        return False
    if isinstance(expected, tuple):
        return isinstance(value, expected)
    return isinstance(value, expected)
