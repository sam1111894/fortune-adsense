#!/usr/bin/env python3
"""Build the syndication publish payload -- with the sizes MEASURED.

WHY THIS FILE EXISTS
--------------------
`tools/sns_publish.py measure` is the sanctioned source of the draft sizes
(the publisher's own `_read_text` + `x_weighted_length`, so the recorded
numbers cannot drift from the numbers the caps are enforced against).
But the payload that CARRIES those numbers into the article was still
hand-written on every fire, which is how a byte count ended up labelled
as a character count and a raw character count ended up next to X's
WEIGHTED 280 limit. Two fires' worth of defects came from that one
hand-authoring step.

This tool closes the loop: it takes the RUN FACTS (which platforms
published, which were skipped, why) and merges the MEASURED sizes into a
payload, so a number can never be typed by hand. The weighted count is
emitted only for the platform whose cap is weighted (X), because the
other three caps are plain character caps -- a weighted number there
would be a number read against the wrong limit.

The result is validated with `sns_frontmatter.validate_syndication`
before it is written, so a payload that would break the build or the
article never reaches the writer.

Usage:
    python3 tools/sns_payload.py --drafts-dir <dir> --facts <facts.json> \
        --out <payload.json>

The facts file holds only what a run KNOWS (statuses, reasons, exit
code); the sizes come from the measurement:

    {
      "fire": 195,
      "attemptedAt": "2026-10-24T19:43:25Z",
      "worker": "reviewer/minimax-M3",
      "outcome": "PUBLISH_BLOCKED_PLATFORM_CREDIT",
      "cronExit": 3,
      "publishPending": true,
      "blockedBy": "...",
      "targets": {
        "x":        {"status": "pending", "reason": "..."},
        "threads":  {"status": "skipped", "reason": "..."},
        "linkedin": {"status": "skipped", "reason": "..."},
        "dev.to":   {"status": "skipped", "reason": "..."}
      }
    }

Any extra top-level key is passed through to the payload's `current`
block untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_frontmatter as sf  # noqa: E402
import sns_publish as sp  # noqa: E402

PLATFORMS = ("x", "threads", "linkedin", "dev.to")


def build_payload(drafts_dir: str, facts: dict, out_path: str) -> dict:
    """Return the payload for one publish attempt.

    The sizes come from `measure_records` -- the same measurement the
    publisher's own cap checks use -- and the weighted count is attached
    only to the platform whose cap is weighted.
    """
    targets_facts = facts.get("targets")
    if not isinstance(targets_facts, dict):
        raise ValueError("facts must carry a 'targets' object")
    missing = [p for p in PLATFORMS if p not in targets_facts]
    if missing:
        raise ValueError("facts are missing targets for: %s"
                         % ", ".join(missing))

    measured = sp.measure_records(drafts_dir)
    absent = [p for p in PLATFORMS if p not in measured]
    if absent:
        raise ValueError("no draft to measure for: %s" % ", ".join(absent))

    targets = []
    for platform in PLATFORMS:
        fact = targets_facts[platform]
        record = measured[platform]
        target = {
            "platform": platform,
            "status": fact["status"],
            "reason": fact.get("reason", ""),
            "draftChars": str(record["chars"]),
            "draftBytes": str(record["bytes"]),
        }
        if "url" in fact:
            target["url"] = fact["url"]
        # The weighted count belongs to the WEIGHTED-cap platform only.
        if record["metric"] == "weighted":
            target["draftWeighted"] = str(record["used"])
        targets.append(target)

    skipped = sum(1 for t in targets if t["status"] == "skipped")
    current = {k: v for k, v in facts.items() if k not in ("targets", "fire")}
    current["targets"] = targets
    current["publishPayload"] = out_path
    current["skipCount"] = skipped
    current["skipTotal"] = len(targets)
    if "cronExit" in current:
        current["exitCode"] = current["cronExit"]

    history_entry = dict(current)
    if "fire" in facts:
        history_entry["fire"] = facts["fire"]

    # Refuse a payload the article could not carry.
    sf.validate_syndication(current)
    return {"current": current, "historyEntry": history_entry}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="build the syndication publish payload with measured sizes")
    parser.add_argument("--drafts-dir", dest="drafts_dir", required=True)
    parser.add_argument("--facts", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.facts, "r", encoding="utf-8") as fh:
        facts = json.load(fh)
    payload = build_payload(args.drafts_dir, facts, args.out)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    for target in payload["current"]["targets"]:
        print("  %-9s %s" % (target["platform"], json.dumps(
            target, ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
