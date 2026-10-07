from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from app.change_analyzer import analyze_change
from app.document_finder import DocumentSearchLimitError, DocumentSection, find_candidate_sections
from app.git_tools import collect_git_diff
from app.llm_client import (
	DocumentationProposal,
	OllamaClient,
	build_documentation_prompt,
	parse_proposal,
)
from app.patch_writer import prepare_patch
from app.review_server import ReviewDraft, ReviewSession, serve_review


PROMPT_VERSION = "v1"
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "documentation_update.txt"


class TextGenerator(Protocol):
	def generate(self, prompt: str) -> str: ...


def create_review_session(
	source_repo: str | Path,
	docs_repo: str | Path,
	base_revision: str,
	head_revision: str,
	model_client: TextGenerator,
	*,
	prompt_template: str | None = None,
	model_name: str = "configured model",
) -> ReviewSession:
	source_diff = collect_git_diff(source_repo, base_revision, head_revision)
	analysis = analyze_change(source_diff)
	sections: list[DocumentSection] = []
	proposal: DocumentationProposal | None = None
	prepared_patch = None
	status = analysis.decision
	note = ""

	if analysis.decision == "update_required":
		try:
			sections = find_candidate_sections(docs_repo, source_diff.changed_files, source_diff.patch)
		except DocumentSearchLimitError as error:
			sections = []
			status = "human_investigation"
			note = str(error)
		if status != "human_investigation":
			selected_sections: dict[tuple[str, str], DocumentSection] = {}
			for section in sections:
				selected_sections.setdefault((section.path, section.heading), section)
			sections = list(selected_sections.values())
			if not sections:
				status = "human_investigation"
				note = "No unambiguous documentation sections matched the changed identifiers."
			else:
				template = prompt_template if prompt_template is not None else PROMPT_PATH.read_text(encoding="utf-8")
				prompt = build_documentation_prompt(template, analysis, source_diff.patch, sections)
				response = model_client.generate(prompt)
				allowed_sections: dict[str, set[str]] = {}
				for section in sections:
					allowed_sections.setdefault(section.path, set()).add(section.heading)
				proposal = parse_proposal(response, allowed_sections, set(analysis.evidence_refs))
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
					status = "awaiting_review"
				else:
					status = proposal.decision
					note = "The model did not propose a documentation patch."

	return ReviewSession(
		ReviewDraft(
			source_diff=source_diff,
			analysis=analysis,
			sections=sections,
			proposal=proposal,
			prepared_patch=prepared_patch,
			target_repo=Path(docs_repo).resolve(),
			status=status,
			note=note,
			model_name=model_name,
			prompt_version=PROMPT_VERSION,
		)
	)


def main() -> None:
	parser = argparse.ArgumentParser(description="Create a human-reviewable documentation patch from a Git diff.")
	parser.add_argument("--source-repo", required=True, help="Path to the product source repository")
	parser.add_argument("--docs-repo", required=True, help="Path to the documentation working tree")
	parser.add_argument("--base", required=True, help="Base commit or ref to compare")
	parser.add_argument("--head", default="HEAD", help="Head commit or ref (default: HEAD)")
	parser.add_argument("--model", required=True, help="Pinned model name available in Ollama")
	parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama base URL")
	parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind address (loopback only)")
	parser.add_argument("--port", type=int, default=8765, help="Dashboard port")
	args = parser.parse_args()

	model_client = OllamaClient(model=args.model, base_url=args.ollama_url)
	session = create_review_session(
		args.source_repo,
		args.docs_repo,
		args.base,
		args.head,
		model_client,
		model_name=args.model,
	)
	serve_review(session, host=args.host, port=args.port)


if __name__ == "__main__":
	main()