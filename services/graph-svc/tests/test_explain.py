"""The explain registry — story S10.1.2's own "every number on a screen has an 'explain'
affordance that opens the query or the events behind it." See `explain.py`'s own module
docstring for why coverage is a representative first pass, not literally every screen.
"""

from __future__ import annotations

from astra_graph.explain import EXPLAIN_REGISTRY


def test_every_entry_is_keyed_by_its_own_metric_key() -> None:
    """A copy-paste mistake building a new entry (the dict key and the field
    disagreeing) would otherwise only surface the day someone actually requests that
    key and gets a different metric back than the one they asked for."""
    for key, entry in EXPLAIN_REGISTRY.items():
        assert entry.metric_key == key


def test_every_entry_names_a_real_source_location() -> None:
    """`source` is how a reader gets from the explain panel back to the real code — it
    must name a real file this codebase actually has, not a placeholder."""
    for entry in EXPLAIN_REGISTRY.values():
        assert entry.source.startswith("astra_graph/"), entry.metric_key
        assert ".py:" in entry.source, entry.metric_key


def test_every_entry_has_real_non_empty_query_or_computation_text() -> None:
    for entry in EXPLAIN_REGISTRY.values():
        assert entry.text.strip(), entry.metric_key
        assert entry.kind in ("sql", "computation"), entry.metric_key


def test_the_flagship_queues_and_boards_are_covered() -> None:
    """The SSE AC names "queues and boards" explicitly -- Exception Desk, Regression
    Monitor, Wave Board and Programme Board (this story's own live-update targets) each
    have at least one real, wired entry, not just Estate Explorer's."""
    covered_titles = " ".join(entry.title for entry in EXPLAIN_REGISTRY.values())
    for surface in ("Exception Desk", "Regression Monitor", "Wave Board", "Programme Board"):
        assert surface in covered_titles, f"no explain entry mentions {surface!r}"
