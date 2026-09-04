"""Capturing a source view's image — story S2.4.2.

    "I want a screenshot of the source view for each sheet, so that I can compare what I see
    today with what I will see." — S2.4.2

§6.2's Screenshot row names the call: "REST queryViewImage for the advisory visual comparison
in §10.6." §10.6 pairs this image against a rendered target visual (via the Power BI export
API, once E6/E7 exist) and scores the two **perceptually** — never as a gate, only to direct a
reviewer to the visual most likely to have drifted.

**Tableau's endpoint has no notion of a caller-chosen size.** It renders a sheet or dashboard
at the size the workbook itself lays it out at, with a DPI choice (`resolution=high`). §10.6's
perceptual comparison needs two images of the *same* size to mean anything, and every caller
of this SDK is entitled to ask for one — S2.4.2's own criterion is "at a configurable size".
So the image Tableau sends back is resized to exactly the size asked for, with Pillow: the one
library in this package whose job is decoding and re-encoding a raster image. Never cropped,
never padded to hide a mismatch — a genuine resize of what Tableau actually rendered.

**A sheet and a dashboard are both "views".** Tableau's own object model does not distinguish
them in the views listing, so one lookup (`views.resolve_view_id`) serves both — which is
what S2.4.2 asks for: "a PNG per sheet and per dashboard".
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from urllib.parse import quote

from astra_adapter import INTERFACE_VERSION, VisualCapture, VisualCase
from PIL import Image

from .rest import TableauRestClient
from .views import resolve_view_id

logger = logging.getLogger(__name__)


class TableauVisualCapturer:
    """Runs one visual case: resolve the view, fetch the render, resize to what was asked."""

    def __init__(
        self, rest: TableauRestClient, *, adapter_name: str, adapter_version: str
    ) -> None:
        self._rest = rest
        self._adapter_name = adapter_name
        self._adapter_version = adapter_version

    async def capture(self, case: VisualCase) -> VisualCapture:
        view_id = await resolve_view_id(self._rest, case.workbook_luid, case.view_name)
        query = "&".join(f"vf_{_encode(name)}={_encode(value)}" for name, value in case.parameters)
        rendered = await self._rest.query_view_image(view_id, filters=query)
        image = _resize(rendered, case.width, case.height)

        return VisualCapture(
            case_id=case.id,
            image=image,
            media_type="image/png",
            width=case.width,
            height=case.height,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._adapter_name,
            adapter_version=self._adapter_version,
            captured_at=datetime.now(UTC).isoformat(),
        )


def _resize(rendered: bytes, width: int, height: int) -> bytes:
    """Tableau's render, re-encoded at exactly the size the caller asked for.

    `Image.Resampling.LANCZOS` because a screenshot destined for a perceptual diff should lose as little
    detail as the resize allows — a cheaper filter would introduce its own artefacts and
    §10.6's comparison would be scoring those rather than a genuine visual difference.
    """
    with Image.open(io.BytesIO(rendered)) as source:
        resized = source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue()


def _encode(value: str) -> str:
    return quote(str(value), safe="")
