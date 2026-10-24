#!/usr/bin/env python3
"""
blog-sns-auto precheck — decides whether this cron fire has real work to do.

WHY THIS FILE EXISTS
--------------------
Two bugs made ~23 consecutive cron cycles skip for the wrong reason:

  1. TOKEN DETECTION ONLY LOOKED AT THE SHELL ENV.
     The cron shell has no SNS tokens, so every cycle concluded "no tokens".
     But the tokens live in .env files on disk. Worse, the size check used
     `tr -cd '\\0' < file | wc -c`, which counts NUL bytes — always 0 for a
     text file — so the files were wrongly reported as "0 bytes / empty".
     The X credential set was live the whole time and was never published to.

  2. CHANGE DETECTION USED mtime.
     Writing the syndication frontmatter bumps mtime, so the next cron cycle
     saw a "new" file and re-fired on its own output — a self-trigger loop
     that also masked genuine user edits.

THIS TOOL FIXES BOTH:
  * tokens are read from .env files (shell env wins), parsed correctly;
  * "did the user write a new post?" is decided by hashing the post BODY
    (everything after the frontmatter), which is immune to frontmatter writes.

Usage:
  python3 tools/sns_precheck.py            # print JSON decision + exit codes
  exit 0 = new post to syndicate
  exit 3 = no new post (nothing to do)
  exit 4 = new post, but no usable tokens at all
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOG_DIR = os.path.join(REPO_ROOT, "src", "content", "blog")
LEDGER = os.path.join(REPO_ROOT, ".hermes", "cron-drafts", "syndication-ledger.jsonl")

ENV_CANDIDATES = [
    os.path.join(REPO_ROOT, ".env"),
    os.path.expanduser("~/.hermes/profiles/reviewer/.env"),
    os.path.expanduser("~/.hermes/.env"),
]

REQUIRED = {
    "x": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    "threads": ["THREADS_TOKEN"],
    "linkedin": ["LINKEDIN_TOKEN"],
    "dev.to": ["DEVTO_API_KEY"],
}

KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def _strip_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if raw[0] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    cut = re.search(r"\s+#", raw)
    if cut:
        raw = raw[: cut.start()]
    return raw.strip()


def load_env() -> tuple[dict, list]:
    merged: dict = {}
    provenance = []
    sources: dict = {}
    for path in ENV_CANDIDATES:
        if not os.path.isfile(path):
            provenance.append({"path": path, "state": "absent"})
            continue
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        keys = 0
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = KEY_RE.match(line)
            if not match:
                continue
            key, value = match.group(1), _strip_value(match.group(2))
            if key not in merged:
                merged[key] = value
                sources[key] = path
                keys += 1
            elif not merged[key].strip() and value.strip():
                # An empty placeholder in a higher-precedence file must not
                # shadow a usable value in a lower-precedence one (e.g. the
                # `X_API_KEY=` template line in the project .env would hide
                # the working value in the reviewer profile .env).
                merged[key] = value
                sources[key] = path
                keys += 1
        provenance.append({
            "path": path,
            "state": "present",
            "bytes": len(raw.encode("utf-8")),
            "keys_parsed": keys,
        })
    # Process environment overrides everything (highest precedence).
    # Only NON-EMPTY process values override: an empty `export THREADS_TOKEN=`
    # in a shell profile must not wipe a valid .env credential.
    for key, value in os.environ.items():
        if value:
            merged[key] = value
            sources[key] = "<process env>"
    return merged, provenance


def body_hash(path: str) -> tuple[str, int, str]:
    """Return (sha256 of body, body length, slug)."""
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    match = FRONTMATTER_RE.match(raw)
    body = raw[match.end():] if match else raw
    slug = ""
    if match:
        slug_match = re.search(r'^slug:\s*["\']?([^"\'\n]+)', match.group(1), re.M)
        if slug_match:
            slug = slug_match.group(1).strip()
    if not slug:
        slug = os.path.splitext(os.path.basename(path))[0]
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return digest, len(body), slug


def read_ledger(ledger_path: str | None = None) -> tuple[dict, dict]:
    """Map slug -> last known body hash, and slug -> pending-publish record.

    Returns (hashes, pending). A slug in `pending` has an unchanged body but
    a publish that could not be completed (e.g. the platform was down or the
    account had no API credit). Those must be RETRIED, not skipped.

    The pending flag is LAST-EXPLICIT-STATEMENT-WINS: a later ledger record
    for the same slug that reports publishPending=false clears an older
    pending flag. Without that, `pending` was monotonic and the precheck
    could never report NO_NEW_POST once an article had been published.
    """
    hashes: dict = {}
    pending: dict = {}
    target = ledger_path or LEDGER
    if not os.path.isfile(target):
        return hashes, pending
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            slug = entry.get("blog")
            digest = entry.get("bodySha256")
            if slug and digest:
                hashes[slug] = digest
                pending_value = entry.get("publishPending")
                if pending_value is False:
                    # An explicit publishPending=false means "the publish
                    # completed" and must CLEAR an older pending flag for the
                    # same slug. Without this, `pending` was monotonic: once a
                    # slug entered it, it stayed there forever, so the
                    # precheck could never return NO_NEW_POST after a
                    # successful publish and the cron re-fired on an
                    # already-published article on every cycle.
                    #
                    # Only a literal False clears it. `None`/absent means the
                    # record says nothing about pending state (the ledger
                    # writer always emits the key, possibly as null), and
                    # that must not be read as "the publish completed".
                    pending.pop(slug, None)
                elif pending_value:
                    # Fall back to the nested history entry's 'attemptedAt'
                    # when the row's own top-level 'ts' is empty. Older
                    # rows were written with an empty 'ts' (the --timestamp
                    # flag defaults to ''), which erased the attempt time
                    # from the pending-publish audit trail.
                    nested = entry.get("entry")
                    nested_ts = nested.get("attemptedAt") if isinstance(nested, dict) else None
                    pending[slug] = {
                        "blockedBy": entry.get("blockedBy"),
                        "outcome": entry.get("outcome"),
                        "ts": entry.get("ts") or nested_ts,
                    }
    return hashes, pending


def main() -> int:
    env, provenance = load_env()
    credential_state = {}
    usable_any = False
    for platform, keys in REQUIRED.items():
        missing = []
        for key in keys:
            value = env.get(key)
            if value is None:
                missing.append({"key": key, "state": "missing"})
            elif value.strip() == "":
                missing.append({"key": key, "state": "empty"})
            else:
                missing.append({"key": key, "state": "present", "length": len(value)})
        ok = all(item["state"] == "present" for item in missing)
        usable_any = usable_any or ok
        credential_state[platform] = {"usable": ok, "keys": missing}

    posts = []
    if os.path.isdir(BLOG_DIR):
        for name in sorted(os.listdir(BLOG_DIR)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(BLOG_DIR, name)
            digest, length, slug = body_hash(path)
            posts.append({
                "file": name,
                "slug": slug,
                "bodySha256": digest,
                "bodyChars": length,
                "mtime": os.path.getmtime(path),
            })
    posts.sort(key=lambda item: (item["mtime"], item["file"]), reverse=True)
    latest = posts[0] if posts else None

    known, pending = read_ledger()
    is_new = None
    retry_pending = None
    if latest:
        is_new = known.get(latest["slug"]) != latest["bodySha256"]
        retry_pending = latest["slug"] in pending

    if is_new:
        decision = "SYNTHESIZE"
    elif retry_pending:
        # Body unchanged, but the previous publish never completed (platform
        # credit / outage). Retry the publish instead of skipping.
        decision = "RETRY_PENDING_PUBLISH"
    else:
        decision = "NO_NEW_POST"
    if not usable_any and decision == "SYNTHESIZE":
        decision = "SYNTHESIZE_NO_TOKENS"

    result = {
        "envSources": provenance,
        "credentialState": credential_state,
        "usablePlatforms": [p for p, v in credential_state.items() if v["usable"]],
        "postCount": len(posts),
        "latest": latest,
        "latestIsNewContent": is_new,
        "retryPendingPublish": retry_pending,
        "pendingDetail": pending.get(latest["slug"]) if latest else None,
        "ledgerKnownHashes": known,
        "decision": decision,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if decision == "NO_NEW_POST":
        return 3
    return 0 if usable_any else 4


if __name__ == "__main__":
    sys.exit(main())
