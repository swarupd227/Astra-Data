"""Property type system for the Estate Graph ontology.

The ontology declares typed properties (spec §4.1.1, §4.1.2). This module owns the
value-level rules: what a declared type accepts, how a submitted value is coerced to its
canonical storage form, and the message produced when it is rejected.

Every rejection names the property. That is an acceptance criterion of S1.1.1, not a
convenience, so the failure message is constructed here rather than at the call site.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

# Crockford base32, 26 characters. ULIDs are the platform's node identifier (spec §3.1).
_ULID_RE = re.compile(r"^[0-7][0123456789ABCDEFGHJKMNPQRSTVWXYZ]{25}$")

# Tableau LUIDs are UUIDs, but other sources may use opaque identifiers. Accept any
# non-empty printable token and let the adapter decide its own format.
_LUID_MAX_LEN = 256

_STRING_MAX_LEN = 4096
_TEXT_MAX_LEN = 1_048_576


class PropertyType(str, Enum):
    """The declared type of an ontology property."""

    STRING = "string"
    """Short free text: names, identifiers, references."""

    TEXT = "text"
    """Long free text retained verbatim: formulae, custom SQL, rationale."""

    INT = "int"
    FLOAT = "float"
    BOOL = "bool"

    TIMESTAMP = "timestamp"
    """RFC 3339 instant. Stored normalised to UTC with a 'Z' offset."""

    DATE = "date"
    """ISO 8601 calendar date."""

    ULID = "ulid"
    """A platform-issued identifier."""

    LUID = "luid"
    """A source-system identifier, opaque to the platform."""

    ENUM = "enum"
    """One of a closed set declared on the PropertySpec."""

    STRING_LIST = "string_list"
    """Ordered list of short strings. Spec renders these as `name[]`."""

    JSON = "json"
    """Structured value the platform stores but does not interpret at write time."""


class Cardinality(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class PropertySpec:
    """One declared property on a node or edge type."""

    name: str
    type: PropertyType
    cardinality: Cardinality = Cardinality.OPTIONAL
    enum: tuple[str, ...] | None = None
    server_managed: bool = False
    """Set by the service, never accepted from a client."""

    note: str = ""
    """Why this property exists or how it is populated. Rendered into the generated
    ontology reference so the spec table and the code carry the same explanation."""

    def __post_init__(self) -> None:
        if self.type is PropertyType.ENUM and not self.enum:
            raise ValueError(f"property {self.name!r} is an enum but declares no values")
        if self.type is not PropertyType.ENUM and self.enum:
            raise ValueError(f"property {self.name!r} declares enum values but is {self.type.value}")

    @property
    def required(self) -> bool:
        return self.cardinality is Cardinality.REQUIRED

    def render_type(self) -> str:
        """Human-readable type, used by the generated ontology reference."""
        if self.type is PropertyType.ENUM:
            assert self.enum is not None
            return "enum(" + "|".join(self.enum) + ")"
        if self.type is PropertyType.STRING_LIST:
            return "string[]"
        return str(self.type.value)


class PropertyValueError(ValueError):
    """A submitted value does not satisfy its declared property type."""

    def __init__(self, property_name: str, message: str) -> None:
        self.property_name = property_name
        self.detail = message
        super().__init__(f"property '{property_name}' {message}")


def _reject(name: str, message: str) -> PropertyValueError:
    return PropertyValueError(name, message)


def _coerce_timestamp(name: str, value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith(("z", "Z")):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise _reject(name, f"is not an RFC 3339 timestamp: {value!r}") from exc
    else:
        raise _reject(name, f"expects an RFC 3339 timestamp, got {type(value).__name__}")

    if dt.tzinfo is None:
        # A naive instant is ambiguous. The platform records instants, so refuse rather
        # than guess a zone: an off-by-hours harvest timestamp is silently wrong data.
        raise _reject(name, f"is missing a UTC offset: {value!r}")
    return str(dt.astimezone(UTC).isoformat().replace("+00:00", "Z"))


def _coerce_date(name: str, value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as exc:
            raise _reject(name, f"is not an ISO 8601 date: {value!r}") from exc
    raise _reject(name, f"expects an ISO 8601 date, got {type(value).__name__}")


def _coerce_string(name: str, value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        raise _reject(name, f"expects a string, got {type(value).__name__}")
    if len(value) > max_len:
        raise _reject(name, f"exceeds the maximum length of {max_len} characters")
    return value


def _coerce_int(name: str, value: Any) -> int:
    # bool is a subclass of int; an accidental True in a count column is a defect.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject(name, f"expects an integer, got {type(value).__name__}")
    return int(value)


def _coerce_float(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _reject(name, f"expects a number, got {type(value).__name__}")
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise _reject(name, "expects a finite number")
    return result


def coerce(spec: PropertySpec, value: Any) -> Any:
    """Validate ``value`` against ``spec`` and return its canonical storage form.

    Raises PropertyValueError, whose message names the property.
    """
    if value is None:
        # Null is the absence of a value, not a value. Callers strip nulls before
        # validation; reaching here means an explicit null on a required property.
        raise _reject(spec.name, "must not be null")

    match spec.type:
        case PropertyType.STRING:
            return _coerce_string(spec.name, value, max_len=_STRING_MAX_LEN)
        case PropertyType.TEXT:
            return _coerce_string(spec.name, value, max_len=_TEXT_MAX_LEN)
        case PropertyType.INT:
            return _coerce_int(spec.name, value)
        case PropertyType.FLOAT:
            return _coerce_float(spec.name, value)
        case PropertyType.BOOL:
            if not isinstance(value, bool):
                raise _reject(spec.name, f"expects a boolean, got {type(value).__name__}")
            return value
        case PropertyType.TIMESTAMP:
            return _coerce_timestamp(spec.name, value)
        case PropertyType.DATE:
            return _coerce_date(spec.name, value)
        case PropertyType.ULID:
            text = _coerce_string(spec.name, value, max_len=26)
            if not _ULID_RE.match(text):
                raise _reject(spec.name, f"is not a ULID: {value!r}")
            return text
        case PropertyType.LUID:
            text = _coerce_string(spec.name, value, max_len=_LUID_MAX_LEN)
            if not text.strip():
                raise _reject(spec.name, "must not be blank")
            return text
        case PropertyType.ENUM:
            assert spec.enum is not None
            if not isinstance(value, str):
                raise _reject(spec.name, f"expects one of {'|'.join(spec.enum)}, got {type(value).__name__}")
            if value not in spec.enum:
                raise _reject(spec.name, f"must be one of {'|'.join(spec.enum)}, got {value!r}")
            return value
        case PropertyType.STRING_LIST:
            if not isinstance(value, list | tuple):
                raise _reject(spec.name, f"expects a list of strings, got {type(value).__name__}")
            out: list[str] = []
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise _reject(
                        spec.name,
                        f"expects a list of strings; element {index} is {type(item).__name__}",
                    )
                if len(item) > _STRING_MAX_LEN:
                    raise _reject(spec.name, f"element {index} exceeds {_STRING_MAX_LEN} characters")
                out.append(item)
            return out
        case PropertyType.JSON:
            if not isinstance(value, dict | list | str | int | float | bool):
                raise _reject(spec.name, f"is not JSON-serialisable: {type(value).__name__}")
            return value

    raise AssertionError(f"unhandled property type {spec.type!r}")  # pragma: no cover
