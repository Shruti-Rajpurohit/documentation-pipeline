from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection
from app.git_tools import GitDiff
from app.llm_client import DocumentationProposal
from app.patch_writer import PreparedPatch


PipelineOutcome = Literal["awaiting_review", "no_update", "human_investigation"]


@dataclass(frozen=True)
class PipelineDraft:
	source_diff: GitDiff
	analysis: ChangeAnalysis
	sections: tuple[DocumentSection, ...]
	proposal: DocumentationProposal | None
	prepared_patch: PreparedPatch | None
	raw_response: str | None
	outcome: PipelineOutcome
	note: str = ""