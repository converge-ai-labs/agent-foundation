# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS builder
WORKDIR /src/frontend
COPY frontend/ ./
RUN npm install --global "$(node -p 'require("./package.json").packageManager')" \
    && pnpm install --frozen-lockfile
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
