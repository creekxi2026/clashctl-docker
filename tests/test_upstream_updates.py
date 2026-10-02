import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_upstream.py"
spec = importlib.util.spec_from_file_location("check_upstream", SCRIPT)
assert spec is not None and spec.loader is not None
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)

PIN = "a" * 40
NEW = "b" * 40
DOCKERFILE = f'''FROM metacubex/mihomo:v1.19.32@sha256:{'c' * 64} AS core
LABEL io.clashctl.upstream.revision="{PIN}"
'''


def release(tag="v1.19.32", **kwargs):
    return dict(tag_name=tag, draft=False, prerelease=False, **kwargs)


class UpstreamTests(unittest.TestCase):
    def test_unchanged_is_silent(self):
        self.assertEqual(upstream.plan_updates(DOCKERFILE, PIN, release()), [])

    def test_stable_upgrade(self):
        updates = upstream.plan_updates(DOCKERFILE, PIN, release("v1.19.33"))
        self.assertEqual(len(updates), 1)
        self.assertIn("v1.19.33", updates[0]["title"])
        self.assertIn("compare/v1.19.32...v1.19.33", updates[0]["body"])

    def test_script_commit_independent_of_core(self):
        updates = upstream.plan_updates(DOCKERFILE, NEW, release())
        self.assertEqual(len(updates), 1)
        self.assertIn(f"compare/{PIN}...{NEW}", updates[0]["body"])
        self.assertIn("尚未", updates[0]["body"])

    def test_never_recommends_downgrade(self):
        self.assertEqual(upstream.plan_updates(DOCKERFILE, PIN, release("v1.19.31")), [])

    def test_invalid_or_prerelease_fails_closed(self):
        for candidate in (
            {"tag_name": "v1.19.33", "draft": False, "prerelease": True},
            {"tag_name": "v1.19.33", "draft": True, "prerelease": False},
            release("Prerelease-Alpha"),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                upstream.plan_updates(DOCKERFILE, PIN, candidate)
        with self.assertRaises(ValueError):
            upstream.plan_updates("unrecognized pins", PIN, release())
        with self.assertRaises(ValueError):
            upstream.plan_updates(DOCKERFILE, "bad-sha", release())

    def test_open_and_closed_issues_deduplicate(self):
        updates = upstream.plan_updates(DOCKERFILE, NEW, release("v1.19.33"))
        issues = [dict(body=u["body"], state=s, user={"login": "github-actions[bot]"})
                  for u, s in zip(updates, ["open", "closed"])]
        self.assertEqual(upstream.unreported(updates, issues), [])

    def test_user_text_cannot_suppress_bot_notification(self):
        updates = upstream.plan_updates(DOCKERFILE, NEW, release())
        issues = [dict(body=updates[0]["body"], user={"login": "someone"})]
        self.assertEqual(upstream.unreported(updates, issues), updates)

    def test_dry_run_never_writes(self):
        updates = upstream.plan_updates(DOCKERFILE, NEW, release())
        calls = []
        upstream.publish(lambda *args, **kwargs: calls.append((args, kwargs)),
                         "owner/repo", updates, dry_run=True)
        self.assertEqual(calls, [])

    def test_create_is_read_back(self):
        updates = upstream.plan_updates(DOCKERFILE, NEW, release())
        calls = []
        def api(path, payload=None):
            calls.append((path, payload))
            return dict(number=7, html_url="https://github.com/owner/repo/issues/7",
                        title=updates[0]["title"], body=updates[0]["body"], state="open")
        upstream.publish(api, "owner/repo", updates, dry_run=False)
        self.assertEqual(calls[0][0], "repos/owner/repo/issues")
        self.assertEqual(calls[1], ("repos/owner/repo/issues/7", None))


if __name__ == "__main__":
    unittest.main()
