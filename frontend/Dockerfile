# syntax=docker/dockerfile:1.7
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

FROM deps AS build
COPY . .
RUN npm run build

# The "deploy" context is supplied by compose (additional_contexts) so the Caddyfile lives in deploy/.
FROM caddy:2-alpine AS web
COPY --from=deploy Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv/www
# Same uid as the backend user so the shared photos volume stays writable by the backend.
RUN mkdir -p /srv/media && chown 10001:10001 /srv/media
EXPOSE 80 443
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "--spider", "http://127.0.0.1/"]
