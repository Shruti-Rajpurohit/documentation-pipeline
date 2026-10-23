import json
import unittest

from app.llm_client import ProposalValidationError, parse_proposal


class ParseProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.allowed_sections = {"docs/api.md": {"Payments API"}}
        self.allowed_evidence = {"head456:src/payments.py"}

    def _response(self, **overrides: object) -> str:
        payload: dict[str, object] = {
            "decision": "update_required",
            "rationale": "The API behavior changed.",
            "changes": [
                {
                    "path": "docs/api.md",
                    "section": "Payments API",
                    "action": "replace",
                    "content": "Call `createPayment` to create a payment.",
                    "evidence": ["head456:src/payments.py"],
                }
            ],
            "uncertainties": [],
            "tests_to_run": ["markdown lint"],
        }
        payload.update(overrides)
        return json.dumps(payload)

    def test_parses_valid_evidence_linked_update(self) -> None:
        proposal = parse_proposal(self._response(), self.allowed_sections, self.allowed_evidence)

        self.assertEqual(proposal.decision, "update_required")
        self.assertEqual(proposal.changes[0].path, "docs/api.md")
        self.assertEqual(proposal.changes[0].evidence, ("head456:src/payments.py",))

    def test_parses_create_for_a_new_heading_in_an_approved_file(self) -> None:
        change = {
            "path": "docs/api.md",
            "section": "Forgot Password",
            "action": "create",
            "content": "Use the reset endpoint to continue.",
            "evidence": ["head456:src/payments.py"],
        }

        proposal = parse_proposal(
            self._response(changes=[change]),
            self.allowed_sections,
            self.allowed_evidence,
        )

        self.assertEqual(proposal.changes[0].action, "create")
        self.assertEqual(proposal.changes[0].section, "Forgot Password")

    def test_rejects_create_when_heading_already_exists(self) -> None:
        change = {
            "path": "docs/api.md",
            "section": "Payments API",
            "action": "create",
            "content": "Duplicate section.",
            "evidence": ["head456:src/payments.py"],
        }

        with self.assertRaises(ProposalValidationError):
            parse_proposal(
                self._response(changes=[change]),
                self.allowed_sections,
                self.allowed_evidence,
            )

    def test_allows_no_update_without_changes(self) -> None:
        response = self._response(decision="no_update", changes=[])

        proposal = parse_proposal(response, self.allowed_sections, self.allowed_evidence)

        self.assertEqual(proposal.decision, "no_update")
        self.assertEqual(proposal.changes, ())

    def test_normalizes_update_decision_synonyms(self) -> None:
        for synonym in ("update_required", "update", "required", "yes", "needed", "update_needed"):
            with self.subTest(decision=synonym):
                proposal = parse_proposal(
                    self._response(decision=synonym),
                    self.allowed_sections,
                    self.allowed_evidence,
                )

                self.assertEqual(proposal.decision, "update_required")

    def test_normalizes_non_update_decision_synonyms(self) -> None:
        for synonym, canonical in (
            ("no_update", "no_update"),
            ("no", "no_update"),
            ("none", "no_update"),
            ("not_required", "no_update"),
            ("no_update_needed", "no_update"),
            ("human_investigation", "human_investigation"),
            ("investigate", "human_investigation"),
            ("uncertain", "human_investigation"),
            ("unknown", "human_investigation"),
            ("human", "human_investigation"),
        ):
            with self.subTest(decision=synonym):
                proposal = parse_proposal(
                    self._response(decision=synonym, changes=[]),
                    self.allowed_sections,
                    self.allowed_evidence,
                )

                self.assertEqual(proposal.decision, canonical)

    def test_normalizes_decision_whitespace_case_and_spaces(self) -> None:
        proposal = parse_proposal(
            self._response(decision="  UPDATE REQUIRED  "),
            self.allowed_sections,
            self.allowed_evidence,
        )

        self.assertEqual(proposal.decision, "update_required")

    def test_rejects_unapproved_document_path(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["path"] = "../../README.md"

        with self.assertRaises(ProposalValidationError):
            parse_proposal(self._response(changes=[change]), self.allowed_sections, self.allowed_evidence)

    def test_rejects_unapproved_evidence(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["evidence"] = ["made-up:source.py"]

        with self.assertRaises(ProposalValidationError):
            parse_proposal(self._response(changes=[change]), self.allowed_sections, self.allowed_evidence)

    def test_rejects_update_without_changes(self) -> None:
        with self.assertRaises(ProposalValidationError):
            parse_proposal(self._response(changes=[]), self.allowed_sections, self.allowed_evidence)

    def test_rejects_malformed_json(self) -> None:
        with self.assertRaises(ProposalValidationError):
            parse_proposal("not json", self.allowed_sections, self.allowed_evidence)

    def test_rejects_non_string_decision(self) -> None:
        with self.assertRaises(ProposalValidationError):
            parse_proposal(self._response(decision=[]), self.allowed_sections, self.allowed_evidence)

    def test_rejects_unsupported_decision(self) -> None:
        with self.assertRaises(ProposalValidationError):
            parse_proposal(
                self._response(decision="not_a_decision"),
                self.allowed_sections,
                self.allowed_evidence,
            )

    def test_rejects_non_string_action(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["action"] = []

        with self.assertRaises(ProposalValidationError):
            parse_proposal(self._response(changes=[change]), self.allowed_sections, self.allowed_evidence)

    def test_rejects_empty_string_in_optional_text_lists(self) -> None:
        for field in ("uncertainties", "tests_to_run"):
            with self.subTest(field=field):
                with self.assertRaises(ProposalValidationError):
                    parse_proposal(
                        self._response(**{field: ["  "]}),
                        self.allowed_sections,
                        self.allowed_evidence,
                    )

    def test_rejects_empty_evidence_string(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["evidence"] = [""]

        with self.assertRaises(ProposalValidationError):
            parse_proposal(self._response(changes=[change]), self.allowed_sections, self.allowed_evidence)


if __name__ == "__main__":
    unittest.main()