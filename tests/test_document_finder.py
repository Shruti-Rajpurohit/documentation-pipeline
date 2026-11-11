import tempfile
import unittest
from pathlib import Path

from app.document_finder import (
    DocumentSearchLimitError,
    _extract_terms,
    classify_document_kind,
    find_candidate_sections,
)


class FindCandidateSectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "api.md").write_text(
            "# Payments API\n\nCall `createPayment` to create a payment.\n\n"
            "## Refunds\n\nUse `refundPayment` to issue a refund.\n",
            encoding="utf-8",
        )
        (self.repo / "docs" / "unrelated.md").write_text(
            "# Accounts\n\nUse `listAccounts` to browse accounts.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_returns_matching_sections_with_evidence_location(self) -> None:
        results = find_candidate_sections(
            self.repo,
            ("src/payments.py",),
            "+def createPayment(request):\n+    return Payment()\n",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].path, "docs/api.md")
        self.assertEqual(results[0].heading, "Payments API")
        self.assertEqual(results[0].start_line, 1)
        self.assertIn("createPayment", results[0].matched_terms)

    def test_matches_document_path_for_new_topics_missing_from_section_text(self) -> None:
        (self.repo / "docs" / "payments.md").write_text(
            "# Overview\n\nGeneral account information only.\n",
            encoding="utf-8",
        )

        results = find_candidate_sections(
            self.repo,
            ("src/payments.py",),
            "+def createPayment(currency):\n+    return Payment(currency)\n",
        )

        self.assertTrue(any(section.path == "docs/payments.md" for section in results))

    def test_returns_no_results_when_patch_has_no_searchable_terms(self) -> None:
        results = find_candidate_sections(self.repo, (), "")

        self.assertEqual(results, [])

    def test_rejects_nonpositive_limits(self) -> None:
        with self.assertRaises(ValueError):
            find_candidate_sections(self.repo, (), "", limit=0)

    def test_fails_when_document_scan_limit_is_exceeded(self) -> None:
        with self.assertRaises(DocumentSearchLimitError):
            find_candidate_sections(
                self.repo,
                ("payments.py",),
                "+createPayment",
                max_files=1,
            )

    def test_classifies_documentation_kinds_from_paths(self) -> None:
        self.assertEqual(classify_document_kind("docs/api/payments.md"), "api")
        self.assertEqual(classify_document_kind("developer/sdk.md"), "developer")
        self.assertEqual(classify_document_kind("guides/getting-started.md"), "user")
        self.assertEqual(classify_document_kind("blog/new-currency.md"), "blog")
        self.assertEqual(classify_document_kind("overview.md"), "other")

    def test_prefers_one_section_from_each_documentation_kind(self) -> None:
        (self.repo / "api").mkdir()
        (self.repo / "guides").mkdir()
        (self.repo / "blog").mkdir()
        (self.repo / "api" / "payments.md").write_text("# API\n\ncreatePayment\n", encoding="utf-8")
        (self.repo / "guides" / "payments.md").write_text("# User\n\ncreatePayment\n", encoding="utf-8")
        (self.repo / "blog" / "payments.md").write_text("# Blog\n\ncreatePayment\n", encoding="utf-8")

        results = find_candidate_sections(
            self.repo,
            ("src/payments.py",),
            "+def createPayment(currency):\n",
            limit=3,
        )

        self.assertEqual({section.kind for section in results}, {"api", "user", "blog"})

    def test_ignores_low_signal_filename_stems(self) -> None:
        for stem in ("main", "utils", "index", "app", "constants", "types"):
            with self.subTest(stem=stem):
                self.assertEqual(_extract_terms((f"src/{stem}.py",), ""), ())


if __name__ == "__main__":
    unittest.main()