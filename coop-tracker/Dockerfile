ARG BUILD_FROM
FROM $BUILD_FROM

ARG TARGETARCH

WORKDIR /app

COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Advanced/heavy deps, two independent features sharing one install gate:
# - statsmodels (advanced forecast, §19): musllinux wheel for amd64, and a
#   manylinux (glibc) wheel for amd64/aarch64 — no musllinux aarch64 wheel,
#   which is why the base image above is Debian, not Alpine.
# - opencv-python-headless (egg photo analysis, §20): amd64/aarch64
#   manylinux wheels, none at all for armv7/armhf/i386, under any libc.
# Both skipped on other architectures rather than failing the whole build;
# the app detects each one's absence via its own import guard (app.py) and
# reports that feature unavailable instead, independently of the other.
COPY app/requirements-advanced.txt .
RUN if [ "$TARGETARCH" = "amd64" ] || [ "$TARGETARCH" = "arm64" ]; then \
      pip3 install --no-cache-dir -r requirements-advanced.txt; \
    fi

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
