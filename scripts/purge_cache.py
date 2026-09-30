#!/usr/bin/env python3
"""
Cloudflare zone-level cache purge — quoting-safe.

Why Python instead of curl + jq?
  - The previous shell attempts mixed single/double quotes around JSON
    payloads, which broke on macOS bash when the JSON contained apostrophes
    or when env values contained spaces/quotes.
  - Python sends the request body as bytes; the shell is never asked to
    quote a JSON string.

Usage:
  export CLOUDFLARE_API_TOKEN=...
  export CLOUDFLARE_ZONE_ID=...
  python3 purge_cache.py                    # Purge Everything on $CLOUDFLARE_ZONE_ID
  python3 purge_cache.py --zone <zone_id>   # explicit zone
  python3 purge_cache.py --url https://x.com/a https://x.com/b   # purge specific URLs
  python3 purge_cache.py --prefix /blog/    # purge by prefix
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"


def post(path: str, token: str, body: dict) -> tuple[int, dict]:
    url = f"{API_BASE}{path}"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, {"raw": body_text}


def main() -> int:
    p = argparse.ArgumentParser(description="Cloudflare zone cache purge")
    p.add_argument("--zone", help="Zone ID (default: $CLOUDFLARE_ZONE_ID)")
    p.add_argument(
        "--mode",
        choices=["everything", "url", "prefix", "tag", "host"],
        default="everything",
    )
    p.add_argument("--url", nargs="+", help="URLs to purge (with --mode url)")
    p.add_argument("--prefix", help="Prefix to purge (with --mode prefix)")
    p.add_argument("--tag", help="Cache-tag to purge (Enterprise, --mode tag)")
    p.add_argument("--host", help="Hostname to purge (--mode host)")
    args = p.parse_args()

    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    zone = args.zone or os.environ.get("CLOUDFLARE_ZONE_ID")
    if not token:
        print("❌ CLOUDFLARE_API_TOKEN not set", file=sys.stderr)
        return 2
    if not zone:
        print("❌ zone id not provided (use --zone or set CLOUDFLARE_ZONE_ID)", file=sys.stderr)
        return 2

    if args.mode == "everything":
        body = {"purge_everything": True}
    elif args.mode == "url":
        if not args.url:
            print("❌ --url required with --mode url", file=sys.stderr)
            return 2
        body = {"files": args.url}
    elif args.mode == "prefix":
        if not args.prefix:
            print("❌ --prefix required with --mode prefix", file=sys.stderr)
            return 2
        body = {"prefixes": [args.prefix]}
    elif args.mode == "tag":
        if not args.tag:
            print("❌ --tag required with --mode tag (Enterprise only)", file=sys.stderr)
            return 2
        body = {"tags": [args.tag]}
    elif args.mode == "host":
        if not args.host:
            print("❌ --host required with --mode host", file=sys.stderr)
            return 2
        body = {"hosts": [args.host]}
    else:
        print(f"❌ unknown mode: {args.mode}", file=sys.stderr)
        return 2

    print(f"▶ POST /zones/{zone}/purge_cache  body={json.dumps(body)}")
    status, data = post(f"/zones/{zone}/purge_cache", token, body)
    ok = data.get("success", False)
    print(f"◀ HTTP {status}  success={ok}")
    if not ok:
        print(json.dumps(data, indent=2)[:1500])
        return 1
    rid = data.get("result", {}).get("id")
    if rid:
        print(f"✅ purge_id={rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())