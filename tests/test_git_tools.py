import subprocess
import tempfile
import unittest
from pathlib import Path

from app.git_tools import DiffLimitExceededError, InvalidRevisionError, collect_git_diff


class CollectGitDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        (self.repo / "feature.py").write_text("value = 1\n", encoding="utf-8")
        self._git("add", "feature.py")
        self._git("commit", "-qm", "initial")
        self.base = self._git("rev-parse", "HEAD").stdout.decode().strip()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            capture_output=True,
        )

    def test_collects_changed_paths_and_patch(self) -> None:
        (self.repo / "feature.py").write_text("value = 2\n", encoding="utf-8")
        self._git("commit", "-qam", "change value")

        result = collect_git_diff(self.repo, self.base, "HEAD")

        self.assertEqual(result.changed_files, ("feature.py",))
        self.assertIn("-value = 1", result.patch)
        self.assertIn("+value = 2", result.patch)
        self.assertTrue(result.base_commit)
        self.assertTrue(result.head_commit)

    def test_rejects_invalid_revision(self) -> None:
        with self.assertRaises(InvalidRevisionError):
            collect_git_diff(self.repo, "not-a-commit", "HEAD")

    def test_rejects_too_many_changed_files(self) -> None:
        (self.repo / "feature.py").write_text("value = 2\n", encoding="utf-8")
        (self.repo / "second.py").write_text("value = 2\n", encoding="utf-8")
        self._git("add", "feature.py", "second.py")
        self._git("commit", "-qm", "add second file")

        with self.assertRaises(DiffLimitExceededError):
            collect_git_diff(self.repo, self.base, "HEAD", max_files=1)

    def test_rejects_oversized_patch(self) -> None:
        (self.repo / "feature.py").write_text("value = 'a much longer value'\n", encoding="utf-8")
        self._git("commit", "-qam", "expand value")

        with self.assertRaises(DiffLimitExceededError):
            collect_git_diff(self.repo, self.base, "HEAD", max_patch_bytes=1)


if __name__ == "__main__":
    unittest.main()