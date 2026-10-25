#!/usr/bin/env python3
"""Generator: write tools/sns_x_emoji.txt from twemoji-parser.

twitter-text counts an emoji SEQUENCE as one 2-unit emoji, using the
twemoji-parser package to find the sequences. Its regex is written in
UTF-16 (surrogate pairs), which Python's `re` cannot use directly.

This script rewrites that regex into code points: a high surrogate
followed by a low surrogate becomes the single code point they encode,
and a high surrogate followed by a CLASS of low surrogates becomes the
matching class of code points.

Run from the repo root:

    python3 tools/sns_x_emoji_gen.py <path-to-twemoji-parser-package>

The output is committed; this script exists so it can be refreshed from
upstream without hand-editing.
"""

import json
import os
import re
import sys

HEADER = """\
# X emoji pattern -- the emoji SEENCES twitter-text counts as one emoji.
#
# twitter-text v3.1.0 counts an emoji sequence (for example the
# ZWJ-joined family) as ONE 2-unit emoji, using the twemoji-parser
# package to find the sequences. Counting its code points separately
# charges 10 units for a family emoji where the platform charges 2.
#
# This file is that regex, rewritten from UTF-16 surrogate pairs into
# code points so Python's `re` can use it. See tools/sns_x_emoji_gen.py.
#
# Source: npm twemoji-parser -> dist/lib/regex.js
# Regenerate: python3 tools/sns_x_emoji_gen.py <twemoji-parser-package-dir>
"""

ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _is_high(value: int) -> bool:
    return 0xD800 <= value <= 0xDBFF


def _is_low(value: int) -> bool:
    return 0xDC00 <= value <= 0xDFFF


def _code_point(high: int, low: int) -> int:
    return 0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00)


def _read_class(source: str, index: int):
    """Return (body, index_after) for the character class at `index`.

    `index` points at the opening "[". The body is the raw text between
    the brackets.
    """
    cursor = index + 1
    depth = 1
    while cursor < len(source):
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[index + 1:cursor], cursor + 1
        cursor += 1
    raise SystemExit("unterminated character class")


def _split_class(body: str):
    """Split a character-class body into its items.

    An item is either a single escape ("\\uXXXX") or a range
    ("\\uXXXX-\\uXXXX"). The two are not separated by anything, so the
    body has to be scanned rather than split.
    """
    items = []
    cursor = 0
    while cursor < len(body):
        match = ESCAPE.match(body, cursor)
        if not match:
            raise SystemExit("unexpected class body: %r" % body)
        first = int(match.group(1), 16)
        cursor = match.end()
        if cursor < len(body) and body[cursor] == "-":
            range_match = ESCAPE.match(body, cursor + 1)
            if not range_match:
                raise SystemExit("unexpected class range: %r" % body)
            second = int(range_match.group(1), 16)
            cursor = range_match.end()
            items.append((first, second))
            continue
        items.append((first, first))
    return items


def _translate_class(body: str, high: int) -> str:
    """Translate a class of low surrogates into a class of code points."""
    parts = []
    for first, second in _split_class(body):
        if not _is_low(first) or not _is_low(second):
            raise SystemExit("class is not made of low surrogates: %r" % body)
        start = _code_point(high, first)
        end = _code_point(high, second)
        if start == end:
            parts.append("\\U%08X" % start)
        else:
            parts.append("\\U%08X-\\U%08X" % (start, end))
    return "[" + "".join(parts) + "]"


def translate(source: str) -> str:
    """Rewrite a UTF-16 regex source into code points."""
    out = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        match = ESCAPE.match(source, index)
        if not match:
            out.append(source[index:index + 2])
            index += 2
            continue
        value = int(match.group(1), 16)
        index = match.end()
        if not _is_high(value):
            out.append(match.group(0))
            continue
        if index < length and source[index] == "[":
            body, index = _read_class(source, index)
            out.append(_translate_class(body, value))
            continue
        low_match = ESCAPE.match(source, index)
        if not low_match:
            raise SystemExit("high surrogate without a low: %r" % source)
        low = int(low_match.group(1), 16)
        if not _is_low(low):
            raise SystemExit("high surrogate without a low: %r" % source)
        index = low_match.end()
        out.append("\\U%08X" % _code_point(value, low))
    return "".join(out)


def _read_regex_literal(blob: str) -> str:
    """Return the source of the regex literal assigned to exports.default.

    A JavaScript regex literal ends at the first unescaped "/" that is
    not inside a character class, so the literal cannot be read with a
    naive regex.
    """
    marker = "exports.default"
    start = blob.index(marker)
    start = blob.index("/", blob.index("=", start) + 1)
    index = start + 1
    in_class = False
    while index < len(blob):
        char = blob[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            if char == "]":
                in_class = False
        elif char == "[":
            in_class = True
        elif char == "/":
            return blob[start + 1:index]
        index += 1
    raise SystemExit("unterminated regex literal")


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    package = argv[1]
    regex_path = os.path.join(package, "dist", "lib", "regex.js")
    with open(regex_path, "r", encoding="utf-8") as handle:
        blob = handle.read()
    source = _read_regex_literal(blob)
    translated = translate(source)
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sns_x_emoji.txt")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(HEADER)
        handle.write(translated)
        handle.write("\n")
    print(json.dumps({
        "out": out_path,
        "sourceChars": len(source),
        "translatedChars": len(translated),
    }))


if __name__ == "__main__":
    main(sys.argv)
