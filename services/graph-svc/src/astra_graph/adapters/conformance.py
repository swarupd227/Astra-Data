"""Conformance reports and adapter promotion — story S2.1.2.

    "Suite output is a signed report stored in the artefact store and linked from Platform
    Health. A failing conformance run blocks adapter promotion to a tenant."

The suite itself lives in `astra-adapter-sdk`, where an adapter author can run it without the
platform. This module is the platform's half: keeping the reports, and refusing to let an
adapter touch a client's estate on anything less than a passing one.

**Promotion is the gate the story asks for.** §6.1 already says an adapter "must pass the
conformance suite in §6.3 before it can be enabled on a tenant" — that was a sentence in a
document; this makes it a check with a record behind it. `adapter acceptance is a test
result, not an opinion` is the story's own phrasing, and an opinion is exactly what an
un-enforced rule degrades into once a client is waiting.

**What a promotion is bound to.** One adapter *build*: name, version, interface version and
grammar version together. A report is about the build that produced it, and promoting a
different build on its strength would promote something nobody has tested — the most likely
route to that being an adapter version bump for a "small fix".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

import asyncpg

from ..ids import new_ulid

REPORT_TABLE = "public.adapter_conformance"
PROMOTION_TABLE = "public.adapter_promotion"


class PromotionError(Exception):
    """An adapter cannot be promoted, and the message says exactly why.

    Never a bare refusal. The three reasons — no report, a failing report, a report for a
    different build — call for three different actions, and an engineer told only "refused"
    will try the same thing again.
    """


class PromotionState(str, Enum):
    PROMOTED = "PROMOTED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class AdapterBuild:
    """The four identifiers a conformance report is about.

    Compared as a whole, because a report is evidence about a build and not about a name. An
    adapter whose grammar version moved has a different parse behaviour than the one that was
    tested, even at the same adapter version — which is why the grammar is in here.
    """

    name: str
    version: str
    interface_version: str
    grammar_version: str | None = None

    def describe(self) -> str:
        grammar = f", grammar {self.grammar_version}" if self.grammar_version else ""
        return f"{self.name} {self.version} (interface {self.interface_version}{grammar})"


@dataclass(frozen=True, slots=True)
class ConformanceRecord:
    """A stored conformance report."""

    id: str
    build: AdapterBuild
    corpus: str
    passed: bool
    content_hash: str
    signed: bool
    report: dict[str, Any]
    recorded_by: str
    recorded_at: str | None = None
    signature: str | None = None
    algorithm: str | None = None
    key_id: str | None = None
    checks_passed: int = 0
    checks_failed: int = 0
    checks_skipped: int = 0

    @property
    def failures(self) -> list[str]:
        """The checks that failed, as one line each. What a refusal quotes."""
        return [
            f"{check.get('name')}: {check.get('summary')}"
            for check in self.report.get("checks", [])
            if check.get("outcome") == "FAILED"
        ]

    def as_dict(self, *, full: bool = False) -> dict[str, Any]:
        """The wire shape. ``full`` includes the report body.

        Omitted by default because Platform Health lists reports and a report is kilobytes of
        check detail — a screen that loads six of them to show six one-line summaries is a
        screen that gets slow and then gets removed.
        """
        summary = {
            "id": self.id,
            "adapter": self.build.name,
            "adapter_version": self.build.version,
            "interface_version": self.build.interface_version,
            "grammar_version": self.build.grammar_version,
            "corpus": self.corpus,
            "passed": self.passed,
            "checks": {
                "passed": self.checks_passed,
                "failed": self.checks_failed,
                "skipped": self.checks_skipped,
            },
            "content_hash": self.content_hash,
            "signed": self.signed,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "recorded_by": self.recorded_by,
            "recorded_at": self.recorded_at,
            "failures": self.failures,
        }
        if full:
            summary["report"] = self.report
            summary["signature"] = self.signature
        return summary


@dataclass(frozen=True, slots=True)
class Promotion:
    """An adapter build enabled on this tenant, and the report it rests on."""

    id: str
    build: AdapterBuild
    report_id: str
    state: PromotionState
    reason: str
    promoted_by: str
    promoted_at: str | None = None
    revoked_by: str | None = None
    revoked_at: str | None = None
    revocation_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.state is PromotionState.PROMOTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "adapter": self.build.name,
            "adapter_version": self.build.version,
            "interface_version": self.build.interface_version,
            "grammar_version": self.build.grammar_version,
            "report_id": self.report_id,
            "state": self.state.value,
            "active": self.active,
            "reason": self.reason,
            "promoted_by": self.promoted_by,
            "promoted_at": self.promoted_at,
            "revoked_by": self.revoked_by,
            "revoked_at": self.revoked_at,
            "revocation_reason": self.revocation_reason,
        }


class ConformanceStore(Protocol):
    async def record(self, signed: dict[str, Any], *, principal: str) -> ConformanceRecord: ...

    async def get(self, report_id: str) -> ConformanceRecord | None: ...

    async def latest(self, adapter: str) -> ConformanceRecord | None: ...

    async def recent(self, *, limit: int = 20) -> list[ConformanceRecord]: ...

    async def passing_for(self, build: AdapterBuild) -> ConformanceRecord | None: ...

    async def promote(self, build: AdapterBuild, *, reason: str, principal: str) -> Promotion: ...

    async def revoke(self, adapter: str, *, reason: str, principal: str) -> Promotion | None: ...

    async def promotion(self, adapter: str) -> Promotion | None: ...

    async def promotions(self) -> list[Promotion]: ...


def build_from_report(report: dict[str, Any]) -> AdapterBuild:
    return AdapterBuild(
        name=str(report.get("adapter", "")),
        version=str(report.get("adapter_version", "")),
        interface_version=str(report.get("interface_version", "")),
        grammar_version=report.get("grammar_version") or None,
    )


def check_promotable(record: ConformanceRecord | None, build: AdapterBuild) -> None:
    """Raise unless this exact build has a passing report. The gate, in one place.

    Kept out of the store so the rule can be read, tested and reasoned about without a
    database — and so the two callers that need it (the promote endpoint and the startup
    check) enforce the same thing rather than two similar things.
    """
    if record is None:
        raise PromotionError(
            f"{build.describe()} has no conformance report on this tenant, so nothing is "
            f"known about whether it works. Run `astra-adapter conformance --adapter "
            f"{build.name} --remote --out report.json` and record the report before "
            f"promoting (§6.1, S2.1.2)."
        )
    if not record.passed:
        failures = "\n  - ".join(record.failures) or "(the report records no check detail)"
        raise PromotionError(
            f"{build.describe()} failed its conformance run ({record.id}); a failing run "
            f"blocks promotion (S2.1.2).\n  - {failures}"
        )
    if record.build != build:
        raise PromotionError(
            f"the passing report {record.id} is for {record.build.describe()}, not "
            f"{build.describe()}. A report is evidence about the build that produced it; "
            f"promoting a different build on its strength promotes something nobody tested."
        )


# --------------------------------------------------------------------------- stores


class PostgresConformanceStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, signed: dict[str, Any], *, principal: str) -> ConformanceRecord:
        report = dict(signed.get("report") or {})
        build = build_from_report(report)
        if not build.name or not build.version or not build.interface_version:
            raise PromotionError(
                "a conformance report must name the adapter, its version and the interface "
                "version it was built against; this one does not, so nothing can be "
                "promoted on it"
            )
        counts = report.get("counts") or {}
        record_id = f"cr_{new_ulid()}"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {REPORT_TABLE}
                    (id, graph, adapter, adapter_version, interface_version, grammar_version,
                     corpus, passed, checks_passed, checks_failed, checks_skipped,
                     content_hash, signed, signature, algorithm, key_id, report, recorded_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                        $16, $17::jsonb, $18)
             RETURNING *
                """,
                record_id,
                self._graph,
                build.name,
                build.version,
                build.interface_version,
                build.grammar_version,
                str(report.get("corpus") or ""),
                bool(report.get("passed")),
                int(counts.get("PASSED", 0)),
                int(counts.get("FAILED", 0)),
                int(counts.get("SKIPPED", 0)),
                str(signed.get("content_hash") or ""),
                bool(signed.get("signed", False)),
                signed.get("signature"),
                signed.get("algorithm"),
                signed.get("key_id"),
                json.dumps(report),
                principal,
            )
        return _record(row)

    async def get(self, report_id: str) -> ConformanceRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {REPORT_TABLE} WHERE graph = $1 AND id = $2",
                self._graph,
                report_id,
            )
        return _record(row) if row else None

    async def latest(self, adapter: str) -> ConformanceRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {REPORT_TABLE}
                 WHERE graph = $1 AND adapter = $2
              ORDER BY recorded_at DESC, id DESC
                 LIMIT 1
                """,
                self._graph,
                adapter,
            )
        return _record(row) if row else None

    async def recent(self, *, limit: int = 20) -> list[ConformanceRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {REPORT_TABLE}
                 WHERE graph = $1
              ORDER BY recorded_at DESC, id DESC
                 LIMIT $2
                """,
                self._graph,
                limit,
            )
        return [_record(row) for row in rows]

    async def passing_for(self, build: AdapterBuild) -> ConformanceRecord | None:
        """The newest passing report for exactly this build.

        Matched on all four identifiers in SQL rather than fetched-and-filtered, so an
        adapter with a hundred reports does not read a hundred rows to answer one question.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {REPORT_TABLE}
                 WHERE graph = $1 AND adapter = $2 AND adapter_version = $3
                   AND interface_version = $4
                   AND grammar_version IS NOT DISTINCT FROM $5
                   AND passed
              ORDER BY recorded_at DESC, id DESC
                 LIMIT 1
                """,
                self._graph,
                build.name,
                build.version,
                build.interface_version,
                build.grammar_version,
            )
        return _record(row) if row else None

    async def promote(self, build: AdapterBuild, *, reason: str, principal: str) -> Promotion:
        record = await self.passing_for(build)
        if record is None:
            # Distinguish "no report at all" from "only failing ones", because the two are
            # different problems and the message is the only thing the caller gets.
            record = await self.latest(build.name)
        check_promotable(record, build)
        assert record is not None  # check_promotable raises otherwise

        promotion_id = f"ap_{new_ulid()}"
        async with self._pool.acquire() as conn, conn.transaction():
            # One promoted build per adapter: the previous is revoked, not replaced, so
            # "what was running last month" stays answerable.
            await conn.execute(
                f"""
                    UPDATE {PROMOTION_TABLE}
                       SET state = 'REVOKED', revoked_by = $3, revoked_at = now(),
                           revocation_reason = $4
                     WHERE graph = $1 AND adapter = $2 AND state = 'PROMOTED'
                    """,
                self._graph,
                build.name,
                principal,
                f"superseded by {build.version}",
            )
            row = await conn.fetchrow(
                f"""
                    INSERT INTO {PROMOTION_TABLE}
                        (id, graph, adapter, adapter_version, interface_version,
                         grammar_version, report_id, state, reason, promoted_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'PROMOTED', $8, $9)
                 RETURNING *
                    """,
                promotion_id,
                self._graph,
                build.name,
                build.version,
                build.interface_version,
                build.grammar_version,
                record.id,
                reason,
                principal,
            )
        return _promotion(row)

    async def revoke(self, adapter: str, *, reason: str, principal: str) -> Promotion | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {PROMOTION_TABLE}
                   SET state = 'REVOKED', revoked_by = $3, revoked_at = now(),
                       revocation_reason = $4
                 WHERE graph = $1 AND adapter = $2 AND state = 'PROMOTED'
             RETURNING *
                """,
                self._graph,
                adapter,
                principal,
                reason,
            )
        return _promotion(row) if row else None

    async def promotion(self, adapter: str) -> Promotion | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {PROMOTION_TABLE}
                 WHERE graph = $1 AND adapter = $2 AND state = 'PROMOTED'
                """,
                self._graph,
                adapter,
            )
        return _promotion(row) if row else None

    async def promotions(self) -> list[Promotion]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {PROMOTION_TABLE}
                 WHERE graph = $1 AND state = 'PROMOTED'
              ORDER BY adapter
                """,
                self._graph,
            )
        return [_promotion(row) for row in rows]


class InMemoryConformanceStore:
    """The same rules without a database, for the unit suite.

    Shares `check_promotable` with the Postgres store, so the gate under test here is the
    gate that runs in production rather than a second implementation of the same intent.
    """

    def __init__(self) -> None:
        self.records: list[ConformanceRecord] = []
        self.promoted: dict[str, Promotion] = {}
        self.history: list[Promotion] = []

    async def record(self, signed: dict[str, Any], *, principal: str) -> ConformanceRecord:
        report = dict(signed.get("report") or {})
        build = build_from_report(report)
        if not build.name or not build.version or not build.interface_version:
            raise PromotionError(
                "a conformance report must name the adapter, its version and the interface "
                "version it was built against; this one does not, so nothing can be "
                "promoted on it"
            )
        counts = report.get("counts") or {}
        record = ConformanceRecord(
            id=f"cr_{new_ulid()}",
            build=build,
            corpus=str(report.get("corpus") or ""),
            passed=bool(report.get("passed")),
            content_hash=str(signed.get("content_hash") or ""),
            signed=bool(signed.get("signed", False)),
            signature=signed.get("signature"),
            algorithm=signed.get("algorithm"),
            key_id=signed.get("key_id"),
            report=report,
            recorded_by=principal,
            recorded_at=_now(),
            checks_passed=int(counts.get("PASSED", 0)),
            checks_failed=int(counts.get("FAILED", 0)),
            checks_skipped=int(counts.get("SKIPPED", 0)),
        )
        self.records.append(record)
        return record

    async def get(self, report_id: str) -> ConformanceRecord | None:
        return next((r for r in self.records if r.id == report_id), None)

    async def latest(self, adapter: str) -> ConformanceRecord | None:
        matching = [r for r in self.records if r.build.name == adapter]
        return matching[-1] if matching else None

    async def recent(self, *, limit: int = 20) -> list[ConformanceRecord]:
        return list(reversed(self.records))[:limit]

    async def passing_for(self, build: AdapterBuild) -> ConformanceRecord | None:
        matching = [r for r in self.records if r.build == build and r.passed]
        return matching[-1] if matching else None

    async def promote(self, build: AdapterBuild, *, reason: str, principal: str) -> Promotion:
        record = await self.passing_for(build) or await self.latest(build.name)
        check_promotable(record, build)
        assert record is not None

        if previous := self.promoted.pop(build.name, None):
            self.history.append(_revoked(previous, principal, f"superseded by {build.version}"))
        promotion = Promotion(
            id=f"ap_{new_ulid()}",
            build=build,
            report_id=record.id,
            state=PromotionState.PROMOTED,
            reason=reason,
            promoted_by=principal,
            promoted_at=_now(),
        )
        self.promoted[build.name] = promotion
        self.history.append(promotion)
        return promotion

    async def revoke(self, adapter: str, *, reason: str, principal: str) -> Promotion | None:
        promotion = self.promoted.pop(adapter, None)
        if promotion is None:
            return None
        revoked = _revoked(promotion, principal, reason)
        self.history.append(revoked)
        return revoked

    async def promotion(self, adapter: str) -> Promotion | None:
        return self.promoted.get(adapter)

    async def promotions(self) -> list[Promotion]:
        return [self.promoted[name] for name in sorted(self.promoted)]


def _revoked(promotion: Promotion, principal: str, reason: str) -> Promotion:
    """A revoked copy, keeping everything about how it was promoted."""
    return replace(
        promotion,
        state=PromotionState.REVOKED,
        revoked_by=principal,
        revoked_at=_now(),
        revocation_reason=reason,
    )


# ------------------------------------------------------------------------ row mapping


def _record(row: Any) -> ConformanceRecord:
    report = row["report"]
    return ConformanceRecord(
        id=row["id"],
        build=AdapterBuild(
            name=row["adapter"],
            version=row["adapter_version"],
            interface_version=row["interface_version"],
            grammar_version=row["grammar_version"],
        ),
        corpus=row["corpus"],
        passed=row["passed"],
        content_hash=row["content_hash"],
        signed=row["signed"],
        signature=row["signature"],
        algorithm=row["algorithm"],
        key_id=row["key_id"],
        report=json.loads(report) if isinstance(report, str) else dict(report),
        recorded_by=row["recorded_by"],
        recorded_at=_iso(row["recorded_at"]),
        checks_passed=row["checks_passed"],
        checks_failed=row["checks_failed"],
        checks_skipped=row["checks_skipped"],
    )


def _promotion(row: Any) -> Promotion:
    return Promotion(
        id=row["id"],
        build=AdapterBuild(
            name=row["adapter"],
            version=row["adapter_version"],
            interface_version=row["interface_version"],
            grammar_version=row["grammar_version"],
        ),
        report_id=row["report_id"],
        state=PromotionState(row["state"]),
        reason=row["reason"],
        promoted_by=row["promoted_by"],
        promoted_at=_iso(row["promoted_at"]),
        revoked_by=row["revoked_by"],
        revoked_at=_iso(row["revoked_at"]),
        revocation_reason=row["revocation_reason"],
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _now() -> str:
    from datetime import UTC

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
