# Multi-stage: build the static Vite bundle with Node, serve it with nginx.
# nginx also reverse-proxies /api/* and /ws/live/* to the backend services —
# see nginx.conf, which mirrors vite.config.ts's dev proxy rules exactly so
# the app talks to the backend the same way in every environment.
FROM node:22-slim AS builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q -O- http://localhost:8080/ || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
