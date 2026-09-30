#!/usr/bin/env bash
# Deploy to Cloudflare Pages via wrangler + zone-level cache purge
# Replaces deploy.py / deploy_v2.py (multipart upload) which missed files.
#
# Why wrangler instead of REST multipart?
#   - wrangler handles file enumeration, MIME, content hashing internally.
#   - It uses Cloudflare's own internal deploy pipeline that is identical
#     to what the dashboard uses, so file omission is not possible.
#   - It triggers Pages-side cache invalidation automatically.
#
# Why a separate zone purge?
#   - Pages-side invalidation hits Pages cache. The zone-level CDN edge cache
#     (custom domain lucksajueun.com) is invalidated separately via REST.
#
# Quoting safety: env values are read into variables and passed to Python
# subprocesses via env vars, not via shell interpolation of JSON strings.

set -euo pipefail

# ── 1. Load .env into current shell ───────────────────────────────────────
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env not found at $ENV_FILE" >&2
  exit 1
fi

# Export only the keys we need; comment lines and blanks ignored.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${CLOUDFLARE_ACCOUNT_ID:?missing CLOUDFLARE_ACCOUNT_ID in .env}"
: "${CLOUDFLARE_API_TOKEN:?missing CLOUDFLARE_API_TOKEN in .env}"
: "${CLOUDFLARE_PAGES_PROJECT:?missing CLOUDFLARE_PAGES_PROJECT in .env}"
: "${CLOUDFLARE_ZONE_ID:?missing CLOUDFLARE_ZONE_ID in .env}"

PROJECT="$CLOUDFLARE_PAGES_PROJECT"
ZONE_ID="$CLOUDFLARE_ZONE_ID"

# ── 2. Build (skip if dist is fresh) ──────────────────────────────────────
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "▶ Building (astro build) ..."
  npm run build
fi

# ── 3. Wrangler deploy (cache-invalidated automatically) ───────────────────
echo "▶ wrangler pages deploy dist --project-name=$PROJECT"
npx wrangler pages deploy dist --project-name="$PROJECT"

# ── 4. Zone-level Purge Everything (REST) ─────────────────────────────────
echo "▶ Zone-level Purge Everything for zone $ZONE_ID ..."
python3 "$(dirname "$0")/purge_cache.py" --zone "$ZONE_ID"

echo "✅ Done. Pages deployed + zone cache purged."