import contextlib
import io
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lithify import hostos, updates


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SelfUpdateTest(unittest.TestCase):
    def test_git_never_waits_for_a_person(self):
        seen = {}

        def popen(cmd, **kw):
            seen.update(kw)
            raise FileNotFoundError(2, "git")  # (enough: what it would have been started with is seen)

        with mock.patch.object(hostos.subprocess, "Popen", side_effect=popen), self.assertRaises(OSError):
            updates.git("status")
        self.assertEqual(seen["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertIs(seen["stdin"], subprocess.DEVNULL)
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))
        self.assertTrue(seen.get("start_new_session") or seen.get("creationflags"))  # its helpers end with it

    @unittest.skipIf(os.name == "nt", "process groups: Linux and macOS")
    def test_a_hung_git_helper_does_not_hold_up_the_timeout(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            hostos.run(["sh", "-c", "sleep 20; true"], timeout=1)  # (a grandchild holding the output)
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(hostos.run(["sh", "-c", "echo ok"], timeout=10, text=True).stdout, "ok\n")

    def test_a_slow_github_is_a_warning_not_a_failed_build(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            for failure in (subprocess.TimeoutExpired(["git", "pull"], 120), FileNotFoundError(2, "git")):
                with self.subTest(failure=type(failure).__name__):
                    err = io.StringIO()
                    with mock.patch.object(updates, "git", side_effect=failure), contextlib.redirect_stderr(err):
                        self.assertFalse(updates.self_update(Path(d)))
                    self.assertIn("could not update itself", err.getvalue())

    def test_going_back_never_discards_uncommitted_work(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "lithify"
            git("init", "-q", "-b", "main", str(repo))
            (repo / "code.py").write_text("v1\n", encoding="utf-8")
            git("add", ".", cwd=repo)
            git("commit", "-q", "-m", "v1", cwd=repo)
            v1 = git("rev-parse", "HEAD", cwd=repo)
            (repo / "code.py").write_text("v2\n", encoding="utf-8")
            git("commit", "-q", "-am", "v2", cwd=repo)
            (repo / "code.py").write_text("v2 with my edit\n", encoding="utf-8")
            undone, why = updates.undo_update(repo, v1)
            self.assertFalse(undone)
            self.assertTrue(why)  # git's reason
            self.assertEqual((repo / "code.py").read_text(encoding="utf-8"), "v2 with my edit\n")
            git("checkout", "--", "code.py", cwd=repo)
            self.assertEqual(updates.undo_update(repo, v1), (True, ""))
            self.assertEqual((updates.head(repo), (repo / "code.py").read_text(encoding="utf-8")), (v1, "v1\n"))
            self.assertEqual(updates.head(Path(d) / "not-a-checkout"), "")

    def test_a_copy_inside_another_repository_is_not_taken_for_a_checkout(self):
        # (e.g. a home directory kept in git: its commits are not Lithify's to move)
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            git("init", "-q", "-b", "main", str(home))
            (home / "dotfile").write_text("x\n", encoding="utf-8")
            git("add", ".", cwd=home)
            git("commit", "-q", "-m", "home", cwd=home)
            first = git("rev-parse", "HEAD", cwd=home)
            git("commit", "-q", "--allow-empty", "-m", "later", cwd=home)
            copy = home / "lithify"
            copy.mkdir()
            self.assertEqual(updates.head(copy), "")
            self.assertFalse(updates.undo_update(copy, first)[0])
            self.assertNotEqual(git("rev-parse", "HEAD", cwd=home), first)  # untouched


class BranchTest(unittest.TestCase):
    def test_newest_commit_and_how_far_behind(self):
        with tempfile.TemporaryDirectory() as d:
            repo, mirror = Path(d) / "librespot", Path(d) / "librespot.git"
            git("init", "-q", "-b", "dev", str(repo))
            shas = []
            for i in range(3):
                git("commit", "-q", "--allow-empty", "-m", f"change {i}", cwd=repo)
                shas.append(git("rev-parse", "HEAD", cwd=repo))
            git("clone", "-q", "--mirror", str(repo), str(mirror))
            lp = {"repo": str(repo), "ref": "dev", "commit": shas[0]}
            self.assertEqual(updates.librespot_branch(lp, mirror), {"head": shas[2], "behind": 2, "source": "github"})
            lp["commit"] = shas[2]
            self.assertEqual(updates.librespot_branch(lp, mirror)["behind"], 0)
            # a new commit upstream: the mirror fetches it to count
            git("commit", "-q", "--allow-empty", "-m", "change 3", cwd=repo)
            b = updates.librespot_branch(lp, mirror)
            self.assertEqual((b["head"], b["behind"]), (git("rev-parse", "HEAD", cwd=repo), 1))
            # GitHub unreachable: the mirror's branch, said so
            b = updates.librespot_branch({**lp, "repo": str(Path(d) / "gone")}, mirror)
            self.assertEqual((b["head"], b["behind"]), (git("rev-parse", "HEAD", cwd=repo), 1))
            self.assertIn("mirror", b["source"])
            # no mirror yet (before the first build): the newest commit, the count unknown
            b = updates.librespot_branch(lp, Path(d) / "none")
            self.assertEqual(b["behind"], None)
            self.assertIsNotNone(b["head"])


if __name__ == "__main__":
    unittest.main()
