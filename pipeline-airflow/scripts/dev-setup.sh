#!/usr/bin/env bash
# Prepare a local environment for the pipeline tests.
#
#   ./scripts/dev-setup.sh && .venv/bin/python -m pytest
#
# Creates .venv, installs requirements-dev.txt, and — only if there is no
# working JVM — fetches a Temurin JDK into .jdk. Both are gitignored and neither
# touches anything outside this directory: the Spark tests need a JVM, but
# needing one should not mean installing Java system-wide to run a test suite.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "==> creating .venv"
  "$PY" -m venv .venv
fi
echo "==> installing requirements-dev.txt (pyspark is large; this takes a minute)"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements-dev.txt

# `java` may exist and still not run: macOS ships a stub that reports "Unable to
# locate a Java Runtime". Run it rather than trusting the path.
if java -version >/dev/null 2>&1; then
  echo "==> JVM already available: $(java -version 2>&1 | head -1)"
elif [ -x .jdk/bin/java ] || [ -x .jdk/Contents/Home/bin/java ]; then
  echo "==> using the JDK already in .jdk"
else
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64)  OS=mac;   ARCH=aarch64 ;;
    Darwin/x86_64) OS=mac;   ARCH=x64 ;;
    Linux/aarch64) OS=linux; ARCH=aarch64 ;;
    Linux/x86_64)  OS=linux; ARCH=x64 ;;
    *) echo "!! unknown platform $(uname -s)/$(uname -m); install a JDK 17 yourself"; exit 1 ;;
  esac
  echo "==> fetching a Temurin JDK 17 for $OS/$ARCH into .jdk"
  mkdir -p .jdk
  curl -fsSL -o /tmp/pipeline-jdk.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/17/ga/${OS}/${ARCH}/jdk/hotspot/normal/eclipse"
  tar -xzf /tmp/pipeline-jdk.tar.gz -C .jdk --strip-components=1
  rm -f /tmp/pipeline-jdk.tar.gz
fi

# Where JAVA_HOME lands differs between the macOS bundle layout and Linux.
if [ -x .jdk/Contents/Home/bin/java ]; then
  JH="$PWD/.jdk/Contents/Home"
elif [ -x .jdk/bin/java ]; then
  JH="$PWD/.jdk"
else
  JH=""
fi

echo
echo "Ready. Run the tests with:"
if [ -n "$JH" ]; then
  echo "    JAVA_HOME=$JH PATH=\$JAVA_HOME/bin:\$PATH .venv/bin/python -m pytest"
else
  echo "    .venv/bin/python -m pytest"
fi
echo
echo "Without a JVM the Spark tests skip and the rest still run:"
echo "    .venv/bin/python -m pytest -m 'not spark'"
