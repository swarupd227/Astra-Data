#!/usr/bin/env python
"""Build, sign and submit a deployment's bill of materials — story S11.1.1, spec §18.1.

    "A deployment produces a signed bill of materials (images, versions, chart values)
    stored as an evidence record."

Run this as the last step of a deployment pipeline, after `helm upgrade --install`
succeeds: it reads the images and chart metadata that were actually deployed, signs them
with the deployment's own Ed25519 private key, and POSTs the signed envelope to
`POST /v1/deployment/bom` where it is stored as evidence (`bom.py`, `routes_deployment_
bom.py`). The private key never leaves this invocation — see `bom.py`'s own module
docstring for why that boundary matters here.

    # once, when a deployment is first set up — keep the private key in Key Vault / CI
    # secrets, publish the public key as ASTRA_BOM_PUBLIC_KEY_PEM on graph-svc
    python tools/generate_bom.py generate-keypair --out-dir ./bom-keys

    # after every deployment
    python tools/generate_bom.py submit \\
        --api-base-url https://graph.internal.example \\
        --private-key ./bom-keys/private.pem \\
        --chart-name astra-data --chart-version 0.1.0 --app-version 0.1.0 \\
        --git-commit "$(git rev-parse HEAD)" \\
        --image graph-svc=myregistry.azurecr.io/astra/graph-svc:0.1.0@sha256:... \\
        --image console-web=myregistry.azurecr.io/astra/console-web:0.1.0@sha256:... \\
        --values-file deploy/helm/astra-data/values.yaml \\
        --principal service:deploy-pipeline

Never run against a live deployment yet — see `bom.py`'s own disclosure. `submit` also
accepts `--out` to write the signed envelope to a file instead of (or as well as) POSTing
it, for a pipeline that wants to keep its own copy before the console can be reached.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

import httpx  # noqa: E402
import yaml  # noqa: E402

from astra_graph import bom  # noqa: E402


def _cmd_generate_keypair(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / "private.pem"
    public_path = out_dir / "public.pem"
    if private_path.exists() and not args.force:
        print(f"{private_path} already exists; pass --force to overwrite", file=sys.stderr)
        return 1

    private_pem, public_pem = bom.generate_signing_keypair()
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    print(f"private key: {private_path} — keep this in Key Vault / CI secrets, never here")
    print(f"public key:  {public_path} — publish as ASTRA_BOM_PUBLIC_KEY_PEM on graph-svc")
    return 0


def _parse_image(raw: str) -> bom.ImageRef:
    """``name=repository:tag@digest``."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--image {raw!r} must be 'name=repository:tag@digest'")
    name, ref = raw.split("=", 1)
    if "@" not in ref:
        raise argparse.ArgumentTypeError(f"--image {raw!r} must carry a '@sha256:...' digest")
    repo_tag, digest = ref.rsplit("@", 1)
    repository, _, tag = repo_tag.rpartition(":")
    if not repository or not tag:
        raise argparse.ArgumentTypeError(f"--image {raw!r} must carry a ':tag'")
    return bom.ImageRef(name=name, repository=repository, tag=tag, digest=digest)


def _cmd_submit(args: argparse.Namespace) -> int:
    images = [_parse_image(raw) for raw in args.image]
    values_text = Path(args.values_file).read_text(encoding="utf-8") if args.values_file else "{}"
    chart_values = yaml.safe_load(values_text) or {}

    document = bom.build_document(
        chart_name=args.chart_name,
        chart_version=args.chart_version,
        app_version=args.app_version,
        git_commit=args.git_commit,
        images=images,
        chart_values=chart_values,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        generated_by=args.principal,
    )
    private_key_pem = Path(args.private_key).read_bytes()
    signature = bom.sign_document(document, private_key_pem)
    envelope = {"document": document.as_dict(), "signature": signature}

    if args.out:
        Path(args.out).write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")

    if args.api_base_url:
        response = httpx.post(
            f"{args.api_base_url.rstrip('/')}/v1/deployment/bom",
            json=envelope,
            headers={"X-Astra-Principal": args.principal, "X-Astra-Roles": args.roles},
            timeout=30.0,
        )
        response.raise_for_status()
        result = response.json()
        print(f"stored: {result.get('id')} (signature_verified={result.get('signature_verified')})")
    elif not args.out:
        print("neither --api-base-url nor --out was given; nothing was recorded", file=sys.stderr)
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    keypair = subparsers.add_parser("generate-keypair", help="Generate the Ed25519 signing keypair")
    keypair.add_argument("--out-dir", default="./bom-keys")
    keypair.add_argument("--force", action="store_true")
    keypair.set_defaults(func=_cmd_generate_keypair)

    submit = subparsers.add_parser("submit", help="Build, sign and submit a bill of materials")
    submit.add_argument("--chart-name", required=True)
    submit.add_argument("--chart-version", required=True)
    submit.add_argument("--app-version", required=True)
    submit.add_argument("--git-commit", required=True)
    submit.add_argument("--image", action="append", required=True, dest="image", metavar="name=repo:tag@digest")
    submit.add_argument("--values-file", default=None)
    submit.add_argument("--private-key", required=True)
    submit.add_argument("--principal", default="service:deploy-pipeline")
    submit.add_argument(
        "--roles",
        default="platform_engineer",
        help="X-Astra-Roles (ArtizentDep gates POST /v1/deployment/bom -- see routes_deployment_bom.py)",
    )
    submit.add_argument("--api-base-url", default=None)
    submit.add_argument("--out", default=None)
    submit.set_defaults(func=_cmd_submit)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
