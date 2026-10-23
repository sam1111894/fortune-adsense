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
    # Free pre-flight: X charges per POST, so check the balance BEFORE the
    # write. GET /2/usage/credits is a free read and tells us whether the
    # attempt is even billable. Without this we burn a POST attempt to learn
    # the same 402 the credit endpoint would have reported for free.
    if not getattr(args, "skip_credit_check", False):
        balance, cost = _x_affordable_tier(env, text)
        if balance is None:
            print("  credit pre-flight unavailable ( proceeding; API will decide)")
        elif balance < cost:
            tier = "URL 포함($%.3f)" % X_COST_WITH_URL if _has_url(text) \
                else "URL 미포함($%.3f)" % X_COST_NO_URL
            print("PUBLISH BLOCKED — 크레딧 부족 (잔액 $%.3f < %s)"
                  % (balance, tier))
            print("  조치: console.x.com에서 이 앱에 크레딧 충전 후 재실행.")
            return 3
        else:
            print("  credit pre-flight OK (잔액 $%.3f, 필요 $%.3f)"
                  % (balance, cost))
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
    """Publish the Threads draft (500 char cap)."""
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
    status, body, creation_id = _threads_publish(env, text)
    print("HTTP %s" % status)
    if status in (200, 201):
        try:
            post_id = (json.loads(body) or {}).get("id")
        except ValueError:
            post_id = None
        print("PUBLISHED id=%s creation_id=%s" % (post_id, creation_id))
        print(json.dumps({"id": post_id, "creation_id": creation_id},
                         ensure_ascii=False))
        return 0
    print("PUBLISH FAILED errors=%s" % body[:400])
    return 1


def cmd_publish_linkedin(args):
    """Publish the LinkedIn draft (1000 char cap)."""
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
    status, body, urn = _linkedin_publish(env, text)
    if status is None:
        print("PUBLISH BLOCKED — %s" % body)
        return 1
    print("HTTP %s" % status)
    if status in (200, 201):
        try:
            post_id = (json.loads(body) or {}).get("id")
        except ValueError:
            post_id = None
        print("PUBLISHED id=%s author=%s" % (post_id, urn))
        print(json.dumps({"id": post_id, "author": urn}, ensure_ascii=False))
        return 0
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
    status, body = _devto_publish(env, title, body_markdown, tags,
                                  args.canonical_url)
    print("HTTP %s" % status)
    if status in (200, 201):
        try:
            data = json.loads(body) or {}
        except ValueError:
            data = {}
        print("PUBLISHED id=%s url=%s" % (data.get("id"), data.get("url")))
        print(json.dumps({"id": data.get("id"), "url": data.get("url")},
                         ensure_ascii=False))
        return 0
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
            code = cmd_publish_x(argparse.Namespace(text_file=draft))
        elif platform == "threads":
            code = cmd_publish_threads(argparse.Namespace(text_file=draft))
        elif platform == "linkedin":
            code = cmd_publish_linkedin(argparse.Namespace(text_file=draft))
        else:
            code = cmd_publish_devto(argparse.Namespace(
                text_file=draft, title=args.devto_title,
                tags=args.devto_tags, canonical_url=args.devto_canonical))
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
    weight = sub.add_parser("weight")
    weight.add_argument("--text-file", required=True)
    sub.add_parser("credits")
    threads = sub.add_parser("publish-threads")
    threads.add_argument("--text-file", required=True)
    linkedin = sub.add_parser("publish-linkedin")
    linkedin.add_argument("--text-file", required=True)
    devto = sub.add_parser("publish-devto")
    devto.add_argument("--text-file", required=True)
    devto.add_argument("--title", default="")
    devto.add_argument("--tags", default="")
    devto.add_argument("--canonical-url", dest="canonical_url", default="")
    publish_all = sub.add_parser("publish-all")
    publish_all.add_argument("--drafts-dir", dest="drafts_dir", required=True)
    publish_all.add_argument("--devto-title", dest="devto_title", default="")
    publish_all.add_argument("--devto-tags", dest="devto_tags", default="")
    publish_all.add_argument("--devto-canonical-url", dest="devto_canonical",
                             default="")
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
