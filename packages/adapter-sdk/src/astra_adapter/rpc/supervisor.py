"""Running an adapter as a child process, and keeping it running.

S2.1.1's second criterion has two halves. The first — adapters speak to the platform over the
adapter RPC — is `client.py` and `server.py`. The second is *"an adapter crash does not take
down a worker"*, and that is this module: something has to notice the process died and start
it again, or the isolation only means the platform survives to fail on every subsequent call.

**In production this is Kubernetes.** §5.4 runs adapters as isolated pods; a pod that dies is
restarted by the kubelet and the platform reconnects to the service address. The supervisor
here is for the local stack, for CI, and for the conformance runner — the places where there
is no orchestrator and an adapter still has to be launched, watched and restarted. It is
deliberately small: no backoff policy worth tuning, no health-based eviction, because a
second scheduler competing with Kubernetes is a worse outcome than no scheduler at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import sys
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from ..contract import AdapterError, AdapterManifest
from .client import RemoteAdapter


def free_port() -> int:
    """A port the OS says is free. Racy by nature — something else can take it between this
    call and the child binding it — which is why ``start`` retries rather than assuming."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass(slots=True)
class AdapterProcess:
    """One adapter running as a child process."""

    name: str
    command: Sequence[str]
    port: int
    process: asyncio.subprocess.Process | None = None
    restarts: int = 0
    log: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def exit_code(self) -> int | None:
        return self.process.returncode if self.process else None


class AdapterSupervisor:
    """Launches an adapter worker, waits for it to serve, and restarts it if it dies.

    ``max_restarts`` is a bound rather than a retry policy: an adapter that crashes on the
    first call of every run is broken, and restarting it forever converts a loud failure into
    a harvest that never finishes. When the bound is reached the supervisor stops and says
    so, and the platform's own per-asset error handling reports what is left.
    """

    def __init__(
        self,
        name: str,
        command: Sequence[str] | None = None,
        *,
        max_restarts: int = 3,
        startup_timeout: float = 20.0,
    ) -> None:
        self.name = name
        self._command = list(command) if command else self._default_command(name)
        self._max_restarts = max_restarts
        self._startup_timeout = startup_timeout
        self._proc: AdapterProcess | None = None

    @staticmethod
    def _default_command(name: str) -> list[str]:
        """Launch through this interpreter, so a virtual environment is inherited.

        ``sys.executable -m astra_adapter.serve`` rather than the ``astra-adapter-serve``
        console script: the script is on PATH only if the SDK was installed with its entry
        points, and a supervisor that works from a source checkout is worth more here than
        one that insists on a packaging step.
        """
        return [sys.executable, "-m", "astra_adapter.serve", "--adapter", name]

    # ---------------------------------------------------------------- lifecycle

    async def start(self, *, attempts: int = 3) -> AdapterProcess:
        last: Exception | None = None
        for _ in range(attempts):
            port = free_port()
            proc = AdapterProcess(name=self.name, command=self._command, port=port)
            proc.process = await asyncio.create_subprocess_exec(
                *self._command,
                "--port",
                str(port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                await self._await_ready(proc)
            except Exception as exc:
                last = exc
                await self._terminate(proc)
                continue
            self._proc = proc
            return proc
        raise AdapterError(
            f"adapter {self.name!r} did not start after {attempts} attempts: {last}",
            retryable=False,
        )

    async def _await_ready(self, proc: AdapterProcess) -> None:
        """Poll ``/healthz`` until it answers, or the child exits, or time runs out.

        Polling rather than a fixed sleep: an adapter that imports a large grammar takes
        longer to serve than one that does not, and a sleep long enough for the slowest is
        wasted on every run of the fastest.
        """
        deadline = asyncio.get_running_loop().time() + self._startup_timeout
        remote = RemoteAdapter(proc.base_url, timeout=2.0)
        try:
            while asyncio.get_running_loop().time() < deadline:
                if not proc.alive:
                    raise AdapterError(
                        f"adapter {self.name!r} exited with code {proc.exit_code} before "
                        f"serving: {await self._drain(proc)}",
                        retryable=False,
                    )
                with contextlib.suppress(AdapterError):
                    await remote._get("/healthz")
                    return
                await asyncio.sleep(0.05)
        finally:
            await remote.aclose()
        raise AdapterError(
            f"adapter {self.name!r} did not answer /healthz within {self._startup_timeout}s",
            retryable=True,
        )

    async def _drain(self, proc: AdapterProcess) -> str:
        """The child's output, for the error message. A crash with no output attached is a
        crash somebody has to reproduce to understand."""
        if proc.process is None or proc.process.stdout is None:
            return ""
        with contextlib.suppress(Exception):
            data = await asyncio.wait_for(proc.process.stdout.read(4096), timeout=1.0)
            text = data.decode(errors="replace").strip()
            if text:
                proc.log.append(text)
            return text
        return ""

    async def ensure_running(self) -> AdapterProcess:
        """Restart the adapter if it has died. Returns the process now serving."""
        if self._proc is not None and self._proc.alive:
            return self._proc

        previous = self._proc
        if previous is None:
            # Starting for the first time is not a restart. Counting it as one made
            # ``max_restarts=1`` refuse the first genuine restart, which is the opposite of
            # what the bound is for.
            return await self.start()

        restarts = previous.restarts + 1
        if restarts > self._max_restarts:
            raise AdapterError(
                f"adapter {self.name!r} has crashed {restarts - 1} times "
                f"(limit {self._max_restarts}); it is broken, not unlucky",
                retryable=False,
            )
        if previous is not None:
            await self._drain(previous)
        proc = await self.start()
        proc.restarts = restarts
        if previous is not None:
            proc.log = [*previous.log, *proc.log]
        return proc

    async def stop(self) -> None:
        if self._proc is not None:
            await self._terminate(self._proc)
            self._proc = None

    async def _terminate(self, proc: AdapterProcess) -> None:
        if proc.process is None or proc.process.returncode is not None:
            return
        proc.process.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.process.wait(), timeout=5.0)
        if proc.process.returncode is None:
            proc.process.kill()
            with contextlib.suppress(Exception):
                await proc.process.wait()

    # ------------------------------------------------------------------ using it

    @contextlib.asynccontextmanager
    async def adapter(self) -> AsyncIterator[RemoteAdapter]:
        """A connected `RemoteAdapter` for the supervised process."""
        proc = await self.ensure_running()
        remote = RemoteAdapter(proc.base_url)
        try:
            await remote.connect()
            yield remote
        finally:
            await remote.aclose()

    async def manifest(self) -> AdapterManifest:
        async with self.adapter() as remote:
            return remote.manifest()


class SupervisedAdapter:
    """A `SourceAdapter` that restarts its worker when it dies.

    This is the object that makes "an adapter crash does not take down a worker" true for
    the *caller* rather than only for the operating system. Each call goes to the running
    process; if that process has died, the next call restarts it and proceeds. The call that
    was in flight when it died still fails — its bytes are gone and inventing a result would
    be worse than reporting one failure — and it fails as a retryable ``AdapterError``
    against one asset, which is what the Harvester already records and carries on from.
    """

    def __init__(self, supervisor: AdapterSupervisor) -> None:
        self._supervisor = supervisor
        self._remote: RemoteAdapter | None = None

    async def _connected(self) -> RemoteAdapter:
        proc = await self._supervisor.ensure_running()
        if self._remote is None or self._remote._base != proc.base_url:
            if self._remote is not None:
                await self._remote.aclose()
            self._remote = RemoteAdapter(proc.base_url)
            await self._remote.connect()
        return self._remote

    def manifest(self) -> AdapterManifest:
        if self._remote is None:
            raise AdapterError("not connected yet; await one call first", retryable=False)
        return self._remote.manifest()

    async def enumerate(self, scope: object) -> AsyncIterator[object]:
        remote = await self._connected()
        async for ref in remote.enumerate(scope):  # type: ignore[arg-type]
            yield ref

    def __getattr__(self, name: str) -> object:
        """Forward the rest of §6.1 to the connected process.

        Written as forwarding rather than as ten near-identical methods because every one of
        them would be the same three lines, and ten copies of three lines is ten places for
        the reconnect to be forgotten.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*args: object, **kwargs: object) -> object:
            remote = await self._connected()
            method = getattr(remote, name)
            return await method(*args, **kwargs)

        return call

    async def aclose(self) -> None:
        if self._remote is not None:
            await self._remote.aclose()
            self._remote = None
        await self._supervisor.stop()
