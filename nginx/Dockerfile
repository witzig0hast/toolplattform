FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY portal /srv/portal
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q --spider --header 'X-Authentik-Username: healthcheck' http://127.0.0.1:8080/assets/style.css || exit 1
