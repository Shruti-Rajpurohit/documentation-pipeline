import json
import unittest
from unittest.mock import MagicMock, patch

from app.llm_client import (
	LLMConfigurationError,
	OllamaClient,
	OpenRouterClient,
	ProposalValidationError,
	get_llm_client,
	parse_proposal,
)


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

    def test_rejects_unapproved_existing_section(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["section"] = "Mistral AI"

        with self.assertRaises(ProposalValidationError):
            parse_proposal(
                self._response(changes=[change]),
                self.allowed_sections,
                self.allowed_evidence,
            )

    def test_accepts_evidence_that_does_not_match_git_refs(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["evidence"] = ["the authentication branch adds this behavior"]

        proposal = parse_proposal(
            self._response(changes=[change]),
            self.allowed_sections,
            self.allowed_evidence,
        )

        self.assertEqual(
            proposal.changes[0].evidence,
            ("the authentication branch adds this behavior",),
        )

    def test_rejects_completely_empty_evidence_list(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["evidence"] = []

        with self.assertRaises(ProposalValidationError):
            parse_proposal(
                self._response(changes=[change]),
                self.allowed_sections,
                self.allowed_evidence,
            )

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

    def test_accepts_nonempty_evidence_list_with_blank_string(self) -> None:
        change = json.loads(self._response())["changes"][0]
        change["evidence"] = [""]

        proposal = parse_proposal(
            self._response(changes=[change]),
            self.allowed_sections,
            self.allowed_evidence,
        )

        self.assertEqual(proposal.changes[0].evidence, ("",))


class LLMClientTests(unittest.TestCase):
	def test_ollama_factory_preserves_local_base_url(self) -> None:
		client = get_llm_client("ollama", "local-model", "http://ollama:11434/")

		self.assertIsInstance(client, OllamaClient)
		self.assertEqual(client.endpoint, "http://ollama:11434/api/generate")

	def test_openrouter_factory_uses_environment_api_key(self) -> None:
		with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
			client = get_llm_client("openrouter", "vendor/model")

		self.assertIsInstance(client, OpenRouterClient)
		self.assertEqual(client.api_key, "test-key")
		self.assertEqual(client.model, "vendor/model")

	def test_openrouter_requires_api_key(self) -> None:
		with patch.dict("os.environ", {}, clear=True):
			with self.assertRaises(LLMConfigurationError):
				get_llm_client("openrouter", "vendor/model")

	def test_rejects_unknown_provider(self) -> None:
		with self.assertRaises(LLMConfigurationError):
			get_llm_client("other", "model")

	def test_openrouter_sends_expected_request_and_extracts_content(self) -> None:
		response = MagicMock()
		response.__enter__.return_value.read.return_value = json.dumps(
			{"choices": [{"message": {"content": '{"ok": true}'}}]}
		).encode()
		client = OpenRouterClient(api_key="test-key", model="vendor/model")

		with patch("app.llm_client.urlopen", return_value=response) as urlopen:
			result = client.generate("Return JSON")

		self.assertEqual(result, '{"ok": true}')
		request = urlopen.call_args.args[0]
		self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
		self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
		self.assertEqual(request.get_header("Http-referer"), "http://localhost")
		self.assertEqual(request.get_header("X-title"), "Documentation Pipeline")
		self.assertEqual(
			json.loads(request.data),
			{
				"model": "vendor/model",
				"messages": [{"role": "user", "content": "Return JSON"}],
			},
		)


if __name__ == "__main__":
    unittest.main()