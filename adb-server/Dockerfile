# Builds `adb` from Google's official, checksum-verified platform-tools release
# rather than trusting a third-party pre-built image with the private key that
# will end up holding every paired device's trust relationship.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS fetch

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

FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin adb

COPY --from=fetch /opt/platform-tools/adb /usr/local/bin/adb

# adbkey/adbkey.pub (the identity every paired device trusts) and paired-device
# state live here, on the named volume mounted over this directory — losing it
# means every device needs to be re-paired.
RUN mkdir -p /home/adb/.android /adbinfo && chown -R adb:adb /home/adb/.android /adbinfo

# The image's version, published into /adbinfo on start (see the entrypoint).
# Built from the repo root, like the app image, so both read the same VERSION.
COPY VERSION /etc/adb-server-version
COPY --chmod=755 adb-server/entrypoint.sh /usr/local/bin/adb-server-entrypoint

USER adb
EXPOSE 5037

CMD ["adb-server-entrypoint"]
