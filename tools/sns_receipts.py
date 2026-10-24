#!/usr/bin/env python3
"""Durable publish receipts -- the duplicate-publish guard for billable posts.

WHY THIS EXISTS
---------------
`tools/sns_publish.py` issues a BILLABLE POST (X charges per Post: $0.015
without a URL, $0.200 with one). The syndication OUTCOME -- the ledger line
and the article frontmatter -- is written by a SEPARATE, LATER step. If the
publisher process dies between the API returning HTTP 200 and that later
step, the next fire finds no evidence that the post exists, treats the
article as "still pending", and publishes the SAME content again: paying
for it a second time and double-posting.

A receipt closes that window because it is written BY THE PUBLISHER, in the
same process, immediately around the API call:

    pending   - written BEFORE the POST, so an attempt that dies mid-flight
                is still visible afterwards
    published - written AFTER a 2xx response
    failed    - written after a definitive non-2xx response (the platform
                rejected it, so nothing was created and a retry is safe)

The store is an APPEND-ONLY JSONL file. Every state change is a NEW line,
so a torn write can never destroy an earlier record: readers skip
unparseable lines and use the last intact record for each attempt.

The receipt is also the only duplicate-publish guard that keeps working when
the remote read endpoints are metered. X's GET /2/users/:id/tweets answers
HTTP 402 "credits depleted" on an empty balance -- precisely the state in
which a paid publish is most likely to be retried.

The store lives beside the syndication ledger under .hermes/ (gitignored,
local operational state). The IMPLEMENTATION is tracked in git; the
records are not, exactly like the ledger.

Public API:
    text_key(text)                     -> sha256 of the exact published text
    load(path=None)                    -> list of receipt records
    append(record, path=None)          -> append one record, durably
    group_attempts(receipts, platform) -> per-attempt final states
    find_published(attempts, ...)      -> the attempt that already published
    find_pending(attempts, ...)        -> the attempt that never resolved
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Local operational state, next to the syndication ledger.
DEFAULT_PATH = os.path.join(REPO_ROOT, ".hermes", "cron-drafts",
                             "publish-receipts.jsonl")

# Overridable so tests (and operators) can point the store elsewhere.
PATH_ENV = "SNS_RECEIPTS_PATH"

STATE_PENDING = "pending"
STATE_PUBLISHED = "published"
STATE_FAILED = "failed"


def path() -> str:
    """Absolute path of the receipt store."""
    return os.environ.get(PATH_ENV) or DEFAULT_PATH


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def text_key(text: str) -> str:
    """sha256 of the exact text handed to the platform.

    This is the primary idempotency key: identical text on the same
    platform is the same post.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def new_attempt_id() -> str:
    return uuid.uuid4().hex


def load(path_override: Optional[str] = None) -> list:
    """Read every intact receipt record. A torn (unparseable) line is
    skipped rather than aborting the read -- the previous line for that
    attempt is still valid."""
    target = path_override or path()
    if not os.path.isfile(target):
        return []
    records = []
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def append(record: dict, path_override: Optional[str] = None) -> dict:
    """Append one receipt record and flush it to disk.

    Opened in append mode and fsynced, so the record survives a process
    crash (not just a clean exit).
    """
    target = path_override or path()
    directory = os.path.dirname(os.path.abspath(target))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return record


def group_attempts(receipts: list, platform: str) -> list:
    """Collapse a receipt log into one entry per publish attempt.

    Each attempt keeps the LAST state it reached (a later line supersedes
    an earlier one) plus the metadata recorded along the way.
    """
    order = []
    by_id = {}
    for record in receipts:
        if record.get("platform") != platform:
            continue
        attempt_id = record.get("attemptId")
        if not attempt_id:
            continue
        if attempt_id not in by_id:
            by_id[attempt_id] = {
                "attemptId": attempt_id,
                "platform": platform,
                "textSha256": record.get("textSha256"),
                "contentSha256": record.get("contentSha256"),
                "startedAt": record.get("startedAt"),
                "completedAt": None,
                "state": None,
                "postId": None,
                "url": None,
                "httpStatus": None,
                "statusesCountBefore": None,
                "screenName": None,
                "note": None,
                # Metadata the FREE-read resolvers need to settle an
                # ambiguous attempt. Dropping them makes every resolver
                # answer "failed" (nothing matched) even when the post IS
                # listed -- which then lets the guard re-publish a post
                # that already landed.
                "creationId": None,
                "textPreview": None,
                "title": None,
                "canonicalUrl": None,
            }
            order.append(attempt_id)
        slot = by_id[attempt_id]
        if record.get("state"):
            slot["state"] = record.get("state")
        for field in ("postId", "url", "httpStatus", "statusesCountBefore",
                      "screenName", "note", "textSha256", "contentSha256",
                      "creationId", "textPreview", "title", "canonicalUrl"):
            if record.get(field) is not None:
                slot[field] = record.get(field)
        if record.get("completedAt"):
            slot["completedAt"] = record.get("completedAt")
    return [by_id[attempt_id] for attempt_id in order]


def _matches(attempt: dict, text_sha: str, content_sha: Optional[str]) -> bool:
    if text_sha and attempt.get("textSha256") == text_sha:
        return True
    if (content_sha and attempt.get("contentSha256")
            and attempt["contentSha256"] == content_sha):
        return True
    return False


def find_published(attempts: list, text_sha: str, content_sha: Optional[str] = None):
    """The attempt that already published this content, if any."""
    for attempt in attempts:
        if attempt.get("state") != STATE_PUBLISHED:
            continue
        if _matches(attempt, text_sha, content_sha):
            return attempt
    return None


def find_pending(attempts: list, text_sha: str, content_sha: Optional[str] = None):
    """The attempt that started but never reached a terminal state.

    Its outcome is UNKNOWN: the POST may have landed without the receipt
    being written, so it must not be repeated blindly.
    """
    for attempt in attempts:
        if attempt.get("state") != STATE_PENDING:
            continue
        if _matches(attempt, text_sha, content_sha):
            return attempt
    return None


def resolve(attempt_id: str, state: str, path_override: Optional[str] = None,
            note: str = "") -> dict:
    """Settle one pending attempt by hand, appending a terminal receipt.

    The escape hatch for an attempt the free-read resolvers cannot settle
    (for example a LinkedIn attempt, which has no free post listing at
    all). It appends to the same append-only log, so the audit trail
    survives and the attempt can never be retried by accident.
    """
    if state not in (STATE_PUBLISHED, STATE_FAILED):
        raise ValueError("state must be %r or %r" % (STATE_PUBLISHED,
                                                     STATE_FAILED))
    records = load(path_override)
    matches = [r for r in records if r.get("attemptId") == attempt_id]
    if not matches:
        raise ValueError("no receipt with attemptId %r" % attempt_id)
    last = matches[-1]
    record = {
        "platform": last.get("platform"),
        "attemptId": attempt_id,
        "state": state,
        "textSha256": last.get("textSha256"),
        "contentSha256": last.get("contentSha256"),
        "startedAt": last.get("startedAt"),
        "completedAt": now_iso(),
        "note": note or "resolved by operator",
    }
    if last.get("postId"):
        record["postId"] = last.get("postId")
    if last.get("url"):
        record["url"] = last.get("url")
    return append(record, path_override)


def summarise(receipts: list, platform: str) -> dict:
    """Counts per state, for reporting."""
    counts = {STATE_PENDING: 0, STATE_PUBLISHED: 0, STATE_FAILED: 0}
    for attempt in group_attempts(receipts, platform):
        state = attempt.get("state")
        if state in counts:
            counts[state] += 1
    return counts


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "resolve":
        if len(args) < 3:
            print("usage: sns_receipts.py resolve <attemptId> "
                  "published|failed [--note TEXT]")
            raise SystemExit(2)
        note = ""
        if "--note" in args:
            note = args[args.index("--note") + 1]
        try:
            record = resolve(args[1], args[2], note=note)
        except ValueError as exc:
            print("ERROR: %s" % exc)
            raise SystemExit(1)
        print("resolved %s -> %s" % (record["attemptId"], record["state"]))
        raise SystemExit(0)

    target = args[0] if args else "all"
    records = load()
    platforms = sorted({r.get("platform") for r in records if r.get("platform")})
    if target != "all" and target not in platforms:
        platforms = [target]
    print("receipt store: %s (%d records)" % (path(), len(records)))
    for platform in platforms:
        print("  %-8s %s" % (platform, summarise(records, platform)))
