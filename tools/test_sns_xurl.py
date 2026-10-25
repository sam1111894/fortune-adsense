#!/usr/bin/env python3
"""Offline tests for tools/sns_xurl.py.

These do NOT need npm: the expected answers were captured from the real
twitter-text 3.1.0 library (the one X uses) into
tools/sns_xurl_fixtures.json by tools/sns_xurl_fixtures_gen.py.

Run: python3 tools/test_sns_xurl.py
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_xurl as xu  # noqa: E402

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "sns_xurl_fixtures.json")

with open(FIXTURE_PATH, "r", encoding="utf-8") as _handle:
    FIXTURES = json.load(_handle)


class TestAgainstTwitterText(unittest.TestCase):
    """The port must agree with the library X itself uses."""

    def test_the_fixtures_are_present(self):
        """A missing fixture file would make every other test vacuous."""
        self.assertGreater(len(FIXTURES), 50)

    def test_the_urls_match_the_reference(self):
        for fixture in FIXTURES:
            text = fixture["text"]
            ours = [url for (url, _s, _e) in xu.extract_urls(text)]
            self.assertEqual(ours, fixture["urls"],
                             "URLs differ for %r" % text)

    def test_the_url_spans_match_the_reference(self):
        """The spans decide where the 23 units are charged, so they matter
        as much as the yes/no answer."""
        for fixture in FIXTURES:
            text = fixture["text"]
            ours = [[start, end] for (_u, start, end) in xu.extract_urls(text)]
            self.assertEqual(ours, fixture["indices"],
                             "spans differ for %r" % text)

    def test_the_weighted_length_matches_the_reference(self):
        for fixture in FIXTURES:
            text = fixture["text"]
            self.assertEqual(xu.weighted_length(text), fixture["weighted"],
                             "weighted length differs for %r" % text)


class TestBareDomainsAreUrls(unittest.TestCase):
    """The bug that made this module necessary.

    X linkifies a BARE domain: "example.com" is a link. A detector that
    requires a scheme says "no URL" for it, which means the post is
    measured without the 23-unit link weight AND priced at the $0.015
    URL-free tier while X charges $0.200.
    """

    def test_a_bare_domain_is_a_url(self):
        self.assertTrue(xu.has_url("example.com"))
        self.assertTrue(xu.has_url("lucksajueun.com/blog/foo"))
        self.assertTrue(xu.has_url("www.example.com"))

    def test_a_dotted_word_that_is_not_a_tld_is_not_a_url(self):
        self.assertFalse(xu.has_url("file.txt"))
        self.assertFalse(xu.has_url("version 1.2"))

    def test_a_bare_domain_costs_23_units_not_its_length(self):
        """The whole point: the length counter and the price tier read
        the same detector."""
        self.assertEqual(xu.weighted_length("example.com"), 23)
        self.assertEqual(xu.weighted_length("a.com"), 23)

    def test_prose_without_a_dot_is_not_a_url(self):
        self.assertFalse(xu.has_url("오늘의 운세는 도구입니다"))
        self.assertFalse(xu.has_url("no link here"))


class TestOneDetectorTwoQuestions(unittest.TestCase):
    """The price tier and the length must never disagree.

    A draft that the length counter treats as a URL post but the tier
    treats as URL-free is priced at $0.015 and billed $0.200 -- the
    fail-open in the credit pre-flight that fire195 fixed at the
    X_URL_RE level, and that this module fixes at the platform level.
    """

    CASES = [
        "https://example.com/a",
        "http://example.com/a",
        "HTTPS://EXAMPLE.COM/a",
        "www.example.com/a",
        "example.com",
        "참고 www.example.com",
        "no link here",
        "오늘의 운세는 도구입니다",
        "",
    ]

    def test_has_url_agrees_with_the_spans(self):
        for text in self.CASES:
            self.assertEqual(xu.has_url(text), bool(xu.find_urls(text)),
                             "detectors disagree on %r" % text)

    def test_every_url_costs_exactly_23_units(self):
        for text in self.CASES:
            spans = xu.find_urls(text)
            if not spans:
                continue
            plain = xu.weighted_length(text.replace(text, text))
            without = text
            for start, end in spans:
                without = without[:start] + without[end:]
            self.assertEqual(plain - xu.weighted_length(without),
                             23 * len(spans),
                             "a URL did not cost 23 units in %r" % text)


class TestTheRealDraft(unittest.TestCase):
    """The draft this pipeline actually publishes."""

    DRAFT = (
        "운세는 예언이 아니라 60갑자·24절기·오행을 압축한 '분류 도구'입니다.\n\n"
        "하루 1회, 한 가지만, 결정은 본인 몫.\n\n"
        "의존 신호 4가지(결정 회피·자기 확정·우울·충동 소비)가 보이면 "
        "확인 빈도를 줄이세요.\n\n"
        "https://lucksajueun.com/blog/daily-fortune-healthy-mindset/"
    )

    def test_the_draft_holds_one_url(self):
        self.assertEqual(xu.find_urls(self.DRAFT),
                         [(len(self.DRAFT) - 59, len(self.DRAFT))])

    def test_the_draft_is_a_url_post(self):
        self.assertTrue(xu.has_url(self.DRAFT))

    def test_the_draft_is_214_of_280_weighted_units(self):
        """The number the syndication card publishes."""
        self.assertEqual(xu.weighted_length(self.DRAFT), 214)
        self.assertLess(xu.weighted_length(self.DRAFT), xu.X_MAX_WEIGHTED)

    def test_the_url_costs_23_units_not_its_59_characters(self):
        without = self.DRAFT[:len(self.DRAFT) - 59]
        self.assertEqual(xu.weighted_length(self.DRAFT),
                         xu.weighted_length(without) + 23)


class TestFailClosed(unittest.TestCase):
    """When the port cannot tell, it must assume the expensive tier."""

    def test_a_missing_tld_list_over_detects(self):
        """The fallback mode is "every dotted token is a domain".

        Over-detecting prices a draft at $0.200 instead of $0.015 -- a
        publish that could have been made is blocked. Under-detecting
        would let a billable write through that was never verified as
        affordable, which is not recoverable.
        """
        fallback = xu._Patterns(None, None)
        self.assertIsNotNone(fallback.extract_url)
        # With no TLD list, "file.txt" -- which is NOT a URL to X --
        # is treated as a domain, so the matcher errs toward the
        # expensive tier.
        self.assertTrue(fallback.extract_url.search("file.txt"))
        # ...while the real matcher does not.
        self.assertIsNone(xu._PATTERNS.extract_url.search("file.txt"))

    def test_the_real_matcher_never_treats_a_non_tld_as_a_domain(self):
        self.assertIsNone(xu._PATTERNS.extract_url.search("file.txt"))
        self.assertIsNone(xu._PATTERNS.extract_url.search("version 1.2"))

    def test_a_url_is_never_dropped_because_of_its_case(self):
        self.assertTrue(xu.has_url("HTTPS://EXAMPLE.COM"))
        self.assertTrue(xu.has_url("Example.COM"))


if __name__ == "__main__":
    unittest.main()
