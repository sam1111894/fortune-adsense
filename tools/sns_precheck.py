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


def select_target(posts, known, pending):
    """Choose the post this fire should act on.

    `posts` must already be sorted newest-mtime first.

    WHY THIS IS NOT JUST "the newest post"
    --------------------------------------
    Recording a syndication attempt rewrites the article's frontmatter,
    which BUMPS ITS MTIME. So the post that was published last is
    normally also the newest one on disk -- and the pending flag of any
    OTHER post was invisible: the old code looked at `posts[0]` only, so
    a publish that never completed (platform credit, outage) sat BELOW
    the newest post and was skipped forever. `read_ledger()` promises the
    opposite -- "Those must be RETRIED, not skipping" -- so the retry
    candidates are collected across EVERY post.

    A retry candidate is a post that (a) the ledger still marks as
    pending and (b) whose body has NOT changed since that attempt: a
    changed body is new content, not a retry of the old attempt.

    WHY THE NEW-CONTENT TEST MUST ALSO SPAN EVERY POST
    ---------------------------------------------------
    The mtime bump has a SECOND and worse consequence. The new-content
    test used to run on `posts[0]` alone, so a post the ledger had NEVER
    seen was eligible only while it happened to be the newest file on
    disk. But the post that was syndicated last is normally the newest
    one -- its own frontmatter rewrite bumped the mtime -- so that one
    post held the "new content" slot permanently. Every OTHER post that
    had never been attempted was in neither the new-content test nor the
    pending map, so it could NEVER be selected at all.

    On this repo 10 of the 11 posts were in exactly that state: no
    ledger row, no pending flag, no path to being published. The
    pipeline could only ever work on the one post it had touched.

    So the new-content test now runs over EVERY post: a post whose body
    hash the ledger has never seen (or has seen differently) needs a
    (re)synthesis, wherever it sits in the mtime order.

    The oldest outstanding attempt is retried first, so when several
    posts are pending none of them starves behind another, and genuinely
    new content still wins over an older retry.

    Returns a dict with the decision, the selected post (`target`), the
    newest post (`latest`) and the full candidate lists.
    """
    latest = posts[0] if posts else None
    is_new = None
    if latest:
        is_new = known.get(latest["slug"]) != latest["bodySha256"]

    # New content = the ledger has never seen this body, or has seen a
    # DIFFERENT one (the user edited the post). Collected across EVERY
    # post, not just the newest: a never-attempted post that sits below
    # the newest one is still work the pipeline has never done.
    #
    # Sorted HERE rather than inherited from the caller's ordering, so the
    # function picks the newest such post regardless of how `posts` was
    # arranged.
    new_candidates = sorted(
        [
            post for post in posts
            if known.get(post["slug"]) != post["bodySha256"]
        ],
        key=lambda post: (post["mtime"], post.get("file", "")),
        reverse=True,
    )

    retry_candidates = [
        post for post in posts
        if post["slug"] in pending
        and known.get(post["slug"]) == post["bodySha256"]
    ]
    retry_candidates.sort(
        key=lambda post: (pending[post["slug"]].get("ts") or "", post["slug"])
    )

    if new_candidates:
        # Content the ledger has never seen is new work, and it wins
        # over retrying an older attempt. `posts` is newest-mtime first,
        # so the most recently written such post is picked.
        decision = "SYNTHESIZE"
        target = new_candidates[0]
    elif retry_candidates:
        # Body unchanged, but the previous publish never completed
        # (platform credit / outage). Retry the publish instead of
        # skipping it.
        decision = "RETRY_PENDING_PUBLISH"
        target = retry_candidates[0]
    else:
        decision = "NO_NEW_POST"
        target = None

    return {
        "decision": decision,
        "target": target,
        "latest": latest,
        "latestIsNewContent": is_new,
        "targetIsNewContent": bool(
            target is not None
            and known.get(target["slug"]) != target["bodySha256"]
        ),
        "newCandidates": new_candidates,
        "retryCandidates": retry_candidates,
    }


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

    known, pending = read_ledger()
    selection = select_target(posts, known, pending)
    latest = selection["latest"]
    is_new = selection["latestIsNewContent"]
    retry_pending = selection["decision"] == "RETRY_PENDING_PUBLISH"
    target = selection["target"]
    decision = selection["decision"]
    if not usable_any and decision == "SYNTHESIZE":
        decision = "SYNTHESIZE_NO_TOKENS"

    result = {
        "envSources": provenance,
        "credentialState": credential_state,
        "usablePlatforms": [p for p, v in credential_state.items() if v["usable"]],
        "postCount": len(posts),
        "latest": latest,
        "target": target,
        "newCandidateSlugs": [p["slug"] for p in selection["newCandidates"]],
        "retryCandidateSlugs": [p["slug"] for p in selection["retryCandidates"]],
        "latestIsNewContent": is_new,
        "targetIsNewContent": selection["targetIsNewContent"],
        "retryPendingPublish": retry_pending,
        "pendingDetail": pending.get(target["slug"]) if target else None,
        "ledgerKnownHashes": known,
        "decision": decision,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if decision == "NO_NEW_POST":
        return 3
    return 0 if usable_any else 4


if __name__ == "__main__":
    sys.exit(main())
