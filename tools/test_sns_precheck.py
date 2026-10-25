#!/usr/bin/env python3
"""Offline tests for tools/sns_precheck.py.

These do NOT hit the network: the precheck only reads local files.

Run: python3 tools/test_sns_precheck.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_precheck as pre  # noqa: E402


def _entry(slug, digest, pending=None, ts="2026-10-23T00:00:00Z"):
    row = {
        "type": "syndication_record",
        "blog": slug,
        "bodySha256": digest,
        "bodyChars": 3996,
        "ts": ts,
    }
    if pending is not None:
        row["publishPending"] = pending
    return row


class TestLedgerPendingState(unittest.TestCase):
    """The pending flag must be LAST-EXPLICIT-STATEMENT-WINS.

    Regression: `pending` used to be monotonic (only ever added to), so once
    an article had one blocked attempt it stayed "pending" forever. After a
    successful publish the precheck could therefore never return
    NO_NEW_POST and the cron re-fired on an already-published article on
    every cycle.
    """

    def _write(self, rows):
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_pending_is_recorded_when_the_ledger_says_so(self):
        path = self._write([_entry("post-a", "sha-a", pending=True)])
        _hashes, pending = pre.read_ledger(path)
        self.assertIn("post-a", pending)
        self.assertEqual(pending["post-a"]["ts"], "2026-10-23T00:00:00Z")

    def test_later_publish_pending_false_clears_an_older_pending_flag(self):
        path = self._write([
            _entry("post-a", "sha-a", pending=True, ts="2026-10-23T00:00:00Z"),
            _entry("post-a", "sha-a", pending=False, ts="2026-10-23T01:00:00Z"),
        ])
        _hashes, pending = pre.read_ledger(path)
        self.assertNotIn("post-a", pending)

    def test_later_pending_true_re_arms_the_pending_flag(self):
        path = self._write([
            _entry("post-a", "sha-a", pending=False, ts="2026-10-23T00:00:00Z"),
            _entry("post-a", "sha-a", pending=True, ts="2026-10-23T01:00:00Z"),
        ])
        _hashes, pending = pre.read_ledger(path)
        self.assertIn("post-a", pending)

    def test_absent_publishPending_key_does_not_clear_the_pending_flag(self):
        """A record that says nothing about pending state must not be read
        as 'the publish completed'."""
        path = self._write([
            _entry("post-a", "sha-a", pending=True, ts="2026-10-23T00:00:00Z"),
            _entry("post-a", "sha-a", pending=None, ts="2026-10-23T01:00:00Z"),
        ])
        _hashes, pending = pre.read_ledger(path)
        self.assertIn("post-a", pending)

    def test_one_slug_clearing_does_not_clear_another(self):
        path = self._write([
            _entry("post-a", "sha-a", pending=True),
            _entry("post-b", "sha-b", pending=True),
            _entry("post-a", "sha-a", pending=False),
        ])
        _hashes, pending = pre.read_ledger(path)
        self.assertNotIn("post-a", pending)
        self.assertIn("post-b", pending)

    def test_hash_map_keeps_the_latest_body_hash(self):
        path = self._write([
            _entry("post-a", "sha-old", pending=True),
            _entry("post-a", "sha-new", pending=True),
        ])
        hashes, _pending = pre.read_ledger(path)
        self.assertEqual(hashes["post-a"], "sha-new")

    def test_missing_ledger_file_is_not_an_error(self):
        hashes, pending = pre.read_ledger("/nonexistent/ledger.jsonl")
        self.assertEqual(hashes, {})
        self.assertEqual(pending, {})

    def test_torn_line_is_skipped(self):
        path = self._write([_entry("post-a", "sha-a", pending=True)])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"blog": "post-a", "bodySha256": "sha-b"\n')  # torn
        hashes, pending = pre.read_ledger(path)
        self.assertEqual(hashes["post-a"], "sha-a")
        self.assertIn("post-a", pending)


class TestBodyHashExtraction(unittest.TestCase):
    def test_body_hash_ignores_the_frontmatter(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("---\ntitle: \"t\"\nslug: \"s\"\n---\n본문\n")
            path = handle.name
        self.addCleanup(os.unlink, path)
        digest, length, slug = pre.body_hash(path)
        self.assertEqual(slug, "s")
        self.assertEqual(length, len("본문\n"))
        with open(path, encoding="utf-8") as handle:
            again, _length, _slug = pre.body_hash(path)
        self.assertEqual(digest, again)

    def test_frontmatter_only_edit_keeps_the_body_hash(self):
        """A syndication-frontmatter rewrite must not look like new content."""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("---\ntitle: \"t\"\nslug: \"s\"\n---\n본문\n")
            path = handle.name
        self.addCleanup(os.unlink, path)
        before, _l, _s = pre.body_hash(path)
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw.replace('title: "t"', 'title: "t2"\nsyndication: {}'))
        after, _l, _s = pre.body_hash(path)
        self.assertEqual(before, after)


class TestSelectTarget(unittest.TestCase):
    """The post a fire acts on must not be chosen by mtime alone.

    Regression: `main()` used `posts[0]` -- the mtime-newest post -- as
    the only candidate. But recording a syndication attempt rewrites the
    article's frontmatter, which BUMPS ITS MTIME, so the post published
    last is normally also the newest on disk. A publish that never
    completed on a DIFFERENT post therefore sat below the newest one
    and was skipped forever, even though `read_ledger()` promises that
    pending slugs "must be RETRIED, not skipping".
    """

    def _post(self, slug, digest, mtime):
        return {
            "file": slug + ".md",
            "slug": slug,
            "bodySha256": digest,
            "bodyChars": 100,
            "mtime": mtime,
        }

    def test_pending_post_below_the_newest_is_still_retried(self):
        """THE DEFECT: the pending post is older on disk than another post."""
        posts = [
            self._post("newer-post", "sha-newer", 200.0),
            self._post("pending-post", "sha-pending", 100.0),
        ]
        known = {"pending-post": "sha-pending", "newer-post": "sha-newer"}
        pending = {"pending-post": {"blockedBy": "credit", "outcome": "X",
                                   "ts": "2026-10-25T00:00:00Z"}}
        selection = pre.select_target(posts, known, pending)
        self.assertEqual(selection["decision"], "RETRY_PENDING_PUBLISH")
        self.assertEqual(selection["target"]["slug"], "pending-post")

    def test_new_content_still_wins_over_a_pending_retry(self):
        """A genuinely new post must not be starved by an older pending one."""
        posts = [
            self._post("new-post", "sha-brand-new", 200.0),
            self._post("pending-post", "sha-pending", 100.0),
        ]
        known = {"pending-post": "sha-pending"}
        pending = {"pending-post": {"blockedBy": "credit", "outcome": "X",
                                   "ts": "2026-10-25T00:00:00Z"}}
        selection = pre.select_target(posts, known, pending)
        self.assertEqual(selection["decision"], "SYNTHESIZE")
        self.assertEqual(selection["target"]["slug"], "new-post")

    def test_changed_body_on_a_pending_post_is_not_a_retry_candidate(self):
        """A pending post whose body changed is NEW content, not a retry."""
        posts = [self._post("post-a", "sha-edited", 100.0)]
        known = {"post-a": "sha-old"}
        pending = {"post-a": {"blockedBy": "credit", "outcome": "X",
                              "ts": "2026-10-25T00:00:00Z"}}
        selection = pre.select_target(posts, known, pending)
        self.assertEqual(selection["decision"], "SYNTHESIZE")
        self.assertEqual(selection["retryCandidates"], [])

    def test_oldest_pending_attempt_is_retried_first(self):
        """With several pending posts, the oldest attempt goes first so
        none of them starves behind another."""
        posts = [
            self._post("post-a", "sha-a", 300.0),
            self._post("post-b", "sha-b", 200.0),
        ]
        known = {"post-a": "sha-a", "post-b": "sha-b"}
        pending = {
            "post-a": {"blockedBy": "credit", "outcome": "X",
                       "ts": "2026-10-25T09:00:00Z"},
            "post-b": {"blockedBy": "credit", "outcome": "X",
                       "ts": "2026-10-25T01:00:00Z"},
        }
        selection = pre.select_target(posts, known, pending)
        self.assertEqual(selection["decision"], "RETRY_PENDING_PUBLISH")
        self.assertEqual(selection["target"]["slug"], "post-b")
        self.assertEqual(
            sorted(p["slug"] for p in selection["retryCandidates"]),
            ["post-a", "post-b"])

    def test_no_pending_and_known_body_is_no_new_post(self):
        posts = [self._post("post-a", "sha-a", 100.0)]
        known = {"post-a": "sha-a"}
        selection = pre.select_target(posts, known, {})
        self.assertEqual(selection["decision"], "NO_NEW_POST")
        self.assertIsNone(selection["target"])

    def test_empty_post_list_is_no_new_post(self):
        selection = pre.select_target([], {}, {})
        self.assertEqual(selection["decision"], "NO_NEW_POST")
        self.assertIsNone(selection["target"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
