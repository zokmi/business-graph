FROM python:3.11-slim AS build
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels business-graph-mcp \
    && rm -rf /wheels \
    && groupadd --gid 10001 bgraph \
    && useradd --uid 10001 --gid 10001 --no-create-home bgraph \
    && mkdir /data \
    && chown 10001:10001 /data
USER 10001:10001
WORKDIR /data
EXPOSE 8000
ENTRYPOINT ["business-graph-mcp"]
