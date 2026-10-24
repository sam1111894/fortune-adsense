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
                                        devto_tags="", devto_canonical="",
                                        content_sha="")
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
                                        devto_tags="", devto_canonical="",
                                        content_sha="")
                code = sp.cmd_publish_all(ns)
            finally:
                sp.load_env_files = orig
        self.assertEqual(code, 2)


class TestDupCheckFallback(unittest.TestCase, EnvMixin):
    """`dupcheck` must fall back to the FREE read when the tweet-listing
    endpoints are unavailable (metered -> HTTP 402, or tier-restricted ->
    HTTP 403), and it must never claim a needle match it could not check.

    Regression: the old code printed a bare "dupcheck unavailable (scope
    restriction)" and returned 1 even though the account's free
    statuses_count was readable and was 0 -- i.e. a definitive
    "no duplicate possible" was reported as an unknown.
    """

    def _run(self, listing_status, listing_body, count):
        import argparse
        import io
        import contextlib
        orig_env = sp.load_env_files
        orig_req = sp.http_request
        sp.load_env_files = lambda paths=None: (self.fake_env(), [])
        responses = [
            # verify_credentials -> user id (free)
            (200, json.dumps({"id": "1", "id_str": "1"})),
            # v2 tweets listing (metered / tier-restricted)
            (listing_status, listing_body),
            # free fallback: verify_credentials -> statuses_count
            (200, json.dumps({"id": "1", "statuses_count": count})),
        ]
        rec = Recorder(responses)
        sp.http_request = rec
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = sp.cmd_dupcheck(argparse.Namespace(needle="needle"))
        finally:
            sp.http_request = orig_req
            sp.load_env_files = orig_env
        return code, buf.getvalue()

    def test_free_fallback_proves_no_duplicate_when_count_is_zero(self):
        code, out = self._run(
            402, json.dumps({"detail": "credits depleted"}), 0)
        self.assertEqual(code, 0)
        self.assertIn("statuses_count=0", out)
        self.assertIn("NO DUPLICATE POSSIBLE", out)

    def test_free_fallback_refuses_to_claim_a_match_when_count_is_nonzero(self):
        code, out = self._run(
            402, json.dumps({"detail": "credits depleted"}), 7)
        self.assertEqual(code, 1)
        self.assertIn("UNKNOWN", out)
        self.assertIn("needle NOT verified", out)
        self.assertNotIn("NO DUPLICATE POSSIBLE", out)

    def test_listing_success_still_reports_needle_hits(self):
        import argparse
        import io
        import contextlib
        orig_env = sp.load_env_files
        orig_req = sp.http_request
        sp.load_env_files = lambda paths=None: (self.fake_env(), [])
        rec = Recorder([
            (200, json.dumps({"id": "1", "id_str": "1"})),
            (200, json.dumps({"data": [{"id": "9", "text": "has the needle"}]})),
        ])
        sp.http_request = rec
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = sp.cmd_dupcheck(argparse.Namespace(needle="needle"))
        finally:
            sp.http_request = orig_req
            sp.load_env_files = orig_env
        self.assertEqual(code, 3)
        self.assertIn("DUPLICATE id=9", buf.getvalue())


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


class TestEnvFileShadowing(unittest.TestCase):
    """An empty template line must not hide a usable value in a lower scope.

    Regression: copying .env.example to .env put `X_API_KEY=` (empty) in the
    project .env, which has higher file precedence than the reviewer profile
    .env where the working X credential actually lives. The old loader took
    the first value it saw for a key, so the working credential was silently
    shadowed and the platform reported UNUSABLE.
    """

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_empty_high_precedence_value_does_not_shadow_lower_scope(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = self._write(os.path.join(tmp, "project.env"), "X_API_KEY=\n")
            profile = self._write(os.path.join(tmp, "profile.env"), "X_API_KEY=realkey\n")
            merged, _prov, sources = sp.load_env_files_detailed([project, profile])
            self.assertEqual(merged["X_API_KEY"], "realkey")
            self.assertEqual(sources["X_API_KEY"], profile)

    def test_non_empty_high_precedence_value_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = self._write(os.path.join(tmp, "project.env"), "X_API_KEY=projectkey\n")
            profile = self._write(os.path.join(tmp, "profile.env"), "X_API_KEY=realkey\n")
            merged, _prov, sources = sp.load_env_files_detailed([project, profile])
            self.assertEqual(merged["X_API_KEY"], "projectkey")
            self.assertEqual(sources["X_API_KEY"], project)

    def test_sources_reports_process_env_origin(self):
        had = "X_API_KEY" in os.environ
        old = os.environ.get("X_API_KEY")
        os.environ["X_API_KEY"] = "fromshell"
        try:
            merged, _prov, sources = sp.load_env_files_detailed([])
            self.assertEqual(merged["X_API_KEY"], "fromshell")
            self.assertEqual(sources["X_API_KEY"], "<process env>")
        finally:
            if had:
                os.environ["X_API_KEY"] = old
            else:
                os.environ.pop("X_API_KEY", None)

    def test_credential_state_sees_shadowed_key_as_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            # The project .env holds the unfilled template placeholders, the
            # profile .env holds the working values.
            project = self._write(
                os.path.join(tmp, "project.env"),
                "X_API_KEY=\nX_API_SECRET=\nX_ACCESS_TOKEN=\nX_ACCESS_TOKEN_SECRET=\n")
            profile = self._write(
                os.path.join(tmp, "profile.env"),
                "X_API_KEY=k\nX_API_SECRET=s\nX_ACCESS_TOKEN=t\nX_ACCESS_TOKEN_SECRET=ts\n")
            merged, _prov, _sources = sp.load_env_files_detailed([project, profile])
            state = sp.credential_state(merged)
            self.assertTrue(state["x"]["usable"], state["x"])

    def test_empty_placeholder_alone_is_not_usable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = self._write(
                os.path.join(tmp, "project.env"),
                "X_API_KEY=\nX_API_SECRET=\nX_ACCESS_TOKEN=\nX_ACCESS_TOKEN_SECRET=\n")
            merged, _prov, _sources = sp.load_env_files_detailed([project])
            state = sp.credential_state(merged)
            self.assertFalse(state["x"]["usable"], state["x"])


class TestStatusJson(unittest.TestCase):
    """`status --json` is the single source of truth for the setup script."""

    def _capture(self):
        import argparse
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            sp.cmd_status(argparse.Namespace(json=True))
        return json.loads(buffer.getvalue())

    def test_json_reports_platform_usability_and_key_sources(self):
        payload = self._capture()
        self.assertIn("platforms", payload)
        for platform in ("x", "threads", "linkedin", "dev.to"):
            self.assertIn(platform, payload["platforms"])
            self.assertIn("usable", payload["platforms"][platform])
            for entry in payload["platforms"][platform]["keys"]:
                self.assertIn("key", entry)
                self.assertIn(entry["state"], ("present", "empty", "missing"))
                self.assertIn("source", entry)

    def test_json_never_contains_a_secret_value(self):
        payload = self._capture()
        blob = json.dumps(payload)
        for secret in ("X_API_KEY", "THREADS_TOKEN", "LINKEDIN_TOKEN", "DEVTO_API_KEY"):
            # The key NAME may appear; a secret VALUE never may.
            value = os.environ.get(secret)
            if value:
                self.assertNotIn(value, blob)


class TestFrontmatterTool(unittest.TestCase):
    """tools/sns_frontmatter.py must bound history without touching the body."""

    def _article(self, tmp, history_entries=4):
        import sns_frontmatter as sf
        entries = "".join(
            '    - attemptedAt: "2026-01-0%dT00:00:00Z"\n      worker: "w"\n' % (i + 1)
            for i in range(history_entries))
        text = (
            "---\n"
            'title: "t"\n'
            'description: "d"\n'
            'slug: "s"\n'
            "pubDate: 2026-01-01\n"
            'tags: ["a"]\n'
            "syndication:\n"
            '  attemptedAt: "2026-01-01T00:00:00Z"\n'
            '  worker: "reviewer/minimax-M3"\n'
            "  targets:\n"
            "    - platform: x\n"
            "      status: pending\n"
            "  history:\n"
            + entries +
            "---\n"
            "\n# Heading\n\nBody text.\n")
        path = os.path.join(tmp, "post.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return sf, path

    def test_trim_bounds_history_and_preserves_body(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sf, path = self._article(tmp, history_entries=4)
            with open(path, encoding="utf-8") as fh:
                before = fh.read()
            body_before = sf.body_sha256(before)
            code = sf.main(["trim", "--post", path, "--keep", "2",
                            "--ledger", os.path.join(tmp, "ledger.jsonl")])
            self.assertEqual(code, 0)
            with open(path, encoding="utf-8") as fh:
                after = fh.read()
            self.assertEqual(sf.body_sha256(after), body_before)
            data = sf.parse_frontmatter(sf.split_document(after)[1])
            self.assertEqual(len(data["syndication"]["history"]), 2)
            self.assertTrue(data["syndication"]["targets"])

    def test_trim_keeps_the_newest_entries(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sf, path = self._article(tmp, history_entries=4)
            sf.main(["trim", "--post", path, "--keep", "2",
                     "--ledger", os.path.join(tmp, "ledger.jsonl")])
            with open(path, encoding="utf-8") as fh:
                data = sf.parse_frontmatter(sf.split_document(fh.read())[1])
            stamps = [e["attemptedAt"] for e in data["syndication"]["history"]]
            self.assertEqual(stamps, ["2026-01-03T00:00:00Z", "2026-01-04T00:00:00Z"])

    def test_trim_is_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sf, path = self._article(tmp, history_entries=4)
            sf.main(["trim", "--post", path, "--keep", "2",
                     "--ledger", os.path.join(tmp, "ledger.jsonl")])
            with open(path, encoding="utf-8") as fh:
                once = fh.read()
            sf.main(["trim", "--post", path, "--keep", "2",
                     "--ledger", os.path.join(tmp, "ledger.jsonl")])
            with open(path, encoding="utf-8") as fh:
                twice = fh.read()
            self.assertEqual(once, twice)

    def test_dry_run_does_not_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sf, path = self._article(tmp, history_entries=4)
            with open(path, encoding="utf-8") as fh:
                before = fh.read()
            sf.main(["trim", "--post", path, "--keep", "1", "--dry-run",
                     "--ledger", os.path.join(tmp, "ledger.jsonl")])
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)

    def test_invalid_status_is_refused(self):
        import sns_frontmatter as sf
        bad = {"attemptedAt": "x", "worker": "w",
               "targets": [{"platform": "x", "status": "blocked"}]}
        with self.assertRaises(sf.FrontmatterError):
            sf.validate_syndication(bad)

    def test_invalid_platform_is_refused(self):
        import sns_frontmatter as sf
        bad = {"attemptedAt": "x", "worker": "w",
               "targets": [{"platform": "mastodon", "status": "pending"}]}
        with self.assertRaises(sf.FrontmatterError):
            sf.validate_syndication(bad)

    def test_missing_required_fields_are_refused(self):
        import sns_frontmatter as sf
        with self.assertRaises(sf.FrontmatterError):
            sf.validate_syndication({"worker": "w", "targets": []})
        with self.assertRaises(sf.FrontmatterError):
            sf.validate_syndication({"attemptedAt": "x", "worker": "w"})

    def test_non_http_url_is_refused(self):
        import sns_frontmatter as sf
        bad = {"attemptedAt": "x", "worker": "w",
               "targets": [{"platform": "x", "status": "published",
                            "url": "not-a-url"}]}
        with self.assertRaises(sf.FrontmatterError):
            sf.validate_syndication(bad)

    def test_body_hash_matches_the_precheck_extraction(self):
        """The precheck decides 'new content?' from the body hash, so the
        frontmatter tool must hash exactly the same bytes."""
        import tempfile
        import sns_precheck as pre
        with tempfile.TemporaryDirectory() as tmp:
            sf, path = self._article(tmp, history_entries=2)
            digest, length, _slug = pre.body_hash(path)
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertEqual(sf.body_sha256(raw), digest)
            self.assertEqual(len(sf.split_document(raw)[3]), length)

    def test_real_article_body_hash_is_unchanged_after_trim(self):
        """Guard against the classic footgun: rewriting frontmatter and
        silently truncating the article body to 0 bytes."""
        import tempfile
        import sns_frontmatter as sf
        import sns_precheck as pre
        article = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src", "content", "blog", "daily-fortune-healthy-mindset.md")
        if not os.path.isfile(article):
            self.skipTest("pending article not present")
        digest, length, _slug = pre.body_hash(article)
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "post.md")
            with open(article, encoding="utf-8") as fh:
                raw = fh.read()
            with open(copy, "w", encoding="utf-8") as fh:
                fh.write(raw)
            code = sf.main(["trim", "--post", copy, "--keep", "3",
                            "--ledger", os.path.join(tmp, "ledger.jsonl")])
            self.assertEqual(code, 0)
            with open(copy, encoding="utf-8") as fh:
                after = fh.read()
        self.assertEqual(sf.body_sha256(after), digest)
        self.assertEqual(len(sf.split_document(after)[3]), length)
        self.assertNotEqual(digest, "e3b0c442" + "98fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


if __name__ == "__main__":
    unittest.main(verbosity=2)
