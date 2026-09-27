import tempfile
import unittest
from pathlib import Path

from app.llm_client import DocumentEdit
from app.patch_writer import PatchError, StalePatchError, apply_approved_patch, prepare_patch


class PatchWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "docs").mkdir()
        self.doc = self.repo / "docs" / "api.md"
        self.doc.write_text("# API\n\nOld behavior.\n\n## Other\n\nKeep this.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _edit(self, *, path: str = "docs/api.md", section: str = "API") -> DocumentEdit:
        return DocumentEdit(
            path=path,
            section=section,
            action="replace",
            content="New behavior.",
            evidence=("head:src/api.py",),
        )

    def test_prepare_generates_preview_without_changing_file(self) -> None:
        before = self.doc.read_text(encoding="utf-8")

        prepared = prepare_patch(self.repo, [self._edit()])

        self.assertEqual(self.doc.read_text(encoding="utf-8"), before)
        self.assertIn("-Old behavior.", prepared.files[0].unified_diff)
        self.assertIn("+New behavior.", prepared.files[0].unified_diff)
        self.assertIn("Keep this.", prepared.files[0].after)

    def test_apply_requires_explicit_approval(self) -> None:
        prepared = prepare_patch(self.repo, [self._edit()])

        with self.assertRaises(PatchError):
            apply_approved_patch(self.repo, prepared)

        self.assertIn("Old behavior.", self.doc.read_text(encoding="utf-8"))

    def test_approved_apply_writes_patch(self) -> None:
        prepared = prepare_patch(self.repo, [self._edit()])

        apply_approved_patch(self.repo, prepared, approved=True)

        result = self.doc.read_text(encoding="utf-8")
        self.assertIn("New behavior.", result)
        self.assertNotIn("Old behavior.", result)

    def test_apply_rejects_stale_reviewed_document(self) -> None:
        prepared = prepare_patch(self.repo, [self._edit()])
        self.doc.write_text("Changed after review.\n", encoding="utf-8")

        with self.assertRaises(StalePatchError):
            apply_approved_patch(self.repo, prepared, approved=True)

        self.assertEqual(self.doc.read_text(encoding="utf-8"), "Changed after review.\n")

    def test_prepare_rejects_path_traversal(self) -> None:
        with self.assertRaises(PatchError):
            prepare_patch(self.repo, [self._edit(path="../outside.md")])

    def test_prepare_rejects_ambiguous_section_heading(self) -> None:
        self.doc.write_text("# API\n\nFirst.\n\n# API\n\nSecond.\n", encoding="utf-8")

        with self.assertRaises(PatchError):
            prepare_patch(self.repo, [self._edit()])


if __name__ == "__main__":
    unittest.main()