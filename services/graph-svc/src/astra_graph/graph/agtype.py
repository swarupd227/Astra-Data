"""Encoding and decoding of Apache AGE's ``agtype``.

AGE returns values as text with a trailing type annotation: a vertex comes back as
``{"id": 8444…, "label": "Workbook", "properties": {…}}::vertex``. asyncpg has no codec
for ``agtype``, so the pool registers it as text and this module does the conversion.

Parameters travel the other way as a single JSON object, which the Cypher query
dereferences by name (``$p_luid``). Property *names* are never interpolated from client
input: they come from the ontology registry, so the query text is built from a closed set
and the values ride in the parameter map.
"""

from __future__ import annotations

import json
from typing import Any

_ANNOTATIONS = ("::vertex", "::edge", "::path", "::numeric")


def encode_params(params: dict[str, Any]) -> str:
    """Serialise a Cypher parameter map for AGE's third ``cypher()`` argument."""
    return json.dumps(params, separators=(",", ":"), ensure_ascii=False)


def decode(value: str | None) -> Any:
    """Decode one agtype value returned by AGE."""
    if value is None:
        return None
    text = value.strip()
    for annotation in _ANNOTATIONS:
        if text.endswith(annotation):
            text = text[: -len(annotation)]
            break
    if text == "":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A bare unquoted token (AGE renders `null` and some scalars this way).
        if text == "null":
            return None
        return text


def decode_vertex(value: str | None) -> dict[str, Any] | None:
    """Decode a vertex into ``{"label": ..., "properties": {...}}``."""
    decoded = decode(value)
    if decoded is None:
        return None
    if not isinstance(decoded, dict) or "label" not in decoded:
        raise ValueError(f"expected an AGE vertex, got {value!r}")
    return {"label": decoded["label"], "properties": decoded.get("properties") or {}}


def decode_edge(value: str | None) -> dict[str, Any] | None:
    """Decode an edge into ``{"label": ..., "properties": {...}}``."""
    decoded = decode(value)
    if decoded is None:
        return None
    if not isinstance(decoded, dict) or "label" not in decoded:
        raise ValueError(f"expected an AGE edge, got {value!r}")
    return {"label": decoded["label"], "properties": decoded.get("properties") or {}}


def property_map(properties: dict[str, Any], *, prefix: str = "p_") -> tuple[str, dict[str, Any]]:
    """Build a Cypher property-map literal and its parameter dictionary.

    Returns e.g. ``("{id: $p_id, name: $p_name}", {"p_id": ..., "p_name": ...})``.
    Keys are prefixed so a property called ``id`` cannot collide with a query-level
    parameter of the same name.
    """
    if not properties:
        return "{}", {}
    params = {f"{prefix}{name}": value for name, value in properties.items()}
    pairs = ", ".join(f"{name}: ${prefix}{name}" for name in properties)
    return "{" + pairs + "}", params
