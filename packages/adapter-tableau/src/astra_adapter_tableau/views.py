"""Resolving a view id from a workbook and a name — shared by execution and visual capture.

Tableau's REST API takes a view id everywhere; a parity case names a sheet and a visual case
names a sheet *or a dashboard*, because that is what a client brief and the estate graph both
use. The lookup is a real REST call and not a rename: a name not among the workbook's published
views is a name no user sees, and §10.1 derives cases from what the user sees.

One function, used by both S2.4.1's `execution.py` and S2.4.2's `visual.py`, so "not
published" means the same thing and is worded the same way in both.
"""

from __future__ import annotations

from astra_adapter import AdapterError

from .rest import TableauRestClient


async def resolve_view_id(rest: TableauRestClient, workbook_luid: str, name: str) -> str:
    """The view LUID for a sheet or dashboard name inside one workbook.

    Both are "views" in Tableau's own model — the REST listing does not distinguish them —
    so one lookup serves a `ParityCase.sheet` and a `VisualCase.view_name` alike.
    """
    response = await rest.call(
        "GET",
        "/api/{version}/sites/{site_id}/workbooks/" + workbook_luid + "/views",
    )
    views = (response.json().get("views") or {}).get("view") or []
    for view in views:
        if str(view.get("name", "")) == name:
            return str(view.get("id", ""))
    raise AdapterError(
        f"{name!r} is not a published view of workbook {workbook_luid!r}, so Tableau has "
        f"no view for it. A view not published is a view no user sees.",
        retryable=False,
    )
