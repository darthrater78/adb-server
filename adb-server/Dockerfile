# Builds `adb` from Google's official, checksum-verified platform-tools release
# rather than trusting a third-party pre-built image with the private key that
# will end up holding every paired device's trust relationship.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS fetch

# Google's repository manifest publishes only SHA-1. These SHA-256 pins were
# computed from downloads that matched those SHA-1s (70954e99…, 477254aa…),
# so a SHA-1 collision can't substitute a different archive.
ARG PLATFORM_TOOLS_URL=https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip
ARG PLATFORM_TOOLS_SHA256=d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/platform-tools.zip "$PLATFORM_TOOLS_URL" \
    && echo "${PLATFORM_TOOLS_SHA256}  /tmp/platform-tools.zip" | sha256sum -c - \
    && unzip -q /tmp/platform-tools.zip -d /opt \
    && rm /tmp/platform-tools.zip

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin adb

COPY --from=fetch /opt/platform-tools/adb /usr/local/bin/adb

# adbkey/adbkey.pub (the identity every paired device trusts) and paired-device
# state live here, on the named volume mounted over this directory — losing it
# means every device needs to be re-paired.
RUN mkdir -p /home/adb/.android && chown -R adb:adb /home/adb/.android

USER adb
EXPOSE 5037

# `-a` binds the server on all interfaces *inside this container* so the `app`
# service can reach it by DNS name over the compose network — it does not by
# itself expose anything to the host or LAN. See the warning in
# docker-compose.yml before ever changing this service to host networking.
CMD ["adb", "-a", "-P", "5037", "server", "nodaemon"]
