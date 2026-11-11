#!/usr/bin/env bash
# Build the web UI and package UI + API into one zip for App Service.
#   deploy/package.sh [output.zip]      (default: build/cnat-app.zip)
# Used by azure-pipelines.yml and by deploy/azure.sh deploy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ZIP="${1:-$ROOT/build/cnat-app.zip}"
OUT="$ROOT/build/azure"

echo "==> Build web UI"
npm --prefix "$ROOT/webapp" ci --no-audit --no-fund
npm --prefix "$ROOT/webapp" run build

echo "==> Package (no .env, keys, local databases or tests)"
rm -rf "$OUT" && mkdir -p "$OUT" "$(dirname "$ZIP")"
EXCLUDES=(--exclude '.env' --exclude 'keys/' --exclude '*.pem' --exclude '*.db' --exclude 'tests/'
          --exclude '__pycache__/' --exclude '*.egg-info/' --exclude 'build/' --exclude '.pytest_cache/')
rsync -a "${EXCLUDES[@]}" "$ROOT/engine" "$ROOT/api" "$ROOT/rulesets" "$OUT/"
rsync -a "$ROOT/webapp/dist/" "$OUT/web/"
# App Service (Oryx) installs these during deployment.
printf './engine\n./api[azure-sql]\n' > "$OUT/requirements.txt"

if grep -rlE 'BEGIN (RSA |EC )?PRIVATE KEY|CLIENT_SECRET=.+' "$OUT" >/dev/null 2>&1; then
  echo "Refusing to package: a secret was found."; exit 1
fi

ZIP="$(cd "$(dirname "$ZIP")" && pwd)/$(basename "$ZIP")"
rm -f "$ZIP"
(cd "$OUT" && zip -qr "$ZIP" .)
echo "==> $ZIP"
