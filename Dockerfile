# Noul Search HTTP server (MCP at /mcp, OpenAPI at /openapi.json) for Google Cloud Run or any container host.
# Public read-only connector: NOUL_PUBLIC=1 (no token, writes off, rate/concurrency limits, request caps;
# behind Cloud Run also NOUL_TRUSTED_PROXY=1) - see docs/deploy/cloud-run.md.
# Private server: set NOUL_HTTP_TOKEN (Bearer auth) and NOUL_CORS_ORIGINS; write tools stay disabled unless
# NOUL_HTTP_ALLOW_WRITE=1 *and* a token are set. /healthz is unauthenticated.
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOUL_HOME=/tmp/noul-cache \
    NOUL_WORKSPACE=/tmp/noul-workspace \
    NOUL_HTTP_HOST=0.0.0.0 \
    PORT=8080

# uv only exports the lockfile and is not left in the image; it is pinned by digest. The base image stays on its
# tag so a rebuild picks up Debian and CPython security fixes.
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc \
    /uv /tmp/uv

WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# Templates installed as data-files by the wheel (used by noul-install).
COPY skills ./skills
COPY agents ./agents
COPY commands ./commands
# Runtime dependencies at the versions and hashes of uv.lock (the image then matches the tested environment),
# then the project itself without resolving anything again.
RUN /tmp/uv export --frozen --no-dev --no-emit-project --no-header -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps . \
    && rm /tmp/uv /tmp/requirements.txt \
    && useradd --create-home --uid 10001 noul \
    && mkdir -p /tmp/noul-cache /tmp/noul-workspace \
    && chown noul:noul /tmp/noul-cache /tmp/noul-workspace \
    && rm -rf /src

USER noul
WORKDIR /tmp/noul-workspace
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'), timeout=3)"
CMD ["noul-mcp", "--http"]
