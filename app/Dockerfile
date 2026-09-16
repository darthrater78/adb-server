# ---- stage 1: fetch and verify Android tooling this container needs ----
# Full build-tools is ~65MB and includes renderscript/LLVM/d8 we don't need;
# apksigner and aapt need exactly four files between them (verified by hand
# against this exact release before writing this Dockerfile):
#   apksigner            - wrapper script, resolves lib/apksigner.jar relative to itself
#   lib/apksigner.jar
#   aapt                 - native binary
#   lib64/libc++.so       - aapt's only non-libc runtime dependency (RUNPATH: $ORIGIN/lib64)
# `adb` itself (from platform-tools, same verified source as adb-server/Dockerfile)
# is also needed here: adb_client.py shells out to a local `adb` binary acting
# as a TCP client against the adb-server container — the protocol has no
# pure-Python client, the `adb` executable *is* the client.
FROM debian:bookworm-slim AS android-tools

ARG BUILD_TOOLS_URL=https://dl.google.com/android/repository/build-tools_r37_linux.zip
ARG BUILD_TOOLS_SHA1=70954e99f4c3d9d46ee70fa32624672fe7cd6ebe
ARG PLATFORM_TOOLS_URL=https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip
ARG PLATFORM_TOOLS_SHA1=477254aa5f903c15cf51001717bdf347fb6b53e0

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/build-tools.zip "$BUILD_TOOLS_URL" \
    && echo "${BUILD_TOOLS_SHA1}  /tmp/build-tools.zip" | sha1sum -c - \
    && unzip -q /tmp/build-tools.zip -d /opt/extracted \
    && rm /tmp/build-tools.zip \
    && BT_DIR=$(find /opt/extracted -maxdepth 1 -mindepth 1 -type d) \
    && mkdir -p /opt/build-tools/lib /opt/build-tools/lib64 \
    && cp "$BT_DIR/apksigner" /opt/build-tools/ \
    && cp "$BT_DIR/lib/apksigner.jar" /opt/build-tools/lib/ \
    && cp "$BT_DIR/aapt" /opt/build-tools/ \
    && cp "$BT_DIR/lib64/libc++.so" /opt/build-tools/lib64/ \
    && chmod +x /opt/build-tools/apksigner /opt/build-tools/aapt \
    && rm -rf /opt/extracted

RUN curl -fsSL -o /tmp/platform-tools.zip "$PLATFORM_TOOLS_URL" \
    && echo "${PLATFORM_TOOLS_SHA1}  /tmp/platform-tools.zip" | sha1sum -c - \
    && unzip -q /tmp/platform-tools.zip -d /opt \
    && cp /opt/platform-tools/adb /opt/build-tools/adb \
    && chmod +x /opt/build-tools/adb \
    && rm -rf /tmp/platform-tools.zip /opt/platform-tools

# ---- stage 2: runtime image ----
FROM python:3.13-slim

# apksigner is a JAR run under `java`; aapt is a standalone native binary and
# needs no JRE.
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=android-tools /opt/build-tools /opt/build-tools
ENV PATH="/opt/build-tools:${PATH}"

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY VERSION .
RUN mkdir -p /data/staging && chown -R appuser:appuser /data /app

USER appuser
EXPOSE 8080

# Single worker is required: APScheduler's poll job is scheduled in-process
# with max_instances=1, which only prevents overlapping runs within one
# worker. A second worker would run its own independent scheduler and double
# every poll and every staged download.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
