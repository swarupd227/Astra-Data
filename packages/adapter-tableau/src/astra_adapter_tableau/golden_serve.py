"""``astra-tableau-golden`` — serve the golden Tableau deployment.

§6.3 has the conformance suite run "in CI and on tenant enablement". The tenant-enablement
half points at a client's Tableau; the CI half needs a Tableau to point at, and this is it.

    astra-tableau-golden --port 8099
    ASTRA_TABLEAU_URL=http://127.0.0.1:8099 \
    ASTRA_TABLEAU_SITE=golden \
    ASTRA_TABLEAU_CREDENTIAL='{"kind":"personal_access_token","token_name":"astra","secret":"a-personal-access-token"}' \
    astra-adapter conformance --adapter tableau

It is a test double served as a real HTTP server on purpose. Running the suite against an
in-process fake would certify the adapter's logic; running it against a socket certifies the
adapter — its timeouts, its connection handling, its retries — which is what a tenant is
being asked to trust.
"""

from __future__ import annotations

import argparse

from .golden import estate


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="astra-tableau-golden",
        description="Serve the golden Tableau deployment the conformance corpus names (§6.3)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--workbooks", type=int, default=5)
    parser.add_argument("--cloud", action="store_true", help="report as Tableau Cloud")
    args = parser.parse_args(argv)

    deployment = estate(args.workbooks)
    deployment.cloud = args.cloud
    uvicorn.run(deployment.app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
