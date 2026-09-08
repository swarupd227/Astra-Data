"""§10.5 Visual parity (advisory) -- story S7.6.1, opening F7.6.

    "As a report owner, I want a structural visual-similarity score and side-by-side
    images, so that I can spot a report that is numerically right and visually wrong.

    Acceptance criteria:
    - Structural score from mark type, encodings, axes, sort, reference lines (0-1);
      image similarity from source screenshot and Power BI export API render
    - Score is shown on the Parity Dashboard and the G3 card; it never gates; the human
      visual review at G3 is the gate"

§10.5 itself, verbatim: *"Structural comparison of the visual specification -- mark
type, encodings, axis fields, sort, reference lines -- produces a visual parity score
per sheet. A screenshot of the source view (via the adapter) and a rendered image of the
target visual (via the Power BI export API) are compared perceptually and the score is
shown next to the structural score. Neither gates acceptance: G3 requires a passing
data-parity verdict and a human visual review, and the advisory scores exist to direct
that review to the visuals most likely to have drifted."*

(The actual spec heading is `## 10.5`; several earlier modules in this codebase --
`proof.py`'s own `VisualCapture`/`VisualCase`, `visual_redesign.py` -- cite it as "§10.6",
a pre-existing off-by-one in this codebase's own citations, not in the specification
document itself. This module cites it correctly; the earlier citations are left alone,
since correcting them is unrelated to this story's own scope.)

**This module is pure where it can be, graph-coupled where it must be -- the identical
split `diff.py`/`verdicts.py` (S7.4.1) and `parity_dashboard.py` (S7.4.2) already
established.** `compute_structural_score` and `compute_image_similarity` take already-
loaded data and return a plain result; nothing in either awaits anything.
`run_visual_parity_for_workbook` is the graph-coupled orchestration: find a workbook's
own composed `Visual`s, score each one, optionally capture and render images, and write
the results back.

**The structural score is five weighted components, the identical "weighted Jaccard over
several discrete-fact sets" shape `lineage.similarity` (the Cartographer's own strength
score, S3.1.1) already established** -- the only existing precedent in this codebase for
a deterministic 0-1 score built from comparing discrete facts. Weights are invented and
disclosed (§10.5 names the five facts, not how to weight them): mark type 0.3, encodings
0.3, axes 0.2, sort 0.1, reference lines 0.1 -- mark type and encodings weighted highest
since they are what a reader notices first; sort and reference lines weighted lowest as
finer detail.

**Mark type reads the already-computed `Visual.redesign_flag`, rather than re-deriving
`compositor.resolve_visual`'s own refinement logic (stacked vs. clustered, dual-axis,
matrix vs. table) a second time.** That refinement needs live graph reads
(`ResolvedWell`s reconstructed with real bindings) this pure function does not have, and
duplicating business logic that already lives in one place is a real maintenance risk,
not a shortcut. `redesign_flag` is definitionally the right signal instead: a mark type
Appendix B.2 could only resolve by flagging it for redesign *is* a real structural
mismatch; one that resolved cleanly is not.

**Axes are read from each field well's own `shelf` -- `"rows"`/`"cols"` -- against the
source `Worksheet`'s own `rows_shelf`/`cols_shelf`.** `compositor._resolve_one_well`
already tags every well this way; this module reads the stored tag rather than
re-deriving it.

**Reference lines score 0 whenever the source has any, since the target never carries
any -- a disclosed, honest structural gap, not a bug.** Confirmed by direct inspection:
`compose_report` never reads `Worksheet.reference_lines` at all, and Appendix B.2 itself
lists reference lines among the mark-type-table entries "not reachable... by design"
(`visual_mapping.py`'s own docstring) -- there is no Power BI-side property anywhere in
this ontology for a preserved reference line. A sheet with reference lines the migration
silently dropped is exactly the kind of "numerically right, visually wrong" case §10.5's
own AC names; scoring it 0 is the honest answer, not a bug to fix by inventing a
property nothing writes.

**Image similarity is a real, deterministic average hash (aHash), not a full
perceptual-diff library this codebase has no other use for.** Resize both images to
8x8 grayscale, threshold each pixel against the image's own mean, and compare the two
64-bit hashes by Hamming distance -- a standard, well-known, "advisory grade" perceptual
metric, appropriate to an AC that is explicitly never a gate. `None` (not zero) when
either side's bytes cannot be decoded as an image at all -- a real, disclosed absence,
the identical "an absent fact is not a zero" posture `first_pass_rate`/`waived_count`
already carry elsewhere in this epic.

**A real, disclosed limitation of aHash, proven by its own test rather than assumed:**
two *solid-colour* images of any two different colours hash identically (every pixel
equals the image's own mean, so every bit thresholds the same way regardless of what
that colour actually is) -- aHash detects *pattern*, not absolute colour. A visual whose
entire drift is a colour-palette change (a redesigned theme, a changed conditional-
format rule) would score a perfect 1.0 despite looking different. This is a real
property of the chosen algorithm, not a bug to fix quietly; a future story wanting
colour-sensitive comparison would need a different metric (e.g. a colour histogram
alongside the structural hash), not a patch to this one.

**`FixtureSourceAdapter.capture_visual` and `FixtureTargetAdapter.render_visual` both
return the identical disclosed one-pixel placeholder** (see `target_fake.py`'s own
docstring) -- so in this platform's own local/demo environment, any computed
`image_score` is real arithmetic over content-free bytes, not a meaningful similarity.
Disclosed here rather than hidden: the orchestration and the algorithm are both real and
correct; only a live Tableau screenshot and a live Power BI export would give the number
real content.

**Capturing the source screenshot uses the SDK's own established
`Capabilities.screenshot` gate (§6.2).** `UnsupportedCapability` is caught and treated
as an honest absence (`image_score=None`), the identical "a capability an adapter does
not claim is not tested" posture the SDK's own conformance suite already applies -- not
every source adapter can screenshot, and that is a fact about the deployment, not a
defect (`Capabilities`'s own docstring). No equivalent gate exists for
`TargetAdapter.render_visual`: `TargetManifest` deliberately carries no `Capabilities`
at all (see `target_contract.py`) -- an adapter either implements this whole narrow
contract or it is not one.

**"Side-by-side images" are two real stored artefacts, not only a number.** A captured
screenshot is stored under the already-established `"visual_capture"` kind (S2.4.2,
`find_screenshot_ref`'s own convention: `case_id` names the source worksheet by name,
not id); a rendered export is stored under a new `"visual_render"` kind, the target-side
counterpart. `Visual.source_screenshot_ref`/`.target_render_ref` point at them.

**Scores are recorded on `Visual`, not `ParityCase`/`Verdict`.** A structural/image
score is a fact about one sheet's own mapping -- static until the sheet or the mapping
changes -- not about a specific grain/measure/filter combination a parity case
represents; storing it on the node it is actually about is the same reasoning
`ParityCase.sampled` already applies in the opposite direction (a per-run fact, stored
on the case describing that run).

**Never gates, literally: nothing in this module is ever read by `diff.py`,
`verdicts.py`, or any `Verdict.result`.** The Parity Dashboard (S7.4.2) is extended to
show both scores per sheet; the G3 card the AC also names does not exist anywhere in
this codebase yet (F9.1/S9.1.1's own later, unbuilt scope, the identical disclosure
S7.5.1 already gave for its own SAMPLED label) -- these are the real facts that card
will read from once it exists.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from astra_adapter import (
    SourceAdapter,
    TargetAdapter,
    UnsupportedCapability,
    VisualCase,
)

from .artefacts import ArtefactStore
from .graph.queries import NODE_INDEX_TABLE
from .lineage import children, hydrate
from .principal import Principal
from .writes import GraphWriter

SOURCE_SCREENSHOT_KIND = "visual_capture"
"""The kind S2.4.2/`visual_redesign.find_screenshot_ref` already established."""

TARGET_RENDER_KIND = "visual_render"
"""The target-side counterpart -- new to this story."""

#: §10.5 names five structural facts, not how to weight them -- see this module's own
#: docstring. Sums to 1.0.
WEIGHT_MARK_TYPE = 0.3
WEIGHT_ENCODINGS = 0.3
WEIGHT_AXES = 0.2
WEIGHT_SORT = 0.1
WEIGHT_REFERENCE_LINES = 0.1

_AXIS_SHELVES = ("rows", "cols")


class VisualParityError(Exception):
    """A visual parity run could not be produced for this workbook."""


# --------------------------------------------------------------------------- pure: structural


def _jaccard(left: set[str], right: set[str]) -> float:
    """1.0 when both sides agree there is nothing here -- unlike `lineage._jaccard`
    (which returns 0.0 for two empty sets), an empty encoding/axis/sort set on both
    sides is agreement, not a mismatch, so it must not drag the structural score down."""
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _worksheet_fields(worksheet: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """(every encoded field name, axis-only field names) from a Worksheet's own shelves."""
    rows = {str(name) for name in (worksheet.get("rows_shelf") or ()) if name}
    cols = {str(name) for name in (worksheet.get("cols_shelf") or ()) if name}
    marks: set[str] = set()
    for entry in worksheet.get("marks_shelf") or ():
        _, _, name = str(entry).partition(":")
        if name:
            marks.add(name)
    return rows | cols | marks, rows | cols


def _visual_fields(visual: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """(every encoded field name, axis-only field names) from a Visual's own field wells
    -- `compositor.ResolvedWell.as_dict()`'s own `sourceName`/`shelf` keys."""
    wells: Sequence[Mapping[str, Any]] = (visual.get("encodings") or {}).get("field_wells") or ()
    all_fields = {str(well["sourceName"]) for well in wells if well.get("sourceName")}
    axis_fields = {
        str(well["sourceName"])
        for well in wells
        if well.get("shelf") in _AXIS_SHELVES and well.get("sourceName")
    }
    return all_fields, axis_fields


def _sort_signature(entries: Sequence[Any]) -> set[str]:
    return {json.dumps(entry, sort_keys=True, default=str) for entry in entries}


@dataclass(frozen=True, slots=True)
class StructuralScore:
    """§10.5's own five components, weighted, plus the breakdown for evidence."""

    score: float
    mark_type: float
    encodings: float
    axes: float
    sort: float
    reference_lines: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "mark_type": self.mark_type, "encodings": self.encodings,
            "axes": self.axes, "sort": self.sort, "reference_lines": self.reference_lines,
        }


def compute_structural_score(
    worksheet: Mapping[str, Any], visual: Mapping[str, Any]
) -> StructuralScore:
    """§10.5: "mark type, encodings, axis fields, sort, reference lines" -- see this
    module's own docstring for how each of the five is read and why."""
    mark_type_score = 0.0 if visual.get("redesign_flag") else 1.0

    source_fields, source_axes = _worksheet_fields(worksheet)
    target_fields, target_axes = _visual_fields(visual)
    encodings_score = _jaccard(source_fields, target_fields)
    axes_score = _jaccard(source_axes, target_axes)

    source_sort = _sort_signature(worksheet.get("sort") or ())
    target_sort = _sort_signature((visual.get("encodings") or {}).get("sort") or ())
    sort_score = _jaccard(source_sort, target_sort)

    reference_lines_score = 1.0 if not (worksheet.get("reference_lines") or ()) else 0.0

    score = (
        WEIGHT_MARK_TYPE * mark_type_score
        + WEIGHT_ENCODINGS * encodings_score
        + WEIGHT_AXES * axes_score
        + WEIGHT_SORT * sort_score
        + WEIGHT_REFERENCE_LINES * reference_lines_score
    )
    return StructuralScore(
        score=score, mark_type=mark_type_score, encodings=encodings_score,
        axes=axes_score, sort=sort_score, reference_lines=reference_lines_score,
    )


# --------------------------------------------------------------------------- pure: image


def _average_hash(image_bytes: bytes) -> int:
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as opened:
        grayscale = opened.convert("L").resize((8, 8))
        pixels = list(grayscale.getdata())
    average = sum(pixels) / len(pixels)
    bits = 0
    for pixel in pixels:
        bits = (bits << 1) | (1 if pixel >= average else 0)
    return bits


def compute_image_similarity(source_image: bytes, target_image: bytes) -> float | None:
    """§10.5's own perceptual comparison -- an 8x8 average hash (aHash); see this
    module's own docstring for why this algorithm, and why `None` rather than 0.0 when
    either side cannot be decoded as an image at all."""
    try:
        source_hash = _average_hash(source_image)
        target_hash = _average_hash(target_image)
    except Exception:
        return None
    hamming_distance = bin(source_hash ^ target_hash).count("1")
    return 1.0 - (hamming_distance / 64)


# --------------------------------------------------------------------------- graph reads


async def _workbook_visuals(
    conn: asyncpg.Connection, graph: str, workbook_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(worksheets by id, visuals by id) -- every live `Visual` whose own
    `source_sheet_ref` is one of this workbook's own `Worksheet`s."""
    worksheet_map = await children(conn, graph, [workbook_id], "CONTAINS", "Worksheet")
    worksheet_ids = sorted(worksheet_map.get(workbook_id, set()))
    worksheets = await hydrate(conn, graph, "Worksheet", worksheet_ids)

    visual_rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'Visual' AND retired_at IS NULL""",
        graph,
    )
    all_visuals = await hydrate(conn, graph, "Visual", [row["id"] for row in visual_rows])
    visuals = {
        vid: props for vid, props in all_visuals.items()
        if props.get("source_sheet_ref") in worksheets
    }
    return worksheets, visuals


# --------------------------------------------------------------------------- orchestration


async def _capture_and_render(
    *,
    source_adapter: SourceAdapter,
    target_adapter: TargetAdapter,
    artefact_store: ArtefactStore,
    workbook_id: str,
    workbook_luid: str,
    worksheet_name: str,
    workspace: str,
    principal: Principal,
) -> tuple[float | None, str | None, str | None]:
    """(image_score, source_screenshot_ref, target_render_ref) -- `None`s throughout
    when the source adapter has not claimed the screenshot capability (an honest
    absence, not a failure). See this module's own docstring."""
    visual_case = VisualCase(id=worksheet_name, workbook_luid=workbook_luid, view_name=worksheet_name)
    try:
        source_capture = await source_adapter.capture_visual(visual_case)
    except UnsupportedCapability:
        return None, None, None

    target_capture = await target_adapter.render_visual(visual_case=visual_case, workspace=workspace)

    source_artefact = await artefact_store.store(
        kind=SOURCE_SCREENSHOT_KIND, mu_ref=workbook_id, case_id=worksheet_name,
        content=source_capture.image, media_type=source_capture.media_type,
        width=source_capture.width or None, height=source_capture.height or None,
        adapter_name=source_capture.adapter_name, created_by=principal.value,
    )
    target_artefact = await artefact_store.store(
        kind=TARGET_RENDER_KIND, mu_ref=workbook_id, case_id=worksheet_name,
        content=target_capture.image, media_type=target_capture.media_type,
        width=target_capture.width or None, height=target_capture.height or None,
        adapter_name=target_capture.adapter_name, created_by=principal.value,
    )
    image_score = compute_image_similarity(source_capture.image, target_capture.image)
    return image_score, source_artefact.id, target_artefact.id


async def run_visual_parity_for_workbook(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    *,
    workbook_id: str,
    source_adapter: SourceAdapter | None,
    target_adapter: TargetAdapter | None,
    workspace: str = "dev",
    principal: Principal,
) -> dict[str, Any]:
    """Score every composed `Visual` for a workbook and write the results back onto it
    -- see this module's own docstring."""
    async with pool.acquire() as conn:
        worksheets, visuals = await _workbook_visuals(conn, graph_name, workbook_id)
        if not visuals:
            raise VisualParityError(
                f"workbook '{workbook_id}' has no composed visuals to score -- "
                "compose a report first"
            )
        workbook_properties = await hydrate(conn, graph_name, "Workbook", [workbook_id])
    workbook_luid = str((workbook_properties.get(workbook_id) or {}).get("luid") or "")

    computed_at = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []
    for visual_id, visual_properties in visuals.items():
        worksheet_id = str(visual_properties.get("source_sheet_ref"))
        worksheet_properties = worksheets.get(worksheet_id) or {}
        structural = compute_structural_score(worksheet_properties, visual_properties)

        image_score: float | None = None
        source_ref: str | None = None
        target_ref: str | None = None
        if source_adapter is not None and target_adapter is not None:
            worksheet_name = str(worksheet_properties.get("name") or "")
            if worksheet_name:
                image_score, source_ref, target_ref = await _capture_and_render(
                    source_adapter=source_adapter, target_adapter=target_adapter,
                    artefact_store=artefact_store, workbook_id=workbook_id,
                    workbook_luid=workbook_luid, worksheet_name=worksheet_name,
                    workspace=workspace, principal=principal,
                )

        await writer.set_node_properties(
            visual_id,
            {
                "structural_score": structural.score,
                "structural_score_breakdown": structural.as_dict(),
                "image_score": image_score,
                "visual_score_computed_at": computed_at,
                "source_screenshot_ref": source_ref,
                "target_render_ref": target_ref,
            },
            principal=principal,
        )
        results.append({
            "visual_id": visual_id, "sheet_ref": worksheet_id,
            "structural_score": structural.score, "image_score": image_score,
        })

    return {"workbook_id": workbook_id, "visuals_scored": len(results), "results": results}


async def get_visual_captures(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, *, visual_id: str,
) -> dict[str, Any] | None:
    """The AC's own "side-by-side images" -- both stored captures for one scored
    visual, base64-encoded for direct inline display (`<img src="data:...;base64,...">`,
    no separate binary route needed). `None` when the visual does not exist, or has
    never had an image score computed (no captures stored yet) -- an honest absence,
    the same posture every other "not yet computed" fact in this epic already carries."""
    async with pool.acquire() as conn:
        visual = (await hydrate(conn, graph_name, "Visual", [visual_id])).get(visual_id)
    if visual is None:
        return None
    source_ref = visual.get("source_screenshot_ref")
    target_ref = visual.get("target_render_ref")
    if not source_ref or not target_ref:
        return None

    source_record = await artefact_store.get(str(source_ref))
    target_record = await artefact_store.get(str(target_ref))
    source_content = await artefact_store.content(str(source_ref))
    target_content = await artefact_store.content(str(target_ref))
    if source_record is None or target_record is None or source_content is None or target_content is None:
        return None

    return {
        "visual_id": visual_id,
        "source": {
            "media_type": source_record.media_type,
            "content_base64": base64.b64encode(source_content).decode("ascii"),
        },
        "target": {
            "media_type": target_record.media_type,
            "content_base64": base64.b64encode(target_content).decode("ascii"),
        },
    }


class VisualParityService:
    """Binds `run_visual_parity_for_workbook` to one pool/graph/writer/artefact store/
    adapters -- the identical "pre-bound object on app.state" shape `VerdictsService`/
    `CaseExecutionService` already take."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore,
        source_adapter: SourceAdapter | None,
        target_adapter: TargetAdapter | None,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._source_adapter = source_adapter
        self._target_adapter = target_adapter

    async def run(self, workbook_id: str, *, workspace: str, principal: Principal) -> dict[str, Any]:
        return await run_visual_parity_for_workbook(
            self._pool, self._graph, self._writer, self._artefact_store,
            workbook_id=workbook_id, source_adapter=self._source_adapter,
            target_adapter=self._target_adapter, workspace=workspace, principal=principal,
        )

    async def captures(self, visual_id: str) -> dict[str, Any] | None:
        """The AC's own "side-by-side images" -- see `get_visual_captures`."""
        return await get_visual_captures(self._pool, self._graph, self._artefact_store, visual_id=visual_id)


__all__ = [
    "SOURCE_SCREENSHOT_KIND",
    "TARGET_RENDER_KIND",
    "StructuralScore",
    "VisualParityError",
    "VisualParityService",
    "compute_image_similarity",
    "compute_structural_score",
    "get_visual_captures",
    "run_visual_parity_for_workbook",
]
