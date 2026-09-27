from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template
from typing import Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection


ProposalDecision = Literal["update_required", "no_update", "human_investigation"]
EditAction = Literal["replace", "append"]


class ProposalValidationError(ValueError):
	pass


class ModelRequestError(RuntimeError):
	pass


class PromptSizeError(ValueError):
	pass


@dataclass(frozen=True)
class DocumentEdit:
	path: str
	section: str
	action: EditAction
	content: str
	evidence: tuple[str, ...]


@dataclass(frozen=True)
class DocumentationProposal:
	decision: ProposalDecision
	rationale: str
	changes: tuple[DocumentEdit, ...]
	uncertainties: tuple[str, ...]
	tests_to_run: tuple[str, ...]


class OllamaClient:
	def __init__(
		self,
		model: str,
		base_url: str = "http://127.0.0.1:11434",
		timeout: float = 120,
		max_response_bytes: int = 1_000_000,
	) -> None:
		if not model.strip():
			raise ValueError("A pinned Ollama model name is required")
		if timeout <= 0:
			raise ValueError("Timeout must be positive")
		if max_response_bytes < 1:
			raise ValueError("Response size limit must be positive")
		self.model = model
		self.endpoint = f"{base_url.rstrip('/')}/api/generate"
		self.timeout = timeout
		self.max_response_bytes = max_response_bytes

	def generate(self, prompt: str) -> str:
		request_body = json.dumps(
			{"model": self.model, "prompt": prompt, "stream": False, "format": "json"}
		).encode("utf-8")
		request = Request(
			self.endpoint,
			data=request_body,
			headers={"Content-Type": "application/json"},
			method="POST",
		)
		try:
			with urlopen(request, timeout=self.timeout) as response:
				response_body = response.read(self.max_response_bytes + 1)
			if len(response_body) > self.max_response_bytes:
				raise ModelRequestError(f"Ollama response exceeds the {self.max_response_bytes}-byte limit")
			payload = json.loads(response_body.decode("utf-8"))
		except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
			raise ModelRequestError(f"Ollama request failed: {error}") from error

		generated_text = payload.get("response") if isinstance(payload, dict) else None
		if not isinstance(generated_text, str):
			raise ModelRequestError("Ollama returned a response without generated text")
		return generated_text


def build_documentation_prompt(
	template: str,
	analysis: ChangeAnalysis,
	source_diff: str,
	sections: list[DocumentSection],
	*,
	max_prompt_chars: int = 200_000,
) -> str:
	if max_prompt_chars < 1:
		raise ValueError("Prompt size limit must be positive")
	source_context = json.dumps(
		{
			"decision": analysis.decision,
			"rationale": analysis.rationale,
			"base_commit": analysis.base_commit,
			"head_commit": analysis.head_commit,
			"changed_files": analysis.changed_files,
			"evidence_refs": analysis.evidence_refs,
			"diff": source_diff,
		},
		ensure_ascii=False,
		indent=2,
	)
	document_context = json.dumps(
		[
			{
				"path": section.path,
				"heading": section.heading,
				"start_line": section.start_line,
				"end_line": section.end_line,
				"matched_terms": section.matched_terms,
				"content": section.content,
			}
			for section in sections
		],
		ensure_ascii=False,
		indent=2,
	)
	prompt = Template(template).substitute(
		source_context=source_context,
		document_context=document_context,
	)
	if len(prompt) > max_prompt_chars:
		raise PromptSizeError(f"Rendered prompt exceeds the {max_prompt_chars}-character limit")
	return prompt


def parse_proposal(
	response_text: str,
	allowed_sections: Mapping[str, set[str]],
	allowed_evidence_refs: set[str],
) -> DocumentationProposal:
	try:
		payload = json.loads(response_text)
	except json.JSONDecodeError as error:
		raise ProposalValidationError("Model response must be valid JSON") from error
	if not isinstance(payload, dict):
		raise ProposalValidationError("Model response must be a JSON object")

	decision = payload.get("decision")
	if not isinstance(decision, str) or decision not in {
		"update_required",
		"no_update",
		"human_investigation",
	}:
		raise ProposalValidationError("Unsupported proposal decision")
	rationale = _required_string(payload, "rationale")
	uncertainties = _string_list(payload, "uncertainties")
	tests_to_run = _string_list(payload, "tests_to_run")
	raw_changes = payload.get("changes")
	if not isinstance(raw_changes, list):
		raise ProposalValidationError("'changes' must be a list")

	changes: list[DocumentEdit] = []
	seen_targets: set[tuple[str, str]] = set()
	for raw_change in raw_changes:
		if not isinstance(raw_change, dict):
			raise ProposalValidationError("Each change must be a JSON object")
		path = _required_string(raw_change, "path")
		section = _required_string(raw_change, "section")
		action = raw_change.get("action")
		if not isinstance(action, str) or action not in {"replace", "append"}:
			raise ProposalValidationError("Change action must be 'replace' or 'append'")
		content = _required_string(raw_change, "content")
		evidence = _string_list(raw_change, "evidence")
		target = (path, section)

		if path not in allowed_sections or section not in allowed_sections[path]:
			raise ProposalValidationError(f"Change targets an unapproved document section: {path} / {section}")
		if target in seen_targets:
			raise ProposalValidationError(f"Duplicate change target: {path} / {section}")
		if not evidence or not set(evidence).issubset(allowed_evidence_refs):
			raise ProposalValidationError(f"Change has missing or unapproved evidence: {path} / {section}")

		seen_targets.add(target)
		changes.append(
			DocumentEdit(
				path=path,
				section=section,
				action=action,
				content=content,
				evidence=evidence,
			)
		)

	if decision == "update_required" and not changes:
		raise ProposalValidationError("An update proposal must contain at least one change")
	if decision != "update_required" and changes:
		raise ProposalValidationError("Non-update proposals cannot contain document changes")

	return DocumentationProposal(
		decision=decision,
		rationale=rationale,
		changes=tuple(changes),
		uncertainties=uncertainties,
		tests_to_run=tests_to_run,
	)


def _required_string(payload: dict[str, object], field: str) -> str:
	value = payload.get(field)
	if not isinstance(value, str) or not value.strip():
		raise ProposalValidationError(f"'{field}' must be a non-empty string")
	return value.strip()


def _string_list(payload: dict[str, object], field: str) -> tuple[str, ...]:
	value = payload.get(field)
	if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
		raise ProposalValidationError(f"'{field}' must be a list of strings")
	return tuple(item.strip() for item in value if item.strip())
