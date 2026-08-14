#!/bin/sh
# Substitutes the backend service hostnames into nginx.conf at container
# start (not build time) so the same image works unmodified in
# docker-compose (service names) and k8s (Service DNS names) — just
# different env vars, never a rebuild.
set -e

: "${INTERCEPTOR_UPSTREAM:=interceptor:4001}"
: "${AGGREGATOR_UPSTREAM:=aggregator:4002}"

sed -i \
    -e "s/__INTERCEPTOR_UPSTREAM__/${INTERCEPTOR_UPSTREAM}/g" \
    -e "s/__AGGREGATOR_UPSTREAM__/${AGGREGATOR_UPSTREAM}/g" \
    /etc/nginx/conf.d/default.conf

exec nginx -g "daemon off;"
