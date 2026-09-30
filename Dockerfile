# Published image: ghcr.io/ehsanhajian/rpcbench
# docker run --rm -v "$PWD:/work" -w /work ghcr.io/ehsanhajian/rpcbench:latest \
#   compare --endpoints endpoints.yaml --out-dir out --ci --max-p95 500

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/ehsanhajian/RPCBench"
LABEL org.opencontainers.image.description="RPCBench: compare JSON-RPC/REST endpoints"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && rm -rf /src /root/.cache/pip

WORKDIR /work
ENTRYPOINT ["rpcbench"]
CMD ["--help"]
