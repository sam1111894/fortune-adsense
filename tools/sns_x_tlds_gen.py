#!/usr/bin/env python3
"""One-shot generator: write tools/sns_x_tlds.txt from twitter-text 3.1.0.

Run from the repo root:

    python3 tools/sns_x_tlds_gen.py <path-to-twitter-text-package>

It extracts the gTLD and cTLD alternations from the package's compiled
regexes and writes them out in the EXACT order twitter-text uses (the
order matters for regex alternation), with a small header.

The output is committed; this script exists so the data can be refreshed
from the upstream package without hand-editing.
"""

import json
import os
import re
import sys

HEADER = """\
# X URL TLD list -- the authority on what counts as a domain.
#
# Extracted from twitter-text 3.1.0 (the library X uses to auto-link and
# to measure weighted length): the gTLD list (1259 entries) and the cTLD
# list (315 entries).
#
# It exists because a URL is not "text that starts with https://": X
# linkifies a BARE domain too ("example.com"), so a detector that requires
# a scheme under-detects; and a detector that treats any "word.word" as a
# URL over-detects ("file.txt" is not a URL). A TLD list is the only way
# to tell those apart.
#
# Format: two sections, "gTLD:" and "cTLD:", each line one TLD, in the
# order twitter-text lists them. The order is preserved deliberately --
# regex alternation is first-match-wins, not longest-match-wins.
#
# Source: npm twitter-text@3.1.0 -> dist/regexp/validGTLD.js, validCCTLD.js
# Regenerate: python3 tools/sns_x_tlds_gen.py <twitter-text-package-dir>
"""


def parse_alternation(source):
    """Pull the plain TLD names out of a compiled validGTLD/validCCTLD."""
    match = re.match(
        r"^\(\?:\(\?:(.*)\)\(\?=\[\^0-9a-zA-Z@\+-\]\|\$\)\)$",
        source,
        re.S,
    )
    if not match:
        raise SystemExit("unexpected TLD regex shape: %r" % source[:80])
    return match.group(1).split("|")


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    package = argv[1]
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sns_x_tlds.txt")

    sources = {}
    for name in ("validGTLD", "validCCTLD"):
        path = os.path.join(package, "dist", "regexp", name + ".js")
        with open(path, "r", encoding="utf-8") as handle:
            blob = handle.read()
        # The compiled file embeds the alternation as a JS string literal
        # concatenation; pull the quoted pieces back out and rejoin them.
        pieces = re.findall(r"'((?:[^'\\]|\\.)*)'", blob)
        text = "".join(pieces)
        # Undo the JS escapes that matter here.
        text = text.replace("\\/", "/").replace("\\$", "$")
        sources[name] = text

    gtl = parse_alternation(sources["validGTLD"])
    cct = parse_alternation(sources["validCCTLD"])

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(HEADER)
        handle.write("\n[gTLD]\n")
        for tld in gtl:
            handle.write(tld + "\n")
        handle.write("\n[cTLD]\n")
        for tld in cct:
            handle.write(tld + "\n")

    print(json.dumps({
        "out": out_path,
        "gTLD": len(gtl),
        "cTLD": len(cct),
    }))


if __name__ == "__main__":
    main(sys.argv)
