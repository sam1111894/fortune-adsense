#!/usr/bin/env bash
# setup-sns-tokens.sh — interactive token setup for blog-sns-auto cron
# Usage: bash tools/setup-sns-tokens.sh [--dry-run] [--json]
#
# What it does:
#   1. Reports credential presence USING THE PUBLISHER'S OWN LOADER
#      (tools/sns_publish.py status). This is the single source of truth:
#      the old version read the shell env only and reported
#      "Missing tokens: 4 / 4" on a machine where the publisher reported
#      X = USABLE, because the credentials live in .env files.
#   2. If --dry-run: prints the status and exits (1 if something is missing).
#   3. If interactive: prompts for each missing token and writes it to .env
#      (read-only .env.example is the template; .env is gitignored).
#
# After setup, the next blog-sns-auto fire will auto-publish instead of [SKIP].

set -u

ENV_FILE="${ENV_FILE:-.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-.env.example}"
STATUS_CMD="${STATUS_CMD:-tools/sns_publish.py}"
DRY_RUN=0
AS_JSON=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --json) AS_JSON=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (it runs $STATUS_CMD)" >&2
  exit 69
fi
if [ ! -f "$STATUS_CMD" ]; then
  echo "$STATUS_CMD missing; cannot determine credential state" >&2
  exit 69
fi

# ---------------------------------------------------------------------------
# Ask the publisher which credentials are usable. It already implements the
# full precedence chain (process env -> project .env -> profile .env ->
# global .env) and knows which key sets each platform needs. Re-implementing
# that here is how the two views drifted apart in the first place.
# ---------------------------------------------------------------------------
STATE_JSON="$(python3 "$STATUS_CMD" status --json 2>/dev/null)"
if [ -z "$STATE_JSON" ]; then
  echo "could not read credential state from $STATUS_CMD" >&2
  exit 69
fi

if [ "$AS_JSON" -eq 1 ]; then
  printf '%s\n' "$STATE_JSON"
  exit 0
fi

MISSING_KEYS="$(printf '%s' "$STATE_JSON" | python3 -c '
import json, sys
state = json.load(sys.stdin)
keys = []
for platform, info in state.get("platforms", {}).items():
    if info.get("usable"):
        continue
    for entry in info.get("keys", []):
        if entry.get("state") != "present":
            keys.append(entry["key"])
print("\n".join(keys))
')"

if [ -z "$MISSING_KEYS" ]; then
  echo "=== SNS token status ==="
  printf '%s' "$STATE_JSON" | python3 -c '
import json, sys
state = json.load(sys.stdin)
for platform, info in state.get("platforms", {}).items():
    print("  %-8s %s" % (platform, "USABLE" if info.get("usable") else "UNUSABLE"))
'
  echo
  echo "=== Summary ==="
  echo "  All platforms usable. Next blog-sns-auto fire will auto-publish."
  exit 0
fi

echo "=== SNS token status ==="
printf '%s' "$STATE_JSON" | python3 -c '
import json, sys
state = json.load(sys.stdin)
for platform, info in state.get("platforms", {}).items():
    print("  %-8s %s" % (platform, "USABLE" if info.get("usable") else "UNUSABLE"))
    for entry in info.get("keys", []):
        source = entry.get("source") or "?"
        print("             %-24s %-8s %s" % (entry["key"], entry.get("state"), source))
'
echo
echo "=== .env file ==="
if [ -f "$ENV_FILE" ]; then
  echo "  $ENV_FILE exists ($(wc -c < "$ENV_FILE" | tr -d ' ') bytes)"
else
  echo "  $ENV_FILE missing"
fi

echo
echo "=== Summary ==="
echo "  Missing keys:"
printf '%s\n' "$MISSING_KEYS" | while read -r key; do
  [ -n "$key" ] && echo "    - $key"
done

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

# Make sure example file exists, copy to .env if needed
if [ ! -f "$ENV_FILE" ]; then
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE from $EXAMPLE_FILE (mode 600)."
fi

prompt_token() {
  local name="$1"
  local current
  # Match both `KEY=` and `export KEY=` forms; the reviewer profile .env uses
  # the `export ` form, so a plain `^KEY=` grep misses it.
  current=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
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
  # Replace or append the line. Values may contain characters that are
  # special in a sed/Perl replacement, so write via a quoted-value rewrite
  # that does no pattern substitution on the value itself.
  python3 - "$ENV_FILE" "$name" "$val" <<'PY'
import os
import re
import sys

path, name, value = sys.argv[1], sys.argv[2], sys.argv[3]
pattern = re.compile(r"^([ \t]*(?:export[ \t]+)?)%s[ \t]*=" % re.escape(name))
with open(path, "r", encoding="utf-8") as fh:
    lines = fh.read().split("\n")
replaced = False
for index, line in enumerate(lines):
    if pattern.match(line):
        prefix = pattern.match(line).group(1)
        lines[index] = "%s%s=%s" % (prefix, name, value)
        replaced = True
        break
if not replaced:
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("%s=%s" % (name, value))
    lines.append("")
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
os.chmod(path, 0o600)
PY
  echo "  $name: written"
}

printf '%s\n' "$MISSING_KEYS" | while read -r key; do
  [ -n "$key" ] && prompt_token "$key"
done

echo
echo "=== After setup ==="
echo "Re-reading state through $STATUS_CMD:"
FINAL_JSON="$(python3 "$STATUS_CMD" status --json 2>/dev/null)"
printf '%s' "$FINAL_JSON" | python3 -c '
import json, sys
state = json.load(sys.stdin)
for platform, info in state.get("platforms", {}).items():
    print("  %-8s %s" % (platform, "USABLE" if info.get("usable") else "UNUSABLE"))
    for entry in info.get("keys", []):
        print("             %-24s %-8s %s" % (entry["key"], entry.get("state"), entry.get("source") or "?"))
'
echo
echo "Done. Cron blog-sns-auto picks up new tokens from $ENV_FILE on its next fire (next 60m tick)."
