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


if __name__ == "__main__":
    unittest.main(verbosity=2)
