#!/usr/bin/env python3
"""One-shot: capture twitter-text fixtures for tools/test_sns_xurl.py.

Run against the real twitter-text package:

    python3 tools/sns_xurl_fixtures_gen.py <twitter-text-package-dir>

The fixtures are committed, so the offline test suite pins the port's
behaviour WITHOUT needing npm: the reference answers were produced by the
library X itself.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

CASES = [
    "https://lucksajueun.com/blog/daily-fortune-healthy-mindset/",
    "http://example.com",
    "HTTPS://EXAMPLE.COM/PATH",
    "hTtPs://x.com",
    "https://x.com/a?b=c#d",
    "https://x.co?q=1",
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
    "Visit example.com now",
    "see foo.bar for details",
    "오늘의 운세는 lucksajueun.com 에서",
    "오늘의 운세는 도구입니다",
    "no link here at all",
    "no url here",
    "",
    "file.txt",
    "version 1.2",
    "e.g. this",
    "Dr. Smith",
    "a.b",
    "test.",
    "my_site.com",
    "notatld.zzzzz",
    "a.zzzzz",
    "한국어.한국",
    "a.한국",
    "한국.com",
    "http://한국.com",
    "x.한국",
    "a.b.한국",
    "foo.한국/path",
    "한국.a.com",
    "a.한국.com",
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
    "first.com and second.org",
    "https://a.com https://b.com",
    "a.com, b.com.",
    "https://t.co/abc",
    "https://t.co/abc/def",
    "https://t.co/" + "a" * 40,
    "https://t.co/" + "a" * 41,
    "foo.com_bar",
    "foo.com-bar",
    "foo.commercial",
    "foo.co",
    "a" * 63 + ".com",
    "a" * 64 + ".com",
    "한국" * 32 + ".com",
    "한국어",
    "ascii",
    "😀 emoji",
    "👨‍👩‍👧 fam",
    "❤️ love",
    "👍🏽 ok",
    "1️⃣ one",
    "🇰🇷 flag",
    "한국어 https://example.com/x",
    "한국어 example.com",
    "https://example.com",
]


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    package = argv[1]
    # The scratch files live in a TEMP directory, not in the repo: the
    # generator writes a cases file and a driver script, and neither
    # belongs in the working tree.
    workdir = tempfile.mkdtemp(prefix="sns_xurl_fixtures_")
    try:
        cases_path = os.path.join(workdir, "fixture_cases.json")
        with open(cases_path, "w", encoding="utf-8") as handle:
            json.dump(CASES, handle, ensure_ascii=False)
        script = os.path.join(workdir, "fixtures.cjs")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(
                "const tt = require(process.argv[2]);\n"
                "const cases = JSON.parse("
                "require('fs').readFileSync(process.argv[3],'utf8'));\n"
                "const out = cases.map(t => {\n"
                "  const u = tt.extractUrlsWithIndices(t);\n"
                "  return {urls: u.map(x => x.url), "
                "indices: u.map(x => x.indices),\n"
                "          weighted: tt.parseTweet(t).weightedLength};\n"
                "});\n"
                "process.stdout.write(JSON.stringify(out));\n"
            )
        proc = subprocess.run(
            ["node", script, os.path.join(package, "dist", "index.js"),
             cases_path], capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise SystemExit("node failed")
        reference = json.loads(proc.stdout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    fixtures = []
    for text, expected in zip(CASES, reference):
        fixtures.append({
            "text": text,
            "urls": expected["urls"],
            "indices": expected["indices"],
            "weighted": expected["weighted"],
        })
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sns_xurl_fixtures.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(fixtures, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(json.dumps({"out": out_path, "cases": len(fixtures)}))


if __name__ == "__main__":
    main(sys.argv)
