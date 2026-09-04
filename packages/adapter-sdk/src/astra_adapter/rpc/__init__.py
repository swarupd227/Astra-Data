"""The adapter RPC — an adapter runs out of process (S2.1.1).

REST over HTTP, per §5.4's "REST for adapters, gates and evidence". `create_app`/`serve` are
the adapter side, `RemoteAdapter` the platform side, and `AdapterSupervisor` keeps a child
process alive where there is no Kubernetes to do it.
"""

from __future__ import annotations

from .client import RemoteAdapter
from .server import create_app, serve
from .supervisor import AdapterProcess, AdapterSupervisor, SupervisedAdapter
from .wire import InterfaceMismatch, WireError

__all__ = [
    "AdapterProcess",
    "AdapterSupervisor",
    "InterfaceMismatch",
    "RemoteAdapter",
    "SupervisedAdapter",
    "WireError",
    "create_app",
    "serve",
]
