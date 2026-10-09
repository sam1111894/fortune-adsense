#!/usr/bin/env bash
# setup-sns-tokens.sh — interactive token setup for blog-sns-auto cron
# Usage: bash tools/setup-sns-tokens.sh [--dry-run]
#
# What it does:
#   1. Reads current token presence (env -i style, no leakage)
#   2. If --dry-run: just prints status and exits
#   3. If interactive: prompts for each missing token and writes to .env
#      (read-only .env.example is the template; .env is gitignored)
#
# After setup, the next blog-sns-auto fire will auto-publish instead of [SKIP].

set -u

ENV_FILE="${ENV_FILE:-.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-.env.example}"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

cd "$(dirname "$0")/.."

check_token() {
  local name="$1"
  local v="${!name:-}"
  if [ -z "$v" ]; then
    echo "  $name  EMPTY"
    return 1
  else
    echo "  $name  SET  ($(printf '%s' "$v" | wc -c | tr -d ' ') chars)"
    return 0
  fi
}

echo "=== SNS token status (current shell) ==="
missing=0
for t in X_API_KEY THREADS_TOKEN LINKEDIN_TOKEN DEVTO_API_KEY; do
  check_token "$t" || missing=$((missing+1))
done

echo
echo "=== .env file ==="
if [ -f "$ENV_FILE" ]; then
  echo "  $ENV_FILE exists ($(wc -c < "$ENV_FILE" | tr -d ' ') bytes)"
else
  echo "  $ENV_FILE missing"
fi

echo
echo "=== Summary ==="
echo "  Missing tokens: $missing / 4"
if [ "$missing" -eq 0 ]; then
  echo "  All 4 tokens present. Next blog-sns-auto fire will auto-publish."
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "  --dry-run: not writing $ENV_FILE"
  exit 1
fi

if [ ! -t 0 ]; then
  echo "  Not a TTY; cannot prompt. Run interactively or set env vars before invoking."
  exit 2
fi

if [ ! -f "$EXAMPLE_FILE" ]; then
  echo "  $EXAMPLE_FILE missing; cannot scaffold $ENV_FILE."
  exit 3
fi

echo
echo "Will now prompt for each missing token."
echo "(press Enter to skip; value is masked via stty)"
echo

# Make sure example file exists, copy to .env if needed
if [ ! -f "$ENV_FILE" ]; then
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE from $EXAMPLE_FILE (mode 600)."
fi

prompt_token() {
  local name="$1"
  local current
  current=$(grep -E "^${name}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
  if [ -n "$current" ]; then
    echo "  $name already set in $ENV_FILE (skipping)"
    return 0
  fi
  local val
  read -r -s -p "  $name: " val
  echo
  if [ -z "$val" ]; then
    echo "  $name: skipped"
    return 1
  fi
  # Replace or append the line
  if grep -qE "^${name}=" "$ENV_FILE"; then
    # macOS sed -i '' is BSD; use perl for portability
    perl -i -pe "s|^${name}=.*|${name}=${val}|" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$name" "$val" >> "$ENV_FILE"
  fi
  echo "  $name: written"
}

for t in X_API_KEY THREADS_TOKEN LINKEDIN_TOKEN DEVTO_API_KEY; do
  if [ -z "${!t:-}" ]; then
    prompt_token "$t"
  fi
done

echo
echo "=== After setup ==="
echo "Re-running check (env-only, not reading .env to keep this script from re-leaking):"
for t in X_API_KEY THREADS_TOKEN LINKEDIN_TOKEN DEVTO_API_KEY; do
  if [ -n "${!t:-}" ]; then
    echo "  $t  (shell) SET"
  else
    echo "  $t  (shell) EMPTY  — token saved in $ENV_FILE but not in current shell; re-source it:"
    echo "      export \$(grep '^$t=' $ENV_FILE | xargs)"
  fi
done
echo
echo "Done. Cron blog-sns-auto will pick up new tokens on its next fire (next 60m tick)."
