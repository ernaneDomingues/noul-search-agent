# Noul Search HTTP server (MCP at /mcp, OpenAPI at /openapi.json) for Google Cloud Run or any container host.
# Security: set NOUL_HTTP_TOKEN (Bearer auth) and NOUL_CORS_ORIGINS; write tools stay disabled unless
# NOUL_HTTP_ALLOW_WRITE=1 *and* a token are set. /healthz is unauthenticated.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOUL_HOME=/tmp/noul-cache \
    NOUL_WORKSPACE=/tmp/noul-workspace \
    NOUL_HTTP_HOST=0.0.0.0 \
    PORT=8080

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY skills ./skills
COPY agents ./agents
COPY commands ./commands
RUN pip install --no-cache-dir . \
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
