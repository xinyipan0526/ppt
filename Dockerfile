FROM node:24-alpine AS web-build

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
RUN npm run build

FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web-build /web/dist ./src/ppt_agent/static
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir . "playwright==1.63.0"

RUN useradd --create-home --uid 10001 pptagent \
    && mkdir -p /data \
    && chown -R pptagent:pptagent /data /app
USER pptagent

ENV PPT_AGENT_WORKSPACE=/data PPT_AGENT_HOST=0.0.0.0 PPT_AGENT_PORT=8000
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["ppt-agent"]
CMD ["serve"]
