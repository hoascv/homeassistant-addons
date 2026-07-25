ARG BUILD_FROM
FROM $BUILD_FROM

# MinIO server + client are static Go binaries, so they run fine on Alpine.
# "latest" release is fetched at build time. bash/jq drive the entrypoint.
# hadolint ignore=DL3018,DL4006
RUN apk add --no-cache bash ca-certificates curl jq \
    && curl -fsSL https://dl.min.io/server/minio/release/linux-amd64/minio -o /usr/bin/minio \
    && curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/bin/mc \
    && chmod +x /usr/bin/minio /usr/bin/mc

COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
