#!/usr/bin/env python3
"""
blog-sns-auto publisher helper.

Loads SNS credentials from the candidate .env files, signs OAuth 1.0a requests
for the X (Twitter) v1.1 API, and can verify credentials or publish a tweet.

Design rules (learned the hard way):
  * NEVER print a secret value. Only lengths / booleans leave this module.
  * "file exists" is not "file is non-empty" and "is not empty" is not
    "has the credential". A missing key must be distinguished from an empty
    value and from an unfilled template line.
  * Env loading order: explicit process env wins, then the project .env,
    then the reviewer profile .env, then the global ~/.hermes/.env.
    A more specific scope wins: a value in the project .env overrides the
    same key in the profile .env, so a token written by
    tools/setup-sns-tokens.sh takes effect as advertised. An EMPTY value
    never overrides a non-empty one in a lower scope.

Usage:
  python3 tools/sns_publish.py verify
  python3 tools/sns_publish.py publish-x --text-file <path>
  python3 tools/sns_publish.py status
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The durable publish-receipt store lives next to this file, so the script
# and the offline test suite import it identically.
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import sns_receipts  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENV_CANDIDATES = [
    os.path.join(REPO_ROOT, ".env"),
    os.path.expanduser("~/.hermes/profiles/reviewer/.env"),
    os.path.expanduser("~/.hermes/.env"),
]

# Credentials required per platform, and the platform label used in the
# "[SKIP] {platform} token 없음" output.
REQUIRED = {
    "x": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    "threads": ["THREADS_TOKEN"],
    "linkedin": ["LINKEDIN_TOKEN"],
    "dev.to": ["DEVTO_API_KEY"],
}

KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

# Human label per platform, used in the "[SKIP] {label} token 없음" output.
PLATFORM_LABEL = {
    "x": "X",
    "threads": "Threads",
    "linkedin": "LinkedIn",
    "dev.to": "dev.to",
}

# Per-platform draft length caps (characters, not weighted units).
PLATFORM_CAP = {"x": 280, "threads": 500, "linkedin": 1000, "dev.to": 2000}

# X pay-per-use write pricing (docs.x.com/x-api/getting-started/pricing,
# retrieved 2026-10-24). A Post that CONTAINS a URL costs 13x more than a
# URL-free Post, so the balance decides which tier we can afford:
#   Post: Create            $0.015 per request
#   Post: Create (with URL) $0.200 per request
X_COST_NO_URL = 0.015
X_COST_WITH_URL = 0.200

# Draft file names inside a run directory, per platform.
DRAFT_FILENAME = {
    "x": "01-x-tweet.txt",
    "threads": "02-threads.txt",
    "linkedin": "03-linkedin.txt",
    "dev.to": "04-devto-en.md",
}


def _strip_quotes(value: str) -> str:
    """Extract the raw secret from an .env line body.

    A .env line looks like:  export KEY="secret"      # trailing comment
    The trailing comment must not become part of the secret, but a secret may
    legitimately contain '#', so we only cut at whitespace-then-# and we honour
    a quoted value first.
    """
    value = value.strip()
    if not value:
        return value
    if value[0] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    # Unquoted: cut at " #" (whitespace followed by #).
    cut = re.search(r"\s+#", value)
    if cut:
        value = value[: cut.start()]
    return value.strip()


def load_env_files(paths=None) -> tuple[dict, list]:
    """Return (merged_env, provenance). See load_env_files_detailed."""
    merged, provenance, _sources = load_env_files_detailed(paths)
    return merged, provenance


def load_env_files_detailed(paths=None) -> tuple[dict, list, dict]:
    """Return (merged_env, provenance, sources).

    Precedence (highest first):
      1. the process environment
      2. the project .env
      3. the reviewer profile .env
      4. the global ~/.hermes/.env

    Two rules keep a credential from being lost by accident:

      * A NON-EMPTY value always wins over an empty one, no matter which
        scope it came from. An unfilled template line such as `X_API_KEY=`
        in the project .env must not shadow the working value in the
        reviewer profile .env -- otherwise merely copying .env.example to
        .env silently disables a live credential.
      * An empty process value never wipes a valid file credential.

    `sources` maps each key to the file (or "<process env>") that supplied
    the value currently in `merged`, so tooling can report where a
    credential comes from without ever printing the value.
    """
    merged: dict = {}
    provenance: list = []
    sources: dict = {}
    for path in paths or ENV_CANDIDATES:
        if not os.path.isfile(path):
            provenance.append((path, "absent"))
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            provenance.append((path, "unreadable:%s" % exc))
            continue
        size = len(raw.encode("utf-8"))
        found = 0
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = KEY_RE.match(line)
            if not match:
                continue
            key, value = match.group(1), _strip_quotes(match.group(2))
            if key not in merged:
                merged[key] = value
                sources[key] = path
                found += 1
            elif not merged[key].strip() and value.strip():
                # An empty placeholder in a higher-precedence file must not
                # shadow a usable value in a lower-precedence one.
                merged[key] = value
                sources[key] = path
                found += 1
        provenance.append((path, "bytes=%d keys=%d" % (size, found)))
    # Process environment overrides .env files (highest precedence), matching
    # sns_precheck.py. Only NON-EMPTY process values override, so an empty
    # `export THREADS_TOKEN=` in a shell profile cannot silently wipe a valid
    # .env credential.
    env_added = 0
    for key, value in os.environ.items():
        if not value:
            continue
        if any(key in keys for keys in REQUIRED.values()):
            if merged.get(key) != value:
                env_added += 1
            merged[key] = value
            sources[key] = "<process env>"
    provenance.append(("<process env>", "overrides=%d" % env_added))
    return merged, provenance, sources


def credential_state(env: dict) -> dict:
    """Classify each platform credential as present / missing / empty."""
    state = {}
    for platform, keys in REQUIRED.items():
        details = []
        usable = True
        for key in keys:
            raw = env.get(key)
            if raw is None:
                details.append((key, "missing"))
                usable = False
            elif raw.strip() == "":
                details.append((key, "empty"))
                usable = False
            else:
                details.append((key, "present(len=%d)" % len(raw)))
        state[platform] = {"usable": usable, "keys": details}
    return state


def oauth1_header(method, url, params, consumer_key, consumer_secret,
                  token, token_secret) -> str:
    """Build an OAuth 1.0a Authorization header (HMAC-SHA1)."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": base64.b32encode(os.urandom(15)).decode().strip("="),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
        "oauth_token": token,
    }
    all_params = dict(params)
    all_params.update(oauth)

    encoded = {
        key: urllib.parse.quote(str(value), safe="~")
        for key, value in all_params.items()
    }
    param_string = "&".join(
        "%s=%s" % (urllib.parse.quote(key, safe="~"), encoded[key])
        for key in sorted(encoded)
    )
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe="~"),
        urllib.parse.quote(param_string, safe="~"),
    ])
    signing_key = "%s&%s" % (
        urllib.parse.quote(consumer_secret, safe="~"),
        urllib.parse.quote(token_secret, safe="~"),
    )
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    oauth["oauth_signature"] = signature

    header = ", ".join(
        '%s="%s"' % (urllib.parse.quote(key, safe="~"),
                     urllib.parse.quote(str(value), safe="~"))
        for key, value in sorted(oauth.items())
    )
    return "OAuth " + header


def http_request(url, method="GET", params=None, body=None,
                 headers=None, timeout=25, json_body=None):
    params = params or {}
    if json_body is not None:
        encoded_body = json.dumps(json_body).encode("utf-8")
        full_url = url + ("?" + urllib.parse.urlencode(params) if params else "")
    elif body is not None:
        encoded_body = urllib.parse.urlencode(body).encode()
        full_url = url
    else:
        encoded_body = None
        full_url = url + ("?" + urllib.parse.urlencode(params) if params else "")

    request = urllib.request.Request(full_url, data=encoded_body, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "blog-sns-auto/1.0")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", "replace")
            return response.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - network layer
        return 0, json.dumps({"error": str(exc)})


def x_request(env, method, path, params=None, body=None, json_body=None):
    """Signed X API request.

    `path` may be a full URL or an absolute API path. Paths are routed to
    the v2 base when they start with /2/, otherwise to the v1.1 base.

    IMPORTANT (learned 2026-10-23): form-encoded body parameters MUST be
    included in the OAuth 1.0a signature base string (RFC 5849 §3.4.1.3.2).
    JSON bodies are NOT part of the signature, only the query string is.

    X retired the v1.1 write endpoints (POST /1.1/statuses/update.json now
    answers HTTP 404 with an empty body), so writes must use v2.
    """
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    elif path.startswith("/2/"):
        url = "https://api.twitter.com" + path
    else:
        url = "https://api.twitter.com/1.1" + path
    consumer_key = env.get("X_API_KEY", "")
    consumer_secret = env.get("X_API_SECRET", "")
    token = env.get("X_ACCESS_TOKEN", "")
    token_secret = env.get("X_ACCESS_TOKEN_SECRET", "")
    sign_params = dict(params or {})
    if body is not None:
        sign_params.update(body)
    header = oauth1_header(method, url, sign_params, consumer_key,
                           consumer_secret, token, token_secret)
    headers = {"Authorization": header}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        return http_request(url, method, params, None, headers, json_body=json_body)
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return http_request(url, method, params, body, headers)


def cmd_timeline(_args):
    """List recent tweets by the authenticated account.

    Used to guarantee we do not publish a duplicate of a post that was
    already tweeted (for example by a manual run outside this pipeline).
    """
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["x"]["usable"]:
        print("X credentials not usable; nothing to list")
        return 2
    status, body = x_request(env, "GET", "/statuses/user_timeline.json",
                              params={"count": "50", "tweet_mode": "extended"})
    print("HTTP %s" % status)
    try:
        payload = json.loads(body)
    except ValueError:
        print(body[:400])
        return 1
    if status != 200 or not isinstance(payload, list):
        print("timeline failed: %s" % json.dumps(
            payload.get("errors", payload), ensure_ascii=False)[:400])
        return 1
    print("tweets fetched: %d" % len(payload))
    for tweet in payload:
        text = tweet.get("full_text") or tweet.get("text") or ""
        created = tweet.get("created_at", "")
        print("--- id=%s created=%s" % (tweet.get("id_str"), created))
        print("    %s" % text.replace("\n", " ")[:280])
    return 0


def cmd_dupcheck(args):
    """Search the account's recent tweets for the given needle.

    Guards against publishing a duplicate when a post was already tweeted by
    an earlier fire or by a manual run.

    X's pay-per-use model meters the READ endpoints too, and the account's
    access tier is narrower still:
      * GET /2/users/:id/tweets   -> HTTP 402 "credits depleted" (metered)
      * GET /1.1/statuses/user_timeline.json -> HTTP 403 code 453 (the app
        only has a subset of v1.1: media post + oauth)

    So there is NO endpoint that can list tweet TEXT for this account. The
    only free read is GET /1.1/account/verify_credentials.json, whose
    `statuses_count` is the account's LIFETIME tweet count. It cannot match
    a needle, but it does answer the question that actually gates a publish:
    "has this account ever posted anything?" (count == 0 => never posted).

    This command therefore reports which signal it used and refuses to
    claim a needle match it could not verify.
    """
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["x"]["usable"]:
        print("X credentials not usable; nothing to check")
        return 2
    needle = (getattr(args, "needle", "") or "").strip()
    if not needle:
        print("dupcheck requires --needle")
        return 2

    status, body = x_request(env, "GET", "/account/verify_credentials.json")
    try:
        uid = str(json.loads(body).get("id", ""))
    except ValueError:
        uid = ""
    if not uid:
        print("could not resolve user id")
        return 1

    attempts = []
    status, body = x_request(
        env, "GET", "/2/users/%s/tweets" % uid,
        params={"max_results": "100", "tweet.fields": "created_at,text"})
    attempts.append(("v2 /2/users/:id/tweets", status))

    print("attempts: " + ", ".join("%s -> HTTP %s" % a for a in attempts))
    try:
        payload = json.loads(body)
    except ValueError:
        print(body[:400])
        return 1
    if status != 200:
        # The tweet-listing endpoints are unavailable for this account
        # (metered or tier-restricted). Fall back to the one FREE read and
        # report exactly what it proves -- never claim a needle match that
        # could not be checked.
        print("tweet listing unavailable (scope restriction): %s" % json.dumps(
            payload.get("errors", payload), ensure_ascii=False)[:300])
        count = _x_statuses_count(env)
        if count is None:
            print("  free fallback unavailable too "
                  "(GET /1.1/account/verify_credentials.json failed)")
            print("  VERDICT: UNKNOWN - cannot verify a duplicate; rely on the "
                  "durable publish-receipt store (tools/sns_receipts.py)")
            return 1
        print("  free fallback: statuses_count=%d "
              "(lifetime tweets by this account)" % count)
        if count == 0:
            print("  VERDICT: NO DUPLICATE POSSIBLE - this account has never "
                  "posted, so the needle cannot be present")
            return 0
        print("  VERDICT: UNKNOWN - the account has %d lifetime tweets but no "
              "tweet text could be read; needle NOT verified" % count)
        print("  조치: X 계정에서 최근 트윗을 눈으로 확인하세요.")
        return 1
    tweets = payload if isinstance(payload, list) else payload.get("data", [])
    hits = []
    for tweet in tweets:
        text = tweet.get("full_text") or tweet.get("text") or ""
        if needle in text:
            hits.append((tweet.get("id_str", tweet.get("id")), text))
    print("tweets scanned: %d" % len(tweets))
    print("needle hits  : %d" % len(hits))
    for tweet_id, text in hits:
        print("  DUPLICATE id=%s :: %s" % (tweet_id, text.replace("\n", " ")[:200]))
    return 0 if not hits else 3


def cmd_status(args) -> int:
    env, provenance, sources = load_env_files_detailed()
    state = credential_state(env)

    if getattr(args, "json", False):
        platforms = {}
        for platform, info in state.items():
            keys = []
            for key, detail in info["keys"]:
                # detail looks like "present(len=25)" / "missing" / "empty"
                state_word = detail.split("(", 1)[0]
                keys.append({
                    "key": key,
                    "state": state_word,
                    "source": sources.get(key),
                })
            platforms[platform] = {"usable": info["usable"], "keys": keys}
        print(json.dumps({
            "platforms": platforms,
            "envSources": [{"path": path, "note": note} for path, note in provenance],
        }, ensure_ascii=False, indent=2))
        return 0

    print("=== env sources ===")
    for path, note in provenance:
        print("  %s -> %s" % (path, note))
    print("=== credential state ===")
    for platform, info in state.items():
        keys = "; ".join("%s=%s" % (k, v) for k, v in info["keys"])
        print("  %-8s %s" % (platform, "USABLE" if info["usable"] else "UNUSABLE"))
        print("           %s" % keys)
    return 0


def cmd_verify(_args):
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["x"]["usable"]:
        print("X credentials not usable; nothing to verify")
        return 2
    status, body = x_request(env, "GET", "/account/verify_credentials.json")
    print("HTTP %s" % status)
    try:
        payload = json.loads(body)
    except ValueError:
        print(body[:400])
        return 1
    if status == 200:
        print("auth OK  screen_name=@%s  id=%s" % (
            payload.get("screen_name"), payload.get("id")))
        return 0
    print("auth FAILED  errors=%s" % json.dumps(
        payload.get("errors", payload), ensure_ascii=False)[:400])
    return 1


# --- X weighted length (twitter-text v3 semantics) -------------------------
# Raw len() is NOT a valid X limit check for Korean copy: ASCII/Latin ranges
# count 1 unit, CJK/Hangul/emoji count 2, and every URL is counted as a
# fixed 23 units after t.co shortening. A 154-char Korean draft can easily
# exceed 280 weighted units, so the check must be weighted, not raw.
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
X_URL_RE = re.compile(r"(?:https?://|www\.)[^\s]+", re.I)
X_URL_TRAIL = ".,;:!?)]}'\"’\"”"


def _char_weight(code_point: int) -> int:
    for start, end, weight in X_WEIGHT_RANGES:
        if start <= code_point <= end:
            return weight
    return X_DEFAULT_WEIGHT


def x_weighted_length(text: str) -> int:
    """Return the twitter-text v3 weighted length of a tweet."""
    total = 0
    cursor = 0
    for match in X_URL_RE.finditer(text):
        total += sum(_char_weight(ord(ch)) for ch in text[cursor:match.start()])
        url = match.group(0)
        trailing = ""
        while url and url[-1] in X_URL_TRAIL:
            trailing = url[-1] + trailing
            url = url[:-1]
        total += X_URL_WEIGHT * X_SCALE
        total += sum(_char_weight(ord(ch)) for ch in trailing)
        cursor = match.end()
    total += sum(_char_weight(ord(ch)) for ch in text[cursor:])
    return total // X_SCALE


def cmd_weight(args):
    """Measure the weighted length of a draft file."""
    with open(args.text_file, "r", encoding="utf-8") as handle:
        text = handle.read().strip()
    weighted = x_weighted_length(text)
    print("file=%s raw=%d weighted=%d/%d" % (
        args.text_file, len(text), weighted, X_MAX_WEIGHTED))
    return 0 if weighted <= X_MAX_WEIGHTED else 3


def _has_url(text):
    """True if the draft contains a URL (X charges $0.200 for URL posts vs
    $0.015 for URL-free ones)."""
    return bool(re.search(r"https?://", text or ""))


def _usable_post_id(value):
    """Normalize a platform post id, or None when there is not a usable one.

    A response without a usable id cannot be recorded as a successful
    publication: we would have no way to point at the post we claim
    exists, and the guard would then skip this content forever.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _publish_outcome(status, post_id, write_attempted):
    """Classify a platform write into a receipt state.

    "published" -- HTTP 2xx AND a usable post id: the post exists.
    "failed"    -- a 4xx rejection, or nothing was ever sent to the
                   platform: definitively NOT published, so a retry is safe.
    "pending"   -- the write may have landed but we cannot prove it: the
                   outcome is UNKNOWN and must be resolved with a free read
                   before any retry.
    """
    if write_attempted and status in (200, 201) and post_id:
        return "published"
    if not write_attempted or status is None:
        # Nothing was sent, so nothing can have been published.
        return "failed"
    if 400 <= status < 500:
        return "failed"
    return "pending"


def _x_affordable_tier(env, text):
    """Return (balance, required_cost) for the X write, or (None, 0) if the
    free credit read is unavailable.

    The cost depends on whether the draft carries a URL, because X prices a
    Post containing a URL far higher than a URL-free Post.
    """
    token, err = _app_only_bearer(env)
    if not token:
        return None, 0.0
    status, body = http_request(
        "https://api.x.com/2/usage/credits", "GET",
        headers={"Authorization": "Bearer %s" % token})
    if status != 200:
        return None, 0.0
    try:
        data = (json.loads(body) or {}).get("data", {})
    except ValueError:
        return None, 0.0
    balance = float(data.get("total_balance", 0.0) or 0.0)
    cost = X_COST_WITH_URL if _has_url(text) else X_COST_NO_URL
    return balance, cost


# --- durable publish receipts -------------------------------------------
# A publish can succeed on the platform and still leave NO trace locally, if
# the process dies between "HTTP 200 from the API" and "the syndication
# ledger is written by the next, separate step". The next fire then sees an
# unchanged body, no successful publish on record, and repeats the write --
# paying for it a second time and double-posting.
#
# The receipt closes that window: it is written by THIS process, around the
# write, so the outcome is durable even if the run dies immediately after.
# See tools/sns_receipts.py for the store format and the state machine.


def _receipt_write(platform, attempt_id, state, text_sha=None,
                   content_sha=None, **fields):
    """Append one receipt line for an attempt."""
    record = {
        "platform": platform,
        "attemptId": attempt_id,
        "state": state,
        "textSha256": text_sha,
        "contentSha256": content_sha,
    }
    record.update(fields)
    return sns_receipts.append(record)


def _receipt_guard(platform, text_sha, content_sha, resolver=None):
    """Decide whether a publish may proceed, based on the receipt store.

    Returns (verdict, reason) where verdict is:
      "proceed" -- no evidence of a prior publish; safe to write
      "skip"    -- this content is already published; do NOT write again
      "block"   -- a previous attempt for this content never reached a
                   terminal state, so its outcome is unknown. Writing again
                   could duplicate a post (and charge again), so the write is
                   refused until the operator resolves it.

    `resolver(attempt)` settles an unresolved attempt and returns
    "published", "failed", or None when it cannot tell. A settled attempt is
    appended to the receipt log so the ambiguity is gone for good.
    """
    receipts = sns_receipts.load()
    attempts = sns_receipts.group_attempts(receipts, platform)
    published = sns_receipts.find_published(attempts, text_sha, content_sha)
    if published is not None:
        return "skip", ("receipt %s: 이미 발행됨 (postId=%s)"
                        % (published.get("attemptId"), published.get("postId")))
    pending = sns_receipts.find_pending(attempts, text_sha, content_sha)
    if pending is None:
        return "proceed", None
    verdict = resolver(pending) if resolver else None
    if verdict == "published":
        _receipt_write(platform, pending["attemptId"], "published",
                       text_sha=pending.get("textSha256"),
                       content_sha=pending.get("contentSha256"),
                       postId=pending.get("postId"),
                       url=pending.get("url"),
                       completedAt=sns_receipts.now_iso(),
                       note="resolved by duplicate-guard probe")
        return "skip", ("receipt %s: 미완료 시도가 실제로 발행됨으로 확인됨"
                        % pending["attemptId"])
    if verdict == "failed":
        _receipt_write(platform, pending["attemptId"], "failed",
                       text_sha=pending.get("textSha256"),
                       content_sha=pending.get("contentSha256"),
                       completedAt=sns_receipts.now_iso(),
                       note="resolved by duplicate-guard probe: not published")
        return "proceed", None
    return "block", ("receipt %s: 이전 발행 시도의 결과 미확정 (중복 발행/중복 과금 위험)"
                     % pending["attemptId"])


def _x_statuses_count(env):
    """Free read of the account's tweet count, or None when unavailable.

    GET /1.1/account/verify_credentials.json is free under X's pay-per-use
    model, unlike GET /2/users/:id/tweets (HTTP 402).
    """
    status, body = x_request(env, "GET", "/account/verify_credentials.json")
    if status != 200:
        return None
    try:
        payload = json.loads(body) or {}
    except ValueError:
        return None
    count = payload.get("statuses_count")
    return count if isinstance(count, int) else None


def _resolve_x_pending(env, attempt):
    """Settle an unresolved X attempt by comparing the tweet count.

    The count is snapshotted BEFORE the write, so a larger count means the
    write landed and must not be repeated; an unchanged count means it did
    not. A smaller count means a tweet was deleted, which tells us nothing
    about our write -> unresolved.
    """
    before = attempt.get("statusesCountBefore")
    if not isinstance(before, int):
        return None
    now = _x_statuses_count(env)
    if now is None:
        return None
    if now > before:
        return "published"
    if now == before:
        return "failed"
    return None


def _devto_article_exists(env, attempt):
    """True/False when the published article is listed for this account, or
    None when the listing could not be read at all.

    GET /api/articles/me is free, so a dev.to publish can be verified without
    spending anything.
    """
    key = env.get("DEVTO_API_KEY", "")
    if not key:
        return None
    status, body = http_request(
        "https://dev.to/api/articles/me", "GET",
        headers={"api-key": key,
                 "Accept": "application/vnd.forem.api-v1-json"})
    if status != 200:
        return None
    try:
        articles = json.loads(body) or []
    except ValueError:
        return None
    if not isinstance(articles, list):
        return None
    canonical = attempt.get("canonicalUrl")
    title = (attempt.get("title") or "").strip()
    for article in articles:
        if not isinstance(article, dict):
            continue
        if canonical and article.get("canonical_url") == canonical:
            return True
        if title and (article.get("title") or "").strip() == title:
            return True
    return False


def _resolve_devto_pending(env, attempt):
    """Settle an unresolved dev.to attempt via the free article listing.

    An unreadable listing resolves to None (still unknown) -- it must never
    be read as "not published", or the guard would re-publish a post that
    had in fact landed.
    """
    exists = _devto_article_exists(env, attempt)
    if exists is None:
        return None
    return "published" if exists else "failed"


def _threads_thread_exists(env, attempt):
    """True/False when the thread is listed for this account, or None when
    the listing could not be read.

    GET /v1.0/me/threads is free, so a Threads publish can be verified
    without spending anything.
    """
    token = env.get("THREADS_TOKEN", "")
    if not token:
        return None
    status, body = http_request(
        "https://graph.threads.net/v1.0/me/threads", "GET",
        params={"access_token": token, "fields": "id,text"})
    if status != 200:
        return None
    try:
        payload = json.loads(body) or {}
    except ValueError:
        return None
    threads = payload.get("data")
    if not isinstance(threads, list):
        return None
    preview = (attempt.get("textPreview") or "").strip()
    text_sha = attempt.get("textSha256")
    creation_id = attempt.get("creationId")
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        if creation_id and thread.get("id") == creation_id:
            return True
        listed = (thread.get("text") or "").strip()
        if not listed:
            continue
        # textPreview is a 120-char TRUNCATION of the posted text, so an
        # exact comparison against the full listed text can never match.
        # Compare the hash of the full text, and fall back to a prefix
        # comparison for platform-side whitespace normalisation. A false
        # "published" verdict only skips a publish (recoverable); a false
        # "failed" verdict would re-post a live thread, so err towards
        # matching.
        if text_sha and sns_receipts.text_key(listed) == text_sha:
            return True
        if preview and len(preview) >= 40 and listed.startswith(preview):
            return True
    return False


def _resolve_threads_pending(env, attempt):
    """Settle an unresolved Threads attempt via the free thread listing."""
    exists = _threads_thread_exists(env, attempt)
    if exists is None:
        return None
    return "published" if exists else "failed"


def cmd_publish_x(args):
    """Publish a tweet via the X v2 API.

    Endpoint notes (verified 2026-10-23 against the live API):
      * POST /1.1/statuses/update.json -> HTTP 404, empty body (retired).
      * POST /2/tweets               -> live; needs a funded credit balance
        under X's pay-per-use model (HTTP 402 "credits depleted" when empty).

    A durable receipt is written around the write (see tools/sns_receipts.py):
    a "pending" record BEFORE the POST, then "published" or "failed". The
    pending record is what lets a later run tell "never attempted" from
    "attempted, outcome unknown" -- the difference between a safe retry and
    a paid duplicate.
    """
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["x"]["usable"]:
        print("[SKIP] X token 없음")
        return 2
    with open(args.text_file, "r", encoding="utf-8") as handle:
        text = handle.read().strip()
    if not text:
        print("[SKIP] X tweet 본문 비어있음")
        return 2
    weighted = x_weighted_length(text)
    if weighted > X_MAX_WEIGHTED:
        print("[SKIP] X tweet weighted %d/%d 초과 (raw %d자)" % (
            weighted, X_MAX_WEIGHTED, len(text)))
        return 2
    print("X weighted length: %d/%d (raw %d자)" % (weighted, X_MAX_WEIGHTED, len(text)))

    # Duplicate guard, backed by the durable receipt store. This must run
    # BEFORE any billable call: a "skip" verdict means the post already
    # exists, and a "block" verdict means an earlier attempt's outcome is
    # unknown -- in both cases spending $0.200 again would be wrong.
    text_sha = sns_receipts.text_key(text)
    content_sha = args.content_sha or None
    verdict, reason = _receipt_guard("x", text_sha, content_sha,
                                     resolver=lambda attempt: _resolve_x_pending(env, attempt))
    if verdict == "skip":
        print("[SKIP] X 이미 발행됨 — 재발행 안 함 (%s)" % reason)
        return 2
    if verdict == "block":
        print("PUBLISH BLOCKED — 중복 발행 위험 (%s)" % reason)
        print("  조치: X 계정에서 해당 게시물을 확인한 뒤 receipts 로그를 정리하세요.")
        return 3
    # Free pre-flight: X charges per POST, so check the balance BEFORE the
    # write. GET /2/usage/credits is a free read and tells us whether the
    # attempt is even billable. Without this we burn a POST attempt to learn
    # the same 402 the credit endpoint would have reported for free.
    if not getattr(args, "skip_credit_check", False):
        balance, cost = _x_affordable_tier(env, text)
        if balance is None:
            # Fail CLOSED: an unreadable balance is not an affordable
            # balance. Proceeding would spend money we could not verify.
            print("PUBLISH BLOCKED — 크레딧 잔액 확인 불가 (fail-closed)")
            print("  조치: GET /2/usage/credits 조회를 복구한 뒤 재실행하세요.")
            print("        (잔액 확인 없이 진행하려면 --skip-credit-check 를 사용하세요)")
            return 3
        if balance < cost:
            tier = "URL 포함($%.3f)" % X_COST_WITH_URL if _has_url(text) \
                else "URL 미포함($%.3f)" % X_COST_NO_URL
            print("PUBLISH BLOCKED — 크레딧 부족 (잔액 $%.3f < %s)"
                  % (balance, tier))
            print("  조치: console.x.com에서 이 앱에 크레딧 충전 후 재실행.")
            return 3
        else:
            print("  credit pre-flight OK (잔액 $%.3f, 필요 $%.3f)"
                  % (balance, cost))

    # Durable receipt: record the attempt BEFORE the billable POST, so a
    # crash between the POST and its response leaves a "pending" record
    # instead of no trace at all. statusesCountBefore is a FREE read and
    # lets a later run settle that pending record: a higher count means the
    # write landed (do not retry), an unchanged count means it did not.
    attempt_id = sns_receipts.new_attempt_id()
    statuses_before = _x_statuses_count(env)
    _receipt_write("x", attempt_id, "pending",
                   text_sha=text_sha, content_sha=content_sha,
                   startedAt=sns_receipts.now_iso(),
                   statusesCountBefore=statuses_before,
                   textPreview=text[:120],
                   weightedLength=weighted)
    print("  receipt pending attemptId=%s" % attempt_id)

    status, body = x_request(env, "POST", "/2/tweets", json_body={"text": text})
    print("HTTP %s" % status)
    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    tweet_id = _usable_post_id(data.get("id"))
    # The POST was sent, so any 2xx without a usable id is UNKNOWN, not a
    # failure: the tweet may exist but we cannot prove it. Leaving it
    # pending lets the free statuses_count probe settle it.
    outcome = _publish_outcome(status, tweet_id, write_attempted=True)
    if outcome == "published":
        url = "https://x.com/%s/status/%s" % (
            data.get("username") or "SamJ868644", tweet_id)
        _receipt_write("x", attempt_id, "published",
                       text_sha=text_sha, content_sha=content_sha,
                       postId=tweet_id, url=url,
                       completedAt=sns_receipts.now_iso())
        print("PUBLISHED id=%s url=%s" % (tweet_id, url))
        print(json.dumps({"id": tweet_id, "url": url}, ensure_ascii=False))
        return 0
    if outcome == "failed":
        # A 4xx is a definitive rejection: the platform created nothing, so
        # a later retry is safe. Record it as failed, not pending.
        _receipt_write("x", attempt_id, "failed",
                       text_sha=text_sha, content_sha=content_sha,
                       httpStatus=status,
                       completedAt=sns_receipts.now_iso())
    else:
        # Ambiguous: a network error (status 0), a 5xx, or a 2xx without
        # a usable tweet id may have happened AFTER the platform created
        # the post. Leave the receipt "pending" so the next run resolves it
        # instead of paying twice.
        print("  결과 불확정 — receipt를 pending으로 남겨 다음 실행에서 확인합니다.")
    if status == 402:
        print("PUBLISH BLOCKED — HTTP 402 credits depleted")
        print("  원인: X API가 2026-02부터 pay-per-use 크레딧 과금 모델로 전환.")
        print("  Developer Console(console.x.com)에서 이 앱에 크레딧을 충전해야 합니다.")
        print("  참고: URL 포함 Post 생성 = $0.200, URL 미포함 = $0.015 (요청당)")
        return 3
    if status == 404:
        print("PUBLISH FAILED — HTTP 404 (endpoint not available for this app/tier)")
        return 1
    if status == 401:
        print("PUBLISH FAILED — HTTP 401 (OAuth 1.0a signature/credential rejected)")
        return 1
    if status == 403:
        print("PUBLISH FAILED — HTTP 403 (permission / access level / account state)")
        print("  detail: %s" % body[:300])
        return 1
    print("PUBLISH FAILED errors=%s" % json.dumps(
        payload.get("errors", payload), ensure_ascii=False)[:400])
    return 1


def _read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _app_only_bearer(env):
    """Mint an OAuth 2.0 Application-Only bearer token.

    X's pay-per-use usage endpoints (/2/usage/credits, /2/usage/tweets) are
    READ-ONLY and FREE, but they reject OAuth 1.0a User Context (HTTP 403
    "Unsupported Authentication"). They accept Application-Only context, which
    we can mint with the OAuth 1.0a CONSUMER key/secret via a
    client_credentials grant.

    IMPORTANT (verified 2026-10-24): the client_credentials grant only works
    with X_API_KEY/X_API_SECRET (the OAuth1 consumer pair). X_CLIENT_ID /
    X_CLIENT_SECRET are a DIFFERENT credential set and the grant fails with
    {"code":99,"message":"Unable to verify your credentials"}.

    Returns (token, error_message). The token is never printed.
    """
    import base64 as _b64
    key = env.get("X_API_KEY", "")
    secret = env.get("X_API_SECRET", "")
    if not key or not secret:
        return None, "X_API_KEY/X_API_SECRET missing"
    basic = _b64.b64encode(("%s:%s" % (key, secret)).encode()).decode()
    status, body = http_request(
        "https://api.x.com/oauth2/token", "POST",
        body={"grant_type": "client_credentials"},
        headers={"Authorization": "Basic %s" % basic,
                 "Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        return None, "client_credentials HTTP %s" % status
    try:
        token = json.loads(body).get("access_token", "")
    except ValueError:
        return None, "client_credentials non-JSON response"
    if not token:
        return None, "client_credentials returned no access_token"
    return token, None


def cmd_credits(_args):
    """Report the X pay-per-use credit balance (free read, no Post charged).

    This is the cheapest possible pre-flight: it tells us whether a write
    attempt would even be billable, without burning a POST attempt.
    """
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["x"]["usable"]:
        print("[SKIP] X token 없음")
        return 2
    token, err = _app_only_bearer(env)
    if not token:
        print("CREDITS UNAVAILABLE — %s" % err)
        print("  (usage endpoints need OAuth 2.0 App-Only; publish may still work)")
        return 2
    status, body = http_request(
        "https://api.x.com/2/usage/credits", "GET",
        headers={"Authorization": "Bearer %s" % token})
    print("GET /2/usage/credits -> HTTP %s" % status)
    if status != 200:
        print("  body: %s" % body[:300])
        return 1
    try:
        data = (json.loads(body) or {}).get("data", {})
    except ValueError:
        print("  non-JSON body: %s" % body[:300])
        return 1
    total = float(data.get("total_balance", 0.0) or 0.0)
    prepaid = float(data.get("prepaid_balance", 0.0) or 0.0)
    free_bal = float(data.get("free_balance", 0.0) or 0.0)
    grants = data.get("free_grants") or []
    print("  total_balance   = %.3f USD" % total)
    print("  prepaid_balance = %.3f USD" % prepaid)
    print("  free_balance    = %.3f USD" % free_bal)
    print("  free_grants     = %d" % len(grants))
    print("  cost tiers: Post with URL = %.3f USD | Post without URL = %.3f USD"
          % (X_COST_WITH_URL, X_COST_NO_URL))
    if total >= X_COST_WITH_URL:
        print("  -> affordable: URL tier OK")
        return 0
    if total >= X_COST_NO_URL:
        print("  -> affordable: NO-URL tier only (%.3f < %.3f)"
              % (total, X_COST_WITH_URL))
        return 0
    print("  -> NOT affordable: balance below the %.3f USD no-URL tier" % X_COST_NO_URL)
    return 3


def _threads_publish(env, text, user_id=None):
    """Publish a text post to Threads via the two-step container flow.

    Threads requires: POST /{id}/threads (create container) then
    POST /{id}/threads_publish (creation_id). The 'me' alias resolves the
    authorized user, so a numeric user id is optional.
    """
    token = env.get("THREADS_TOKEN", "")
    target = user_id or env.get("THREADS_USER_ID", "") or "me"
    base = "https://graph.threads.net/v1.0/%s" % target
    status, body = http_request(
        base + "/threads", "POST",
        body={"media_type": "TEXT", "text": text, "access_token": token},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    if status not in (200, 201):
        return status, body, None
    try:
        creation_id = (json.loads(body) or {}).get("id")
    except ValueError:
        return status, body, None
    if not creation_id:
        return status, body, None
    pstatus, pbody = http_request(
        base + "/threads_publish", "POST",
        body={"creation_id": creation_id, "access_token": token},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    return pstatus, pbody, creation_id


def _linkedin_author_urn(env):
    """Resolve the member's person URN from the access token.

    GET /v2/me needs the 'r_liteprofile' scope; GET /v2/userinfo needs
    'openid'. We try both so either token flavour works.
    """
    token = env.get("LINKEDIN_TOKEN", "")
    headers = {"Authorization": "Bearer %s" % token,
               "X-Restli-Protocol-Version": "2.0.0"}
    for path, key in (("/v2/me", "id"), ("/v2/userinfo", "sub")):
        status, body = http_request("https://api.linkedin.com" + path,
                                     "GET", headers=headers)
        if status != 200:
            continue
        try:
            value = (json.loads(body) or {}).get(key)
        except ValueError:
            continue
        if value:
            return "urn:li:person:%s" % value, None
    return None, "cannot resolve LinkedIn author URN (need r_liteprofile or openid scope)"


def _linkedin_publish(env, text):
    """Create a LinkedIn member post via POST /v2/ugcPosts.

    Returns (status, body, urn). status is None when the author URN could
    not be resolved (an auth/scope problem, not an API error).
    """
    token = env.get("LINKEDIN_TOKEN", "")
    urn, err = _linkedin_author_urn(env)
    if not urn:
        return None, err or "author URN unresolved", None
    payload = {
        "author": urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    headers = {
        "Authorization": "Bearer %s" % token,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    status, body = http_request("https://api.linkedin.com/v2/ugcPosts",
                                 "POST", headers=headers, json_body=payload)
    return status, body, urn


def _devto_publish(env, title, body_markdown, tags, canonical_url=None):
    """Create (and publish) a dev.to article via POST /api/articles."""
    key = env.get("DEVTO_API_KEY", "")
    article = {
        "title": title,
        "body_markdown": body_markdown,
        "published": True,
        "tags": list(tags)[:4],
    }
    if canonical_url:
        article["canonical_url"] = canonical_url
    headers = {
        "api-key": key,
        "Accept": "application/vnd.forem.api-v1+json",
        "Content-Type": "application/json",
    }
    status, body = http_request("https://dev.to/api/articles", "POST",
                                 headers=headers, json_body={"article": article})
    return status, body


def cmd_publish_threads(args):
    """Publish the Threads draft (500 char cap).

    A durable receipt is written around the write, so a crash mid-publish
    leaves a resolvable trace instead of an invisible post.
    """
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["threads"]["usable"]:
        print("[SKIP] Threads token 없음")
        return 2
    text = _read_text(args.text_file)
    if not text:
        print("[SKIP] Threads 본문 비어있음")
        return 2
    if len(text) > PLATFORM_CAP["threads"]:
        print("[SKIP] Threads %d자/%d 초과" % (len(text), PLATFORM_CAP["threads"]))
        return 2
    print("Threads length: %d/%d" % (len(text), PLATFORM_CAP["threads"]))
    text_sha = sns_receipts.text_key(text)
    content_sha = args.content_sha or None
    verdict, reason = _receipt_guard(
        "threads", text_sha, content_sha,
        resolver=lambda attempt: _resolve_threads_pending(env, attempt))
    if verdict == "skip":
        print("[SKIP] Threads 이미 발행됨 — 재발행 안 함 (%s)" % reason)
        return 2
    if verdict == "block":
        print("PUBLISH BLOCKED — 중복 발행 위험 (%s)" % reason)
        print("  조치: Threads 계정에서 해당 게시물을 확인한 뒤 receipts 로그를 정리하세요.")
        return 3
    attempt_id = sns_receipts.new_attempt_id()
    _receipt_write("threads", attempt_id, "pending",
                   text_sha=text_sha, content_sha=content_sha,
                   startedAt=sns_receipts.now_iso(),
                   textPreview=text[:120])
    status, body, creation_id = _threads_publish(env, text)
    print("HTTP %s" % status)
    # creation_id is non-None exactly when the PUBLISH step was actually
    # sent: _threads_publish returns None for it whenever the container
    # step failed, and a container alone never publishes a thread.
    publish_attempted = creation_id is not None
    try:
        post_id = _usable_post_id((json.loads(body) or {}).get("id"))
    except ValueError:
        post_id = None
    outcome = _publish_outcome(status, post_id, publish_attempted)
    if outcome == "published":
        _receipt_write("threads", attempt_id, "published",
                       text_sha=text_sha, content_sha=content_sha,
                       postId=post_id, creationId=creation_id,
                       completedAt=sns_receipts.now_iso())
        print("PUBLISHED id=%s creation_id=%s" % (post_id, creation_id))
        print(json.dumps({"id": post_id, "creation_id": creation_id},
                         ensure_ascii=False))
        return 0
    if outcome == "failed":
        _receipt_write("threads", attempt_id, "failed",
                       text_sha=text_sha, content_sha=content_sha,
                       httpStatus=status, creationId=creation_id,
                       completedAt=sns_receipts.now_iso())
    else:
        # Ambiguous: the publish request was sent but we cannot prove it
        # landed. Keep it pending and carry the container id, so the next
        # run can settle it against the free thread listing instead of
        # publishing the same thread twice.
        _receipt_write("threads", attempt_id, "pending",
                       text_sha=text_sha, content_sha=content_sha,
                       httpStatus=status, creationId=creation_id,
                       note="publish outcome unproven")
        print("  결과 불확정 — receipt를 pending으로 남겨 다음 실행에서 확인합니다.")
    print("PUBLISH FAILED errors=%s" % body[:400])
    return 1


def cmd_publish_linkedin(args):
    """Publish the LinkedIn draft (1000 char cap).

    A durable receipt is written around the write, so a crash mid-publish
    leaves a resolvable trace instead of an invisible post.
    """
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["linkedin"]["usable"]:
        print("[SKIP] LinkedIn token 없음")
        return 2
    text = _read_text(args.text_file)
    if not text:
        print("[SKIP] LinkedIn 본문 비어있음")
        return 2
    if len(text) > PLATFORM_CAP["linkedin"]:
        print("[SKIP] LinkedIn %d자/%d 초과" % (len(text), PLATFORM_CAP["linkedin"]))
        return 2
    print("LinkedIn length: %d/%d" % (len(text), PLATFORM_CAP["linkedin"]))
    text_sha = sns_receipts.text_key(text)
    content_sha = args.content_sha or None
    verdict, reason = _receipt_guard("linkedin", text_sha, content_sha)
    if verdict == "skip":
        print("[SKIP] LinkedIn 이미 발행됨 — 재발행 안 함 (%s)" % reason)
        return 2
    if verdict == "block":
        print("PUBLISH BLOCKED — 중복 발행 위험 (%s)" % reason)
        print("  조치: LinkedIn 계정에서 해당 게시물을 확인한 뒤 receipts 로그를 정리하세요.")
        return 3
    attempt_id = sns_receipts.new_attempt_id()
    _receipt_write("linkedin", attempt_id, "pending",
                   text_sha=text_sha, content_sha=content_sha,
                   startedAt=sns_receipts.now_iso(),
                   textPreview=text[:120])
    status, body, urn = _linkedin_publish(env, text)
    print("HTTP %s" % status)
    # status is None when the author URN could not be resolved, which
    # happens BEFORE any write is sent: nothing was published, so the
    # attempt must be settled as failed. Leaving it pending would be a
    # permanent deadlock -- LinkedIn has no free post listing, so the
    # guard could never resolve it and would refuse every later attempt.
    if status is None:
        _receipt_write("linkedin", attempt_id, "failed",
                       text_sha=text_sha, content_sha=content_sha,
                       note="author URN unresolved: no write attempted",
                       completedAt=sns_receipts.now_iso())
        print("PUBLISH BLOCKED — %s" % body)
        print("  조치: LINKEDIN_TOKEN에 r_liteprofile 또는 openid 스코프를 추가하세요.")
        return 1
    try:
        post_id = _usable_post_id((json.loads(body) or {}).get("id"))
    except ValueError:
        post_id = None
    outcome = _publish_outcome(status, post_id, write_attempted=True)
    if outcome == "published":
        _receipt_write("linkedin", attempt_id, "published",
                       text_sha=text_sha, content_sha=content_sha,
                       postId=post_id, url=post_id,
                       completedAt=sns_receipts.now_iso())
        print("PUBLISHED id=%s author=%s" % (post_id, urn))
        print(json.dumps({"id": post_id, "author": urn}, ensure_ascii=False))
        return 0
    if outcome == "failed":
        _receipt_write("linkedin", attempt_id, "failed",
                       text_sha=text_sha, content_sha=content_sha,
                       httpStatus=status,
                       completedAt=sns_receipts.now_iso())
    else:
        # Ambiguous: the write was sent but we cannot prove it landed, and
        # LinkedIn has no free listing to settle it. Stay pending so the
        # guard blocks a duplicate until an operator resolves it.
        print("  결과 불확정 — receipt를 pending으로 남겨 확인이 필요합니다.")
    print("PUBLISH FAILED errors=%s" % body[:400])
    return 1


def cmd_publish_devto(args):
    """Publish the dev.to draft (2000 char cap, English markdown)."""
    env, _ = load_env_files()
    state = credential_state(env)
    if not state["dev.to"]["usable"]:
        print("[SKIP] dev.to token 없음")
        return 2
    body_markdown = _read_text(args.text_file)
    if not body_markdown:
        print("[SKIP] dev.to 본문 비어있음")
        return 2
    if len(body_markdown) > PLATFORM_CAP["dev.to"]:
        print("[SKIP] dev.to %d자/%d 초과" % (len(body_markdown), PLATFORM_CAP["dev.to"]))
        return 2
    title = args.title
    if not title:
        for line in body_markdown.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    print("dev.to length: %d/%d title=%s" % (len(body_markdown),
                                             PLATFORM_CAP["dev.to"], title))
    text_sha = sns_receipts.text_key(body_markdown)
    content_sha = args.content_sha or None
    verdict, reason = _receipt_guard(
        "dev.to", text_sha, content_sha,
        resolver=lambda attempt: _resolve_devto_pending(env, attempt))
    if verdict == "skip":
        print("[SKIP] dev.to 이미 발행됨 — 재발행 안 함 (%s)" % reason)
        return 2
    if verdict == "block":
        print("PUBLISH BLOCKED — 중복 발행 위험 (%s)" % reason)
        print("  조치: dev.to 계정에서 해당 글을 확인한 뒤 receipts 로그를 정리하세요.")
        return 3
    attempt_id = sns_receipts.new_attempt_id()
    _receipt_write("dev.to", attempt_id, "pending",
                   text_sha=text_sha, content_sha=content_sha,
                   startedAt=sns_receipts.now_iso(),
                   title=title, canonicalUrl=args.canonical_url or None,
                   textPreview=body_markdown[:120])
    status, body = _devto_publish(env, title, body_markdown, tags,
                                  args.canonical_url)
    print("HTTP %s" % status)
    try:
        data = json.loads(body) or {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    post_id = _usable_post_id(data.get("id"))
    outcome = _publish_outcome(status, post_id, write_attempted=True)
    if outcome == "published":
        _receipt_write("dev.to", attempt_id, "published",
                       text_sha=text_sha, content_sha=content_sha,
                       postId=post_id, url=data.get("url"),
                       title=title, canonicalUrl=args.canonical_url or None,
                       completedAt=sns_receipts.now_iso())
        print("PUBLISHED id=%s url=%s" % (post_id, data.get("url")))
        print(json.dumps({"id": post_id, "url": data.get("url")},
                         ensure_ascii=False))
        return 0
    if outcome == "failed":
        _receipt_write("dev.to", attempt_id, "failed",
                       text_sha=text_sha, content_sha=content_sha,
                       httpStatus=status, title=title,
                       canonicalUrl=args.canonical_url or None,
                       completedAt=sns_receipts.now_iso())
    else:
        # Ambiguous: the write was sent but we cannot prove it landed.
        # Keep it pending so the free article listing settles it.
        print("  결과 불확정 — receipt를 pending으로 남겨 다음 실행에서 확인합니다.")
    print("PUBLISH FAILED errors=%s" % body[:400])
    return 1


def cmd_publish_all(args):
    """Publish every platform that has usable credentials and a draft.

    Exit codes: 0 = all attempted platforms published, 2 = nothing to do
    (no credentials), 3 = at least one platform blocked/failed.
    """
    env, _ = load_env_files()
    state = credential_state(env)
    published = 0
    blocked = 0
    skipped = 0
    for platform in ("x", "threads", "linkedin", "dev.to"):
        label = PLATFORM_LABEL[platform]
        if not state[platform]["usable"]:
            print("[SKIP] %s token 없음" % label)
            skipped += 1
            continue
        draft = os.path.join(args.drafts_dir, DRAFT_FILENAME[platform])
        if not os.path.isfile(draft):
            print("[SKIP] %s draft 없음 (%s)" % (label, DRAFT_FILENAME[platform]))
            skipped += 1
            continue
        if platform == "x":
            code = cmd_publish_x(argparse.Namespace(
                text_file=draft, content_sha=args.content_sha))
        elif platform == "threads":
            code = cmd_publish_threads(argparse.Namespace(
                text_file=draft, content_sha=args.content_sha))
        elif platform == "linkedin":
            code = cmd_publish_linkedin(argparse.Namespace(
                text_file=draft, content_sha=args.content_sha))
        else:
            code = cmd_publish_devto(argparse.Namespace(
                text_file=draft, title=args.devto_title,
                tags=args.devto_tags, canonical_url=args.devto_canonical,
                content_sha=args.content_sha))
        if code == 0:
            published += 1
        elif code == 2:
            skipped += 1
        else:
            blocked += 1
    print("=== publish summary ===")
    print("published=%d blocked=%d skipped=%d" % (published, blocked, skipped))
    if blocked:
        return 3
    return 0 if published else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="blog-sns-auto publisher")
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status")
    status.add_argument("--json", dest="json", action="store_true")
    sub.add_parser("verify")
    sub.add_parser("timeline")
    dup = sub.add_parser("dupcheck")
    dup.add_argument("--needle", required=True)
    publish = sub.add_parser("publish-x")
    publish.add_argument("--text-file", required=True)
    publish.add_argument("--skip-credit-check", dest="skip_credit_check",
                         action="store_true")
    publish.add_argument("--content-sha", dest="content_sha", default="",
                         help="sha256 of the article body, for cross-draft dedup")
    weight = sub.add_parser("weight")
    weight.add_argument("--text-file", required=True)
    sub.add_parser("credits")
    threads = sub.add_parser("publish-threads")
    threads.add_argument("--text-file", required=True)
    threads.add_argument("--content-sha", dest="content_sha", default="")
    linkedin = sub.add_parser("publish-linkedin")
    linkedin.add_argument("--text-file", required=True)
    linkedin.add_argument("--content-sha", dest="content_sha", default="")
    devto = sub.add_parser("publish-devto")
    devto.add_argument("--text-file", required=True)
    devto.add_argument("--title", default="")
    devto.add_argument("--tags", default="")
    devto.add_argument("--canonical-url", dest="canonical_url", default="")
    devto.add_argument("--content-sha", dest="content_sha", default="")
    publish_all = sub.add_parser("publish-all")
    publish_all.add_argument("--drafts-dir", dest="drafts_dir", required=True)
    publish_all.add_argument("--devto-title", dest="devto_title", default="")
    publish_all.add_argument("--devto-tags", dest="devto_tags", default="")
    publish_all.add_argument("--devto-canonical-url", dest="devto_canonical",
                             default="")
    publish_all.add_argument("--content-sha", dest="content_sha", default="")
    args = parser.parse_args(argv)
    handler = {"status": cmd_status, "verify": cmd_verify,
               "timeline": cmd_timeline,
               "dupcheck": cmd_dupcheck,
               "weight": cmd_weight,
               "credits": cmd_credits,
               "publish-x": cmd_publish_x,
               "publish-threads": cmd_publish_threads,
               "publish-linkedin": cmd_publish_linkedin,
               "publish-devto": cmd_publish_devto,
               "publish-all": cmd_publish_all}.get(args.command)
    if not handler:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
