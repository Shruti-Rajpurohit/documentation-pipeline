import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.main import create_review_session


class FakeModel:
    model = "fake-model"

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class CreateReviewSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.docs = self.root / "docs"
        self.source.mkdir()
        (self.docs / "docs").mkdir(parents=True)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        (self.source / "payments.py").write_text("def createPayment():\n    return None\n", encoding="utf-8")
        self._git("add", "payments.py")
        self._git("commit", "-qm", "initial")
        self.base = self._git("rev-parse", "HEAD").stdout.decode().strip()
        (self.source / "payments.py").write_text(
            "def createPayment(currency):\n    return Payment(currency)\n", encoding="utf-8"
        )
        self._git("commit", "-qam", "require currency")
        self.head = self._git("rev-parse", "HEAD").stdout.decode().strip()
        (self.docs / "docs" / "payments.md").write_text(
            "## Payments\n\nUse `createPayment` to start a payment.\n\n"
            "## Payments\n\nThis duplicate section must remain unchanged.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.source), *arguments], check=True, capture_output=True
        )

    def test_builds_reviewable_patch_from_git_diff(self) -> None:
        response = json.dumps(
            {
                "decision": "update_required",
                "rationale": "The function now requires currency.",
                "changes": [
                    {
                        "path": "docs/payments.md",
                        "section": "Payments",
                        "action": "replace",
                        "content": "Pass a currency when calling `createPayment`.",
                        "evidence": [f"{self.head}:payments.py"],
                    }
                ],
                "uncertainties": [],
                "tests_to_run": ["documentation build"],
            }
        )
        model = FakeModel(response)

        session = create_review_session(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
            model_name=model.model,
        )

        self.assertEqual(session.draft.status, "awaiting_review")
        self.assertEqual(len(model.prompts), 1)
        self.assertIn("createPayment", model.prompts[0])
        self.assertIn("Pass a currency", session.draft.prepared_patch.files[0].after)
        updated_doc = (self.docs / "docs" / "payments.md").read_text(encoding="utf-8")
        self.assertIn("start a payment", session.draft.prepared_patch.files[0].before)
        self.assertIn("This duplicate section must remain unchanged.", updated_doc)
        self.assertEqual(session.draft.sections[0].start_line, 1)

    def test_missing_candidate_does_not_call_model(self) -> None:
        (self.docs / "docs" / "payments.md").write_text(
            "# Overview\n\nGeneral account information only.\n", encoding="utf-8"
        )
        model = FakeModel("{}")

        session = create_review_session(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(session.draft.status, "human_investigation")
        self.assertEqual(model.prompts, [])


if __name__ == "__main__":
    unittest.main()