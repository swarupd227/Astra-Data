# The Migration Console: a static SPA behind nginx (spec §5.2, "console-web | SPA | static").
#
# Two stages. The build stage compiles TypeScript and bundles; the runtime stage is nginx
# with the bundle and nothing else — no Node, no package manager, no source. A console
# image that shipped its own build toolchain would carry a much larger attack surface than
# the thing it serves, which is a folder of files.

FROM node:22-alpine AS build

WORKDIR /build

# Dependencies first, so a source change does not re-resolve the tree.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

# The build type-checks. A console that compiled with type errors would be one whose
# API client had already drifted from the service it calls.
ARG VITE_ASTRA_ENV=production
ENV VITE_ASTRA_ENV=${VITE_ASTRA_ENV}
RUN npm run build


FROM nginx:1.27-alpine AS runtime

# nginx's own default site would serve its welcome page at / and 404 every deep link.
RUN rm /etc/nginx/conf.d/default.conf
# A template, not a static file: the base image's own entrypoint envsubst's
# /etc/nginx/templates/*.template into /etc/nginx/conf.d/ at container start, so
# $GRAPH_SVC_UPSTREAM (default below, matching the compose service name locally) can be
# overridden per deployment without a rebuild. $DNS_RESOLVER's own default is this
# platform's own deployed Azure Container Apps environment (story S5.3.2); local Docker
# Compose overrides it (see docker-compose.yml's own console-web service) to Docker's own
# embedded resolver, since Azure's is unreachable and meaningless outside Azure.
COPY nginx.conf /etc/nginx/templates/console.conf.template
ENV GRAPH_SVC_UPSTREAM=http://graph-svc:8080
ENV GRAPH_SVC_HOST=graph-svc
ENV DNS_RESOLVER=168.63.129.16
COPY --from=build /build/dist /usr/share/nginx/html

# The image runs unprivileged. nginx:alpine ships an `nginx` user; the directories it
# writes to at runtime are chowned so it does not need to start as root to do it —
# including conf.d now, since the entrypoint's own envsubst step writes the rendered
# template there as this same non-root user.
RUN touch /var/run/nginx.pid \
 && chown -R nginx:nginx /var/run/nginx.pid /var/cache/nginx /usr/share/nginx/html /etc/nginx/conf.d

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
