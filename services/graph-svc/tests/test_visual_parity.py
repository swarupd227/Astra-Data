"""§10.5 Visual parity (advisory), the pure half -- story S7.6.1, opening F7.6.

    "Structural score from mark type, encodings, axes, sort, reference lines (0-1);
    image similarity from source screenshot and Power BI export API render."

`compute_structural_score`/`compute_image_similarity` are pure -- no database, no
adapter -- so they are testable here directly. The graph-coupled orchestration
(`run_visual_parity_for_workbook`) is covered by the integration suite instead.
"""

from __future__ import annotations

import io

import pytest

from astra_graph.visual_parity import compute_image_similarity, compute_structural_score


def _worksheet(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "rows_shelf": ["Desk"], "cols_shelf": ["MarginCalc"], "marks_shelf": [],
        "sort": [], "reference_lines": [],
    }
    return {**defaults, **overrides}


def _well(shelf: str, source_name: str) -> dict[str, object]:
    return {"shelf": shelf, "sourceName": source_name}


def _visual(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "redesign_flag": False,
        "encodings": {"field_wells": [_well("rows", "Desk"), _well("cols", "MarginCalc")], "sort": []},
    }
    return {**defaults, **overrides}


# ------------------------------------------------------------------------ structural score


def test_a_perfectly_mirrored_visual_scores_one() -> None:
    result = compute_structural_score(_worksheet(), _visual())
    assert result.score == 1.0
    assert result.mark_type == 1.0
    assert result.encodings == 1.0
    assert result.axes == 1.0
    assert result.sort == 1.0
    assert result.reference_lines == 1.0


def test_a_redesign_flagged_visual_scores_mark_type_zero() -> None:
    result = compute_structural_score(_worksheet(), _visual(redesign_flag=True))
    assert result.mark_type == 0.0
    assert result.score < 1.0


def test_encodings_score_reflects_partial_field_overlap() -> None:
    worksheet = _worksheet(rows_shelf=["Desk", "TradeDate"])
    visual = _visual(encodings={"field_wells": [_well("rows", "Desk")], "sort": []})
    result = compute_structural_score(worksheet, visual)
    # source fields {Desk, TradeDate, MarginCalc}, target fields {Desk} -> 1/3
    assert result.encodings == pytest.approx(1 / 3)


def test_axes_score_is_independent_of_non_axis_encodings() -> None:
    worksheet = _worksheet(marks_shelf=["color:Region"])
    visual = _visual(encodings={
        "field_wells": [_well("rows", "Desk"), _well("cols", "MarginCalc"), _well("color", "Region")],
        "sort": [],
    })
    result = compute_structural_score(worksheet, visual)
    # Both axes fields present on both sides -> perfect axes score, despite the extra
    # "color" encoding that is not itself an axis.
    assert result.axes == 1.0
    assert result.encodings == 1.0


def test_a_missing_axis_field_lowers_the_axes_score_but_not_the_encodings_score() -> None:
    visual = _visual(encodings={"field_wells": [_well("rows", "Desk")], "sort": []})
    result = compute_structural_score(_worksheet(), visual)
    assert result.axes == pytest.approx(0.5)  # {Desk, MarginCalc} vs {Desk}


def test_sort_score_reflects_matching_sort_specs() -> None:
    sort_spec = [{"field": "Desk", "direction": "ASC"}]
    worksheet = _worksheet(sort=sort_spec)
    visual = _visual()
    visual["encodings"] = {**visual["encodings"], "sort": sort_spec}  # type: ignore[dict-item]
    result = compute_structural_score(worksheet, visual)
    assert result.sort == 1.0


def test_sort_score_is_zero_when_the_target_dropped_the_sort() -> None:
    worksheet = _worksheet(sort=[{"field": "Desk", "direction": "ASC"}])
    result = compute_structural_score(worksheet, _visual())
    assert result.sort == 0.0


def test_no_sort_on_either_side_is_agreement_not_a_mismatch() -> None:
    result = compute_structural_score(_worksheet(sort=[]), _visual())
    assert result.sort == 1.0


def test_reference_lines_score_zero_when_the_source_has_any() -> None:
    worksheet = _worksheet(reference_lines=[{"axis": "y", "value": 100}])
    result = compute_structural_score(worksheet, _visual())
    assert result.reference_lines == 0.0


def test_reference_lines_score_one_when_the_source_has_none() -> None:
    result = compute_structural_score(_worksheet(reference_lines=[]), _visual())
    assert result.reference_lines == 1.0


def test_the_weighted_total_matches_the_five_disclosed_weights() -> None:
    # Force mark_type=0, everything else=1 -- the total should be exactly 1 - 0.3.
    result = compute_structural_score(_worksheet(), _visual(redesign_flag=True))
    assert result.score == pytest.approx(0.7)


def test_a_worksheet_with_no_encodings_at_all_still_scores() -> None:
    worksheet = _worksheet(rows_shelf=[], cols_shelf=[], marks_shelf=[])
    visual = _visual(encodings={"field_wells": [], "sort": []})
    result = compute_structural_score(worksheet, visual)
    assert result.encodings == 1.0
    assert result.axes == 1.0


# -------------------------------------------------------------------------- image score


def _png(color: tuple[int, int, int], size: tuple[int, int] = (16, 16)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_identical_images_score_perfect_similarity() -> None:
    image = _png((100, 150, 200))
    assert compute_image_similarity(image, image) == 1.0


def test_two_solid_colour_images_hash_identically_regardless_of_colour() -> None:
    black = _png((0, 0, 0))
    white = _png((255, 255, 255))
    # A uniform image's own average hash is all-zero bits regardless of the actual
    # colour (every pixel equals the mean) -- aHash detects *pattern*, not raw colour,
    # so two uniform images of any two colours hash identically. This is a real,
    # disclosed limitation of the algorithm, not a bug: proven here rather than assumed.
    assert compute_image_similarity(black, white) == 1.0


def test_a_half_and_half_image_differs_from_a_solid_image() -> None:
    from PIL import Image

    half = Image.new("RGB", (16, 16), color=(255, 255, 255))
    for y in range(8, 16):
        for x in range(16):
            half.putpixel((x, y), (0, 0, 0))
    buffer = io.BytesIO()
    half.save(buffer, format="PNG")

    solid = _png((255, 255, 255))
    similarity = compute_image_similarity(buffer.getvalue(), solid)
    assert similarity is not None
    assert similarity < 1.0


def test_undecodable_bytes_return_none_not_zero() -> None:
    assert compute_image_similarity(b"not an image", _png((0, 0, 0))) is None
    assert compute_image_similarity(_png((0, 0, 0)), b"") is None
