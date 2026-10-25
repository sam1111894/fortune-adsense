#!/usr/bin/env python3
"""X URL detection and weighted length -- a port of twitter-text v3.1.0.

WHY THIS FILE EXISTS
--------------------
X's pay-per-use pricing bills a Post that contains a URL at $0.200 and a
URL-free Post at $0.015. The publisher used to decide that with a regex of
its own:

    X_URL_RE = r"(?:https?://|www\\.)[^\\s]+"

which requires a scheme (or a literal "www."). That is NOT what X does.
twitter-text -- the library X uses to linkify and to measure the weighted
length -- makes the protocol OPTIONAL, so a BARE DOMAIN is a URL too:

    "example.com"          -> twitter-text: a URL, 23 weighted units
    "lucksajueun.com/blog" -> twitter-text: a URL, 23 weighted units
    "file.txt"             -> twitter-text: NOT a URL (".txt" is not a TLD)

The old detector said "no URL" for the first two. So a draft that mentioned
the site without the scheme was measured as if it had no link (raw character
weight instead of 23) and PRICED AT THE $0.015 TIER while X charged $0.200.

That is a fail-OPEN in the credit pre-flight -- the one gate that exists to
avoid spending money that was never verified as available. With a $0.02
balance and a bare-domain draft the old code reserved $0.015, waved the
billable write through, and X charged $0.200. It is the same defect class
fire195 fixed (the price tier and the length counter disagreeing); this is
the level UNDER that one, where both detectors disagree with the PLATFORM.

THE RULE, AND WHERE IT COMES FROM
---------------------------------
twitter-text v3.1.0 composes its URL matcher out of these pieces:

    extractUrl = (validUrlPrecedingChars)
                 ( (https?://)?            # protocol OPTIONAL
                   (validDomain)           # labels + a real TLD
                   (?::(validPortNumber))?
                   (\\/validUrlPath*)?
                   (\\?validUrlQuery...)? )

`validDomain` requires the LAST label to be a real TLD (gTLD, cCCTLD or
punycode) -- which is why "file.txt" is not a URL and why a TLD list is
needed at all. The TLD lists live in `tools/sns_x_tlds.txt`, extracted from
twitter-text 3.1.0's own `validGTLD` / `validCCTLD` regexes by
`tools/sns_x_tlds_gen.py`.

This module reproduces that behaviour in Python by building the SAME regex
from the SAME primitives, and then running the same post-processing
twitter-text runs:

  * a protocol-less match is re-scanned for an ASCII-only domain, and is
    DROPPED entirely when there is none ("한국.com" is not a URL, because
    the label is not ASCII);
  * a t.co URL whose slug is longer than 40 characters is dropped;
  * every domain label is length-checked the way twitter-text checks them.

`tools/test_sns_xurl.py` pins the result against fixtures captured from the
real twitter-text 3.1.0 library, and `tools/sns_xurl_diff.py` re-checks it
against that library whenever it is installed.

ONE DETECTOR, TWO QUESTIONS
----------------------------
This module answers BOTH questions the publisher asks about a draft:

    has_url(text)         -> is this Post billed at the URL tier?
    weighted_length(text) -> how many of X's 280 units does it use?

and it answers them from the same URL spans. If the two could disagree --
one detector for the price, a different one for the length -- the credit
pre-flight would again reserve the cheap tier for a request X bills at the
expensive one. `tools/sns_publish.py` therefore imports BOTH from here.

FAIL-CLOSED BY CONSTRUCTION
----------------------------
Where this port could be wrong, it is wrong in the EXPENSIVE direction:

  * a draft it thinks contains a URL is priced at $0.200 instead of
    $0.015 -- a publish that could have been made is blocked, which is
    recoverable;
  * a draft it thinks is URL-free is priced at $0.015 when X charges
    $0.200 -- money spent that was never verified as available, which is
    not recoverable.

So every uncertain case here resolves to "this looks like a URL", and a
missing TLD list switches the matcher to "every dotted token is a domain".

KNOWN BOUNDARY
---------------
The port indexes text by CODE POINT, where twitter-text indexes it by UTF-16
code unit. The two agree for every BMP character -- which is all Korean
copy, all ASCII, and every realistic URL -- and can differ only when a URL
or the text before it contains an ASTRAL character (U+10000 and above).
Case folding is the other place the two can differ, and only for the
exotic folds (U+0170, U+212A). Neither affects the tier decision, which is
a yes/no question about the text as a whole.
"""

from __future__ import annotations

import os
import re
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
TLD_PATH = os.path.join(_HERE, "sns_x_tlds.txt")
EMOJI_PATH = os.path.join(_HERE, "sns_x_emoji.txt")

# twitter-text's limits, verbatim.
MAX_URL_LENGTH = 4096
MAX_TCO_SLUG_LENGTH = 40
MAX_DOMAIN_LABEL_LENGTH = 63
PUNYCODE_PREFIX = "xn--"

# --- the primitives twitter-text builds its matcher from ------------------
# Copied from twitter-text 3.1.0's compiled regexes (dist/regexp/*.js).
# These are regex SOURCES, so they are used inside character classes and
# keep their escapes.
_PUNCT = r"\!'#%&'\(\)*\+,\\\-\.\/:;<=>\?@\[\]\^_{|}~\$"
_SPACES = r"\u0009-\u000D\u0020\u0085\u00A0\u1680\u180E\u2000-\u200A\u2028\u2029\u202F\u205F\u3000"
_INVALID_CHARS = r"\uFFFE\uFEFF\uFFFF"
_DIRECTIONAL = r"\u202A-\u202E\u061C\u200E\u200F\u2066\u2067\u2068\u2069"
_LATIN_ACCENT = r"\xC0-\xD6\xD8-\xF6\xF8-\xFF\u0100-\u024F\u0253-\u0254\u0256-\u0257\u0259\u025B\u0263\u0268\u026F\u0272\u0289\u028B\u02BB\u0300-\u036F\u1E00-\u1EFF"
_CYRILLIC = r"\u0400-\u04FF"


def _load_tld_sections():
    """Return (gTLD list, cCCTLD list) exactly as twitter-text orders them.

    The ORDER matters: a regex alternation is first-match-wins, not
    longest-match-wins. The two lists are kept apart because twitter-text
    keeps them apart -- `validDomain` tries the gTLD list, then the cCCTLD
    list, then punycode.

    Returns (None, None) when the list cannot be read, which switches the
    matcher to the conservative "every dotted token is a domain" mode.
    """
    try:
        with open(TLD_PATH, "r", encoding="utf-8") as handle:
            gtl, cct, section = [], [], None
            for raw in handle:
                line = raw.strip()
                if line == "[gTLD]":
                    section = gtl
                    continue
                if line == "[cTLD]":
                    section = cct
                    continue
                if not line or line.startswith("#"):
                    continue
                if section is None:
                    continue
                section.append(line)
            if not gtl and not cct:
                return None, None
            return gtl, cct
    except OSError:
        return None, None


_GTLD, _CCTLD = _load_tld_sections()


class _Patterns(object):
    """The compiled matcher, built from a TLD list.

    Building it in a function (rather than at import time) keeps the
    "no TLD list" fallback a CODE PATH that can be exercised, instead of
    a branch that only runs on a machine with a broken checkout.
    """

    def __init__(self, gtld, cctld):
        # A domain label character: anything that is not punctuation,
        # whitespace, an invalid character or a directional marker.
        domain_chars = ("[^" + _PUNCT + _SPACES + _INVALID_CHARS +
                        _DIRECTIONAL + "]")

        # The gTLD / cCCTLD alternations, each followed by twitter-text's
        # boundary lookahead ("the next character must not be part of a
        # domain label").
        if gtld is None:
            # No TLD list: treat any label as a TLD. Over-detecting only
            # means a draft is priced at $0.200 instead of $0.015 (the
            # expensive, safe direction); under-detecting would spend
            # unverified money.
            gTLD_re = r"(?:[^\W_]+)"
            cctld_re = gTLD_re
        else:
            gTLD_re = (r"(?:(?:" + "|".join(gtld) +
                       r")(?=[^0-9a-zA-Z@+-]|\Z))")
            cctld_re = (r"(?:(?:" + "|".join(cctld) +
                        r")(?=[^0-9a-zA-Z@+-]|\Z))")

        valid_subdomain = (r"(?:(?:" + domain_chars +
                           r"(?:[_-]|" + domain_chars + r")*)?" +
                           domain_chars + r"\.)")
        valid_domain_name = (r"(?:(?:" + domain_chars +
                             r"(?:-|" + domain_chars + r")*)?" +
                             domain_chars + r"\.)")
        valid_punycode = r"(?:xn--[\-0-9a-z]+)"

        valid_domain = (r"(?:" + valid_subdomain + r"*" + valid_domain_name +
                        r"(?:" + gTLD_re + r"|" + cctld_re + r"|" +
                        valid_punycode + r"))")

        valid_port_number = r"[0-9]+"

        # Characters allowed inside a URL path.
        general_path_chars = (r"[a-z" + _CYRILLIC +
                              r"0-9!\*';:=\+,\.\$\/%#\[\]\-\u2013_~@\|&" +
                              _LATIN_ACCENT + r"]")
        # Balanced parentheses, up to one nested level.
        balanced_parens = (r"\((?:" + general_path_chars + r"+|(?:" +
                           general_path_chars + r"*\(" +
                           general_path_chars + r"+\)" +
                           general_path_chars + r"*))\)")
        # The last character of a path may not be prose punctuation --
        # this is why "foo.bar/path." links as "foo.bar/path" and not
        # "foo.bar/path.".
        path_ending_chars = (r"[\+\-a-z" + _CYRILLIC + r"0-9=_#\/" +
                             _LATIN_ACCENT + r"]|(?:" + balanced_parens +
                             r")")
        valid_url_path = (r"(?:(?:" + general_path_chars + r"*(?:" +
                          balanced_parens + general_path_chars + r"*)*" +
                          path_ending_chars + r")|(?:@" + general_path_chars +
                          r"+/))")

        query_chars = r"[a-z0-9!?\*'@\(\);:=\&\+\$\/%#\[\]\-_\.,~|]"
        query_ending_chars = r"[a-z0-9\-_&=#\/]"

        # A URL may not directly follow an alphanumeric, "@", "$" or "#".
        preceding_chars = (r"(?:[^A-Za-z0-9@＠$#＃" + _INVALID_CHARS +
                           r"]|[" + _DIRECTIONAL + r"]|\A)")

        self.extract_url = re.compile(
            r"(" + preceding_chars + r")("
            r"(https?://)?(" + valid_domain + r")"
            r"(?::(" + valid_port_number + r"))?"
            r"(\/" + valid_url_path + r"*)?"
            r"(\?" + query_chars + r"*" + query_ending_chars + r")?"
            r")", re.IGNORECASE)

        # The ASCII-only domain scan twitter-text runs over a
        # protocol-less match.
        self.ascii_domain = re.compile(
            r"(?:(?:" + r"[\-a-z0-9" + _LATIN_ACCENT + r"]+" + r"\.)+(?:" +
            gTLD_re + r"|" + cctld_re + r"|" + valid_punycode + r"))",
            re.IGNORECASE)

        self.tco_url = re.compile(
            r"\Ahttps?://t\.co/([a-z0-9]+)(?:\?" + query_chars + r"*" +
            query_ending_chars + r")?", re.IGNORECASE)

        # A protocol-less URL may not follow one of these characters.
        self.invalid_without_protocol = re.compile(r"[-_./]\Z")


_PATTERNS = _Patterns(_GTLD, _CCTLD)


# --- weighted length ------------------------------------------------------
# twitter-text's version 3 config (dist/configs.js).
X_MAX_WEIGHTED = 280
X_SCALE = 100
X_URL_WEIGHT = 23
X_DEFAULT_WEIGHT = 200
X_WEIGHT_RANGES = (
    (0, 4351, 100),
    (8192, 8205, 100),
    (8208, 8223, 100),
    (8242, 8247, 100),
)


def _load_emoji_pattern():
    """The emoji SEQUENCE pattern twitter-text counts as one emoji.

    twitter-text parses emoji with twemoji-parser and charges a whole
    sequence -- the ZWJ-joined family, a skin-toned thumb, a keycap -- as
    ONE 2-unit emoji. Counting the code points separately would charge 10
    units for a family emoji where the platform charges 2.

    The pattern is generated from twemoji-parser by
    `tools/sns_x_emoji_gen.py`. Without it, emoji are counted per code
    point, which OVER-counts -- the safe direction for a cap check.
    """
    try:
        with open(EMOJI_PATH, "r", encoding="utf-8") as handle:
            body = "".join(line for line in handle
                           if not line.startswith("#")).strip()
        if not body:
            return None
        return re.compile(body)
    except (OSError, re.error):
        return None


_EMOJI = _load_emoji_pattern()


def _char_weight(code_point: int) -> int:
    """twitter-text's per-character weight (scale 100)."""
    for start, end, weight in X_WEIGHT_RANGES:
        if start <= code_point <= end:
            return weight
    return X_DEFAULT_WEIGHT


def _punycode_label_length(label: str):
    """The length twitter-text sees for a domain label, or None if invalid.

    twitter-text checks every label's PUNYCODE-encoded length (1..63): an
    ASCII label is its own length, and a non-ASCII label is "xn--" plus
    its punycode encoding.
    """
    if not label:
        return None
    if label.isascii():
        return len(label)
    try:
        encoded = label.encode("punycode")
    except (UnicodeError, ValueError):
        return None
    return len(PUNYCODE_PREFIX) + len(encoded)


def _is_valid_url(url: str, protocol: str, domain: str) -> bool:
    """twitter-text's `isValidUrl`, reproduced.

    twitter-text's idna helper returns the labels it was given, so the
    length check uses the ORIGINAL domain length; the punycode work here
    is only the per-label validity check.
    """
    if (domain.startswith(PUNYCODE_PREFIX)
            and not _PATTERNS.ascii_domain.search(domain)):
        return False
    for label in domain.split("."):
        length = _punycode_label_length(label)
        if length is None:
            # An empty label is invalid for twitter-text. A label this
            # port cannot decode is left alone rather than rejected, so a
            # URL X accepts is never priced as URL-free.
            if not label:
                return False
            continue
        if length < 1 or length > MAX_DOMAIN_LABEL_LENGTH:
            return False
    return len(protocol) + len(url) <= MAX_URL_LENGTH


def extract_urls(text: str):
    """Return the URLs in `text` as (url, start, end) character offsets.

    This mirrors twitter-text's `extractUrlsWithIndices`: same regex, same
    ASCII-only-domain pass, same t.co rule, same validity checks.

    The offsets are into `text` as given. (twitter-text's own
    `extractUrls` does not normalize either; only `parseTweet` does, and
    `weighted_length` below normalizes for the same reason it does.)
    """
    if not text:
        return []
    if "." not in text:
        # twitter-text's fast path: with protocol-less extraction enabled,
        # a text without a dot cannot hold a URL.
        return []
    found = []
    for match in _PATTERNS.extract_url.finditer(text):
        before = match.group(1)
        url = match.group(2)
        protocol = match.group(3)
        domain = match.group(4)
        path = match.group(6)
        end_position = match.end()
        start_position = end_position - len(url)
        if not _is_valid_url(url, protocol or "https://", domain):
            continue
        if protocol:
            tco = _PATTERNS.tco_url.match(url)
            if tco:
                slug = tco.group(1)
                if len(slug) > MAX_TCO_SLUG_LENGTH:
                    continue
                url = tco.group(0)
                end_position = start_position + len(url)
            found.append((url, start_position, end_position))
            continue
        if _PATTERNS.invalid_without_protocol.search(before):
            continue
        # Protocol-less: twitter-text keeps only the ASCII-only domain.
        # Every ASCII-domain match inside the matched domain is a URL of
        # its own; only the LAST one keeps the path and query.
        last = None
        for ascii_match in _PATTERNS.ascii_domain.finditer(domain):
            ascii_domain = ascii_match.group(0)
            last = (ascii_domain,
                    start_position + ascii_match.start(),
                    start_position + ascii_match.end())
            found.append(last)
        if last is None:
            continue
        if path:
            # The path belongs to the URL, so the URL keeps it.
            found[-1] = (_replace_first(url, domain, last[0]),
                         last[1], end_position)
    return found


def _replace_first(text: str, needle: str, replacement: str) -> str:
    """`String.prototype.replace` with a string pattern: first match only."""
    index = text.find(needle)
    if index < 0:
        return text
    return text[:index] + replacement + text[index + len(needle):]


def find_urls(text: str):
    """Return the URL spans in `text` as (start, end) character offsets."""
    return [(start, end) for (_url, start, end) in extract_urls(text)]


def has_url(text: str) -> bool:
    """True when X would treat `text` as containing a link.

    This is the price-tier question: a Post that X linkifies is billed at
    $0.200, one it does not is billed at $0.015.
    """
    return bool(extract_urls(text or ""))


def weighted_length(text: str) -> int:
    """Return the twitter-text v3 weighted length of a tweet.

    A URL counts as a fixed 23 units no matter how long it is; a Hangul or
    CJK character counts as 2; ASCII counts as 1; an emoji sequence counts
    as one 2-unit emoji.
    """
    if not text:
        return 0
    text = unicodedata.normalize("NFC", text)
    urls = {}
    for url, start, _end in extract_urls(text):
        urls[start] = url
    emoji = {}
    if _EMOJI is not None:
        for match in _EMOJI.finditer(text):
            emoji[match.start()] = match.group(0)
    total = 0
    index = 0
    length = len(text)
    while index < length:
        url = urls.get(index)
        if url is not None:
            total += X_URL_WEIGHT * X_SCALE
            index += len(url)
            continue
        sequence = emoji.get(index)
        if sequence is not None:
            total += X_DEFAULT_WEIGHT
            index += len(sequence)
            continue
        total += _char_weight(ord(text[index]))
        index += 1
    return total // X_SCALE
