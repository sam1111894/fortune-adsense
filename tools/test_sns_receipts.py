#!/usr/bin/env python3
"""Offline tests for the durable publish-receipt store (tools/sns_receipts.py)
and the duplicate-publish guard it powers in tools/sns_publish.py.

These do NOT hit the network: the receipt store is a local file and every
HTTP call is monkeypatched.

The scenario under test is the expensive one: X charges $0.200 per Post, and
the syndication ledger is written by a SEPARATE, LATER step. If the process
dies between "the API accepted the post" and "the ledger line was written",
the next fire sees no evidence of a publish and repeats the write.

Run: python3 tools/test_sns_receipts.py
"""

import argparse
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_publish as sp  # noqa: E402
import sns_receipts as sr  # noqa: E402


class ReceiptTestCase(unittest.TestCase):
    """Points the receipt store at a temp file for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.receipt_path = os.path.join(self.tmp.name, "receipts.jsonl")
        self._old = os.environ.get(sr.PATH_ENV)
        os.environ[sr.PATH_ENV] = self.receipt_path

    def tearDown(self):
        if self._old is None:
            os.environ.pop(sr.PATH_ENV, None)
        else:
            os.environ[sr.PATH_ENV] = self._old
        self.tmp.cleanup()

    def records(self):
        return sr.load()


class TestReceiptStore(ReceiptTestCase):
    def test_load_on_missing_file_is_empty(self):
        self.assertEqual(sr.load(), [])

    def test_append_creates_file_and_records(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending"})
        self.assertTrue(os.path.isfile(self.receipt_path))
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["attemptId"], "a1")

    def test_append_is_durable_and_append_only(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending"})
        sr.append({"platform": "x", "attemptId": "a1", "state": "published"})
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["state"], "pending")
        self.assertEqual(records[1]["state"], "published")

    def test_torn_line_is_skipped_not_fatal(self):
        """A crash mid-append can leave a partial line. The store must not
        lose the PREVIOUS intact record because of it."""
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending"})
        with open(self.receipt_path, "a", encoding="utf-8") as fh:
            fh.write('{"platform": "x", "attemptId": "a2", "sta')
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "pending")

    def test_text_key_is_stable_and_content_specific(self):
        self.assertEqual(sr.text_key("hello"), sr.text_key("hello"))
        self.assertNotEqual(sr.text_key("hello"), sr.text_key("hello "))

    def test_group_attempts_keeps_last_state_per_attempt(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": "T"})
        sr.append({"platform": "x", "attemptId": "a1", "state": "published",
                   "textSha256": "T", "postId": "123"})
        attempts = sr.group_attempts(self.records(), "x")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "published")
        self.assertEqual(attempts[0]["postId"], "123")

    def test_group_attempts_separates_platforms(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "published",
                   "textSha256": "T"})
        sr.append({"platform": "threads", "attemptId": "a2",
                   "state": "published", "textSha256": "T"})
        self.assertEqual(len(sr.group_attempts(self.records(), "x")), 1)
        self.assertEqual(len(sr.group_attempts(self.records(), "threads")), 1)

    def test_find_published_matches_by_text_or_content(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "published",
                   "textSha256": "T", "contentSha256": "C"})
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNotNone(sr.find_published(attempts, "T", None))
        self.assertIsNotNone(sr.find_published(attempts, "other", "C"))
        self.assertIsNone(sr.find_published(attempts, "other", "other"))

    def test_find_pending_finds_only_unresolved_attempts(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": "T"})
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNotNone(sr.find_pending(attempts, "T", None))
        sr.append({"platform": "x", "attemptId": "a1", "state": "published",
                   "textSha256": "T"})
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNone(sr.find_pending(attempts, "T", None))


class TestReceiptGuard(ReceiptTestCase):
    """The guard is what stands between a crash and a paid duplicate."""

    def test_proceeds_when_no_receipt_exists(self):
        verdict, _reason = sp._receipt_guard("x", "T", None)
        self.assertEqual(verdict, "proceed")

    def test_skips_when_content_already_published(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "published",
                   "textSha256": "T", "postId": "999"})
        verdict, reason = sp._receipt_guard("x", "T", None)
        self.assertEqual(verdict, "skip")
        self.assertIn("999", reason)

    def test_blocks_when_a_previous_attempt_is_unresolved(self):
        """No resolver -> outcome unknown -> refuse to publish again."""
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": "T"})
        verdict, reason = sp._receipt_guard("x", "T", None)
        self.assertEqual(verdict, "block")
        self.assertIn("a1", reason)

    def test_resolver_published_marks_attempt_and_skips(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": "T"})
        verdict, _reason = sp._receipt_guard(
            "x", "T", None, resolver=lambda attempt: "published")
        self.assertEqual(verdict, "skip")
        states = [r["state"] for r in self.records()]
        self.assertIn("published", states)
        # The ambiguity is now settled: no pending attempt remains.
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNone(sr.find_pending(attempts, "T", None))

    def test_resolver_failed_marks_attempt_and_proceeds(self):
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": "T"})
        verdict, _reason = sp._receipt_guard(
            "x", "T", None, resolver=lambda attempt: "failed")
        self.assertEqual(verdict, "proceed")
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNone(sr.find_pending(attempts, "T", None))


class TestXPendingResolver(ReceiptTestCase):
    """The X resolver decides an unresolved attempt using the FREE tweet
    count (GET /1.1/account/verify_credentials.json)."""

    def _attempt(self, before):
        return {"attemptId": "a1", "statusesCountBefore": before}

    def test_higher_count_means_the_write_landed(self):
        orig = sp.x_request
        sp.x_request = lambda *a, **k: (200, json.dumps({"statuses_count": 5}))
        try:
            verdict = sp._resolve_x_pending({}, self._attempt(4))
        finally:
            sp.x_request = orig
        self.assertEqual(verdict, "published")

    def test_unchanged_count_means_the_write_did_not_land(self):
        orig = sp.x_request
        sp.x_request = lambda *a, **k: (200, json.dumps({"statuses_count": 4}))
        try:
            verdict = sp._resolve_x_pending({}, self._attempt(4))
        finally:
            sp.x_request = orig
        self.assertEqual(verdict, "failed")

    def test_unreadable_count_stays_unresolved(self):
        orig = sp.x_request
        sp.x_request = lambda *a, **k: (500, "boom")
        try:
            verdict = sp._resolve_x_pending({}, self._attempt(4))
        finally:
            sp.x_request = orig
        self.assertIsNone(verdict)

    def test_missing_snapshot_stays_unresolved(self):
        """Without the pre-write snapshot there is nothing to compare, and
        guessing would risk a duplicate."""
        orig = sp.x_request
        sp.x_request = lambda *a, **k: (200, json.dumps({"statuses_count": 5}))
        try:
            verdict = sp._resolve_x_pending({}, self._attempt(None))
        finally:
            sp.x_request = orig
        self.assertIsNone(verdict)


class TestPublishXReceiptLifecycle(ReceiptTestCase):
    """End-to-end (offline) check that cmd_publish_x writes the receipt
    lifecycle around the billable POST."""

    class ByUrl(object):
        """Dispatch canned responses by URL, so the test does not depend on
        the exact ORDER of the internal free reads."""

        def __init__(self, mapping, default=None):
            self.mapping = mapping
            self.default = default
            self.calls = []

        def __call__(self, url, method="GET", params=None, body=None,
                     headers=None, timeout=25, json_body=None):
            self.calls.append({"url": url, "method": method,
                               "params": params or {}, "body": body,
                               "headers": headers or {},
                               "json_body": json_body})
            for needle, response in self.mapping.items():
                if needle in url:
                    return response
            if self.default is not None:
                return self.default
            return 200, json.dumps({"ok": True})

        def tweet_posts(self):
            return [c for c in self.calls
                    if c["method"] == "POST" and "tweets" in c["url"]]

    def _write_draft(self, text="hello world https://example.com/a"):
        path = os.path.join(self.tmp.name, "01-x-tweet.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _env(self):
        return {"X_API_KEY": "ck", "X_API_SECRET": "cs",
                "X_ACCESS_TOKEN": "at", "X_ACCESS_TOKEN_SECRET": "ats"}

    def _run(self, fake, draft, content_sha=""):
        orig_http, orig_env = sp.http_request, sp.load_env_files
        sp.http_request = fake
        sp.load_env_files = lambda paths=None: (self._env(), [])
        try:
            return sp.cmd_publish_x(argparse.Namespace(
                text_file=draft, skip_credit_check=False,
                content_sha=content_sha))
        finally:
            sp.http_request, sp.load_env_files = orig_http, orig_env

    def _funded_fake(self, statuses_count=0, post_status=201,
                     post_body=None):
        if post_body is None:
            post_body = json.dumps({"data": {"id": "42", "username": "u"}})
        return self.ByUrl({
            "oauth2/token": (200, json.dumps({"access_token": "bearer"})),
            "/2/usage/credits": (200, json.dumps(
                {"data": {"total_balance": 1.0}})),
            "verify_credentials": (200, json.dumps(
                {"statuses_count": statuses_count})),
            "/2/tweets": (post_status, post_body),
        })

    def test_success_writes_pending_then_published(self):
        draft = self._write_draft()
        code = self._run(self._funded_fake(), draft)
        self.assertEqual(code, 0)
        states = [r["state"] for r in self.records()]
        self.assertEqual(states, ["pending", "published"])
        self.assertEqual(self.records()[1]["postId"], "42")

    def test_4xx_writes_pending_then_failed(self):
        """A definitive rejection creates nothing, so a retry is safe."""
        draft = self._write_draft()
        fake = self._funded_fake(
            post_status=402,
            post_body=json.dumps({"detail": "credits depleted"}))
        code = self._run(fake, draft)
        self.assertEqual(code, 3)
        states = [r["state"] for r in self.records()]
        self.assertEqual(states, ["pending", "failed"])

    def test_network_error_leaves_attempt_pending(self):
        """An ambiguous outcome must NOT be recorded as failed: the write
        may have landed, and the next run must resolve it rather than
        pay twice."""
        draft = self._write_draft()
        fake = self.ByUrl({
            "oauth2/token": (200, json.dumps({"access_token": "bearer"})),
            "/2/usage/credits": (200, json.dumps(
                {"data": {"total_balance": 1.0}})),
            "verify_credentials": (200, json.dumps({"statuses_count": 0})),
            "/2/tweets": (0, json.dumps({"error": "connection reset"})),
        })
        code = self._run(fake, draft)
        self.assertEqual(code, 1)
        states = [r["state"] for r in self.records()]
        self.assertEqual(states, ["pending"])

    def test_second_run_after_success_does_not_post_again(self):
        """THE regression: the second run must not issue a second POST."""
        draft = self._write_draft()
        fake = self._funded_fake()
        first = self._run(fake, draft)
        posts_after_first = len(fake.tweet_posts())
        second = self._run(fake, draft)
        posts_after_second = len(fake.tweet_posts())
        self.assertEqual(first, 0)
        self.assertEqual(second, 2)
        self.assertEqual(posts_after_first, 1)
        self.assertEqual(posts_after_second, 1)

    def test_unresolved_attempt_with_unchanged_count_is_retried_safely(self):
        """A pending attempt from a crashed run is resolved by the free tweet
        count. Unchanged count -> the write did NOT land -> retry is safe
        and exactly one more POST is issued."""
        draft = self._write_draft()
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": sr.text_key("hello world https://example.com/a"),
                   "statusesCountBefore": 0})
        fake = self._funded_fake(statuses_count=0)
        code = self._run(fake, draft)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.tweet_posts()), 1)
        # The stale pending attempt is settled, so it cannot wedge the
        # pipeline forever.
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNone(sr.find_pending(
            attempts, sr.text_key("hello world https://example.com/a"), None))

    def test_unresolved_attempt_with_higher_count_is_not_reposted(self):
        """A higher tweet count means the earlier write DID land. The next
        run must skip it -- no second POST, no second charge."""
        draft = self._write_draft()
        text_sha = sr.text_key("hello world https://example.com/a")
        sr.append({"platform": "x", "attemptId": "a1", "state": "pending",
                   "textSha256": text_sha,
                   "statusesCountBefore": 0})
        fake = self._funded_fake(statuses_count=1)
        code = self._run(fake, draft)
        self.assertEqual(code, 2)
        self.assertEqual(fake.tweet_posts(), [])
        attempts = sr.group_attempts(self.records(), "x")
        self.assertIsNone(sr.find_pending(attempts, text_sha, None))
        self.assertIsNotNone(sr.find_published(attempts, text_sha, None))


class TestPublishAllForwardsContentSha(ReceiptTestCase):
    def test_publish_all_forwards_content_sha_to_handlers(self):
        """The content hash must reach the handlers, or cross-draft dedup
        silently degrades to text-only matching."""
        import tempfile
        seen = {}
        with tempfile.TemporaryDirectory() as tmp:
            for name in sp.DRAFT_FILENAME.values():
                with open(os.path.join(tmp, name), "w") as fh:
                    fh.write("draft")
            orig_env = sp.load_env_files
            sp.load_env_files = lambda paths=None: ({
                "X_API_KEY": "ck", "X_API_SECRET": "cs",
                "X_ACCESS_TOKEN": "at", "X_ACCESS_TOKEN_SECRET": "ats",
                "THREADS_TOKEN": "tt", "LINKEDIN_TOKEN": "lt",
                "DEVTO_API_KEY": "dk"}, [])
            origs = (sp.cmd_publish_x, sp.cmd_publish_threads,
                      sp.cmd_publish_linkedin, sp.cmd_publish_devto)
            try:
                sp.cmd_publish_x = lambda a: seen.__setitem__("x", a.content_sha) or 0
                sp.cmd_publish_threads = lambda a: seen.__setitem__("threads", a.content_sha) or 0
                sp.cmd_publish_linkedin = lambda a: seen.__setitem__("linkedin", a.content_sha) or 0
                sp.cmd_publish_devto = lambda a: seen.__setitem__("dev.to", a.content_sha) or 0
                ns = argparse.Namespace(drafts_dir=tmp, devto_title="",
                                        devto_tags="", devto_canonical="",
                                        content_sha="BODYHASH")
                sp.cmd_publish_all(ns)
            finally:
                sp.load_env_files = orig_env
                (sp.cmd_publish_x, sp.cmd_publish_threads,
                 sp.cmd_publish_linkedin, sp.cmd_publish_devto) = origs
        self.assertEqual(seen, {"x": "BODYHASH", "threads": "BODYHASH",
                                "linkedin": "BODYHASH", "dev.to": "BODYHASH"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
