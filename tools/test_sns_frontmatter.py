#!/usr/bin/env python3
"""Offline tests for tools/sns_frontmatter.py -- the frontmatter writer.

These do NOT hit the network and do NOT touch the real blog: every test
writes to a temporary directory.

WHY THIS SUITE EXISTS
---------------------
The writer could not bootstrap a BRAND-NEW post. `record` called
`syndication_span()`, which raised

    ERROR: frontmatter has no 'syndication:' block

for any post that had never been syndicated -- i.e. exactly the post the
cron job is triggered by ("the user wrote a new blog .md"). The publish
attempt itself worked, but the sanctioned way to RECORD that attempt was
unusable, so the syndication state of a new post could never be written
to the article. The failure only showed up on a post that had never been
syndicated, which is why it survived: every post in the repo that had
been through a fire already had a block.

The regression tests below pin the bootstrap path: a new post must gain a
valid block, the body must not change, the other frontmatter keys must
survive, and invalid input must still be refused.

Run: python3 tools/test_sns_frontmatter.py
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_frontmatter as sf  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A brand-new post: valid frontmatter per src/content.config.ts, but with
# NO 'syndication:' block -- the state that used to break the writer.
NEW_POST = """---
title: "새로운 글"
description: "설명"
pubDate: 2026-10-24
tags: ["오늘의운세", "마인드풀니스"]
author: "오늘의 운세 편집팀"
---

# 새로운 글

본문입니다. 이 본문은 절대 바뀌면 안 됩니다.
"""

PAYLOAD = {
    "current": {
        "attemptedAt": "2026-10-24T06:00:00Z",
        "worker": "reviewer/minimax-M3",
        "targets": [
            {"platform": "x", "status": "pending", "reason": "credit"},
            {"platform": "threads", "status": "skipped", "reason": "token 없음"},
        ],
        "outcome": "PUBLISH_BLOCKED_PLATFORM_CREDIT",
        "publishPending": True,
    },
    "historyEntry": {
        "attemptedAt": "2026-10-24T06:00:00Z",
        "worker": "reviewer/minimax-M3",
        "fire": 188,
        "blog": "new-post",
        "outcome": "PUBLISH_BLOCKED_PLATFORM_CREDIT",
    },
}


@unittest.skipUnless(sf.yaml is not None, "PyYAML is required by the writer")
class NewPostBootstrapTest(unittest.TestCase):
    """`record` must bootstrap a 'syndication:' block on a new post."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "new-post.md")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(NEW_POST)
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")
        self.payload_path = os.path.join(self.tmp.name, "payload.json")
        with open(self.payload_path, "w", encoding="utf-8") as fh:
            json.dump(PAYLOAD, fh, ensure_ascii=False)

    def _read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def _record(self, payload=None):
        if payload is not None:
            with open(self.payload_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["record", "--post", self.path,
                            "--payload", self.payload_path,
                            "--ledger", self.ledger])
        return code, out.getvalue()

    def _record_expecting_refusal(self, payload):
        """`main` catches FrontmatterError and returns 2, so the refusal is
        observed through the exit code -- not through an exception."""
        with open(self.payload_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["record", "--post", self.path,
                            "--payload", self.payload_path,
                            "--ledger", self.ledger])
        return code

    def _frontmatter(self, raw=None):
        raw = self._read() if raw is None else raw
        _prefix, fm_text, _tail, _body = sf.split_document(raw)
        return sf.parse_frontmatter(fm_text)

    # -- the defect itself ------------------------------------------------

    def test_record_on_a_new_post_succeeds_and_creates_the_block(self):
        """The defect: 'record' died with "no 'syndication:' block"."""
        code, _out = self._record()
        self.assertEqual(code, 0)
        data = self._frontmatter()
        self.assertIn("syndication", data)
        block = data["syndication"]
        self.assertEqual(block["attemptedAt"], "2026-10-24T06:00:00Z")
        self.assertEqual(block["worker"], "reviewer/minimax-M3")
        self.assertEqual(len(block["targets"]), 2)
        self.assertEqual(len(block["history"]), 1)
        self.assertEqual(block["history"][0]["fire"], 188)

    def test_the_created_block_is_valid_for_the_content_schema(self):
        self._record()
        block = self._frontmatter()["syndication"]
        # validate_syndication mirrors src/content.config.ts.
        sf.validate_syndication(block)
        for field in ("attemptedAt", "worker"):
            self.assertIsInstance(block[field], str)
        self.assertIsInstance(block["targets"], list)
        for target in block["targets"]:
            self.assertIn(target["platform"], sf.PLATFORMS)
            self.assertIn(target["status"], sf.STATUSES)

    def test_only_one_syndication_key_is_written(self):
        """A second `record` must replace the block, not nest a duplicate."""
        self._record()
        self._record()
        raw = self._read()
        top_level = re.findall(r"^syndication:", raw, flags=re.M)
        self.assertEqual(len(top_level), 1)
        data = self._frontmatter()
        self.assertEqual(len(data["syndication"]["history"]), 2)
        self.assertEqual(data["syndication"]["history"][-1]["fire"], 188)

    # -- the body must never change ---------------------------------------

    def test_the_body_is_byte_identical_after_the_write(self):
        before = sf.body_sha256(self._read())
        self._record()
        after = sf.body_sha256(self._read())
        self.assertEqual(before, after)
        self.assertIn("본문입니다. 이 본문은 절대 바뀌면 안 됩니다.", self._read())

    def test_the_body_hash_agrees_with_the_precheck(self):
        """The precheck decides 'new content?' from the body hash, so the
        writer must hash exactly the same bytes or it self-triggers."""
        import sns_precheck as pre
        self._record()
        with open(self.path, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertEqual(sf.body_sha256(raw), pre.body_hash(self.path)[0])

    # -- the rest of the frontmatter must survive --------------------------

    def test_the_other_frontmatter_keys_are_preserved(self):
        self._record()
        data = self._frontmatter()
        self.assertEqual(data["title"], "새로운 글")
        self.assertEqual(data["description"], "설명")
        self.assertEqual(data["tags"], ["오늘의운세", "마인드풀니스"])
        self.assertEqual(data["author"], "오늘의 운세 편집팀")
        self.assertEqual(data["pubDate"], __import__("datetime").date(2026, 10, 24))

    def test_the_block_is_appended_at_the_top_level(self):
        """It must land as a sibling key, not be swallowed by the key above.

        A frontmatter whose last value is a multi-line block scalar is the
        case where a naive append corrupts the YAML.
        """
        raw = self._read().replace(
            'author: "오늘의 운세 편집팀"',
            'author: "오늘의 운세 편집팀"\ndescription2: |\n  여러 줄\n  스칼라',
        )
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        self._record()
        data = self._frontmatter()
        self.assertIn("syndication", data)
        self.assertEqual(data["description2"], "여러 줄\n스칼라\n")
        self.assertEqual(data["title"], "새로운 글")

    # -- invalid input must still be refused -------------------------------

    def test_an_invalid_payload_is_refused_and_nothing_is_written(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["current"]["targets"][0]["status"] = "blocked"  # not in STATUSES
        before = self._read()
        self.assertNotEqual(self._record_expecting_refusal(bad), 0)
        self.assertEqual(self._read(), before)

    def test_an_invalid_platform_is_refused_and_nothing_is_written(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["current"]["targets"][0]["platform"] = "mastodon"
        before = self._read()
        self.assertNotEqual(self._record_expecting_refusal(bad), 0)
        self.assertEqual(self._read(), before)

    def test_a_payload_without_current_is_refused(self):
        before = self._read()
        code = self._record_expecting_refusal(
            {"historyEntry": PAYLOAD["historyEntry"]})
        self.assertNotEqual(code, 0)
        self.assertEqual(self._read(), before)

    def test_dry_run_does_not_write(self):
        before = self._read()
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["record", "--post", self.path, "--payload",
                            self.payload_path, "--dry-run",
                            "--ledger", self.ledger])
        self.assertEqual(code, 0)
        self.assertEqual(self._read(), before)

    # -- trim must not fabricate a block -----------------------------------

    def test_trim_on_a_new_post_is_a_noop(self):
        """Trimming a never-syndicated post must not invent a block.

        'syndication' is optional in the content schema, but when present
        it requires attemptedAt/worker/targets -- so a fabricated block
        would both break the build and claim an attempt that never ran.
        """
        before = self._read()
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["trim", "--post", self.path, "--keep", "3",
                            "--ledger", self.ledger])
        self.assertEqual(code, 0)
        self.assertEqual(self._read(), before)
        self.assertNotIn("syndication", self._frontmatter())
        self.assertIn("skipped", out.getvalue())

    # -- status must not blow up either ------------------------------------

    def test_status_on_a_new_post_reports_no_history(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["status", "--post", self.path])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["historyLength"], 0)
        self.assertFalse(payload["hasTargets"])


@unittest.skipUnless(sf.yaml is not None, "PyYAML is required by the writer")
class ExistingBlockTest(unittest.TestCase):
    """The replace path must keep working exactly as before."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "post.md")
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")
        self.payload_path = os.path.join(self.tmp.name, "payload.json")
        with open(self.payload_path, "w", encoding="utf-8") as fh:
            json.dump(PAYLOAD, fh, ensure_ascii=False)
        self.raw = (
            "---\n"
            'title: "t"\n'
            'description: "d"\n'
            "pubDate: 2026-01-01\n"
            'tags: ["a"]\n'
            "syndication:\n"
            '  attemptedAt: "2026-01-01T00:00:00Z"\n'
            '  worker: "reviewer/minimax-M3"\n'
            "  targets:\n"
            "    - platform: x\n"
            "      status: pending\n"
            "---\n"
            "\n"
            "본문\n"
        )
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.raw)

    def test_record_replaces_the_existing_block(self):
        before = sf.body_sha256(self.raw)
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["record", "--post", self.path, "--payload",
                            self.payload_path, "--ledger", self.ledger])
        self.assertEqual(code, 0)
        with open(self.path, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertEqual(sf.body_sha256(raw), before)
        self.assertEqual(len(re.findall(r"^syndication:", raw, flags=re.M)), 1)
        _prefix, fm_text, _tail, _body = sf.split_document(raw)
        block = sf.parse_frontmatter(fm_text)["syndication"]
        self.assertEqual(block["attemptedAt"], "2026-10-24T06:00:00Z")
        self.assertEqual(len(block["history"]), 1)

    def test_build_new_raw_without_create_still_raises(self):
        """The explicit error for genuinely invalid input is preserved."""
        with self.assertRaises(sf.FrontmatterError):
            sf.build_new_raw(NEW_POST, {"attemptedAt": "x", "worker": "w",
                                        "targets": []})


@unittest.skipUnless(sf.yaml is not None, "PyYAML is required by the writer")
class LedgerTimestampTest(unittest.TestCase):
    """The ledger row's top-level 'ts' must never be empty.

    `--timestamp` defaults to '', so a record call that omits it wrote a
    ledger row whose 'ts' was the empty string. The precheck copies that
    field straight into the pending record's 'ts', so the pending-publish
    audit trail lost the attempt time entirely (observed live: the
    fire190 row had ts='' and the precheck reported pendingDetail.ts='').
    The writer now falls back to the payload's own 'attemptedAt'.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "post.md")
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")
        self.payload_path = os.path.join(self.tmp.name, "payload.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(NEW_POST)
        with open(self.payload_path, "w", encoding="utf-8") as fh:
            json.dump(PAYLOAD, fh, ensure_ascii=False)

    def _record(self, extra_args=()):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sf.main(["record", "--post", self.path,
                            "--payload", self.payload_path,
                            "--ledger", self.ledger] + list(extra_args))
        self.assertEqual(code, 0)
        with open(self.ledger, "r", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_ts_falls_back_to_the_payload_attempted_at(self):
        row = self._record()
        self.assertEqual(row["ts"], "2026-10-24T06:00:00Z")
        self.assertNotEqual(row["ts"], "")
        self.assertEqual(row["ts"], row["entry"]["attemptedAt"])

    def test_explicit_timestamp_still_wins(self):
        row = self._record(["--timestamp", "2026-10-25T01:02:03Z"])
        self.assertEqual(row["ts"], "2026-10-25T01:02:03Z")

    def test_the_precheck_can_still_report_the_pending_timestamp(self):
        """A row written before the fix (empty 'ts') must still surface a
        usable timestamp through the nested history entry."""
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sns_precheck as pre
        with open(self.ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "syndication_record",
                "ts": "",
                "blog": "new-post",
                "bodySha256": "sha-x",
                "publishPending": True,
                "outcome": "PUBLISH_BLOCKED_PLATFORM_CREDIT",
                "blockedBy": "credit",
                "entry": {"attemptedAt": "2026-10-24T06:00:00Z"},
            }, ensure_ascii=False) + "\n")
        _hashes, pending = pre.read_ledger(self.ledger)
        self.assertEqual(pending["new-post"]["ts"], "2026-10-24T06:00:00Z")


class KeepHistoryTest(unittest.TestCase):
    """`_keep_history` must never drop an entry it was asked to keep.

    `len(history) - keep` is NEGATIVE whenever the post has fewer entries
    than `keep` -- exactly the state of a brand-new post on its first
    fires. A negative slice index selects from the END of the list, so
    history[-1:] kept ONE entry instead of all of them: the first publish
    attempt on a new post silently vanished from the article.
    """

    def test_fewer_entries_than_keep_are_all_kept(self):
        for size in range(0, 4):  # size < keep: nothing may be dropped
            history = [{"i": i} for i in range(size)]
            kept = sf._keep_history(history, 3)
            self.assertEqual(len(kept), size,
                             "keep=3 dropped entries at size=%d" % size)
            self.assertEqual(kept, history)

    def test_more_entries_than_keep_keeps_the_newest(self):
        history = [{"i": i} for i in range(10)]
        kept = sf._keep_history(history, 3)
        self.assertEqual(len(kept), 3)
        self.assertEqual([entry["i"] for entry in kept], [7, 8, 9])

    def test_keep_zero_empties_the_history(self):
        self.assertEqual(sf._keep_history([{"i": 1}, {"i": 2}], 0), [])

    def test_the_history_never_grows_without_bound(self):
        history = []
        for _ in range(20):
            history = sf._keep_history(history + [{"i": len(history)}], 3)
            self.assertLessEqual(len(history), 3)


class SchemaDriftTest(unittest.TestCase):
    """The writer's validator must not drift from src/content.config.ts."""

    def test_platform_and_status_enums_match_the_content_schema(self):
        path = os.path.join(REPO_ROOT, "src", "content.config.ts")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        def _enum(name):
            match = re.search(r"%s:\s*z\.enum\(\[([^\]]*)\]\)" % name, source)
            if match is None:
                self.fail("%s enum not found in %s" % (name, path))
            return set(re.findall(r"'([^']+)'", match.group(1)))

        self.assertEqual(set(sf.PLATFORMS), _enum("platform"))
        self.assertEqual(set(sf.STATUSES), _enum("status"))


if __name__ == "__main__":
    unittest.main()
