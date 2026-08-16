# Base image named directly rather than via BUILD_FROM/build.yaml. Supervisor
# 2026.04.0 stopped passing BUILD_FROM and now warns that build.yaml is
# deprecated — and an unpassed ARG makes `FROM $BUILD_FROM` an empty base, so
# this is a build failure waiting for the next rebuild, not just a warning. The
# tag is a multi-arch manifest and covers both arches in config.yaml, which is
# all build.yaml was doing: it named the same image for each.
FROM docker.io/library/python:3.12-slim-bookworm

WORKDIR /app

COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
