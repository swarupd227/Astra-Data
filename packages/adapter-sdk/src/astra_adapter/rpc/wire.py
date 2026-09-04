"""Encoding the §6.1 contract for the wire.

**Why by hand and not by a serialisation library.** The wire format *is* the contract's
compatibility surface. A library that derives it from the dataclasses would make every
incidental change to a field a change to the protocol, and would silently accept a payload
from an adapter built against a different interface version. Written out, the encoding is
something you can read, version and refuse.

**Transport is REST over HTTP**, per §5.4 ("Interfaces: GraphQL for the graph and console;
REST for adapters, gates and evidence"). JSON bodies; bytes are base64, because JSON has no
bytes and an adapter's fetch result is bytes by §6.1.
"""

from __future__ import annotations

import base64
from typing import Any

from ..calc import CalcAST, CalcNode, NodeKind
from ..contract import (
    INTERFACE_VERSION,
    AdapterManifest,
    AssetRef,
    Capabilities,
    EdgeFragment,
    NodeFragment,
    OwnershipRecord,
    ParseResult,
    RawAsset,
    Scope,
    SiteRecord,
    Unrecognised,
    UsageKind,
    UsageRecord,
    ViewerRecord,
)
from ..proof import (
    Column,
    ColumnRole,
    ExecutionOutcome,
    ExecutionStrategy,
    ParityCase,
    ResultSet,
    VisualCapture,
    VisualCase,
)


class WireError(Exception):
    """A payload could not be read as the contract says it should be."""


class InterfaceMismatch(WireError):
    """The two ends were built against different versions of §6.1.

    Refused rather than negotiated. A platform that quietly accepted an older adapter would
    be deciding, at runtime and invisibly, which parts of the contract still hold — and the
    first symptom would be a missing field in a graph fragment, months later, in a harvest
    nobody was watching.
    """

    def __init__(self, theirs: str, ours: str = INTERFACE_VERSION) -> None:
        super().__init__(
            f"adapter speaks interface version {theirs!r}, platform speaks {ours!r}. "
            f"An adapter is a versioned worker image (§6.1); rebuild it against this "
            f"interface or run the interface version it was built for."
        )
        self.theirs = theirs
        self.ours = ours


def check_interface(theirs: str) -> None:
    if theirs != INTERFACE_VERSION:
        raise InterfaceMismatch(theirs)


# --------------------------------------------------------------------------- encoders


def encode_capabilities(value: Capabilities) -> dict[str, bool]:
    return {
        "live_query": value.live_query,
        "extract_read": value.extract_read,
        "usage": value.usage,
        "ownership": value.ownership,
        "screenshot": value.screenshot,
    }


def decode_capabilities(raw: dict[str, Any]) -> Capabilities:
    return Capabilities(
        live_query=bool(raw.get("live_query", False)),
        extract_read=bool(raw.get("extract_read", False)),
        usage=bool(raw.get("usage", False)),
        ownership=bool(raw.get("ownership", False)),
        screenshot=bool(raw.get("screenshot", False)),
    )


def encode_manifest(value: AdapterManifest) -> dict[str, Any]:
    return {
        "name": value.name,
        "version": value.version,
        "grammar_version": value.grammar_version,
        "interface_version": value.interface_version,
        "capabilities": encode_capabilities(value.capabilities),
    }


def decode_manifest(raw: dict[str, Any]) -> AdapterManifest:
    return AdapterManifest(
        name=str(raw["name"]),
        version=str(raw["version"]),
        grammar_version=str(raw["grammar_version"]),
        interface_version=str(raw["interface_version"]),
        capabilities=decode_capabilities(raw.get("capabilities", {})),
    )


def encode_scope(value: Scope) -> dict[str, Any]:
    return {"site": value.site, "project": value.project}


def decode_scope(raw: dict[str, Any]) -> Scope:
    return Scope(site=raw.get("site"), project=raw.get("project"))


def encode_asset(value: AssetRef) -> dict[str, Any]:
    return {
        "luid": value.luid,
        "name": value.name,
        "site": value.site,
        "project": value.project,
        "revision": value.revision,
        "project_path": list(value.project_path),
        "updated_at": value.updated_at,
    }


def decode_asset(raw: dict[str, Any]) -> AssetRef:
    return AssetRef(
        luid=str(raw["luid"]),
        name=str(raw["name"]),
        site=str(raw["site"]),
        project=str(raw["project"]),
        revision=str(raw["revision"]),
        project_path=tuple(raw.get("project_path") or ()),
        updated_at=raw.get("updated_at"),
    )


def encode_raw_asset(value: RawAsset) -> dict[str, Any]:
    return {
        "ref": encode_asset(value.ref),
        "content_hash": value.content_hash,
        "payload": base64.b64encode(value.payload).decode("ascii"),
        "size_bytes": value.size_bytes,
        "media_type": value.media_type,
    }


def decode_raw_asset(raw: dict[str, Any]) -> RawAsset:
    return RawAsset(
        ref=decode_asset(raw["ref"]),
        content_hash=str(raw["content_hash"]),
        payload=base64.b64decode(raw["payload"]),
        size_bytes=int(raw.get("size_bytes", 0)),
        media_type=str(raw.get("media_type", "application/octet-stream")),
    )


def encode_parse_result(value: ParseResult) -> dict[str, Any]:
    return {
        "nodes": [{"key": n.key, "type": n.type, "properties": n.properties} for n in value.nodes],
        "edges": [
            {
                "type": e.type,
                "from_key": e.from_key,
                "to_key": e.to_key,
                "properties": e.properties,
            }
            for e in value.edges
        ],
        "parse_quality": value.parse_quality,
        "unrecognised": [
            {"construct": u.construct, "location": u.location, "detail": u.detail}
            for u in value.unrecognised
        ],
        "constructs_total": value.constructs_total,
        "constructs_recognised": value.constructs_recognised,
    }


def decode_parse_result(raw: dict[str, Any]) -> ParseResult:
    return ParseResult(
        nodes=tuple(
            NodeFragment(key=str(n["key"]), type=str(n["type"]), properties=dict(n["properties"]))
            for n in raw["nodes"]
        ),
        edges=tuple(
            EdgeFragment(
                type=str(e["type"]),
                from_key=str(e["from_key"]),
                to_key=str(e["to_key"]),
                properties=dict(e.get("properties") or {}),
            )
            for e in raw["edges"]
        ),
        parse_quality=float(raw["parse_quality"]),
        unrecognised=tuple(
            Unrecognised(
                construct=str(u["construct"]),
                location=str(u["location"]),
                detail=str(u.get("detail", "")),
            )
            for u in raw.get("unrecognised") or ()
        ),
        constructs_total=int(raw.get("constructs_total", 0)),
        constructs_recognised=int(raw.get("constructs_recognised", 0)),
    )


def encode_usage(value: UsageRecord) -> dict[str, Any]:
    return {
        "asset_luid": value.asset_luid,
        "views": value.views,
        "distinct_viewers": value.distinct_viewers,
        "last_view": value.last_view,
        "kind": value.kind.value,
        "workbook_luid": value.workbook_luid,
        "view_name": value.view_name,
    }


def decode_usage(raw: dict[str, Any]) -> UsageRecord:
    return UsageRecord(
        asset_luid=str(raw["asset_luid"]),
        views=int(raw["views"]),
        distinct_viewers=int(raw["distinct_viewers"]),
        last_view=raw.get("last_view"),
        kind=UsageKind(raw.get("kind", UsageKind.WORKBOOK.value)),
        workbook_luid=raw.get("workbook_luid"),
        view_name=raw.get("view_name"),
    )


def encode_viewer(value: ViewerRecord) -> dict[str, Any]:
    return {
        "asset_luid": value.asset_luid,
        "viewer_upn": value.viewer_upn,
        "views": value.views,
        "last_view": value.last_view,
    }


def decode_viewer(raw: dict[str, Any]) -> ViewerRecord:
    return ViewerRecord(
        asset_luid=str(raw["asset_luid"]),
        viewer_upn=str(raw["viewer_upn"]),
        views=int(raw["views"]),
        last_view=raw.get("last_view"),
    )


def encode_owner(value: OwnershipRecord) -> dict[str, Any]:
    return {
        "asset_luid": value.asset_luid,
        "owner_upn": value.owner_upn,
        "owner_display": value.owner_display,
        "licence_tier": value.licence_tier,
        "site_roles": list(value.site_roles),
    }


def decode_owner(raw: dict[str, Any]) -> OwnershipRecord:
    return OwnershipRecord(
        asset_luid=str(raw["asset_luid"]),
        owner_upn=str(raw["owner_upn"]),
        owner_display=raw.get("owner_display"),
        licence_tier=raw.get("licence_tier"),
        site_roles=tuple(raw.get("site_roles") or ()),
    )


def encode_site(value: SiteRecord) -> dict[str, Any]:
    return {
        "site": value.site,
        "licence_tier": value.licence_tier,
        "user_count": value.user_count,
        "detail": value.detail,
    }


def decode_site(raw: dict[str, Any]) -> SiteRecord:
    return SiteRecord(
        site=str(raw["site"]),
        licence_tier=raw.get("licence_tier"),
        user_count=raw.get("user_count"),
        detail=dict(raw.get("detail") or {}),
    )


def encode_calc_node(value: CalcNode) -> dict[str, Any]:
    return {
        "kind": value.kind.value,
        "name": value.name,
        "value": value.value,
        "children": [encode_calc_node(child) for child in value.children],
        "detail": [list(pair) for pair in value.detail],
        "span": list(value.span) if value.span else None,
    }


def decode_calc_node(raw: dict[str, Any]) -> CalcNode:
    return CalcNode(
        kind=NodeKind(raw["kind"]),
        name=str(raw.get("name", "")),
        value=raw.get("value"),
        children=tuple(decode_calc_node(child) for child in raw.get("children") or ()),
        detail=tuple((str(k), str(v)) for k, v in (raw.get("detail") or ())),
        span=(int(raw["span"][0]), int(raw["span"][1])) if raw.get("span") else None,
    )


def encode_calc(value: CalcAST) -> dict[str, Any]:
    return {
        "root": encode_calc_node(value.root),
        "expression": value.expression,
        "grammar_version": value.grammar_version,
        "recognised": value.recognised,
        "total": value.total,
    }


def decode_calc(raw: dict[str, Any]) -> CalcAST:
    return CalcAST(
        root=decode_calc_node(raw["root"]),
        expression=str(raw["expression"]),
        grammar_version=str(raw["grammar_version"]),
        recognised=int(raw.get("recognised", 0)),
        total=int(raw.get("total", 0)),
    )


def encode_parity_case(value: ParityCase) -> dict[str, Any]:
    return {
        "id": value.id,
        "workbook_luid": value.workbook_luid,
        "sheet": value.sheet,
        "grain": list(value.grain),
        "measures": list(value.measures),
        "filters": [list(pair) for pair in value.filters],
        "parameters": [list(pair) for pair in value.parameters],
        "row_limit": value.row_limit,
    }


def decode_parity_case(raw: dict[str, Any]) -> ParityCase:
    return ParityCase(
        id=str(raw["id"]),
        workbook_luid=str(raw["workbook_luid"]),
        sheet=raw.get("sheet"),
        grain=tuple(raw.get("grain") or ()),
        measures=tuple(raw.get("measures") or ()),
        filters=tuple((str(k), str(v)) for k, v in (raw.get("filters") or ())),
        parameters=tuple((str(k), str(v)) for k, v in (raw.get("parameters") or ())),
        row_limit=int(raw.get("row_limit", 10_000)),
    )


def encode_result_set(value: ResultSet) -> dict[str, Any]:
    return {
        "case_id": value.case_id,
        "columns": [{"name": c.name, "role": c.role.value, "type": c.type} for c in value.columns],
        "rows": [list(row) for row in value.rows],
        "strategy": value.strategy.value,
        "interface_version": value.interface_version,
        "adapter_name": value.adapter_name,
        "adapter_version": value.adapter_version,
        "grammar_version": value.grammar_version,
        "outcome": value.outcome.value,
        "reason": value.reason,
        "truncated": value.truncated,
        "executed_at": value.executed_at,
        "detail": value.detail,
    }


def decode_result_set(raw: dict[str, Any]) -> ResultSet:
    return ResultSet(
        case_id=str(raw["case_id"]),
        columns=tuple(
            Column(
                name=str(item["name"]),
                role=ColumnRole(item.get("role", "dimension")),
                type=str(item.get("type", "string")),
            )
            for item in raw["columns"]
        ),
        rows=tuple(tuple(row) for row in raw["rows"]),
        strategy=ExecutionStrategy(raw["strategy"]),
        interface_version=str(raw["interface_version"]),
        adapter_name=str(raw["adapter_name"]),
        adapter_version=str(raw["adapter_version"]),
        grammar_version=raw.get("grammar_version"),
        outcome=ExecutionOutcome(raw.get("outcome", "OK")),
        reason=str(raw.get("reason", "")),
        truncated=bool(raw.get("truncated", False)),
        executed_at=raw.get("executed_at"),
        detail=dict(raw.get("detail") or {}),
    )


def encode_visual_case(value: VisualCase) -> dict[str, Any]:
    return {
        "id": value.id,
        "workbook_luid": value.workbook_luid,
        "view_name": value.view_name,
        "width": value.width,
        "height": value.height,
        "parameters": [list(pair) for pair in value.parameters],
    }


def decode_visual_case(raw: dict[str, Any]) -> VisualCase:
    return VisualCase(
        id=str(raw["id"]),
        workbook_luid=str(raw["workbook_luid"]),
        view_name=str(raw["view_name"]),
        width=int(raw.get("width", 1200)),
        height=int(raw.get("height", 800)),
        parameters=tuple((str(k), str(v)) for k, v in (raw.get("parameters") or ())),
    )


def encode_visual_capture(value: VisualCapture) -> dict[str, Any]:
    return {
        "case_id": value.case_id,
        "image": base64.b64encode(value.image).decode("ascii"),
        "media_type": value.media_type,
        "width": value.width,
        "height": value.height,
        "interface_version": value.interface_version,
        "adapter_name": value.adapter_name,
        "adapter_version": value.adapter_version,
        "captured_at": value.captured_at,
    }


def decode_visual_capture(raw: dict[str, Any]) -> VisualCapture:
    return VisualCapture(
        case_id=str(raw["case_id"]),
        image=base64.b64decode(raw["image"]),
        media_type=str(raw.get("media_type", "image/png")),
        width=int(raw.get("width", 0)),
        height=int(raw.get("height", 0)),
        interface_version=str(raw.get("interface_version", "")),
        adapter_name=str(raw.get("adapter_name", "")),
        adapter_version=str(raw.get("adapter_version", "")),
        captured_at=raw.get("captured_at"),
    )
