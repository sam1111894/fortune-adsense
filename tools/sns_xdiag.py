#!/usr/bin/env python3
"""
X API endpoint probe for the blog-sns-auto publisher.

WHY THIS EXISTS
---------------
POST /1.1/statuses/update.json returned HTTP 404 with an EMPTY body while
GET /1.1/account/verify_credentials.json returned HTTP 200 using the same
OAuth 1.0a credentials. A 404-with-empty-body on a write endpoint is not a
transient fault, so it must be diagnosed, not retried.

This probe tries every plausible write/read path and prints status + body
(excluding secrets) so the real cause is visible:
  1. GET  /1.1/account/verify_credentials.json  (read baseline)
  2. GET  /2/users/me                          (v2 read, OAuth1 user ctx)
  3. GET  /2/users/<id>/tweets                 (v2 tweets, correct base)
  4. POST /1.1/statuses/update.json            (v1.1 write, form body)
  5. POST /2/tweets                            (v2 write, JSON body)

It also distinguishes two failure families:
  * 401 -> OAuth 1.0a signature/credential problem
  * 403 -> permission / tier / account-state problem
  * 404 -> endpoint decommissioned or URL wrong

Usage:
  python3 tools/sns_xdiag.py [--publish-text-file <path>]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [
    os.path.join(REPO_ROOT, ".env"),
    os.path.expanduser("~/.hermes/profiles/reviewer/.env"),
    os.path.expanduser("~/.hermes/.env"),
]
KEY_RE = __import__("re").compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _strip_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if raw[0] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    cut = __import__("re").search(r"\s+#", raw)
    if cut:
        raw = raw[: cut.start()]
    return raw.strip()


def load_env() -> dict:
    merged = {}
    for path in ENV_CANDIDATES:
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                match = KEY_RE.match(line.rstrip("\n"))
                if not match:
                    continue
                if match.group(1) not in merged:
                    merged[match.group(1)] = _strip_value(match.group(2))
    merged.update(os.environ)
    return merged


def _pct(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~")


def oauth1_header(method, url, params, consumer_key, consumer_secret,
                  token, token_secret):
    """Build an OAuth 1.0a Authorization header (RFC 5849, HMAC-SHA1)."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": base64.b16encode(os.urandom(16)).decode().lower(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(__import__("time").time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    oauth.update({k: str(v) for k, v in params.items()})
    base_string = "&".join([
        method.upper(),
        _pct(url),
        _pct("&".join("%s=%s" % (_pct(k), _pct(v))
                       for k, v in sorted(oauth.items()))),
    ])
    signing_key = "%s&%s" % (_pct(consumer_secret), _pct(token_secret))
    signature = base64.b64encode(hmac.new(
        signing_key.encode(), base_string.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = signature
    return "OAuth " + ", ".join(
        '%s="%s"' % (_pct(k), _pct(v)) for k, v in sorted(oauth.items()))


def request(url, method="GET", params=None, body=None, json_body=None,
            headers=None, timeout=25):
    """Perform a signed request; return (status, body_text, headers)."""
    params = params or {}
    data = None
    hdrs = {"Accept": "application/json",
            "User-Agent": "blog-sns-auto/1.0"}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif body is not None:
        data = urllib.parse.urlencode(body).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in hdrs.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)
    except Exception as exc:  # noqa: BLE001
        return 0, json.dumps({"error": str(exc)}), {}


def signed(env, url, method="GET", params=None, body=None, json_body=None):
    """Signed GET/POST. For form bodies the body params go into the OAuth
    signature base string, as RFC 5849 requires."""
    key = env.get("X_API_KEY", "")
    secret = env.get("X_API_SECRET", "")
    token = env.get("X_ACCESS_TOKEN", "")
    token_secret = env.get("X_ACCESS_TOKEN_SECRET", "")
    params = params or {}
    if json_body is not None:
        header = oauth1_header(method, url, params, key, secret, token, token_secret)
    else:
        header = oauth1_header(method, url,
                               dict(params, **(body or {})), key, secret,
                               token, token_secret)
    return request(url, method, params, body, json_body, {"Authorization": header})


def brief(body: str, limit: int = 300) -> str:
    body = (body or "").strip()
    if not body:
        return "<empty body>"
    return body[:limit].replace("\n", " ")


def classify(status: int) -> str:
    return {
        200: "OK", 201: "OK", 401: "OAUTH-FAIL (signature/credential)",
        403: "PERMISSION/TIER/ACCOUNT-STATE", 404: "ENDPOINT-DECOMMISSIONED-OR-BAD-URL",
        429: "RATE-LIMITED",
    }.get(status, "OTHER")


def main(argv=None):
    parser = argparse.ArgumentParser(description="X API endpoint probe")
    parser.add_argument("--publish-text-file")
    parser.add_argument("--do-write", action="store_true",
                        help="actually attempt the write endpoints")
    parser.add_argument("--write-target", choices=["v1.1", "v2", "both"], default="both",
                        help="which write endpoint to attempt (avoids double-posting)")
    args = parser.parse_args(argv)

    env = load_env()
    text = "probe"
    if args.publish_text_file:
        with open(args.publish_text_file, "r", encoding="utf-8") as handle:
            text = handle.read().strip()

    results = []

    status, body, hdrs = signed(env, "https://api.twitter.com/1.1/account/verify_credentials.json")
    uid = ""
    try:
        uid = str(json.loads(body).get("id", ""))
    except ValueError:
        pass
    results.append(("GET  /1.1/account/verify_credentials.json", status, body))

    status, body, hdrs = signed(env, "https://api.twitter.com/2/users/me")
    results.append(("GET  /2/users/me", status, body))

    if uid:
        status, body, hdrs = signed(
            env, "https://api.twitter.com/2/users/%s/tweets" % uid,
            params={"max_results": "10", "tweet.fields": "created_at,text"})
        results.append(("GET  /2/users/<id>/tweets", status, body))

    if args.do_write:
        if args.write_target in ("v1.1", "both"):
            status, body, hdrs = signed(
                env, "https://api.twitter.com/1.1/statuses/update.json",
                method="POST", body={"status": text})
            results.append(("POST /1.1/statuses/update.json", status, body))
        if args.write_target in ("v2", "both"):
            status, body, hdrs = signed(
                env, "https://api.twitter.com/2/tweets",
                method="POST", json_body={"text": text})
            results.append(("POST /2/tweets", status, body))

    print("=" * 78)
    for label, status, body in results:
        print("%-42s HTTP %-4s %s" % (label, status, classify(status)))
        print("    body: %s" % brief(body))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
