ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.27-alpine

# ---- build the static site ----
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ---- serve it with nginx, proxying /api to the backend ----
FROM ${NGINX_IMAGE}
# nginx renders these templates at start-up; only the variables named in the filter are substituted.
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/real-ip.inc.template /etc/nginx/templates/real-ip.inc.template
ENV BACKEND_UPSTREAM=backend:8000 REAL_IP_DIRECTIVES= NGINX_ENVSUBST_FILTER=^(BACKEND_UPSTREAM|REAL_IP_DIRECTIVES)$
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/healthz || exit 1
