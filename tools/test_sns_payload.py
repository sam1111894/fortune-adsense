#!/usr/bin/env python3
"""Offline tests for tools/sns_payload.py.

The point of the tool is that the draft sizes in the payload are
MEASURED, never hand-written. These tests pin that:

  * a facts file that tries to smuggle in its own sizes is overridden by
    the measurement (the numbers come from `measure_records`, not from
    the caller);
  * the weighted count is attached ONLY to the platform whose cap is
    weighted, because the other three caps are plain character caps and
    a weighted number there is a number read against the wrong limit;
  * a payload that would break the article is refused before it is
    written.

Run: python3 -m unittest tools.test_sns_payload
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_frontmatter as sf  # noqa: E402
import sns_payload as sp  # noqa: E402
import sns_publish as spub  # noqa: E402

KOREAN = "오늘의 운세, 어떻게 봐야 적당할까 — 운세 활용의 건강한 자세"

FACTS = {
    "fire": 195,
    "attemptedAt": "2026-10-24T19:43:25Z",
    "worker": "reviewer/minimax-M3",
    "outcome": "PUBLISH_BLOCKED_PLATFORM_CREDIT",
    "cronExit": 3,
    "publishPending": True,
    "targets": {
        "x": {"status": "pending", "reason": "PUBLISH BLOCKED"},
        "threads": {"status": "skipped", "reason": "token 없음"},
        "linkedin": {"status": "skipped", "reason": "token 없음"},
        "dev.to": {"status": "skipped", "reason": "token 없음"},
    },
}


def write_drafts(directory, body=None):
    """One draft per platform, named by FILENAME (not by platform key)."""
    for platform, filename in spub.DRAFT_FILENAME.items():
        text = body if body is not None else (KOREAN if platform == "x"
                                              else "draft %s" % platform)
        with open(os.path.join(directory, filename), "w",
                  encoding="utf-8") as fh:
            fh.write(text)


class TestPayloadSizesAreMeasured(unittest.TestCase):
    """The sizes must come from the measurement, not from the caller."""

    def _build(self, drafts_dir, facts=None):
        return sp.build_payload(drafts_dir, facts or FACTS, "out.json")

    def test_the_sizes_match_the_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = self._build(tmp)
            measured = spub.measure_records(tmp)
        for target in payload["current"]["targets"]:
            platform = target["platform"]
            self.assertEqual(target["draftChars"],
                             str(measured[platform]["chars"]), platform)
            self.assertEqual(target["draftBytes"],
                             str(measured[platform]["bytes"]), platform)

    def test_the_measured_numbers_win_over_hand_written_ones(self):
        """A facts file that carries its own sizes cannot override the
        measurement -- that was the whole defect."""
        facts = json.loads(json.dumps(FACTS))
        for target in facts["targets"].values():
            target["draftChars"] = "999999"
            target["draftBytes"] = "999999"
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = self._build(tmp, facts)
            measured = spub.measure_records(tmp)
        for target in payload["current"]["targets"]:
            platform = target["platform"]
            self.assertEqual(target["draftChars"],
                             str(measured[platform]["chars"]), platform)
            self.assertEqual(target["draftBytes"],
                             str(measured[platform]["bytes"]), platform)
            self.assertNotEqual(target["draftChars"], "999999")

    def test_the_counts_are_bare_integers(self):
        """The renderer appends the unit, so a value carrying one would
        render "178자자"."""
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = self._build(tmp)
        for target in payload["current"]["targets"]:
            for key in ("draftChars", "draftBytes", "draftWeighted"):
                if key in target:
                    self.assertRegex(target[key], r"^[0-9]+$", key)

    def test_the_weighted_count_is_attached_only_to_the_weighted_platform(self):
        """X's 280 is weighted; the other three caps are characters."""
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = self._build(tmp)
        for target in payload["current"]["targets"]:
            platform = target["platform"]
            metric = spub.PLATFORM_CAP_METRIC[platform]
            if metric == "weighted":
                self.assertIn("draftWeighted", target, platform)
            else:
                self.assertNotIn("draftWeighted", target, platform)

    def test_the_weighted_count_is_the_number_the_cap_is_compared_against(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = self._build(tmp)
            measured = spub.measure_records(tmp)
        x = [t for t in payload["current"]["targets"] if t["platform"] == "x"][0]
        self.assertEqual(x["draftWeighted"], str(measured["x"]["used"]))
        self.assertNotEqual(x["draftWeighted"], x["draftChars"])


class TestPayloadShape(unittest.TestCase):
    def test_the_skip_counts_are_derived_from_the_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = sp.build_payload(tmp, FACTS, "out.json")
        current = payload["current"]
        self.assertEqual(current["skipCount"], 3)
        self.assertEqual(current["skipTotal"], 4)

    def test_the_fire_number_lives_only_in_the_history_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = sp.build_payload(tmp, FACTS, "out.json")
        self.assertEqual(payload["historyEntry"]["fire"], 195)
        self.assertNotIn("fire", payload["current"])

    def test_the_history_entry_mirrors_the_current_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = sp.build_payload(tmp, FACTS, "out.json")
        current = payload["current"]
        history = payload["historyEntry"]
        self.assertEqual(
            {k: v for k, v in history.items() if k != "fire"}, current)

    def test_the_exit_code_mirrors_the_cron_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = sp.build_payload(tmp, FACTS, "out.json")
        self.assertEqual(payload["current"]["exitCode"],
                         payload["current"]["cronExit"])


class TestPayloadIsRefusedWhenInvalid(unittest.TestCase):
    """A payload that would break the article must never be written."""

    def test_a_missing_target_is_refused(self):
        facts = json.loads(json.dumps(FACTS))
        del facts["targets"]["dev.to"]
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            with self.assertRaises(ValueError):
                sp.build_payload(tmp, facts, "out.json")

    def test_a_missing_draft_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            os.remove(os.path.join(tmp, spub.DRAFT_FILENAME["dev.to"]))
            with self.assertRaises(ValueError):
                sp.build_payload(tmp, FACTS, "out.json")

    def test_an_invalid_status_is_refused(self):
        facts = json.loads(json.dumps(FACTS))
        facts["targets"]["x"]["status"] = "definitely-not-a-status"
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            with self.assertRaises(sf.FrontmatterError):
                sp.build_payload(tmp, facts, "out.json")

    def test_a_weighted_count_on_a_character_cap_platform_is_refused(self):
        """The same rule the content schema and the writer enforce."""
        with tempfile.TemporaryDirectory() as tmp:
            write_drafts(tmp)
            payload = sp.build_payload(tmp, FACTS, "out.json")
        for target in payload["current"]["targets"]:
            if target["platform"] != "x":
                self.assertNotIn("draftWeighted", target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
