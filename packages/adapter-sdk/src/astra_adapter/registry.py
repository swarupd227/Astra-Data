"""Finding adapters by name.

§20: "A new source adapter is a repository that passes the harness." For that to be true the
harness has to be able to *find* an adapter it has never heard of, without the SDK importing
it — so adapters register themselves through a Python entry point group, and installing an
adapter package is what makes `astra-adapter conformance --adapter tableau` work.

The SDK ships exactly one adapter, ``fake``, and it is not a source: it is the reference
implementation the conformance suite tests itself against (S2.1.1: "The SDK includes
fixtures, a fake source, and the conformance suite runner"). A suite that has never been run
against a passing adapter is not a suite, it is an assertion.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

#: The entry-point group an adapter package declares itself in:
#:
#:     [project.entry-points."astra.adapters"]
#:     tableau = "astra_adapter_tableau:build"
#:
#: The value is a zero-argument callable returning something satisfying §6.1.
ENTRY_POINT_GROUP = "astra.adapters"

#: Adapters registered in this process, by name. Populated by ``register`` — used by tests
#: and by an adapter that is being developed before it has been packaged.
_REGISTERED: dict[str, Callable[[], Any]] = {}


class UnknownAdapter(LookupError):
    """No adapter of that name is installed.

    The message names what *is* installed, because the usual cause is that the adapter's
    package is not on the path, and a bare "not found" sends people looking for a typo.
    """

    def __init__(self, name: str, known: list[str]) -> None:
        self.name = name
        self.known = known
        available = ", ".join(known) if known else "none"
        hint = ""
        if name == "tableau":
            hint = (
                "\nThe Tableau adapter is F2.2 to F2.4 and is not built yet; the SDK, the "
                "RPC and the conformance suite (S2.1.1) are, and run against 'fake'."
            )
        super().__init__(
            f"no source adapter named {name!r} is registered. Registered: {available}."
            f"\nAn adapter registers itself in the {ENTRY_POINT_GROUP!r} entry-point group; "
            f"installing its package is what makes it visible here.{hint}"
        )


def register(name: str, factory: Callable[[], Any]) -> None:
    """Register an adapter in this process, ahead of packaging."""
    _REGISTERED[name] = factory


def registered_names() -> list[str]:
    """Every adapter this process can load, in name order."""
    names = set(_REGISTERED)
    names.update(point.name for point in entry_points(group=ENTRY_POINT_GROUP))
    return sorted(names)


def load_adapter(name: str) -> Any:
    """Build the named adapter, or say precisely why it cannot be built."""
    if name in _REGISTERED:
        return _REGISTERED[name]()
    for point in entry_points(group=ENTRY_POINT_GROUP):
        if point.name == name:
            return point.load()()
    raise UnknownAdapter(name, registered_names())
