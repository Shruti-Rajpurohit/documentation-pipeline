from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from string import Template
from typing import Protocol

from app.change_analyzer import analyze_change
from app.document_finder import DocumentSearchLimitError, DocumentSection, find_candidate_sections
from app.git_tools import GitDiff, collect_git_diff
from app.llm_client import (
	DocumentationProposal,
	ProposalValidationError,
	parse_proposal,
)
from app.models.pipeline import PipelineDraft, PipelineOutcome
from app.patch_writer import PreparedPatch, prepare_patch


PROMPT_VERSION = "v2"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "documentation_update.txt"
HALLUCINATION_NOTE = "AI hallucinated content not found in source code."
_WORD_PATTERN = re.compile(r"[a-z]+|\d+")
_CAMEL_CASE_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_GENERIC_DOCUMENTATION_WORDS = {
	"a", "about", "added", "addition", "after", "all", "an", "and", "any", "api",
	"are", "as", "at", "be", "because", "before", "between", "by", "call", "calling",
	"can", "code", "currently", "does", "each", "enables", "existing", "feature",
	"for", "from", "function", "functions", "has", "have", "helps", "if", "in", "include", "including",
	"into", "is", "it", "its", "now", "of", "on", "or", "pass", "previously", "provides",
	"require", "requires", "supports", "the", "their", "this", "to", "up", "user", "users", "using", "via",
	"was", "when", "with", "within",
}
_FEW_SHOT_EXAMPLES = """\
### Style examples
Change: Image support increased from 1 to 3 images
Update: "User can now upload up to 3 images for analysis."

Change: Added "Forgot Password" feature
Update: "Added password reset flow via email link."

Change: Added multiple chat support
Update: "Users can now maintain multiple independent chat threads."
"""


class TextGenerator(Protocol):
	def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class ImpactReport:
	changed_functions_endpoints: tuple[str, ...]
	technical_summary: str
	documentation_sections_to_update: tuple[str, ...]


class ImpactReportValidationError(ProposalValidationError):
	pass


def analyze_change_impact(source_diff: GitDiff, model_client: TextGenerator) -> ImpactReport:
	prompt = f"""\
You are a code-change analyst. Inspect only the source diff below and extract facts.
Do not write user-facing documentation or infer behavior not established by the diff.
Return only a JSON object with exactly these keys:
- "changed_functions_endpoints": an array naming each changed function or endpoint; use [] if none.
- "technical_summary": one concise, technical, factual summary of the change.
- "documentation_sections_to_update": an array of documentation topics that the diff requires updating.

SOURCE DIFF:
{source_diff.patch}
"""
	return _parse_impact_report(model_client.generate(prompt))


def generate_doc_proposal(
	impact_report: ImpactReport,
	sections: list[DocumentSection],
	model_client: TextGenerator,
	allowed_evidence_refs: set[str],
	*,
	prompt_template: str | None = None,
) -> tuple[DocumentationProposal, str]:
	template = prompt_template if prompt_template is not None else PROMPT_PATH.read_text(encoding="utf-8")
	source_context = json.dumps(
		{
			"changed_functions_endpoints": impact_report.changed_functions_endpoints,
			"technical_summary": impact_report.technical_summary,
			"documentation_sections_to_update": impact_report.documentation_sections_to_update,
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
	prompt = f"{prompt.rstrip()}\n\n{_FEW_SHOT_EXAMPLES}"
	raw_response = model_client.generate(prompt)
	allowed_sections: dict[str, set[str]] = {}
	for section in sections:
		allowed_sections.setdefault(section.path, set()).add(section.heading)
	return (
		parse_proposal(raw_response, allowed_sections, allowed_evidence_refs),
		raw_response,
	)


def validate_proposal(
	proposal: DocumentationProposal,
	source_diff: GitDiff,
) -> DocumentationProposal:
	if proposal.decision != "update_required":
		return proposal

	source_tokens: set[str] = set()
	source_numbers: set[str] = set()
	for line in source_diff.patch.splitlines():
		if line.startswith(("+++", "---")):
			continue
		if line.startswith(("+", " ")):
			tokens, numbers = _facts_from_text(line[1:])
			source_tokens.update(tokens)
			source_numbers.update(numbers)
	for path in source_diff.changed_files:
		tokens, numbers = _facts_from_text(path)
		source_tokens.update(tokens)
		source_numbers.update(numbers)

	proposed_text = " ".join(
		[proposal.rationale, *(change.content for change in proposal.changes)]
	)
	proposed_tokens, proposed_numbers = _facts_from_text(proposed_text)
	unsupported_tokens = proposed_tokens - source_tokens - _GENERIC_DOCUMENTATION_WORDS
	unsupported_numbers = proposed_numbers - source_numbers
	if unsupported_tokens or unsupported_numbers:
		return replace(
			proposal,
			decision="human_investigation",
			changes=(),
			uncertainties=(*proposal.uncertainties, HALLUCINATION_NOTE),
		)
	return proposal


def build_pipeline_draft(
	source_repo: str | Path,
	docs_repo: str | Path,
	base_revision: str,
	head_revision: str,
	model_client: TextGenerator,
	*,
	prompt_template: str | None = None,
	model_name: str = "configured model",
) -> PipelineDraft:
	source_diff = collect_git_diff(source_repo, base_revision, head_revision)
	analysis = analyze_change(source_diff)
	sections: list[DocumentSection] = []
	proposal: DocumentationProposal | None = None
	prepared_patch: PreparedPatch | None = None
	raw_response: str | None = None
	outcome: PipelineOutcome = analysis.decision
	note = ""

	if analysis.decision == "update_required":
		impact_report = analyze_change_impact(source_diff, model_client)
		try:
			sections = find_candidate_sections(docs_repo, source_diff.changed_files, source_diff.patch)
		except DocumentSearchLimitError as error:
			outcome = "human_investigation"
			note = str(error)
		if outcome != "human_investigation":
			selected_sections: dict[tuple[str, str], DocumentSection] = {}
			for section in sections:
				selected_sections.setdefault((section.path, section.heading), section)
			sections = list(selected_sections.values())
			if not sections:
				outcome = "human_investigation"
				note = "No unambiguous documentation sections matched the changed identifiers."
			else:
				proposal, raw_response = generate_doc_proposal(
					impact_report,
					sections,
					model_client,
					set(analysis.evidence_refs),
					prompt_template=prompt_template,
				)
				proposal = validate_proposal(proposal, source_diff)
				if HALLUCINATION_NOTE in proposal.uncertainties:
					outcome = "human_investigation"
					note = HALLUCINATION_NOTE
				elif proposal.decision == "update_required":
					section_lines = {
						(section.path, section.heading): section.start_line for section in sections
					}
					proposal = replace(
						proposal,
						changes=tuple(
							replace(change, start_line=section_lines[(change.path, change.section)])
							for change in proposal.changes
						),
					)
					prepared_patch = prepare_patch(docs_repo, proposal.changes)
					outcome = "awaiting_review"
				else:
					outcome = proposal.decision
					note = "The model did not propose a documentation patch."

	return PipelineDraft(
		source_diff=source_diff,
		analysis=analysis,
		sections=tuple(sections),
		proposal=proposal,
		prepared_patch=prepared_patch,
		raw_response=raw_response,
		outcome=outcome,
		note=note,
	)


def _parse_impact_report(response_text: str) -> ImpactReport:
	try:
		payload = json.loads(response_text)
	except json.JSONDecodeError as error:
		raise ImpactReportValidationError("Impact report must be valid JSON") from error
	if not isinstance(payload, dict) or set(payload) != {
		"changed_functions_endpoints",
		"technical_summary",
		"documentation_sections_to_update",
	}:
		raise ImpactReportValidationError("Impact report must contain exactly the required fields")

	changed_functions = _string_tuple(payload, "changed_functions_endpoints")
	technical_summary = payload.get("technical_summary")
	if not isinstance(technical_summary, str) or not technical_summary.strip():
		raise ImpactReportValidationError("Impact report technical_summary must be a non-empty string")
	documentation_sections = _string_tuple(payload, "documentation_sections_to_update")
	return ImpactReport(
		changed_functions_endpoints=changed_functions,
		technical_summary=technical_summary.strip(),
		documentation_sections_to_update=documentation_sections,
	)


def _string_tuple(payload: dict[str, object], field: str) -> tuple[str, ...]:
	value = payload.get(field)
	if not isinstance(value, list) or any(
		not isinstance(item, str) or not item.strip() for item in value
	):
		raise ImpactReportValidationError(f"Impact report {field} must be a list of non-empty strings")
	return tuple(item.strip() for item in value)


def _facts_from_text(text: str) -> tuple[set[str], set[str]]:
	words = _WORD_PATTERN.findall(_CAMEL_CASE_BOUNDARY.sub(r"\1 \2", text).lower())
	tokens = {_normalize_fact(word) for word in words if not word.isdigit()}
	numbers = {word for word in words if word.isdigit()}
	return tokens, numbers


def _normalize_fact(word: str) -> str:
	if len(word) > 4 and word.endswith("ies"):
		return word[:-3] + "y"
	if len(word) > 3 and word.endswith(("ches", "shes", "xes", "zes")):
		return word[:-2]
	if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
		return word[:-1]
	return word


async def analyze_repository_change(
	source_repo: str | Path,
	docs_repo: str | Path,
	base_revision: str,
	head_revision: str,
	model_client: TextGenerator,
	*,
	prompt_template: str | None = None,
	model_name: str = "configured model",
) -> PipelineDraft:
	return await asyncio.to_thread(
		build_pipeline_draft,
		source_repo,
		docs_repo,
		base_revision,
		head_revision,
		model_client,
		prompt_template=prompt_template,
		model_name=model_name,
	)
