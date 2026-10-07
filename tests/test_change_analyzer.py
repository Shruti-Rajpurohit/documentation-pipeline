import unittest

from app.change_analyzer import analyze_change
from app.git_tools import GitDiff


class AnalyzeChangeTests(unittest.TestCase):
    def _diff(self, *paths: str) -> GitDiff:
        return GitDiff(
            base_commit="base123",
            head_commit="head456",
            changed_files=paths,
            patch="diff contents",
        )

    def test_no_changed_files_does_not_require_update(self) -> None:
        result = analyze_change(self._diff())

        self.assertEqual(result.decision, "no_update")
        self.assertEqual(result.evidence_refs, ())

    def test_documentation_only_changes_do_not_trigger_source_review(self) -> None:
        result = analyze_change(self._diff("docs/guide.md", "README.md"))

        self.assertEqual(result.decision, "no_update")

    def test_source_and_schema_changes_require_documentation_review(self) -> None:
        result = analyze_change(self._diff("src/api.py", "openapi.yaml"))

        self.assertEqual(result.decision, "update_required")
        self.assertEqual(
            result.evidence_refs,
            ("head456:src/api.py", "head456:openapi.yaml"),
        )

    def test_test_only_changes_need_human_investigation(self) -> None:
        result = analyze_change(self._diff("tests/test_api.py"))

        self.assertEqual(result.decision, "human_investigation")

    def test_unknown_file_does_not_override_source_change(self) -> None:
        result = analyze_change(self._diff("src/api.py", "generated/output.bin"))

        self.assertEqual(result.decision, "update_required")

    def test_non_text_file_in_docs_folder_is_not_documentation(self) -> None:
        result = analyze_change(self._diff("docs/architecture.png"))

        self.assertEqual(result.decision, "human_investigation")

    def test_text_file_in_docs_folder_is_documentation(self) -> None:
        result = analyze_change(self._diff("docs/notes.txt"))

        self.assertEqual(result.decision, "no_update")


if __name__ == "__main__":
    unittest.main()