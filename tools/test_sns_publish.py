#!/usr/bin/env python3
"""Offline tests for tools/sns_publish.py.

These do NOT hit the network: every HTTP call is monkeypatched so the
request shape (method, URL, headers, body) can be asserted.

Run: python3 tools/test_sns_publish.py
"""

import ast
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sns_publish as sp  # noqa: E402


class Recorder(object):
    """Fake http_request that records calls and replays canned responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or []

    def __call__(self, url, method="GET", params=None, body=None,
                 headers=None, timeout=25, json_body=None):
        self.calls.append({
            "url": url, "method": method, "params": params or {},
            "body": body, "headers": headers or {}, "json_body": json_body,
        })
        if self.responses:
            return self.responses.pop(0)
        return 200, json.dumps({"ok": True})


class EnvMixin(object):
    def fake_env(self, **overrides):
        env = {
            "X_API_KEY": "ck", "X_API_SECRET": "cs",
            "X_ACCESS_TOKEN": "at", "X_ACCESS_TOKEN_SECRET": "ats",
            "THREADS_TOKEN": "tt", "LINKEDIN_TOKEN": "lt",
            "DEVTO_API_KEY": "dk",
        }
        env.update(overrides)
        return env


class TestLoadEnv(unittest.TestCase):
    def test_env_file_parsed_and_process_env_used_as_fallback(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write('X_API_KEY="fromfile"\n# comment\nTHREADS_TOKEN=\n')
            path = fh.name
        # The real shell may already export X_API_KEY; pin it so the
        # .env-vs-process precedence is asserted deterministically.
        had_key = "X_API_KEY" in os.environ
        old_key = os.environ.get("X_API_KEY")
        os.environ.pop("X_API_KEY", None)
        os.environ["LINKEDIN_TOKEN"] = "fromshell"
        os.environ["DEVTO_API_KEY"] = "fromshell2"
        try:
            merged, prov = sp.load_env_files([path])
            self.assertEqual(merged["X_API_KEY"], "fromfile")
            self.assertEqual(merged["LINKEDIN_TOKEN"], "fromshell")
            self.assertEqual(merged["DEVTO_API_KEY"], "fromshell2")
            # Empty .env value counts as a key but is NOT usable.
            self.assertIn("THREADS_TOKEN", merged)
            self.assertEqual(merged["THREADS_TOKEN"], "")
            env_rows = [row for row in prov if row[0] == "<process env>"]
            self.assertEqual(len(env_rows), 1)
            self.assertTrue(env_rows[0][1].startswith("overrides="))
        finally:
            os.environ.pop("LINKEDIN_TOKEN", None)
            os.environ.pop("DEVTO_API_KEY", None)
            if had_key:
                os.environ["X_API_KEY"] = old_key
            os.unlink(path)

    def test_process_env_overrides_env_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write("LINKEDIN_TOKEN=fromfile\n")
            path = fh.name
        os.environ["LINKEDIN_TOKEN"] = "fromshell"
        try:
            merged, _ = sp.load_env_files([path])
            self.assertEqual(merged["LINKEDIN_TOKEN"], "fromshell")
        finally:
            os.environ.pop("LINKEDIN_TOKEN", None)
            os.unlink(path)

    def test_empty_process_env_does_not_wipe_env_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write("LINKEDIN_TOKEN=fromfile\n")
            path = fh.name
        os.environ["LINKEDIN_TOKEN"] = ""
        try:
            merged, _ = sp.load_env_files([path])
            self.assertEqual(merged["LINKEDIN_TOKEN"], "fromfile")
        finally:
            os.environ.pop("LINKEDIN_TOKEN", None)
            os.unlink(path)


class TestCredentialState(unittest.TestCase):
    def test_missing_and_empty_keys_are_not_usable(self):
        state = sp.credential_state({"THREADS_TOKEN": "", "LINKEDIN_TOKEN": "x"})
        self.assertFalse(state["threads"]["usable"])
        self.assertTrue(state["linkedin"]["usable"])
        self.assertFalse(state["x"]["usable"])

    def test_platform_labels_cover_all_required_platforms(self):
        self.assertEqual(set(sp.PLATFORM_LABEL), set(sp.REQUIRED))
        self.assertEqual(set(sp.DRAFT_FILENAME), set(sp.REQUIRED))


class TestThreads(unittest.TestCase, EnvMixin):
    def test_two_step_container_then_publish(self):
        rec = Recorder([
            (200, json.dumps({"id": "container-1"})),
            (200, json.dumps({"id": "post-99"})),
        ])
        orig = sp.http_request
        sp.http_request = rec
        try:
            status, body, creation_id = sp._threads_publish(
                self.fake_env(), "hello world")
        finally:
            sp.http_request = orig
        self.assertEqual(status, 200)
        self.assertEqual(creation_id, "container-1")
        self.assertEqual(len(rec.calls), 2)
        self.assertTrue(rec.calls[0]["url"].endswith("/threads"))
        self.assertTrue(rec.calls[1]["url"].endswith("/threads_publish"))
        self.assertEqual(rec.calls[0]["body"]["media_type"], "TEXT")
        self.assertEqual(rec.calls[0]["body"]["text"], "hello world")
        self.assertEqual(rec.calls[1]["body"]["creation_id"], "container-1")

    def test_container_failure_short_circuits(self):
        rec = Recorder([(400, json.dumps({"error": {"code": 190}}))])
        orig = sp.http_request
        sp.http_request = rec
        try:
            status, _body, creation_id = sp._threads_publish(
                self.fake_env(), "hi")
        finally:
            sp.http_request = orig
        self.assertEqual(status, 400)
        self.assertIsNone(creation_id)
        self.assertEqual(len(rec.calls), 1)


class TestLinkedIn(unittest.TestCase, EnvMixin):
    def test_author_urn_resolved_from_v2_me(self):
        rec = Recorder([(200, json.dumps({"id": "abc123"}))])
        orig = sp.http_request
        sp.http_request = rec
        try:
            urn, err = sp._linkedin_author_urn(self.fake_env())
        finally:
            sp.http_request = orig
        self.assertEqual(urn, "urn:li:person:abc123")
        self.assertIsNone(err)

    def test_author_urn_falls_back_to_userinfo_sub(self):
        rec = Recorder([
            (401, json.dumps({"code": "INVALID_ACCESS_TOKEN"})),
            (200, json.dumps({"sub": "sub-42"})),
        ])
        orig = sp.http_request
        sp.http_request = rec
        try:
            urn, err = sp._linkedin_author_urn(self.fake_env())
        finally:
            sp.http_request = orig
        self.assertEqual(urn, "urn:li:person:sub-42")
        self.assertIsNone(err)

    def test_ugcposts_payload_shape(self):
        rec = Recorder([
            (200, json.dumps({"id": "abc123"})),
            (201, json.dumps({"id": "urn:li:share:1"})),
        ])
        orig = sp.http_request
        sp.http_request = rec
        try:
            status, _body, urn = sp._linkedin_publish(self.fake_env(), "text")
        finally:
            sp.http_request = orig
        self.assertEqual(status, 201)
        self.assertEqual(urn, "urn:li:person:abc123")
        payload = rec.calls[1]["json_body"]
        self.assertEqual(payload["author"], "urn:li:person:abc123")
        self.assertEqual(payload["lifecycleState"], "PUBLISHED")
        self.assertEqual(
            payload["specificContent"]["com.linkedin.ugc.ShareContent"]
            ["shareCommentary"]["text"], "text")
        self.assertEqual(rec.calls[1]["headers"]["Content-Type"],
                         "application/json")

    def test_unresolvable_urn_returns_none_status(self):
        rec = Recorder([(401, "nope"), (401, "nope")])
        orig = sp.http_request
        sp.http_request = rec
        try:
            status, body, urn = sp._linkedin_publish(self.fake_env(), "text")
        finally:
            sp.http_request = orig
        self.assertIsNone(status)
        self.assertIsNone(urn)
        self.assertIn("URN", body)


class TestDevto(unittest.TestCase, EnvMixin):
    def test_article_payload_shape(self):
        rec = Recorder([(201, json.dumps({"id": 7, "url": "https://dev.to/x"}))])
        orig = sp.http_request
        sp.http_request = rec
        try:
            status, body = sp._devto_publish(
                self.fake_env(), "Title", "# Body\ntext", ["a", "b", "c", "d", "e"],
                "https://lucksajueun.com/blog/x/")
        finally:
            sp.http_request = orig
        self.assertEqual(status, 201)
        self.assertIn("url", body)
        article = rec.calls[0]["json_body"]["article"]
        self.assertEqual(article["title"], "Title")
        self.assertTrue(article["published"])
        self.assertEqual(article["tags"], ["a", "b", "c", "d"])  # capped at 4
        self.assertEqual(article["canonical_url"],
                         "https://lucksajueun.com/blog/x/")
        self.assertEqual(rec.calls[0]["headers"]["api-key"], "dk")
        self.assertEqual(rec.calls[0]["headers"]["Accept"],
                         "application/vnd.forem.api-v1+json")

    def test_title_falls_back_to_first_h1(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# Heading Title\n\nbody\n")
            path = fh.name
        try:
            self.assertEqual(sp._read_text(path), "# Heading Title\n\nbody")
        finally:
            os.unlink(path)


class TestCredits(unittest.TestCase, EnvMixin):
    def test_app_only_bearer_uses_api_key_pair(self):
        rec = Recorder([(200, json.dumps({"access_token": "bearer-xyz"}))])
        orig = sp.http_request
        sp.http_request = rec
        try:
            token, err = sp._app_only_bearer(self.fake_env())
        finally:
            sp.http_request = orig
        self.assertEqual(token, "bearer-xyz")
        self.assertIsNone(err)
        call = rec.calls[0]
        self.assertEqual(call["url"], "https://api.x.com/oauth2/token")
        self.assertEqual(call["body"]["grant_type"], "client_credentials")
        self.assertTrue(call["headers"]["Authorization"].startswith("Basic "))

    def test_app_only_bearer_missing_keys(self):
        token, err = sp._app_only_bearer({})
        self.assertIsNone(token)
        self.assertIn("missing", err)

    def test_credit_tier_thresholds(self):
        self.assertGreater(sp.X_COST_WITH_URL, sp.X_COST_NO_URL)
        self.assertAlmostEqual(sp.X_COST_NO_URL, 0.015)
        self.assertAlmostEqual(sp.X_COST_WITH_URL, 0.200)


class TestCreditPreflight(unittest.TestCase, EnvMixin):
    def test_has_url_detection(self):
        self.assertTrue(sp._has_url("read https://example.com/x now"))
        self.assertFalse(sp._has_url("no link here"))

    def test_tier_cost_depends_on_url_presence(self):
        orig = sp.http_request
        sp.http_request = Recorder([
            (200, json.dumps({"access_token": "bearer"})),
            (200, json.dumps({"data": {"total_balance": 1.0}})),
        ])
        try:
            balance, cost = sp._x_affordable_tier(self.fake_env(),
                                                  "with https://x.com/a")
        finally:
            sp.http_request = orig
        self.assertAlmostEqual(balance, 1.0)
        self.assertAlmostEqual(cost, sp.X_COST_WITH_URL)

    def test_tier_cost_without_url_is_cheap_tier(self):
        orig = sp.http_request
        sp.http_request = Recorder([
            (200, json.dumps({"access_token": "bearer"})),
            (200, json.dumps({"data": {"total_balance": 1.0}})),
        ])
        try:
            _balance, cost = sp._x_affordable_tier(self.fake_env(), "no url")
        finally:
            sp.http_request = orig
        self.assertAlmostEqual(cost, sp.X_COST_NO_URL)

    def test_unavailable_credit_read_returns_none(self):
        orig = sp.http_request
        sp.http_request = Recorder([(500, "boom")])
        try:
            balance, cost = sp._x_affordable_tier(self.fake_env(), "text")
        finally:
            sp.http_request = orig
        self.assertIsNone(balance)
        self.assertEqual(cost, 0.0)


class TestPublishAll(unittest.TestCase, EnvMixin):
    def test_skips_platforms_without_credentials(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in sp.DRAFT_FILENAME.values():
                with open(os.path.join(tmp, name), "w") as fh:
                    fh.write("draft")
            orig = sp.load_env_files
            sp.load_env_files = lambda paths=None: (
                {"X_API_KEY": "ck", "X_API_SECRET": "cs",
                 "X_ACCESS_TOKEN": "at", "X_ACCESS_TOKEN_SECRET": "ats"}, [])
            calls = []
            orig_pub = sp.cmd_publish_x
            sp.cmd_publish_x = lambda args: calls.append("x") or 0
            try:
                import argparse
                ns = argparse.Namespace(drafts_dir=tmp, devto_title="",
                                        devto_tags="", devto_canonical="")
                code = sp.cmd_publish_all(ns)
            finally:
                sp.cmd_publish_x = orig_pub
                sp.load_env_files = orig
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["x"])

    def test_missing_draft_is_skipped_not_failed(self):
        import argparse
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            orig = sp.load_env_files
            sp.load_env_files = lambda paths=None: ({}, [])
            try:
                ns = argparse.Namespace(drafts_dir=tmp, devto_title="",
                                        devto_tags="", devto_canonical="")
                code = sp.cmd_publish_all(ns)
            finally:
                sp.load_env_files = orig
        self.assertEqual(code, 2)


class TestCaps(unittest.TestCase):
    def test_caps_match_job_spec(self):
        self.assertEqual(sp.PLATFORM_CAP["x"], 280)
        self.assertEqual(sp.PLATFORM_CAP["threads"], 500)
        self.assertEqual(sp.PLATFORM_CAP["linkedin"], 1000)
        self.assertEqual(sp.PLATFORM_CAP["dev.to"], 2000)


class TestSyndicationPageUrls(unittest.TestCase):
    """Regression guard: the syndication card must link to the real blog URL.

    Astro 5 content collections (glob loader) expose the entry identifier as
    `post.id`. `post.slug` does not exist and `post.data.slug` is stripped
    because the collection schema does not declare it, so using either one
    renders `https://lucksajueun.com/blog/undefined/` on every syndication
    page (11 pages live) and advertises a broken URL to social crawlers.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PAGE = os.path.join(REPO, "src", "pages", "syndication", "[slug].astro")
    DIST = os.path.join(REPO, "dist")

    def test_source_does_not_use_nonexistent_post_slug(self):
        with open(self.PAGE, encoding="utf-8") as fh:
            src = fh.read()
        # Strip comments so an explanatory note mentioning `post.slug` does not
        # trip the assertion; only real code usage is checked.
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        self.assertNotIn(
            "post.slug", code,
            "syndication page uses post.slug, which is undefined in Astro 5 "
            "content collections -> renders /blog/undefined/")
        self.assertIn(
            "post.id", code,
            "syndication page should build the post URL from post.id")

    def test_built_output_has_no_undefined_blog_url(self):
        if not os.path.isdir(self.DIST):
            self.skipTest("dist/ not built; run `npm run build` first")
        offenders = []
        for root, _dirs, files in os.walk(self.DIST):
            for name in files:
                if not name.endswith((".html", ".xml")):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    if "blog/undefined" in fh.read():
                        offenders.append(os.path.relpath(path, self.DIST))
        self.assertEqual(
            offenders, [],
            "built pages still contain /blog/undefined/: %s" % offenders)


class TestArgparseWiring(unittest.TestCase):
    """Every args.<attr> a handler reads must have a matching parser dest.

    A flag declared `--canonical-url` with `dest="devto_canonical"` while the
    handler reads `args.canonical_url` raises AttributeError at runtime — and
    only on the path that actually publishes. cmd_publish_all also forwards
    args into each handler via argparse.Namespace(...), so a handler attr
    missing from that Namespace breaks the same way.
    """

    SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sns_publish.py")

    def _tree(self):
        with open(self.SRC, encoding="utf-8") as fh:
            return ast.parse(fh.read())

    def test_handlers_read_only_attrs_the_parser_defines(self):
        tree = self._tree()
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

        dests: set[str] = set()
        for node in ast.walk(funcs["main"]):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            flags = [a.value for a in node.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            dest = next((kw.value.value for kw in node.keywords
                         if kw.arg == "dest"
                         and isinstance(kw.value, ast.Constant)
                         and isinstance(kw.value.value, str)), None)
            if dest is None and flags:
                dest = flags[0].lstrip("-").replace("-", "_")
            if dest:
                dests.add(dest)

        for name, fn in funcs.items():
            if not name.startswith("cmd_publish_"):
                continue
            used = {n.attr for n in ast.walk(fn)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "args"}
            self.assertEqual(
                used - dests, set(),
                "%s reads args.%s with no matching parser dest"
                % (name, sorted(used - dests)))

    def test_publish_all_forwards_every_handler_attr(self):
        tree = self._tree()
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        publish_all = funcs["cmd_publish_all"]

        for call in ast.walk(publish_all):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id.startswith("cmd_publish_")):
                continue
            handler = funcs[call.func.id]
            used = {n.attr for n in ast.walk(handler)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "args"}
            ns = next((a for a in call.args
                       if isinstance(a, ast.Call)
                       and isinstance(a.func, ast.Attribute)
                       and a.func.attr == "Namespace"), None)
            self.assertIsNotNone(
                ns, "%s is called without an argparse.Namespace" % call.func.id)
            given = {kw.arg for kw in ns.keywords if kw.arg}  # type: ignore[union-attr]
            self.assertEqual(
                used - given, set(),
                "cmd_publish_all calls %s with a Namespace missing %s"
                % (call.func.id, sorted(used - given)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
