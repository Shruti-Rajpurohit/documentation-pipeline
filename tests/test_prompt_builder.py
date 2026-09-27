import unittest

from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection
from app.llm_client import PromptSizeError, build_documentation_prompt


class BuildDocumentationPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = ChangeAnalysis(
            decision="update_required",
            changed_files=("src/api.py",),
            evidence_refs=("head456:src/api.py",),
            rationale="Source changed.",
            base_commit="base123",
            head_commit="head456",
        )
        self.section = DocumentSection(
            path="docs/api.md",
            heading="API",
            start_line=1,
            end_line=3,
            content="# API\n\nCreate a payment.",
            matched_terms=("payment",),
        )

    def test_includes_json_encoded_source_and_document_context(self) -> None:
        prompt = build_documentation_prompt(
            "Source: $source_context\nDocs: $document_context",
            self.analysis,
            "+def create_payment(): pass",
            [self.section],
        )

        self.assertIn('"head456:src/api.py"', prompt)
        self.assertIn('"docs/api.md"', prompt)
        self.assertIn("create_payment", prompt)

    def test_rejects_oversized_rendered_prompt(self) -> None:
        with self.assertRaises(PromptSizeError):
            build_documentation_prompt("$source_context $document_context", self.analysis, "x", [], max_prompt_chars=1)


if __name__ == "__main__":
    unittest.main()