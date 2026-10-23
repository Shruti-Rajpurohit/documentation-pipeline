from __future__ import annotations

import json
import os
from dataclasses import dataclass
from string import Template
from typing import Literal, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection


ProposalDecision = Literal["update_required", "no_update", "human_investigation"]
EditAction = Literal["replace", "append", "create"]
LLMProvider = Literal["ollama", "openrouter"]

DECISION_MAP: dict[ProposalDecision, set[str]] = {
	"update_required": {"update_required", "update", "required", "yes", "needed", "update_needed"},
	"no_update": {"no_update", "no", "none", "not_required", "no_update_needed"},
	"human_investigation": {"human_investigation", "investigate", "uncertain", "unknown", "human"},
}


class ProposalValidationError(ValueError):
	pass


class ModelRequestError(RuntimeError):
	pass


class PromptSizeError(ValueError):
	pass


class LLMConfigurationError(ValueError):
	pass


class TextGenerator(Protocol):
	def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class DocumentEdit:
	path: str
	section: str
	action: EditAction
	content: str
	evidence: tuple[str, ...]
	start_line: int | None = None


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


class OpenRouterClient:
	def __init__(
		self,
		api_key: str | None,
		model: str,
		*,
		timeout: float = 120,
		max_response_bytes: int = 1_000_000,
	) -> None:
		resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
		if not resolved_api_key or not resolved_api_key.strip():
			raise LLMConfigurationError("OPENROUTER_API_KEY must be set to use OpenRouter")
		if not model.strip():
			raise ValueError("An OpenRouter model name is required")
		if timeout <= 0:
			raise ValueError("Timeout must be positive")
		if max_response_bytes < 1:
			raise ValueError("Response size limit must be positive")
		self.api_key = resolved_api_key.strip()
		self.model = model
		self.endpoint = "https://openrouter.ai/api/v1/chat/completions"
		self.timeout = timeout
		self.max_response_bytes = max_response_bytes

	def generate(self, prompt: str) -> str:
		payload = {
			"model": self.model,
			"messages": [
				{
					"role": "user",
					"content": prompt,
					}
					],
			"response_format": {"type": "json_object"}, 
		}
		
		request_body = json.dumps(payload).encode("utf-8")
		request = Request(
			self.endpoint,
			data=request_body,
			headers={
				"Content-Type": "application/json",
				"Authorization": f"Bearer {self.api_key}",
				"HTTP-Referer": "http://localhost:8000", 
				"X-Title": "Patchwork DocOps",
			},
			method="POST",
		)
		
		try:
			# Use the unverified context if you are still having SSL issues
			import ssl
			context = ssl._create_unverified_context() 
			
			with urlopen(request, timeout=120, context=context) as response:
				response_body = response.read()
			
			payload = json.loads(response_body.decode("utf-8"))
			
			# OpenRouter response structure: choices[0].message.content
			if "choices" in payload and len(payload["choices"]) > 0:
				return payload["choices"][0]["message"]["content"]
			else:
				raise ModelRequestError(f"OpenRouter returned an unexpected response format: {payload}")
				
		except (HTTPError, URLError, json.JSONDecodeError) as error:
			# This will now print the actual body of the 400 error from OpenRouter
			if isinstance(error, HTTPError):
				error_body = error.read().decode("utf-8")
				raise ModelRequestError(f"OpenRouter API Error {error.code}: {error_body}") from error
			raise ModelRequestError(f"OpenRouter request failed: {error}") from error


def get_llm_client(
	provider: str,
	model: str,
	base_url: str | None = None,
) -> TextGenerator:
	normalized_provider = provider.strip().lower()
	if normalized_provider == "ollama":
		return OllamaClient(model=model, base_url=base_url or "http://127.0.0.1:11434")
	if normalized_provider == "openrouter":
		return OpenRouterClient(api_key=os.getenv("OPENROUTER_API_KEY"), model=model)
	raise LLMConfigurationError(f"Unsupported LLM provider: {provider}")


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
	*,
	allowed_create_paths: set[str] | None = None,
	existing_sections: Mapping[str, set[str]] | None = None,
) -> DocumentationProposal:
	print(f"DEBUG RAW RESPONSE: {response_text}") # Debugging line to print the raw response from the model
	try:
		payload = json.loads(response_text)
	except json.JSONDecodeError as error:
		raise ProposalValidationError("Model response must be valid JSON") from error
	if not isinstance(payload, dict):
		raise ProposalValidationError("Model response must be a JSON object")

	raw_decision = payload.get("decision")
	if not isinstance(raw_decision, str):
		raise ProposalValidationError(f"Decision must be a string, got {type(raw_decision)}")
	normalized_decision = raw_decision.strip().lower().replace(" ", "_")
	canonical_decision = next(
		(
			canonical
			for canonical, synonyms in DECISION_MAP.items()
			if normalized_decision in synonyms
		),
		None,
	)
	if canonical_decision is None:
		raise ProposalValidationError(f"Unsupported proposal decision: {raw_decision}")
	decision: ProposalDecision = canonical_decision
	rationale = _required_string(payload, "rationale")
	uncertainties = _string_list(payload, "uncertainties")
	tests_to_run = _string_list(payload, "tests_to_run")
	raw_changes = payload.get("changes")
	if not isinstance(raw_changes, list):
		raise ProposalValidationError("'changes' must be a list")

	changes: list[DocumentEdit] = []
	seen_targets: set[tuple[str, str]] = set()
	create_paths = allowed_create_paths if allowed_create_paths is not None else set(allowed_sections)
	for raw_change in raw_changes:
		if not isinstance(raw_change, dict):
			raise ProposalValidationError("Each change must be a JSON object")
		path = _required_string(raw_change, "path")
		section = _required_string(raw_change, "section")
		action = raw_change.get("action")
		if not isinstance(action, str) or action not in {"replace", "append", "create"}:
			raise ProposalValidationError("Change action must be 'replace', 'append', or 'create'")
		content = _required_string(raw_change, "content")
		evidence = _string_list(raw_change, "evidence")
		target = (path, section)

		if action == "create":
			if path not in create_paths:
				raise ProposalValidationError(f"New section targets an unapproved document path: {path}")
			headings = existing_sections if existing_sections is not None else allowed_sections
			if any(existing.casefold() == section.casefold() for existing in headings.get(path, set())):
				raise ProposalValidationError(f"New section already exists: {path} / {section}")
		elif path not in allowed_sections or section not in allowed_sections[path]:
			raise ProposalValidationError(f"Change targets an unapproved document section: {path} / {section}")
		if target in seen_targets:
			raise ProposalValidationError(f"Duplicate change target: {path} / {section}")
		has_plausible_evidence = False
		for ev in evidence:
			# A piece of evidence is plausible if it looks like a path, endpoint, or is found in the refs
			if "/" in ev or "." in ev or any(ev.lower() in ref.lower() for ref in allowed_evidence_refs):
				has_plausible_evidence = True
				break
		
		if not has_plausible_evidence:
			raise ProposalValidationError(f"Change has missing or implausible evidence: {path} / {section}")

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
	if not isinstance(value, list) or any(
		not isinstance(item, str) or not item.strip() for item in value
	):
		raise ProposalValidationError(f"'{field}' must be a list of strings")
	return tuple(item.strip() for item in value)
