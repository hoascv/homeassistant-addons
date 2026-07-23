ARG BUILD_FROM
FROM $BUILD_FROM

ARG TARGETARCH

WORKDIR /app

COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Advanced-forecast deps (statsmodels) only have musllinux wheels for
# amd64 and manylinux (glibc) wheels for amd64/aarch64 — no musllinux
# aarch64 wheel exists, which is why the base image above is Debian, not
# Alpine. armv7/armhf/i386 have no wheel at all under any libc. Skipped on
# other architectures rather than failing the whole build; the app
# detects the absence via an import guard (app.py) and reports the
# feature unavailable instead.
COPY app/requirements-advanced.txt .
RUN if [ "$TARGETARCH" = "amd64" ] || [ "$TARGETARCH" = "arm64" ]; then \
      pip3 install --no-cache-dir -r requirements-advanced.txt; \
    fi

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
