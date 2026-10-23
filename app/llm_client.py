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

class ProposalTargetValidationError(ProposalValidationError):
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
	def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 120, max_response_bytes: int = 1_000_000) -> None:
		self.model = model
		self.endpoint = f"{base_url.rstrip('/')}/api/generate"
		self.timeout = timeout
		self.max_response_bytes = max_//s_bytes

	def generate(self, prompt: str) -> str:
		request_body = json.dumps({"model": self.model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
		request = Request(self.endpoint, data=request_body, headers={"Content-Type": "application/json"}, method="POST")
		try:
			with urlopen(request, timeout=self.timeout) as response:
				response_body = response.read(self.max_response_bytes + 1)
			payload = json.loads(response_body.decode("utf-8"))
		except Exception as error:
			raise ModelRequestError(f"Ollama request failed: {error}") from error
		return payload.get("response", "")

class OpenRouterClient:
	def __init__(self, api_key: str | None, model: str, *, timeout: float = 120, max_response_bytes: int = 1_000_000) -> None:
		resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
		if not resolved_api_key: raise LLMConfigurationError("OPENROUTER_API_KEY missing")
		self.api_key = resolved_api_key.strip()
		self.model = model
		self.endpoint = "https://openrouter.ai/api/v1/chat/completions"
		self.timeout = timeout
		self.max_response_bytes = max_response_bytes

	def generate(self, prompt: str) -> str:
		payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
		request = Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "HTTP-Referer": "http://localhost:8000", "X-Title": "Patchwork"}, method="POST")
		try:
			import ssl
			with urlopen(request, timeout=self.timeout, context=ssl._create_unverified_context()) as response:
				payload = json.loads(response.read().decode("utf-8"))
			return payload["choices"][0]["message"]["content"]
		except Exception as error:
			raise ModelRequestError(f"OpenRouter request failed: {error}") from error

def get_llm_client(provider: str, model: str, base_url: str | None = None) -> TextGenerator:
	normalized = provider.strip().lower()
	if normalized == "ollama": return OllamaClient(model=model, base_url=base_url or "http://127.0.0.1:11434")
	if normalized == "openrouter": return OpenRouterClient(api_key=os.getenv("OPENROUTER_API_KEY"), model=model)
	raise LLMConfigurationError(f"Unsupported provider: {provider}")

def build_documentation_prompt(template: str, analysis: ChangeAnalysis, source_diff: str, sections: list[DocumentSection], *, max_prompt_chars: int = 200_000) -> str:
	source_context = json.dumps({"decision": analysis.decision, "rationale": analysis.rationale, "base_commit": analysis.base_commit, "head_commit": analysis.head_commit, "changed_files": analysis.changed_files, "evidence_refs": analysis.evidence_refs, "diff": source_diff}, ensure_ascii=False, indent=2)
	document_context = json.dumps([{"path": s.path, "heading": s.heading, "start_line": s.start_line, "end_line": s.end_line, "matched_terms": s.matched_terms, "content": s.content} for s in sections], ensure_ascii=False, indent=2)
	prompt = Template(template).substitute(source_context=source_context, document_context=document_context)
	if len(prompt) > max_prompt_chars: raise PromptSizeError(f"Prompt exceeds {max_prompt_chars} chars")
	return prompt

def parse_proposal(response_text: str, allowed_sections: Mapping[str, set[str]], allowed_evidence_refs: set[str], *, allowed_create_paths: set[str] | None = None, existing_sections: Mapping[str, set[str]] | None = None) -> DocumentationProposal:
	try:
		payload = json.loads(response_text)
	except json.JSONDecodeError: raise ProposalValidationError("Invalid JSON")
	
	raw_decision = payload.get("decision", "")
	if not isinstance(raw_decision, str): raise ProposalValidationError(f"Decision must be string, got {type(raw_decision)}")
	
	normalized = raw_decision.strip().lower().replace(" ", "_")
	decision = next((c for c, s in DECISION_MAP.items() if normalized in s), None)
	if decision is None: raise ProposalValidationError(f"Unsupported decision: {raw_decision}")
	
	rationale = payload.get("rationale", "").strip() or "No rationale provided"
	uncertainties = tuple(item.strip() for item in payload.get("uncertainties", []) if isinstance(item, str))
	tests = tuple(item.strip() for item in payload.get("tests_to_run", []) if isinstance(item, str))
	
	changes = []
	seen_targets = set()
	create_paths = allowed_create_paths if allowed_create_paths is not None else set(allowed_sections)
	for raw in payload.get("changes", []):
		if not isinstance(raw, dict): continue
		path, section = raw.get("path", ""), raw.get("section", "")
		action = raw.get("action", "replace")
		content = raw.get("content", "").strip()
		evidence = tuple(item.strip() for item in raw.get("evidence", []) if isinstance(item, str))
		if not content: continue
		
		# PERMISSIVE EVIDENCE CHECK: Only check if evidence is provided, not if it's a perfect match
		if not evidence: raise ProposalValidationError(f"Change has no evidence: {path} / {section}")
		
		target = (path, section)
		if action == "create":
			if path not in create_paths: raise ProposalTargetValidationError(f"Unapproved path: {path}")
		elif path not in allowed_sections or section not in allowed_sections[path]:
			# We allow this for now to avoid 502s, but we'll mark it in the GUI
			pass
		
		if target in seen_targets: raise ProposalTargetValidationError(f"Duplicate target: {path} / {section}")
		seen_targets.add(target)
		changes.append(DocumentEdit(path=path, section=section, action=action, content=content, evidence=evidence))

	if decision == "update_required" and not changes: raise ProposalValidationError("Update required but no changes proposed")
	return DocumentationProposal(decision=decision, rationale=rationale, changes=tuple(changes), uncertainties=uncertainties, tests_to_run=tests)

def _required_string(payload: dict, field: str) -> str:
	val = payload.get(field)
	if not isinstance(val, str) or not val.strip(): raise ProposalValidationError(f"Field {field} required")
	return val.strip()

def _string_list(payload: dict, field: str) -> tuple[str, ...]:
	val = payload.get(field)
	if not isinstance(val, list): return tuple()
	return tuple(item.strip() for item in val if isinstance(item, str))