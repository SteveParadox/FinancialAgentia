# Build from repository root: docker build -f python-backend/Dockerfile .
FROM oven/bun:1.4.3 AS web
WORKDIR /workspace
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production
COPY web ./web
COPY scripts/build-web.ts ./scripts/build-web.ts
COPY src/services/research-contract.ts ./src/services/research-contract.ts
RUN bun run build:web

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WEB_DIST_DIR=/app/web
WORKDIR /app
COPY python-backend/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 && pip install --no-cache-dir -r /app/requirements.txt && useradd --create-home runtime
COPY python-backend/app /app/app
COPY python-backend/dexter_py /app/dexter_py
COPY --from=web /workspace/dist/web /app/web
USER runtime
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
