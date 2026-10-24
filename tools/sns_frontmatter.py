#!/usr/bin/env python3
"""
Bounded syndication-state writer for blog frontmatter.

WHY THIS EXISTS
---------------
Every blog-sns-auto fire used to append a `syndication.history` entry to the
article frontmatter by hand. That list grew without bound: on
daily-fortune-healthy-mindset it reached 11 entries covering 430 of the 482
frontmatter lines, while the article body itself is only 3996 characters.
The full audit trail already exists in two better places -- the append-only
ledger (.hermes/cron-drafts/syndication-ledger.jsonl) and the git history of
the article file -- so the frontmatter only needs a bounded window of the most
recent attempts.

This module is the sanctioned write path and it is deliberately conservative:

  * it edits ONLY the `syndication:` block of the frontmatter;
  * it always re-appends the body and asserts the body SHA-256 is unchanged.
    A frontmatter-only write that truncates the body to 0 bytes hashes to
    e3b0c442... (sha256 of the empty string) and looks exactly like a
    self-trigger to the precheck, which decides "new content?" from the body
    hash, not from mtime;
  * it validates the payload against the same rules as
    src/content.config.ts (platform/status enums, required fields, optional
    url must be a real URL) BEFORE writing, so a bad status value cannot
    break `npm run build`;
  * it trims `syndication.history` to the last --keep entries.

HOW THE EDIT IS MADE
--------------------
Line-based splicing on YAML is unsafe: a double-quoted scalar may span
several physical lines (the `stdout:` field does), and a continuation line
starting at column 0 would be mistaken for the end of a block. So the block
boundaries are taken from the YAML parser itself (yaml.compose node marks)
and only that byte range is replaced. Everything else in the file -- the
opening '---', the closing '---', and the body -- is copied verbatim.

Usage:
  python3 tools/sns_frontmatter.py status --post <path-or-slug>
  python3 tools/sns_frontmatter.py trim   --post <path-or-slug> [--keep N] [--dry-run]
  python3 tools/sns_frontmatter.py record --post <path-or-slug> --payload <json> [--keep N] [--dry-run]

The payload for `record` is a JSON object:
  {
    "current":      { ...syndication fields, without "history"... },
    "historyEntry": { ...optional; defaults to "current"... }
  }
"current" is overlaid on the existing syndication block, so fields you do not
mention are preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from urllib.parse import urlparse

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is required by the build too
    yaml = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOG_DIR = os.path.join(REPO_ROOT, "src", "content", "blog")
DEFAULT_LEDGER = os.path.join(REPO_ROOT, ".hermes", "cron-drafts", "syndication-ledger.jsonl")
DEFAULT_KEEP = 3

# Must stay byte-for-byte equivalent to sns_precheck.FRONTMATTER_RE so the
# body extracted here hashes identically to the body the precheck hashes.
# (A naive split gives an off-by-one newline and a false "body changed".)
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

PLATFORMS = ("x", "threads", "linkedin", "dev.to")
STATUSES = ("pending", "skipped", "published")

# The draft size fields must be BARE INTEGERS (no unit suffix, no
# "214/280 weighted (raw 178자)" composite). The renderer appends the
# unit itself, so a value that already carries one renders as "475자자",
# and a BYTE count labelled as a character count misreports the draft's
# size (Korean text is 3 UTF-8 bytes per character). src/content.config.ts
# enforces the same rule at build time; SchemaDriftTest keeps the two in
# sync.
DRAFT_COUNT_RE = re.compile(r"^[0-9]+$")


class FrontmatterError(Exception):
    """Raised for malformed input or a refused write."""


# ---------------------------------------------------------------- primitives


def split_document(raw: str) -> tuple:
    """Return (prefix, frontmatter_text, tail, body).

    prefix  = raw up to (not including) the frontmatter text -- i.e. the
              opening '---' line.
    tail    = raw from the end of the frontmatter text onwards -- i.e. the
              closing '---' line and everything after it.
    body    = exactly what sns_precheck hashes: raw[match.end():].

    Rebuilding a document as prefix + fm_text + tail is byte-identical to
    the original for any fm_text.
    """
    match = FRONTMATTER_RE.match(raw)
    if not match:
        raise FrontmatterError("no YAML frontmatter found (expected a leading '---' block)")
    prefix = raw[: match.start(1)]
    fm_text = match.group(1)
    tail = raw[match.end(1):]
    body = raw[match.end():]
    return prefix, fm_text, tail, body


def body_sha256(raw: str) -> str:
    """SHA-256 of the body, computed exactly like sns_precheck.body_hash."""
    _prefix, _fm, _tail, body = split_document(raw)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def slug_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def resolve_post(target: str) -> str:
    """Accept a filesystem path or a blog slug and return an existing path."""
    if os.path.isfile(target):
        return os.path.abspath(target)
    candidate = os.path.join(BLOG_DIR, "%s.md" % target)
    if os.path.isfile(candidate):
        return candidate
    raise FrontmatterError("post not found: %s" % target)


# ------------------------------------------------------------- yaml handling


def parse_frontmatter(fm_text: str) -> dict:
    if yaml is None:
        raise FrontmatterError("PyYAML is required but not importable")
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise FrontmatterError("frontmatter is not valid YAML: %s" % exc)
    return data if isinstance(data, dict) else {}


def find_syndication_span(fm_text: str):
    """Return (start, end) offsets of the top-level syndication key, or None.

    Offsets come from the YAML parser's node marks, so multi-line scalars
    are handled correctly. None means the frontmatter is a valid mapping
    that simply has no 'syndication' key yet -- the state of a BRAND-NEW
    post, which the writer has to be able to bootstrap.
    """
    if yaml is None:
        raise FrontmatterError("PyYAML is required but not importable")
    try:
        root = yaml.compose(fm_text)
    except yaml.YAMLError as exc:
        raise FrontmatterError("frontmatter is not valid YAML: %s" % exc)
    if root is None or not isinstance(root, yaml.MappingNode):
        raise FrontmatterError("frontmatter is not a YAML mapping")
    for key_node, value_node in root.value:
        if getattr(key_node, "value", None) == "syndication":
            start = key_node.start_mark.index
            end = value_node.end_mark.index
            return start, end
    return None


def syndication_span(fm_text: str) -> tuple:
    """Like find_syndication_span, but raise when the block is absent."""
    span = find_syndication_span(fm_text)
    if span is None:
        raise FrontmatterError("frontmatter has no 'syndication:' block")
    return span


def render_syndication(syndication: dict) -> str:
    """Render a syndication mapping as a 'syndication:' frontmatter block."""
    if yaml is None:
        raise FrontmatterError("PyYAML is required but not importable")
    try:
        dumped = yaml.safe_dump(
            {"syndication": syndication},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=1000000,
        )
    except yaml.YAMLError as exc:
        raise FrontmatterError("cannot serialise syndication block: %s" % exc)
    return dumped.rstrip("\n")


# ------------------------------------------------------------- schema checks


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in ("http", "https") and parsed.netloc)


def validate_syndication(syndication: dict) -> None:
    """Mirror src/content.config.ts so a bad write cannot break the build."""
    if not isinstance(syndication, dict):
        raise FrontmatterError("syndication block must be a mapping")
    for field in ("attemptedAt", "worker"):
        if field not in syndication:
            raise FrontmatterError("syndication.%s is required by the content schema" % field)
        if not isinstance(syndication[field], str):
            raise FrontmatterError("syndication.%s must be a string" % field)
    targets = syndication.get("targets")
    if not isinstance(targets, list):
        raise FrontmatterError("syndication.targets must be a list")
    for target in targets:
        if not isinstance(target, dict):
            raise FrontmatterError("each syndication.targets entry must be a mapping")
        platform = target.get("platform")
        status = target.get("status")
        if platform not in PLATFORMS:
            raise FrontmatterError(
                "targets[].platform must be one of %s (got %r)" % (", ".join(PLATFORMS), platform))
        if status not in STATUSES:
            raise FrontmatterError(
                "targets[].status must be one of %s (got %r)" % (", ".join(STATUSES), status))
        for optional in ("reason", "draft", "draftChars", "draftBytes"):
            if optional in target and not isinstance(target[optional], str):
                raise FrontmatterError("targets[].%s must be a string" % optional)
        # The draft size fields must be BARE INTEGERS. The renderer appends
        # the unit itself, so a value that already carries one renders as
        # "475자자"; and a byte count labelled as a character count
        # misreports the size (Korean text is 3 UTF-8 bytes per char).
        # src/content.config.ts enforces the same rule at build time.
        for numeric in ("draftChars", "draftBytes"):
            if numeric in target and not DRAFT_COUNT_RE.match(target[numeric]):
                raise FrontmatterError(
                    "targets[].%s must be a bare integer string (no unit "
                    "suffix, no composite) -- got %r" % (numeric, target[numeric]))
        if "url" in target:
            if not isinstance(target["url"], str) or not _is_url(target["url"]):
                raise FrontmatterError("targets[].url must be a valid http(s) URL")


# ------------------------------------------------------------------ rewriting


def build_new_raw(raw: str, syndication: dict, create: bool = False) -> str:
    """Return `raw` with the syndication block replaced; body untouched.

    When `create` is True and the post has no syndication block yet, the
    rendered block is APPENDED to the frontmatter instead of replacing an
    existing one. That is the bootstrap path for a brand-new post: the
    writer is the only sanctioned way to record a publish attempt, and a
    post that has never been syndicated has no block to replace.

    Either way only the frontmatter changes -- the body is copied
    verbatim and its SHA-256 is asserted to be unchanged.
    """
    prefix, fm_text, tail, _body = split_document(raw)
    span = find_syndication_span(fm_text)
    if span is None:
        if not create:
            raise FrontmatterError("frontmatter has no 'syndication:' block")
        # fm_text is the frontmatter body without its trailing newline, so
        # the separator has to be added unless it is already there. The
        # block goes in at column 0, which correctly terminates any
        # multi-line scalar the previous key may have used.
        separator = "" if fm_text.endswith("\n") else "\n"
        new_fm_text = fm_text + separator + render_syndication(syndication)
    else:
        start, end = span
        new_fm_text = fm_text[:start] + render_syndication(syndication) + fm_text[end:]
    new_raw = prefix + new_fm_text + tail
    # The body must survive the rewrite byte-for-byte.
    if body_sha256(new_raw) != body_sha256(raw):
        raise FrontmatterError("body changed during rewrite -- refusing to write")
    return new_raw


def write_atomic(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    handle, tmp_path = tempfile.mkstemp(dir=directory, prefix=".sns-fm-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(content)
        try:
            os.chmod(tmp_path, os.stat(path).st_mode & 0o7777)
        except OSError:
            os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def append_ledger(path: str, entry: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _load_post(path: str) -> tuple:
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    _prefix, fm_text, _tail, _body = split_document(raw)
    return raw, parse_frontmatter(fm_text)


def _keep_history(history: list, keep: int) -> list:
    # `len(history) - keep` can be NEGATIVE (a post that has fewer
    # entries than `keep` -- e.g. a brand-new post on its first fires),
    # and a negative slice index silently selects from the END of the
    # list: history[-1:] keeps ONE entry instead of all of them. Clamp
    # at 0 so "keep the last N" never drops an entry it should keep.
    if not keep:
        return []
    return history[max(0, len(history) - keep):]


# ------------------------------------------------------------------- commands


def cmd_status(args) -> int:
    path = resolve_post(args.post)
    raw, data = _load_post(path)
    _prefix, fm_text, _tail, body = split_document(raw)
    syndication = data.get("syndication") or {}
    history = syndication.get("history") or []
    payload = {
        "path": path,
        "slug": slug_from_path(path),
        "frontmatterLines": len(fm_text.split("\n")),
        "bodyChars": len(body),
        "bodySha256": body_sha256(raw),
        "historyLength": len(history),
        "historyAttemptedAt": [entry.get("attemptedAt") for entry in history
                               if isinstance(entry, dict)],
        "hasTargets": bool(syndication.get("targets")),
        "keep": args.keep if args.keep is not None else DEFAULT_KEEP,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_trim(args) -> int:
    path = resolve_post(args.post)
    raw, data = _load_post(path)
    keep = args.keep if args.keep is not None else DEFAULT_KEEP
    if keep < 0:
        raise FrontmatterError("--keep must be >= 0")
    if not data.get("syndication"):
        # A post that was never syndicated has no history to trim. Do NOT
        # fabricate a block here: 'syndication' is optional in the content
        # schema but, when present, it requires attemptedAt/worker/targets,
        # and inventing them would claim an attempt that never happened.
        result = {
            "path": path,
            "removedHistoryEntries": 0,
            "historyLength": 0,
            "keep": keep,
            "bodySha256Before": body_sha256(raw),
            "bodySha256After": body_sha256(raw),
            "dryRun": bool(args.dry_run),
            "written": False,
            "skipped": "post has no 'syndication:' block; nothing to trim",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    syndication = dict(data.get("syndication") or {})
    history = list(syndication.get("history") or [])
    removed = max(0, len(history) - keep)
    syndication["history"] = _keep_history(history, keep)
    validate_syndication(syndication)
    new_raw = build_new_raw(raw, syndication)

    result = {
        "path": path,
        "removedHistoryEntries": removed,
        "historyLength": len(syndication["history"]),
        "keep": keep,
        "bodySha256Before": body_sha256(raw),
        "bodySha256After": body_sha256(new_raw),
        "dryRun": bool(args.dry_run),
        "written": False,
    }
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    write_atomic(path, new_raw)
    result["written"] = True
    if removed and args.ledger:
        append_ledger(args.ledger, {
            "type": "history_trim",
            "ts": args.timestamp,
            "blog": slug_from_path(path),
            "removed": removed,
            "kept": [entry.get("attemptedAt") for entry in syndication["history"]
                     if isinstance(entry, dict)],
            "note": "history entries dropped from article frontmatter; the full trail "
                    "remains in the git history of this file and in the ledger",
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_record(args) -> int:
    path = resolve_post(args.post)
    raw, data = _load_post(path)
    existing = dict(data.get("syndication") or {})

    try:
        with open(args.payload, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise FrontmatterError("cannot read payload %s: %s" % (args.payload, exc))
    if not isinstance(payload, dict):
        raise FrontmatterError("payload must be a JSON object")

    current = payload.get("current")
    if not isinstance(current, dict):
        raise FrontmatterError("payload must contain a 'current' object")
    history_entry = payload.get("historyEntry", current)
    if not isinstance(history_entry, dict):
        raise FrontmatterError("payload['historyEntry'] must be an object")
    if "history" in history_entry:
        raise FrontmatterError("payload['historyEntry'] must not contain 'history'")

    keep = args.keep if args.keep is not None else DEFAULT_KEEP
    if keep < 0:
        raise FrontmatterError("--keep must be >= 0")

    syndication = {key: value for key, value in existing.items() if key != "history"}
    syndication.update(current)
    history = list(existing.get("history") or [])
    history.append(history_entry)
    syndication["history"] = _keep_history(history, keep)
    validate_syndication(syndication)
    # create=True: a brand-new post has no 'syndication:' block to
    # replace, and the writer is the only sanctioned way to record a
    # publish attempt -- so the block is bootstrapped here. The payload is
    # still validated above, so invalid input is refused before any write.
    new_raw = build_new_raw(raw, syndication, create=True)
    result = {
        "path": path,
        "historyLength": len(syndication["history"]),
        "keep": keep,
        "bodySha256Before": body_sha256(raw),
        "bodySha256After": body_sha256(new_raw),
        "dryRun": bool(args.dry_run),
        "written": False,
    }
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    write_atomic(path, new_raw)
    result["written"] = True
    if args.ledger:
        _prefix, _fm, _tail, body = split_document(new_raw)
        # The ledger row's top-level 'ts' must NEVER be empty:
        # sns_precheck.read_ledger() copies it into the pending record's
        # 'ts', and an empty string there silently erases the attempt time
        # from the pending-publish audit trail. --timestamp is optional
        # (default ''), so fall back to the payload's own 'attemptedAt',
        # which validate_syndication() guarantees to be a present string.
        ledger_ts = (
            args.timestamp
            or str(current.get("attemptedAt") or "")
            or str(history_entry.get("attemptedAt") or "")
        )
        append_ledger(args.ledger, {
            "type": "syndication_record",
            "ts": ledger_ts,
            "blog": slug_from_path(path),
            "bodySha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "bodyChars": len(body),
            "worker": syndication.get("worker"),
            "outcome": syndication.get("outcome"),
            "publishPending": syndication.get("publishPending"),
            "blockedBy": syndication.get("blockedBy"),
            "targets": syndication.get("targets"),
            "entry": history_entry,
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="bounded syndication frontmatter writer")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status")
    status.add_argument("--post", required=True)
    status.add_argument("--keep", type=int, default=None)

    trim = sub.add_parser("trim")
    trim.add_argument("--post", required=True)
    trim.add_argument("--keep", type=int, default=None)
    trim.add_argument("--dry-run", dest="dry_run", action="store_true")
    trim.add_argument("--ledger", default=DEFAULT_LEDGER)
    trim.add_argument("--timestamp", default="")

    record = sub.add_parser("record")
    record.add_argument("--post", required=True)
    record.add_argument("--payload", required=True)
    record.add_argument("--keep", type=int, default=None)
    record.add_argument("--dry-run", dest="dry_run", action="store_true")
    record.add_argument("--ledger", default=DEFAULT_LEDGER)
    record.add_argument("--timestamp", default="")

    args = parser.parse_args(argv)
    handlers = {"status": cmd_status, "trim": cmd_trim, "record": cmd_record}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1
    try:
        return handler(args)
    except FrontmatterError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
