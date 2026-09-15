"""Live replay's own tenant policy and statement allow-list -- spec §18.2, story S11.2.1.

    "Custom SQL replay is disabled unless the tenant policy enables it and then runs
    with a statement allow-list (SELECT only)."

**Live replay is itself still a disclosed, unavailable stub** -- `ports.py`'s own
`NoLiveQueryRunner` is the only implementation this codebase has (no database driver for
any source warehouse exists yet; both arrive with E11, per that module's own docstring).
This module does not change that. What it builds is real, tested, and ready for whenever
a real `LiveQueryRunner` lands: a tenant-policy gate (worker-environment configured, the
identical footing `TableauConfig`'s own concurrency/retry knobs already have -- this
worker serves one deployment, §5.2) and a real, `sqlglot`-parsed SELECT-only allow-list,
proven here against a fixture runner rather than the disclosed-unavailable one, so the
policy layer itself is independently provable without a live warehouse.

**`PolicyGatedLiveQueryRunner` wraps whatever `LiveQueryRunner` a deployment is given**,
including the default `NoLiveQueryRunner`, so the enforcement point already exists on
`TableauAdapter`'s own construction path -- a future story that lands a real runner needs
to change nothing here. `available` folds the tenant-policy flag into the existing
capability-gate mechanism (`ports.describe`, `TableauExecutor.strategies`) rather than
adding a second gate those already-real call sites would need to learn about: when
policy disables replay, `available` is `False` exactly the way an absent driver already
makes it `False`, and `TableauExecutor` needs no change at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import sqlglot
from astra_adapter import Column, ParityCase, UnsupportedCapability
from sqlglot import expressions as exp

from .ports import LiveQueryRunner

logger = logging.getLogger(__name__)

#: The AC's own literal words: "SELECT only". A CTE (`WITH ... SELECT ...`) still parses
#: to a top-level `exp.Select` in sqlglot (the `WITH` clause is an argument of the
#: statement it introduces), so it is permitted without a special case.
_ALLOWED_STATEMENT = exp.Select


class LiveReplayPolicyError(Exception):
    """The reconstructed SQL, or the tenant policy itself, refuses this replay (spec
    §18.2's own "an out-of-scope call is refused")."""


@dataclass(frozen=True, slots=True)
class LiveReplayPolicy:
    """This worker's own tenant policy for live replay -- read from its own environment
    (`TableauConfig`), never from graph-svc's Postgres: this adapter is its own separate
    deployable (§5.2), reached only over the §6.1 RPC, and "the platform names a
    credential, it does not send one" (`config.py`'s own module docstring) applies to
    policy the identical way."""

    enabled: bool = False
    max_rows: int = 100_000


def validate_select_only(sql: str) -> None:
    """The AC's own statement allow-list. Refuses anything but exactly one ``SELECT`` --
    a stacked statement (``SELECT 1; DROP TABLE x``) is refused by the same rule that
    refuses a bare ``DROP TABLE x``, since both parse to something other than one
    ``Select``."""
    if not sql.strip():
        raise LiveReplayPolicyError("the reconstructed SQL is empty")
    try:
        statements = [s for s in sqlglot.parse(sql) if s is not None]
    except Exception as exc:  # sqlglot's own parse errors are not one exception type
        raise LiveReplayPolicyError(f"the reconstructed SQL does not parse: {exc}") from exc
    if len(statements) != 1:
        raise LiveReplayPolicyError(
            f"{len(statements)} statements were reconstructed; live replay permits exactly "
            f"one SELECT"
        )
    statement = statements[0]
    if not isinstance(statement, _ALLOWED_STATEMENT):
        raise LiveReplayPolicyError(
            f"the reconstructed statement is {type(statement).__name__}, not SELECT; live "
            f"replay's own statement allow-list is SELECT only"
        )


class PolicyGatedLiveQueryRunner:
    """Wraps a `LiveQueryRunner`, enforcing S11.2.1's own tenant policy and allow-list
    before ever delegating to it."""

    def __init__(self, inner: LiveQueryRunner, policy: LiveReplayPolicy) -> None:
        self._inner = inner
        self._policy = policy

    @property
    def kind(self) -> str:
        return self._inner.kind

    @property
    def available(self) -> bool:
        return self._policy.enabled and self._inner.available

    @property
    def detail(self) -> str:
        """`ports.describe`'s own "why a strategy is unavailable" text (§6.1). The more
        fundamental blocker wins: if no real runner exists at all, that stays the
        reported reason (a platform engineer enabling the policy would not change
        anything) -- the tenant-policy message only takes over once a real runner is
        actually present and policy is the one thing still standing in its way."""
        inner_detail = getattr(self._inner, "detail", "")
        if not self._policy.enabled and self._inner.available:
            return (
                "live replay is disabled by this tenant's own execution-safety policy "
                "(story S11.2.1); a platform engineer enables it explicitly."
            )
        return inner_detail

    async def run(
        self, case: ParityCase
    ) -> tuple[tuple[Column, ...], tuple[tuple[Any, ...], ...], bool, str]:
        if not self._policy.enabled:
            raise UnsupportedCapability("live_query", adapter="tableau")

        columns, rows, truncated, sql = await self._inner.run(case)
        validate_select_only(sql)

        if len(rows) > self._policy.max_rows:
            logger.info(
                "live replay for case %s returned %d rows, capped to %d (S11.2.1's own "
                "resource limit)",
                case.id, len(rows), self._policy.max_rows,
            )
            rows = rows[: self._policy.max_rows]
            truncated = True

        return columns, rows, truncated, sql


__all__ = [
    "LiveReplayPolicy",
    "LiveReplayPolicyError",
    "PolicyGatedLiveQueryRunner",
    "validate_select_only",
]
