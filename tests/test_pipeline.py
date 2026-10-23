import json
import subprocess
import tempfile
from pathlib import Path
import unittest

from app.services.pipeline import (
	HALLUCINATION_NOTE,
	analyze_repository_change,
)


class FakeModel:
    model = "fake-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class AnalyzeRepositoryChangeTests(unittest.IsolatedAsyncioTestCase):
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

    def _impact_report(self) -> str:
        return json.dumps(
            {
                "changed_functions_endpoints": ["createPayment"],
                "technical_summary": "createPayment now requires currency.",
                "documentation_sections_to_update": ["Payments API"],
            }
        )

    def _proposal(self, content: str = "Pass a currency when calling `createPayment`.") -> str:
        return json.dumps(
            {
                "decision": "update_required",
                "rationale": "The function now requires currency.",
                "changes": [
                    {
                        "path": "docs/payments.md",
                        "section": "Payments",
                        "action": "replace",
                        "content": content,
                        "evidence": [f"{self.head}:payments.py"],
                    }
                ],
                "uncertainties": [],
                "tests_to_run": ["documentation build"],
            }
        )

    async def test_builds_reviewable_patch_from_git_diff(self) -> None:
        model = FakeModel([self._impact_report(), self._proposal()])

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
            model_name=model.model,
        )

        self.assertEqual(draft.outcome, "awaiting_review")
        self.assertEqual(len(model.prompts), 2)
        self.assertIn("createPayment", model.prompts[0])
        self.assertIn('"technical_summary": "createPayment now requires currency."', model.prompts[1])
        self.assertIn("User can now upload up to 3 images for analysis.", model.prompts[1])
        self.assertIn("Added password reset flow via email link.", model.prompts[1])
        self.assertIn("Users can now maintain multiple independent chat threads.", model.prompts[1])
        self.assertIn("Pass a currency", draft.prepared_patch.files[0].after)
        updated_doc = (self.docs / "docs" / "payments.md").read_text(encoding="utf-8")
        self.assertIn("start a payment", draft.prepared_patch.files[0].before)
        self.assertIn("This duplicate section must remain unchanged.", updated_doc)
        self.assertEqual(draft.sections[0].start_line, 1)

    async def test_retries_once_with_allowed_targets_after_invalid_section(self) -> None:
        invalid_proposal = json.loads(self._proposal())
        invalid_proposal["changes"][0]["section"] = "Mistral AI"
        model = FakeModel(
            [
                self._impact_report(),
                json.dumps(invalid_proposal),
                self._proposal(),
            ]
        )

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(draft.outcome, "awaiting_review")
        self.assertEqual(draft.proposal.changes[0].section, "Payments")
        self.assertEqual(len(model.prompts), 3)
        self.assertIn("AUTHORIZED TARGETS", model.prompts[1])
        self.assertIn('"heading": "Payments"', model.prompts[1])
        self.assertIn("unapproved document section", model.prompts[2])
        self.assertIn('"heading": "Payments"', model.prompts[2])

    async def test_returns_human_investigation_after_second_invalid_target(self) -> None:
        invalid_proposal = json.loads(self._proposal())
        invalid_proposal["changes"][0]["section"] = "Mistral AI"
        invalid_response = json.dumps(invalid_proposal)
        model = FakeModel(
            [
                self._impact_report(),
                invalid_response,
                invalid_response,
            ]
        )

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(draft.outcome, "human_investigation")
        self.assertIsNone(draft.proposal)
        self.assertIsNone(draft.prepared_patch)
        self.assertIn("remained invalid after one constrained retry", draft.note)
        self.assertEqual(len(model.prompts), 3)
        self.assertIn("unapproved document section", model.prompts[2])

    async def test_rejects_out_of_domain_content_for_human_investigation(self) -> None:
        model = FakeModel(
            [
                self._impact_report(),
                self._proposal("Added tax calculations for `createPayment`."),
            ]
        )

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(draft.outcome, "human_investigation")
        self.assertEqual(draft.note, HALLUCINATION_NOTE)
        self.assertIsNone(draft.prepared_patch)
        self.assertEqual(draft.proposal.decision, "human_investigation")
        self.assertEqual(draft.proposal.changes, ())
        self.assertIn(HALLUCINATION_NOTE, draft.proposal.uncertainties)

    async def test_creates_new_section_when_analyst_requests_creation_mode(self) -> None:
        (self.docs / "docs" / "payments.md").write_text(
            "# Overview\n\nGeneral account information only.\n",
            encoding="utf-8",
        )
        impact_report = json.dumps(
            {
                "decision": "update_required",
                "create_new_section": True,
                "changed_functions_endpoints": ["createPayment"],
                "technical_summary": "createPayment now requires currency.",
            }
        )
        proposal = self._proposal("createPayment requires currency.")
        proposal_payload = json.loads(proposal)
        proposal_payload["changes"][0].update(
            {"section": "Currency", "action": "create"}
        )
        model = FakeModel([impact_report, json.dumps(proposal_payload)])

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(draft.outcome, "awaiting_review")
        self.assertEqual(draft.proposal.changes[0].action, "create")
        self.assertIsNone(draft.proposal.changes[0].start_line)
        self.assertIn("## Currency\n\ncreatePayment requires currency.", draft.prepared_patch.files[0].after)
        self.assertIn('"create_new_section": true', model.prompts[0])
        self.assertIn('"heading": "Overview"', model.prompts[0])

    async def test_passes_incomplete_analyst_response_to_writer(self) -> None:
        incomplete_impact_report = json.dumps({"analyst_confidence": 0.95})
        model = FakeModel([incomplete_impact_report, self._proposal()])

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(draft.outcome, "awaiting_review")
        self.assertIn(incomplete_impact_report, model.prompts[1])

    async def test_missing_candidate_does_not_call_model(self) -> None:
        (self.docs / "docs" / "payments.md").write_text(
            "# Overview\n\nGeneral account information only.\n", encoding="utf-8"
        )
        model = FakeModel([self._impact_report()])

        draft = await analyze_repository_change(
            self.source,
            self.docs,
            self.base,
            self.head,
            model,
            prompt_template="$source_context $document_context",
        )

        self.assertEqual(draft.outcome, "human_investigation")
        self.assertEqual(len(model.prompts), 1)


if __name__ == "__main__":
    unittest.main()