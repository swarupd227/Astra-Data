"""The §3.2 Migration Unit state machine -- story S12.1.1, opening E12/F12.1.

    "As a platform engineer, I want each MU to be a Temporal workflow that encodes the
    §3.2 state machine, so that long-running, retried, resumable migration work with
    the state always known."

**A pure, Temporal-free module on purpose.** `migration_units.MU_STATES` already names
the fifteen real state values (`migration_units.py`'s own docstring: "held as strings
rather than an enum: the state machine belongs to the control plane, and this service
should not be the place it is defined" -- true of the *value list*, but the *legal
transitions* between them are exactly what a real workflow needs to encode and what
nothing in this codebase validates today). This module is that transition table, kept
free of any Temporal import so `mu_workflow.py`'s own workflow code (which must be
deterministic -- no I/O, no non-deterministic imports) can call `validate_transition`
directly inside workflow logic, and so the table itself is unit-testable without a
Temporal test environment at all.

**The table, from spec §3.2's own "Exits to" column, transcribed directly** (see
`docs/reference/Astra-Data-Migration-Accelerator-Product-Spec-v1.0.md`'s own §3.2
table -- HARVESTED through DECOMMISSIONED is one real, spec-cited chain; WITHDRAWN is
the one deliberate exception, spec's own words: entered "by Programme Manager via
change control," reachable from any non-terminal state, not itself named in any other
row's "Exits to" list because it is an out-of-band interruption, not a step in the
normal lifecycle).
"""

from __future__ import annotations

from .migration_units import MU_STATES

#: Terminal: no further real transition ever leaves one of these.
TERMINAL_STATES: frozenset[str] = frozenset({"DECOMMISSIONED", "WITHDRAWN"})

#: The real §3.2 chain, `{state: legal next states}` -- every state in `MU_STATES`
#: appears as a key, including the two terminal ones (mapped to an empty set, so
#: `validate_transition` can still give an honest "no legal transitions" answer rather
#: than a `KeyError`).
MU_TRANSITIONS: dict[str, frozenset[str]] = {
    "HARVESTED": frozenset({"CLUSTERED"}),
    "CLUSTERED": frozenset({"MODEL_READY", "BLOCKED"}),
    "BLOCKED": frozenset({"MODEL_READY"}),
    "MODEL_READY": frozenset({"GENERATED"}),
    "GENERATED": frozenset({"PROVING"}),
    "PROVING": frozenset({"PASSED", "FAILED"}),
    "FAILED": frozenset({"MENDING", "ESCALATED"}),
    "MENDING": frozenset({"PROVING", "ESCALATED"}),
    "ESCALATED": frozenset({"ADJUDICATED"}),
    # §3.2's own waiver case: an adjudication may re-run proof, or pass the MU directly
    # under a written waiver (permanently visible on the Parity Dashboard) without a
    # further real proof attempt -- both are real "Exits to" the spec table names.
    "ADJUDICATED": frozenset({"PROVING", "PASSED"}),
    "PASSED": frozenset({"ACCEPTED"}),
    "ACCEPTED": frozenset({"RELEASED"}),
    "RELEASED": frozenset({"DECOMMISSIONED"}),
    "DECOMMISSIONED": frozenset(),
    "WITHDRAWN": frozenset(),
}

assert set(MU_TRANSITIONS) == set(MU_STATES), "every §3.2 state must have a real transition row"


class InvalidTransitionError(Exception):
    """`from_state -> to_state` is not one the real §3.2 chain permits."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        legal = sorted(MU_TRANSITIONS.get(from_state, frozenset()) | {"WITHDRAWN"})
        super().__init__(
            f"illegal MU transition {from_state!r} -> {to_state!r}; "
            f"from {from_state!r} the real chain allows {legal}"
        )


def validate_transition(from_state: str, to_state: str) -> None:
    """Raises `InvalidTransitionError` unless `to_state` is a real §3.2 exit from
    `from_state` -- `WITHDRAWN` is always legal from any non-terminal state (spec's own
    "via change control," an out-of-band interruption every row's own "Exits to" list
    deliberately omits, the identical reasoning `migration_units.py`'s own
    `IN_PROGRESS_STATES` already gives for excluding it there)."""
    if from_state not in MU_TRANSITIONS:
        raise InvalidTransitionError(from_state, to_state)
    if to_state == "WITHDRAWN" and from_state not in TERMINAL_STATES:
        return
    if to_state not in MU_TRANSITIONS[from_state]:
        raise InvalidTransitionError(from_state, to_state)


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


__all__ = [
    "MU_TRANSITIONS",
    "TERMINAL_STATES",
    "InvalidTransitionError",
    "is_terminal",
    "validate_transition",
]
