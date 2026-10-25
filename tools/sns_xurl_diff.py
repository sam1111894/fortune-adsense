#!/usr/bin/env python3
"""Differential harness: compare tools/sns_xurl.py against twitter-text.

Not part of the test suite -- it needs the npm package. It is how the
Python port was validated:

    python3 tools/sns_xurl_diff.py <twitter-text-package-dir>

It compares THREE things against the reference library:

  * the URL strings twitter-text extracts,
  * the character SPANS of those URLs (the spans are what the weighted
    length and the price tier both read, so they have to agree too),
  * the weighted length.

The third one matters because a URL is charged a fixed 23 units: if the
port found a different set of URLs, the length would drift even when the
"is there a URL" answer happened to match.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_xurl as xu  # noqa: E402

CASES = [
    # scheme'd
    "https://lucksajueun.com/blog/daily-fortune-healthy-mindset/",
    "http://example.com",
    "HTTPS://EXAMPLE.COM/PATH",
    "hTtPs://x.com",
    "https://x.com/a?b=c#d",
    "https://x.co?q=1",
    # bare domains -- the bug this port exists for
    "example.com",
    "lucksajueun.com/blog/foo",
    "www.example.com",
    "a.com",
    "x.co",
    "foo.bar",
    "sub.domain.example.com/path?q=1",
    "foo.github.io",
    "my-site.co.kr",
    "not-a-url.de",
    "xn--fiqs8s.xn--0zwm56d",
    # embedded in prose
    "Visit example.com now",
    "see foo.bar for details",
    "오늘의 운세는 lucksajueun.com 에서",
    "오늘의 운세는 도구입니다",
    "no link here at all",
    "no url here",
    "",
    # NOT urls
    "file.txt",
    "version 1.2",
    "e.g. this",
    "Dr. Smith",
    "a.b",
    "test.",
    "my_site.com",
    "notatld.zzzzz",
    "a.zzzzz",
    # non-ASCII domains
    "한국어.한국",
    "a.한국",
    "한국.com",
    "http://한국.com",
    "x.한국",
    "a.b.한국",
    "foo.한국/path",
    "한국.a.com",
    "a.한국.com",
    # punctuation / boundaries
    "example.com.",
    "example.com,",
    "(example.com)",
    "<example.com>",
    '"example.com"',
    "@example.com",
    "foo@example.com",
    "xexample.com",
    "Visit example.com.",
    "foo.bar/path.",
    "foo.bar/path,",
    "foo.bar/path)",
    "foo.bar/(x)",
    "foo.bar/(x)y",
    "foo.bar/a.b",
    "foo.bar:8080",
    "foo.bar:8080/",
    "foo.bar?q=1",
    "foo.bar?q=1&r=2",
    "foo.bar/p?q=1#f",
    "foo.bar/p#frag",
    "foo.bar?",
    "foo.bar#x",
    "foo.bar/a b",
    "https://foo.bar/p.",
    "https://foo.bar/p?q=1",
    # multiple
    "first.com and second.org",
    "https://a.com https://b.com",
    "a.com, b.com.",
    # t.co
    "https://t.co/abc",
    "https://t.co/abc/def",
    "https://t.co/" + "a" * 40,
    "https://t.co/" + "a" * 41,
    # hosts that only look like domains
    "foo.com_bar",
    "foo.com-bar",
    "foo.commercial",
    "foo.co",
    # long labels
    "a" * 63 + ".com",
    "a" * 64 + ".com",
    "한국" * 32 + ".com",
    # weighted length: hangul, ascii, emoji
    "한국어",
    "ascii",
    "😀 emoji",
    "👨‍👩‍👧 fam",
    "❤️ love",
    "👍🏽 ok",
    "1️⃣ one",
    "🇰🇷 flag",
    "한국어 " + "https://example.com/x",
    "한국어 example.com",
    "https://example.com",
]


def reference(package, cases):
    # The scratch files live in a TEMP directory, not in the repo: the
    # harness writes cases and a driver script, and neither belongs in
    # the working tree.
    workdir = tempfile.mkdtemp(prefix="sns_xurl_diff_")
    try:
        cases_path = os.path.join(workdir, "cases.json")
        with open(cases_path, "w", encoding="utf-8") as handle:
            json.dump(cases, handle, ensure_ascii=False)
        script = os.path.join(workdir, "run.cjs")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(
                "const tt = require(process.argv[2]);\n"
                "const cases = JSON.parse("
                "require('fs').readFileSync(process.argv[3],'utf8'));\n"
                "const out = cases.map(t => {\n"
                "  const withIndices = tt.extractUrlsWithIndices(t);\n"
                "  return {\n"
                "    urls: withIndices.map(u => u.url),\n"
                "    indices: withIndices.map(u => u.indices),\n"
                "    weighted: tt.parseTweet(t).weightedLength,\n"
                "  };\n"
                "});\n"
                "process.stdout.write(JSON.stringify(out));\n"
            )
        proc = subprocess.run(
            ["node", script, os.path.join(package, "dist", "index.js"),
             cases_path], capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise SystemExit("node failed")
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    ref = reference(argv[1], CASES)
    mismatches = 0
    for text, expected in zip(CASES, ref):
        ours = xu.extract_urls(text)
        our_urls = [u for (u, _s, _e) in ours]
        our_indices = [[s, e] for (_u, s, e) in ours]
        our_weighted = xu.weighted_length(text)
        if our_urls != expected["urls"]:
            mismatches += 1
            print("URL MISMATCH %r\n  twitter-text = %r\n  ours        = %r"
                  % (text, expected["urls"], our_urls))
            continue
        if our_indices != expected["indices"]:
            mismatches += 1
            print("SPAN MISMATCH %r\n  twitter-text = %r\n  ours        = %r"
                  % (text, expected["indices"], our_indices))
            continue
        if our_weighted != expected["weighted"]:
            mismatches += 1
            print("WEIGHT MISMATCH %r\n  twitter-text = %r\n  ours        = %r"
                  % (text, expected["weighted"], our_weighted))
    print("\n%d cases, %d mismatches" % (len(CASES), mismatches))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
