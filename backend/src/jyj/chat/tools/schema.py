"""Pydantic args models -> JSON Schema that strict structured output accepts.

Strict mode (OpenAI structured outputs, which ``codex exec --output-schema`` uses) wants
every object closed (``additionalProperties: false``) with every property listed in
``required``; optional values are expressed as a union with ``null``. It rejects or ignores
many validation keywords, so the exported schema only describes shape: bounds, lengths and
cross-field rules stay in the Pydantic model, which validates every call before it runs.
"""

from typing import Any

from pydantic import BaseModel

# Keywords kept in the exported schema; everything else (title, default, minLength,
# maximum, pattern, format, ...) is dropped.
ALLOWED_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "anyOf",
        "description",
    }
)
MAX_OBJECT_DEPTH = 5
_PRIMITIVE_TYPES = frozenset({"string", "number", "integer", "boolean", "null"})


class StrictSchemaError(ValueError):
    """The model cannot be expressed as (or the schema is not) a strict structured schema."""


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema(mode="validation")
    defs = raw.get("$defs", {})
    return _convert(raw, defs, path=model.__name__, seen=())


def _resolve(
    node: dict[str, Any], defs: dict[str, Any], seen: tuple[str, ...]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    ref = node["$ref"]
    name = ref.rsplit("/", 1)[-1]
    if name in seen:
        raise StrictSchemaError(f"recursive model {name} is not supported")
    if name not in defs:
        raise StrictSchemaError(f"unresolvable $ref {ref}")
    return defs[name], (*seen, name)


def _convert(
    node: dict[str, Any], defs: dict[str, Any], *, path: str, seen: tuple[str, ...]
) -> dict[str, Any]:
    description = node.get("description")
    if "$ref" in node:
        node, seen = _resolve(node, defs, seen)
        description = description or node.get("description")

    out: dict[str, Any]
    if "anyOf" in node or "oneOf" in node:
        options = node.get("anyOf") or node.get("oneOf")
        out = {
            "anyOf": [
                _convert(opt, defs, path=f"{path}|{i}", seen=seen) for i, opt in enumerate(options)
            ]
        }
    elif node.get("type") == "object" or "properties" in node:
        out = _convert_object(node, defs, path=path, seen=seen)
    elif node.get("type") == "array":
        if "items" not in node:
            raise StrictSchemaError(f"{path}: arrays need an items schema")
        out = {"type": "array", "items": _convert(node["items"], defs, path=f"{path}[]", seen=seen)}
    elif "enum" in node or "const" in node:
        values = node["enum"] if "enum" in node else [node["const"]]
        out = {"type": node.get("type") or _enum_type(values, path), "enum": list(values)}
    elif node.get("type") in _PRIMITIVE_TYPES:
        out = {"type": node["type"]}
    else:
        raise StrictSchemaError(f"{path}: unsupported schema {sorted(node)}")

    if description:
        out["description"] = description
    return out


def _convert_object(
    node: dict[str, Any], defs: dict[str, Any], *, path: str, seen: tuple[str, ...]
) -> dict[str, Any]:
    if node.get("additionalProperties") is not False:
        raise StrictSchemaError(f"{path}: models must use extra='forbid'")
    properties = node.get("properties", {})
    required = set(node.get("required", []))
    converted = {}
    for key, prop in properties.items():
        value = _convert(prop, defs, path=f"{path}.{key}", seen=seen)
        if key not in required and not _nullable(value):
            # Strict mode makes the model always send every field, so an optional field
            # must accept null or the model is forced to invent a value.
            raise StrictSchemaError(f"{path}.{key}: optional fields must allow None")
        converted[key] = value
    return {
        "type": "object",
        "properties": converted,
        "required": list(converted),
        "additionalProperties": False,
    }


def _nullable(schema: dict[str, Any]) -> bool:
    return schema.get("type") == "null" or any(
        opt.get("type") == "null" for opt in schema.get("anyOf", [])
    )


def _enum_type(values: list[Any], path: str) -> str:
    kinds = {type(v) for v in values}
    if kinds == {str}:
        return "string"
    if kinds <= {int} and bool not in kinds:
        return "integer"
    raise StrictSchemaError(f"{path}: enums must be all strings or all integers")


def check_strict(schema: dict[str, Any], *, path: str = "$") -> None:
    """Raise ``StrictSchemaError`` unless ``schema`` follows the strict-mode rules."""
    _check(schema, path=path, depth=0)


def _check(node: dict[str, Any], *, path: str, depth: int) -> None:
    extra = set(node) - ALLOWED_KEYWORDS
    if extra:
        raise StrictSchemaError(f"{path}: unsupported keywords {sorted(extra)}")
    if "anyOf" in node:
        if set(node) - {"anyOf", "description"}:
            raise StrictSchemaError(f"{path}: anyOf must stand alone")
        for i, opt in enumerate(node["anyOf"]):
            _check(opt, path=f"{path}.anyOf[{i}]", depth=depth)
        return
    kind = node.get("type")
    if kind == "object":
        if depth + 1 > MAX_OBJECT_DEPTH:
            raise StrictSchemaError(f"{path}: objects nested deeper than {MAX_OBJECT_DEPTH}")
        if node.get("additionalProperties") is not False:
            raise StrictSchemaError(f"{path}: additionalProperties must be false")
        props = node.get("properties")
        if not isinstance(props, dict):
            raise StrictSchemaError(f"{path}: objects need properties")
        if sorted(node.get("required", [])) != sorted(props):
            raise StrictSchemaError(f"{path}: every property must be required")
        for key, prop in props.items():
            _check(prop, path=f"{path}.{key}", depth=depth + 1)
    elif kind == "array":
        if "items" not in node:
            raise StrictSchemaError(f"{path}: arrays need items")
        _check(node["items"], path=f"{path}[]", depth=depth)
    elif kind in _PRIMITIVE_TYPES:
        if set(node) & {"properties", "required", "additionalProperties", "items"}:
            raise StrictSchemaError(f"{path}: object keywords on a {kind}")
    else:
        raise StrictSchemaError(f"{path}: missing or unknown type {kind!r}")
