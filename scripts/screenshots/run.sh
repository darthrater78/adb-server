#!/usr/bin/env bash
# Regenerates docs/screenshots/*.png from invented data, with GitHub mocked.
# Needs Docker and a host IP the browser container can reach:
#   bash scripts/screenshots/run.sh 10.0.0.252
# The app runs from an image built from this checkout, on tmpfs, and is
# removed afterwards; nothing touches real repos, devices or tokens.
set -euo pipefail
HOST_IP=${1:?usage: run.sh <host-ip>}
PORT=${PORT:-18190}
cd "$(dirname "$0")/../.."
HERE=scripts/screenshots
OUT=$(mktemp -d)
chmod 777 "$OUT"
NAME=adb-shots-$$
PASSWORD=$(openssl rand -hex 12)
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$OUT"; }
trap cleanup EXIT

docker build -q -t adb-server-shots/app -f app/Dockerfile . >/dev/null
docker run -d --name "$NAME" -p "$HOST_IP:$PORT:8080" --read-only \
  --tmpfs /tmp:size=64m --tmpfs /data:uid=10001,gid=10001,size=256m \
  --tmpfs /home/appuser/.android:size=1m,uid=10001,gid=10001,mode=0700 \
  -v "$PWD/$HERE:/shots:ro" --entrypoint python \
  -e SECRET_KEY="$(openssl rand -hex 32)" -e APP_USERNAME=admin -e APP_PASSWORD="$PASSWORD" \
  -e COOKIE_SECURE=false -e ALLOWED_HOSTS="$HOST_IP" -e DB_PATH=/data/app.db -e STAGING_ROOT=/data/staging \
  adb-server-shots/app /shots/mock_github.py >/dev/null
for _ in $(seq 1 40); do curl -sf "http://$HOST_IP:$PORT/healthz" >/dev/null && break; sleep 1; done
docker exec -i "$NAME" python - base < "$HERE/seed.py"

shoot() {
  docker run --rm --network host -v "$PWD/$HERE:/shots:ro" -v "$OUT:/out" \
    mcr.microsoft.com/playwright/python:v1.62.0-jammy \
    bash -c "pip install -q playwright==1.62.0 >/dev/null 2>&1 && python /shots/shoot.py http://$HOST_IP:$PORT /out '$PASSWORD' $1"
}
shoot base
docker exec -i "$NAME" python - mfa < "$HERE/seed.py"
shoot mfa
cp "$OUT"/*.png docs/screenshots/
ls docs/screenshots
