"""Re-scoring a workbook after a decision about its unrecognised constructs.

S1.2.2: "A Platform Engineer can mark a construct 'ignorable' with a reason, or extend the
grammar; either action re-scores the workbook without a full re-harvest."

Two actions, two mechanisms:

* **Marking a construct ignorable** needs no source access at all. The constructs and the
  counts are already stored, so the score is recomputed from them and written back to the
  Workbook node.
* **Extending the grammar** does need the workbook re-parsed, because only the adapter can
  say what the new grammar reads. That is a *targeted* re-parse of the affected workbooks
  rather than a re-harvest of the estate — which is what "without a full re-harvest"
  asks for.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..principal import Principal
from ..writes import GraphWriter
from .identity import derive_id
from .quality import ParseQualityStore, Rescore, score

logger = logging.getLogger(__name__)


class WorkbookCounts(Protocol):
    """The counts a re-score reads back."""

    async def counts(
        self, graph: str, site: str, workbook_luid: str
    ) -> tuple[int, int, int, float | None] | None: ...

    async def set_parse_quality(
        self,
        graph: str,
        site: str,
        workbook_luid: str,
        *,
        parse_quality: float,
        ignorable: int,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RescoreResult:
    rescored: list[Rescore]
    released: list[Rescore]

    def as_dict(self) -> dict[str, object]:
        return {
            "rescored": [item.as_dict() for item in self.rescored],
            "released_count": len(self.released),
            "rescored_count": len(self.rescored),
        }


class Rescorer:
    """Recomputes parse quality from stored constructs and writes it to the graph."""

    def __init__(
        self,
        *,
        quality: ParseQualityStore,
        counts: WorkbookCounts,
        writer: GraphWriter,
        graph_name: str,
        threshold: float,
    ) -> None:
        self._quality = quality
        self._counts = counts
        self._writer = writer
        self._graph = graph_name
        self._threshold = threshold

    async def rescore(
        self, workbooks: Sequence[tuple[str, str]], *, principal: Principal
    ) -> RescoreResult:
        """Recompute and persist the score for each (site, workbook_luid)."""
        results: list[Rescore] = []
        for site, workbook_luid in workbooks:
            outcome = await self._rescore_one(site, workbook_luid, principal=principal)
            if outcome is not None:
                results.append(outcome)
        released = [item for item in results if item.released]
        if released:
            logger.info(
                "re-score released %s workbook(s) above the %.2f parse-quality threshold",
                len(released),
                self._threshold,
            )
        return RescoreResult(rescored=results, released=released)

    async def _rescore_one(
        self, site: str, workbook_luid: str, *, principal: Principal
    ) -> Rescore | None:
        counts = await self._counts.counts(self._graph, site, workbook_luid)
        if counts is None:
            return None
        recognised, _previous_ignorable, total, previous_quality = counts

        constructs = await self._quality.constructs_for(self._graph, site, workbook_luid)
        ignorable = len([c for c in constructs if not c.unrecognised])
        updated = score(recognised, ignorable, total)

        await self._counts.set_parse_quality(
            self._graph,
            site,
            workbook_luid,
            parse_quality=updated,
            ignorable=ignorable,
        )
        await self._write_to_graph(site, workbook_luid, updated, principal=principal)

        was_held = previous_quality is not None and previous_quality < self._threshold
        return Rescore(
            site=site,
            workbook_luid=workbook_luid,
            previous_quality=previous_quality,
            parse_quality=updated,
            released=was_held and updated >= self._threshold,
        )

    async def _write_to_graph(
        self, site: str, workbook_luid: str, parse_quality: float, *, principal: Principal
    ) -> None:
        """Update the score on the Workbook node.

        A targeted property update rather than an upsert of the whole node: the re-score
        knows the new score and nothing else about the workbook, and an upsert would need
        to reconstruct every other property from a parse it is not doing.
        """
        node_id = derive_id(site, f"workbook:{workbook_luid}")
        await self._writer.set_node_properties(
            node_id, {"parse_quality": parse_quality}, principal=principal
        )
