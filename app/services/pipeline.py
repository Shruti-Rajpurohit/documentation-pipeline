from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from app.change_analyzer import analyze_change
from app.document_finder import DocumentSearchLimitError, DocumentSection, find_candidate_sections
from app.git_tools import collect_git_diff
from app.llm_client import DocumentationProposal, build_documentation_prompt, parse_proposal
from app.models.pipeline import PipelineDraft, PipelineOutcome
from app.patch_writer import PreparedPatch, prepare_patch


PROMPT_VERSION = "v1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "documentation_update.txt"


class TextGenerator(Protocol):
	def generate(self, prompt: str) -> str: ...


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
			selected_sections: dict[tuple[str, str], DocumentSection] = {}
			for section in sections:
				selected_sections.setdefault((section.path, section.heading), section)
			sections = list(selected_sections.values())
			if not sections:
				outcome = "human_investigation"
				note = "No unambiguous documentation sections matched the changed identifiers."
			else:
				template = prompt_template if prompt_template is not None else PROMPT_PATH.read_text(encoding="utf-8")
				prompt = build_documentation_prompt(template, analysis, source_diff.patch, sections)
				raw_response = model_client.generate(prompt)
				allowed_sections: dict[str, set[str]] = {}
				for section in sections:
					allowed_sections.setdefault(section.path, set()).add(section.heading)
				proposal = parse_proposal(raw_response, allowed_sections, set(analysis.evidence_refs))
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
				if proposal.decision == "update_required":
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