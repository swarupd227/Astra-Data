"""Error types and their HTTP shape.

One response body for every rejection, so a client parses one thing:

    {
      "error": "ontology_violation",
      "message": "...",
      "violations": [ { "code": ..., "message": ..., "property": ..., "type": ... } ]
    }

``violations`` is present on ontology rejections and absent on the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .ontology import Violation


class AstraGraphError(Exception):
    """Base for errors this service turns into a response rather than a 500."""

    status_code = 500
    error_code = "internal_error"

    def payload(self) -> dict[str, Any]:
        return {"error": self.error_code, "message": str(self)}


class OntologyViolationError(AstraGraphError):
    """A write does not conform to the ontology. Rendered as 422 (S1.1.1)."""

    status_code = 422
    error_code = "ontology_violation"

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = list(violations)
        super().__init__(self._summarise())

    def _summarise(self) -> str:
        count = len(self.violations)
        if count == 1:
            return self.violations[0].message
        named = [v.property for v in self.violations if v.property]
        if named:
            unique = sorted(set(named))
            shown = ", ".join(f"'{name}'" for name in unique[:5])
            more = f" and {len(unique) - 5} more" if len(unique) > 5 else ""
            return f"{count} ontology violations, affecting {shown}{more}."
        return f"{count} ontology violations."

    def payload(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "message": str(self),
            "violations": [v.as_dict() for v in self.violations],
        }


class ElementNotFoundError(AstraGraphError):
    status_code = 404
    error_code = "not_found"


class DuplicateElementError(AstraGraphError):
    status_code = 409
    error_code = "duplicate"


class GraphUnavailableError(AstraGraphError):
    """The graph store could not serve the request."""

    status_code = 503
    error_code = "graph_unavailable"


class CypherExecutionError(AstraGraphError):
    """A read-only Cypher query failed to execute."""

    status_code = 400
    error_code = "cypher_failed"


class CypherTimeoutError(AstraGraphError):
    """A read-only Cypher query exceeded its time limit."""

    status_code = 504
    error_code = "cypher_timeout"


class ForbiddenError(AstraGraphError):
    """The caller's roles do not permit this operation."""

    status_code = 403
    error_code = "forbidden"


class InvalidRequestError(AstraGraphError):
    """The request is well-formed but asks for something the service will not do."""

    status_code = 400
    error_code = "invalid_request"
