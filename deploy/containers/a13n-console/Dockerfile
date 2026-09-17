# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS builder
WORKDIR /src/frontend
COPY frontend/package.json ./
RUN --mount=type=cache,id=a13n-console-npm,target=/root/.npm \
    npm install --global --no-audit --no-fund "$(node -p 'require("./package.json").packageManager')"

# Only these workspace packages are needed by Console. Keep source changes out
# of the dependency layer and retain downloaded packages when manifests change.
COPY frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/apps/a13n-console/package.json ./apps/a13n-console/package.json
COPY frontend/packages/a13n-ui/package.json ./packages/a13n-ui/package.json
RUN --mount=type=cache,id=a13n-console-pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir=/pnpm/store --reporter=append-only

COPY frontend/tsconfig.base.json ./
COPY frontend/apps/a13n-console/ ./apps/a13n-console/
COPY frontend/packages/a13n-ui/ ./packages/a13n-ui/
COPY proto/a13n-service/openapi.json /src/proto/a13n-service/openapi.json
RUN pnpm --filter a13n-console build

FROM nginx:1.28-alpine AS runtime
COPY --from=builder /src/frontend/apps/a13n-console/dist /usr/share/nginx/html
COPY deploy/containers/a13n-console/nginx.conf.template /etc/a13n/nginx.conf.template
ENV A13N_CONTROL_UPSTREAM=http://127.0.0.1:8000 \
    A13N_CONNECTIVITY_UPSTREAM=http://127.0.0.1:8000
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/bin/sh", "-ec"]
CMD ["envsubst '${A13N_CONTROL_UPSTREAM} ${A13N_CONNECTIVITY_UPSTREAM}' < /etc/a13n/nginx.conf.template > /tmp/nginx.conf; exec nginx -c /tmp/nginx.conf -g 'daemon off;'"]
