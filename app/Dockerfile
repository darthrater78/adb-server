# ---- stage 1: fetch and verify Android tooling this container needs ----
# Full build-tools is ~65MB and includes renderscript/LLVM/d8 we don't need;
# apksigner, aapt2 and zipalign need exactly five files between them:
#   apksigner            - wrapper script, resolves lib/apksigner.jar relative to itself
#   lib/apksigner.jar
#   aapt2                - native binary, links nothing beyond libc/libm/libgcc_s
#   zipalign             - native binary, for signing an unsigned APK the operator opted in to
#   lib64/libc++.so      - zipalign's only non-libc dependency (RUNPATH: $ORIGIN/lib64)
# aapt2, not aapt: the legacy aapt's `dump badging` fails outright on APKs
# built against current SDKs ("ERROR getting 'android:icon'"), while aapt2
# prints the same badging output and exits cleanly.
# `adb` itself (from platform-tools, same verified source as adb-server/Dockerfile)
# is also needed here: adb_client.py shells out to a local `adb` binary acting
# as a TCP client against the adb-server container — the protocol has no
# pure-Python client, the `adb` executable *is* the client.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS android-tools

# Google's repository manifest publishes only SHA-1. These SHA-256 pins were
# computed from downloads that matched those SHA-1s (70954e99…, 477254aa…),
# so a SHA-1 collision can't substitute a different archive.
ARG BUILD_TOOLS_URL=https://dl.google.com/android/repository/build-tools_r37_linux.zip
ARG BUILD_TOOLS_SHA256=01af179347cbcd9c208b7f8171f7b21f6dd1d2f85bcd15e88caa51d5d7b86060
ARG PLATFORM_TOOLS_URL=https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip
ARG PLATFORM_TOOLS_SHA256=d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/build-tools.zip "$BUILD_TOOLS_URL" \
    && echo "${BUILD_TOOLS_SHA256}  /tmp/build-tools.zip" | sha256sum -c - \
    && unzip -q /tmp/build-tools.zip -d /opt/extracted \
    && rm /tmp/build-tools.zip \
    && BT_DIR=$(find /opt/extracted -maxdepth 1 -mindepth 1 -type d) \
    && mkdir -p /opt/build-tools/lib /opt/build-tools/lib64 \
    && cp "$BT_DIR/apksigner" /opt/build-tools/ \
    && cp "$BT_DIR/lib/apksigner.jar" /opt/build-tools/lib/ \
    && cp "$BT_DIR/aapt2" "$BT_DIR/zipalign" /opt/build-tools/ \
    && cp "$BT_DIR/lib64/libc++.so" /opt/build-tools/lib64/ \
    && chmod +x /opt/build-tools/apksigner /opt/build-tools/aapt2 /opt/build-tools/zipalign \
    && rm -rf /opt/extracted

RUN curl -fsSL -o /tmp/platform-tools.zip "$PLATFORM_TOOLS_URL" \
    && echo "${PLATFORM_TOOLS_SHA256}  /tmp/platform-tools.zip" | sha256sum -c - \
    && unzip -q /tmp/platform-tools.zip -d /opt \
    && cp /opt/platform-tools/adb /opt/build-tools/adb \
    && chmod +x /opt/build-tools/adb \
    && rm -rf /tmp/platform-tools.zip /opt/platform-tools

# ---- stage 2: runtime image ----
FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0

# apksigner (and keytool, for this server's own signing key) run under the
# JRE; aapt2 and zipalign are standalone native binaries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=android-tools /opt/build-tools /opt/build-tools
ENV PATH="/opt/build-tools:${PATH}"

WORKDIR /app
# requirements.txt is a lockfile: every package, direct and transitive, pinned
# with its hashes (compiled from requirements.in), so the image installs
# exactly the tree CI audited and nothing else.
COPY app/requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

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
