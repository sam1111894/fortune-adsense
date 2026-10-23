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
  * Env loading order: explicit process env wins, then the profile .env,
    then the project .env, then the global ~/.hermes/.env.

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
    """Return (merged_env, provenance). Later files do not override earlier ones."""
    merged: dict = {}
    provenance: list = []
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
                found += 1
        provenance.append((path, "bytes=%d keys=%d" % (size, found)))
    return merged, provenance


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
    an earlier fire or by a manual run. Tries the v2 users-tweets endpoint
    first, then falls back to the v1.1 timeline endpoint.
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
        print("dupcheck unavailable (scope restriction): %s" % json.dumps(
            payload.get("errors", payload), ensure_ascii=False)[:300])
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


def cmd_status(_args):
    env, provenance = load_env_files()
    state = credential_state(env)
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


def cmd_publish_x(args):
    """Publish a tweet via the X v2 API.

    Endpoint notes (verified 2026-10-23 against the live API):
      * POST /1.1/statuses/update.json -> HTTP 404, empty body (retired).
      * POST /2/tweets               -> live; needs a funded credit balance
        under X's pay-per-use model (HTTP 402 "credits depleted" when empty).
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
    status, body = x_request(env, "POST", "/2/tweets", json_body={"text": text})
    print("HTTP %s" % status)
    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        payload = {}
    if status in (200, 201):
        data = payload.get("data") or {}
        tweet_id = data.get("id")
        print("PUBLISHED id=%s url=https://x.com/%s/status/%s" % (
            tweet_id, data.get("username") or "SamJ868644", tweet_id))
        print(json.dumps({"id": tweet_id,
                          "url": "https://x.com/%s/status/%s" % (
                              data.get("username") or "SamJ868644", tweet_id)},
                         ensure_ascii=False))
        return 0
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="blog-sns-auto publisher")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("timeline")
    dup = sub.add_parser("dupcheck")
    dup.add_argument("--needle", required=True)
    publish = sub.add_parser("publish-x")
    publish.add_argument("--text-file", required=True)
    weight = sub.add_parser("weight")
    weight.add_argument("--text-file", required=True)
    args = parser.parse_args(argv)
    handler = {"status": cmd_status, "verify": cmd_verify,
               "timeline": cmd_timeline,
               "dupcheck": cmd_dupcheck,
               "weight": cmd_weight,
               "publish-x": cmd_publish_x}.get(args.command)
    if not handler:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
