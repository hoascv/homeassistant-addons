FROM docker.io/library/alpine:3.20

# MinIO server + client are static Go binaries, so they run fine on Alpine.
# "latest" release is fetched at build time. bash/jq drive the entrypoint.
# nginx fronts the console for Home Assistant's ingress sidebar — see
# nginx.conf.template for why it's needed at all.
# hadolint ignore=DL3018,DL4006
RUN apk add --no-cache bash ca-certificates curl jq nginx \
    && curl -fsSL https://dl.min.io/server/minio/release/linux-amd64/minio -o /usr/bin/minio \
    && curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/bin/mc \
    && chmod +x /usr/bin/minio /usr/bin/mc

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
