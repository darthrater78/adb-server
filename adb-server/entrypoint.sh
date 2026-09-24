#!/bin/sh
# Publishes this image's version where the app can read it (ADB_INFO_DIR, a
# directory the app mounts read-only), so the app can warn when the two
# containers come from different releases. Then runs the adb server.
# A missing mount is logged, never fatal: the app then says it can't tell.
set -eu
info="${ADB_INFO_DIR:-/adbinfo}"
if [ -d "$info" ] && [ -w "$info" ]; then
  # rm first: cp would follow a symlink left at the temp name. mv replaces a
  # symlink at "version" itself rather than following it.
  rm -f "$info/.version.tmp"
  cp /etc/adb-server-version "$info/.version.tmp"
  mv -f "$info/.version.tmp" "$info/version"
else
  echo "adb-server: $info isn't a writable mount, so the app can't check this image's version" >&2
fi
# `-a` binds the server on all interfaces *inside this container* so the `app`
# service can reach it by DNS name over the compose network — it does not by
# itself expose anything to the host or LAN. See the warning in compose.yaml
# before ever changing this service to host networking.
exec adb -a -P 5037 server nodaemon
