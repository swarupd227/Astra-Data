"""S2.4.2 — a screenshot of the source view, for every sheet and every dashboard.

    "I want a screenshot of the source view for each sheet, so that I can compare what I see
    today with what I will see."

The comparison it serves (§10.6) is perceptual and advisory: it never gates, and it exists to
point a reviewer at the visual most likely to have drifted. That only works if the two images
being compared are the same size — so the tests here hold the resize to account, not just the
fetch.
"""

from __future__ import annotations

import io
from typing import Any

from astra_adapter import INTERFACE_VERSION, VisualCase
from PIL import Image

from .fake_tableau import FakeTableau


def case(**kwargs: Any) -> VisualCase:
    settings: dict[str, Any] = {
        "id": "visual-1",
        "workbook_luid": "wb-00000",
        "view_name": "Workbook 0 sheet 0",
    }
    settings.update(kwargs)
    return VisualCase(**settings)


def dimensions(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:
        return image.size


async def test_a_sheet_is_captured_as_a_real_png(adapter) -> None:
    capture = await adapter.capture_visual(case())

    assert capture.case_id == "visual-1"
    assert capture.media_type == "image/png"
    assert capture.image.startswith(b"\x89PNG\r\n\x1a\n"), "a real PNG, not a stand-in"
    assert capture.interface_version == INTERFACE_VERSION
    assert capture.adapter_name == "tableau"
    assert capture.captured_at is not None


async def test_a_dashboard_is_also_a_view(adapter) -> None:
    """S2.4.2: "a PNG per sheet and per dashboard". Tableau's own object model does not
    distinguish them in the views listing, and neither does this adapter's lookup."""
    capture = await adapter.capture_visual(case(view_name="Overview"))

    assert capture.image.startswith(b"\x89PNG\r\n\x1a\n")
    assert dimensions(capture.image) == (1200, 800), "VisualCase's own default size"


async def test_the_image_is_resized_to_exactly_what_was_asked_for(adapter) -> None:
    """The story's own words: "at a configurable size". Tableau's queryViewImage has no
    notion of a caller-chosen size — it renders at the workbook's own layout — so this
    adapter resizes what comes back rather than passing through whatever size Tableau chose.
    """
    capture = await adapter.capture_visual(case(width=400, height=300))

    assert dimensions(capture.image) == (400, 300)
    assert capture.width == 400
    assert capture.height == 300


async def test_the_native_render_is_a_different_size_than_the_default_request(
    adapter, server: FakeTableau
) -> None:
    """Proves the resize is real rather than coincidental: the golden deployment's own render
    is a fixed 960x720 regardless of what is asked for, so a capture at the default 1200x800
    only matches because this adapter changed it."""
    from astra_adapter_tableau.golden import _NATIVE_VIEW_SIZE

    capture = await adapter.capture_visual(case())

    assert dimensions(capture.image) == (1200, 800)
    assert dimensions(capture.image) != _NATIVE_VIEW_SIZE


async def test_two_different_views_are_two_different_images(adapter) -> None:
    """A fake that drew the same picture for every view could not show that the adapter
    fetched *this* view and not some other one."""
    first = await adapter.capture_visual(case(view_name="Workbook 0 sheet 0"))
    second = await adapter.capture_visual(case(id="visual-2", view_name="Workbook 0 sheet 1"))

    assert first.image != second.image


async def test_parameters_reach_tableau_as_vf_query_parameters(adapter) -> None:
    """§6.2's Screenshot row implies the same filter/parameter application as view data —
    a screenshot taken without the case's parameters is a picture of a report nobody has."""
    plain = await adapter.capture_visual(case())
    filtered = await adapter.capture_visual(
        case(id="visual-filtered", parameters=(("Stress Factor", "2.0"),))
    )

    assert plain.image != filtered.image, "the parameter changed what was fetched"


async def test_a_view_that_is_not_published_is_refused(adapter) -> None:
    """A view not published is a view no user sees, and §10.1 derives cases from what the
    user sees. Unlike `execute_case`, `capture_visual` has no INCONCLUSIVE outcome to fall
    back on — it raises, the same way `fetch` raises on an asset that does not exist."""
    from astra_adapter import AdapterError

    try:
        await adapter.capture_visual(case(view_name="A View Nobody Published"))
    except AdapterError as exc:
        assert "not a published view" in str(exc)
    else:
        raise AssertionError("expected an AdapterError")


async def test_the_manifest_claims_screenshot_unconditionally(adapter) -> None:
    """Unlike extract read and live replay, screenshot needs nothing this deployment might
    lack — REST queryViewImage is a call the adapter already knows how to make."""
    assert adapter.manifest().capabilities.screenshot is True


async def test_a_capture_survives_the_wire(adapter) -> None:
    """The RPC boundary (S2.1.1) round-trips a capture, base64 and all."""
    import json

    from astra_adapter.rpc import wire

    capture = await adapter.capture_visual(case())
    restored = wire.decode_visual_capture(
        json.loads(json.dumps(wire.encode_visual_capture(capture)))
    )

    assert restored == capture
    assert restored.image == capture.image
