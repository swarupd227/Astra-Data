# Spec §5.4: services run as isolated pods with per-run ephemeral filesystems and
# default-deny egress. The image carries no shell tooling it does not need and runs as a
# non-root user.

FROM python:3.12-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# The adapter SDK is a sibling package in this repository rather than something on an index,
# so the image builds it from source. Build context is the repository root — see the
# `graph-svc` service in docker-compose.yml.
COPY packages/adapter-sdk /build/adapter-sdk
COPY services/graph-svc/pyproject.toml services/graph-svc/README.md ./
COPY services/graph-svc/src ./src
RUN python -m pip install --upgrade pip build \
 && python -m build --wheel --outdir /dist /build/adapter-sdk \
 && python -m build --wheel --outdir /dist


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin astra

# git: not installed. The target adapter (story S4.3.1) commits TMDL to the client's Git
# repository through Dulwich — a pure-Python Git implementation pulled in as a pip
# dependency (`astra-adapter-sdk[target]`) rather than an OS package this image would
# otherwise need `apt-get` to install, keeping "the image carries no shell tooling it does
# not need" true in the strongest sense: no shelling out to `git` at all.
COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install /tmp/*.whl && rm -f /tmp/*.whl

WORKDIR /app
COPY services/graph-svc/tools ./tools

USER astra
EXPOSE 8080

# Migrations are applied by an init container or a job, not by the service on start-up:
# several replicas racing to migrate is a failure mode worth designing out.
CMD ["uvicorn", "astra_graph.main:app", "--host", "0.0.0.0", "--port", "8080"]
