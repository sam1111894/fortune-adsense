#!/usr/bin/env python3
"""Build Cloudflare Pages manifest.json from dist/ directory"""
import os
import json
import hashlib
import sys

src = sys.argv[1] if len(sys.argv) > 1 else "dist"
out = sys.argv[2] if len(sys.argv) > 2 else "manifest.json"

if not os.path.isdir(src):
    print(f"Error: {src} not found", file=sys.stderr)
    sys.exit(1)

files_meta = {}
deployed = []

for root, _, fs in os.walk(src):
    for f in fs:
        full = os.path.join(root, f)
        rel = os.path.relpath(full, src).replace(os.sep, "/")
        with open(full, "rb") as fp:
            h = hashlib.sha256(fp.read()).hexdigest()
        files_meta[rel] = {
            "contentType": "application/octet-stream",
            "contentHash": h,
        }
        deployed.append(rel)

manifest = {"files": files_meta, "deployed": deployed}
with open(out, "w") as f:
    json.dump(manifest, f)

print(f"Manifest entries: {len(deployed)}")
print(f"Output: {out}")
