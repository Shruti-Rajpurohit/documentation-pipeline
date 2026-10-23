from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path
from string import Template

from app.change_analyzer import analyze_change
from app.document_finder import (
	DocumentSearchLimitError,
	DocumentSection,
	find_candidate_sections,
	list_document_section_index,
)
from app.git_tools import GitDiff, collect_git_diff
from app.llm_client import (
	DocumentationProposal,
	ProposalValidationError,
	TextGenerator,
	parse_proposal,
)
from app.models.pipeline import PipelineDraft, PipelineOutcome
from app.patch_writer import PreparedPatch, prepare_patch


PROMPT_VERSION = "v3"
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


def analyze_change_impact(
	source_diff: GitDiff,
	document_context: list[DocumentSection],
	model_client: TextGenerator,
) -> str:
	document_index = json.dumps(
		[
			{"path": section.path, "heading": section.heading, "content": section.content}
			for section in document_context
		],
		ensure_ascii=False,
		indent=2,
	)
	prompt = f"""\
You are a code-change analyst. Inspect only the source diff below and extract facts.
Do not write user-facing documentation or infer behavior not established by the diff.
Compare changed features and endpoints with DOCUMENT_CONTEXT. Some entries contain only an existing file path and heading, with empty content; these are an index, not section content. If a feature requires documentation but has no matching existing section, report decision "update_required" and set "create_new_section" to true. Otherwise set it to false.
Return a JSON object containing these fields; extra fields are allowed:
- "decision": "update_required", "no_update", or "human_investigation".
- "create_new_section": true when a new documentation section is needed, otherwise false.
- "changed_functions_endpoints": an array naming each changed function or endpoint; use [] if none.
- "technical_summary": one concise, technical, factual summary of the change.
- "documentation_sections_to_update": an array of documentation topics that the diff requires updating.

SOURCE DIFF:
{source_diff.patch}

DOCUMENT_CONTEXT:
{document_index}
"""
	return model_client.generate(prompt)


def _creation_requested(impact_report: str) -> bool:
	try:
		report = json.loads(impact_report)
	except json.JSONDecodeError:
		return False
	return isinstance(report, dict) and report.get("create_new_section") is True


def generate_doc_proposal(
	impact_report: str,
	sections: list[DocumentSection],
	model_client: TextGenerator,
	allowed_evidence_refs: set[str],
	*,
	prompt_template: str | None = None,
	validation_feedback: str | None = None,
) -> tuple[DocumentationProposal, str]:
	template = prompt_template if prompt_template is not None else PROMPT_PATH.read_text(encoding="utf-8")
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
		source_context=impact_report,
		document_context=document_context,
	)
	allowed_sections: dict[str, set[str]] = {}
	existing_sections: dict[str, set[str]] = {}
	for section in sections:
		existing_sections.setdefault(section.path, set()).add(section.heading)
		if section.matched_terms:
			allowed_sections.setdefault(section.path, set()).add(section.heading)
	allowed_targets = json.dumps(
		{
			"replace_or_append": [
				{"path": path, "heading": heading}
				for path, headings in allowed_sections.items()
				for heading in sorted(headings)
			],
			"create_in_existing_files": sorted({section.path for section in sections}),
		},
		ensure_ascii=False,
		indent=2,
	)
	prompt = (
		f"{prompt.rstrip()}\n\n"
		"DOCUMENT_CONTEXT entries with empty content are an index only. Use their path "
		"and heading only to choose a file for a create action; do not replace or append "
		"to those sections. Use create only when the impact report sets "
		'"create_new_section": true, and use a genuinely new heading.\n\n'
		"AUTHORIZED TARGETS (follow exactly):\n"
		f"{allowed_targets}\n"
		"Use replace/append only for a path and heading listed under replace_or_append. "
		"Use create only for a genuinely new heading in a path listed under create_in_existing_files."
	)
	if validation_feedback is not None:
		prompt = (
			f"{prompt}\n\n"
			"The previous response was rejected by proposal validation: "
			f"{validation_feedback}\n"
			"Return a corrected JSON proposal. Do not repeat the rejected target."
		)
	prompt = f"{prompt}\n\n{_FEW_SHOT_EXAMPLES}"
	raw_response = model_client.generate(prompt)
	return (
		parse_proposal(
			raw_response,
			allowed_sections,
			allowed_evidence_refs,
			allowed_create_paths={section.path for section in sections},
			existing_sections=existing_sections,
		),
		raw_response,
	)


def _generate_doc_proposal_with_retry(
	impact_report: str,
	sections: list[DocumentSection],
	model_client: TextGenerator,
	allowed_evidence_refs: set[str],
	*,
	prompt_template: str | None = None,
) -> tuple[DocumentationProposal | None, str | None, str | None]:
	try:
		proposal, raw_response = generate_doc_proposal(
			impact_report,
			sections,
			model_client,
			allowed_evidence_refs,
			prompt_template=prompt_template,
		)
		return proposal, raw_response, None
	except ProposalValidationError as first_error:
		try:
			proposal, raw_response = generate_doc_proposal(
				impact_report,
				sections,
				model_client,
				allowed_evidence_refs,
				prompt_template=prompt_template,
				validation_feedback=str(first_error),
			)
			return proposal, raw_response, None
		except ProposalValidationError as retry_error:
			return (
				None,
				None,
				"Writer proposal remained invalid after one constrained retry; "
				f"human investigation is required. {retry_error}",
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
		try:
			sections = find_candidate_sections(docs_repo, source_diff.changed_files, source_diff.patch)
		except DocumentSearchLimitError as error:
			outcome = "human_investigation"
			note = str(error)
		if outcome != "human_investigation":
			matching_section_map: dict[tuple[str, str], DocumentSection] = {}
			for section in sections:
				matching_section_map.setdefault((section.path, section.heading), section)
			matching_sections = list(matching_section_map.values())
			try:
				document_index = list_document_section_index(docs_repo)
			except DocumentSearchLimitError as error:
				outcome = "human_investigation"
				note = str(error)
			document_context_map: dict[tuple[str, str], DocumentSection] = {}
			if outcome != "human_investigation":
				document_context_map = {
					(section.path, section.heading): section
					for section in document_index
				}
			for section in matching_sections:
				document_context_map[(section.path, section.heading)] = section
			document_context = list(document_context_map.values())
			if outcome != "human_investigation":
				impact_report = analyze_change_impact(source_diff, document_context, model_client)
				create_new_section = _creation_requested(impact_report)
			else:
				create_new_section = False
			selected_sections: dict[tuple[str, str], DocumentSection] = {}
			for section in document_context:
				selected_sections.setdefault((section.path, section.heading), section)
			sections = list(selected_sections.values())
			if (
				outcome != "human_investigation"
				and not matching_sections
				and (not create_new_section or not sections)
			):
				outcome = "human_investigation"
				note = (
					"No existing documentation file is available for a new section."
					if create_new_section and not sections
					else "No unambiguous documentation sections matched the changed identifiers."
				)
			elif outcome != "human_investigation":
				proposal, raw_response, proposal_error = _generate_doc_proposal_with_retry(
					impact_report,
					sections,
					model_client,
					set(analysis.evidence_refs),
					prompt_template=prompt_template,
				)
				if proposal_error is not None:
					outcome = "human_investigation"
					note = proposal_error
				else:
					assert proposal is not None
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
								replace(
									change,
									start_line=(
										None
										if change.action == "create"
										else section_lines[(change.path, change.section)]
									),
								)
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
