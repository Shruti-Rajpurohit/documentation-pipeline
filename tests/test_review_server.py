from functools import partial
from http.server import HTTPServer
import tempfile
from threading import Thread
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection
from app.git_tools import GitDiff
from app.llm_client import DocumentationProposal, DocumentEdit
from app.patch_writer import prepare_patch
from app.review_server import ReviewDraft, ReviewRequestHandler, ReviewSession, render_dashboard


class ReviewSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "docs").mkdir()
        self.doc = self.repo / "docs" / "api.md"
        self.doc.write_text("# API\n\nOld behavior.\n", encoding="utf-8")
        self.edit = DocumentEdit("docs/api.md", "API", "replace", "New behavior.", ("head:src/api.py",))
        prepared = prepare_patch(self.repo, [self.edit])
        self.session = ReviewSession(
            ReviewDraft(
                source_diff=GitDiff("base", "head", ("src/api.py",), "+new behavior"),
                analysis=ChangeAnalysis(
                    "update_required",
                    ("src/api.py",),
                    ("head:src/api.py",),
                    "Product source changed.",
                    "base",
                    "head",
                ),
                sections=[DocumentSection("docs/api.md", "API", 1, 3, "# API\n\nOld behavior.", ("api",))],
                proposal=DocumentationProposal("update_required", "The behavior changed.", (self.edit,), (), ()),
                prepared_patch=prepared,
                target_repo=self.repo,
                status="awaiting_review",
                model_name="test-model",
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_approval_applies_patch(self) -> None:
        self.session.approve()

        self.assertEqual(self.session.draft.status, "approved")
        self.assertIn("New behavior.", self.doc.read_text(encoding="utf-8"))

    def test_rejection_does_not_modify_docs_and_closes_review(self) -> None:
        self.session.reject()

        self.assertEqual(self.session.draft.status, "rejected")
        self.assertIn("Old behavior.", self.doc.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            self.session.approve()

    def test_human_edit_is_applied_after_approval(self) -> None:
        self.session.edit("docs/api.md", "# API\n\nHuman-edited behavior.\n")
        self.session.approve()

        self.assertIn("Human-edited behavior.", self.doc.read_text(encoding="utf-8"))

    def test_dashboard_escapes_untrusted_content(self) -> None:
        self.session.draft.proposal = DocumentationProposal(
            "update_required", "<script>alert(1)</script>", (self.edit,), (), ()
        )

        page = render_dashboard(self.session)

        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_http_approval_endpoint_applies_draft(self) -> None:
        server = HTTPServer(
            ("127.0.0.1", 0),
            partial(ReviewRequestHandler, session=self.session),
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        try:
            with urlopen(url) as response:
                page = response.read().decode("utf-8")
            self.assertIn("Documentation review", page)
            self.assertIn("Approve and apply", page)

            request = Request(
                f"{url}approve",
                data=b"",
                headers={"Origin": url.rstrip("/")},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
                approved_page = response.read().decode("utf-8")

            self.assertIn("Approved", approved_page)
            self.assertIn("New behavior.", self.doc.read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()