from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
	DocumentEdit as DocumentEditRecord,
	Proposal as ProposalRecord,
	ReviewSession as ReviewSessionRecord,
)
from app.llm_client import DocumentEdit as ProposedDocumentEdit
from app.models.pipeline import PipelineDraft
from app.models.types import EditAction, ProposalDecision, ReviewStatus
from app.patch_writer import (
	FilePatch,
	PatchError,
	PreparedPatch,
	apply_approved_patch,
	prepare_patch_from_originals,
)
from app.services.pipeline import PROMPT_VERSION


def serialize_patch_snapshot(prepared_patch: PreparedPatch) -> list[dict[str, str]]:
	return [
		{
			"path": file_patch.path,
			"original_sha256": file_patch.original_sha256,
			"before": file_patch.before,
			"after": file_patch.after,
			"unified_diff": file_patch.unified_diff,
		}
		for file_patch in prepared_patch.files
	]


def restore_patch_snapshot(snapshot: Any) -> PreparedPatch:
	if not isinstance(snapshot, list) or not snapshot:
		raise PatchError("The persisted patch snapshot is missing or invalid")
	files: list[FilePatch] = []
	for row in snapshot:
		if not isinstance(row, dict) or any(
			not isinstance(row.get(field), str)
			for field in ("path", "original_sha256", "before", "after", "unified_diff")
		):
			raise PatchError("The persisted patch snapshot is invalid")
		files.append(
			FilePatch(
				path=row["path"],
				original_sha256=row["original_sha256"],
				before=row["before"],
				after=row["after"],
				unified_diff=row["unified_diff"],
			)
		)
	return PreparedPatch(files=tuple(files))


def rebuild_review_patch(
	session: ReviewSessionRecord,
	edited_document: DocumentEditRecord | None = None,
	new_content: str | None = None,
) -> PreparedPatch:
	if session.proposal is None or session.patch_snapshot is None:
		raise PatchError("The review does not have a persisted patch snapshot")
	if (edited_document is None) != (new_content is None):
		raise ValueError("An edited document and new content must be supplied together")

	original_files = {
		file_patch.path: (file_patch.before, file_patch.original_sha256)
		for file_patch in restore_patch_snapshot(session.patch_snapshot).files
	}
	edits = [
		ProposedDocumentEdit(
			path=edit.file_path,
			section=edit.section_heading,
			action=edit.action.value,
			content=new_content if edited_document is not None and edit.id == edited_document.id else edit.content,
			evidence=tuple(edit.evidence),
			start_line=edit.start_line,
		)
		for edit in session.proposal.edits
	]
	return prepare_patch_from_originals(original_files, edits)


async def apply_review_patch(docs_repo: str, prepared_patch: PreparedPatch) -> None:
	await asyncio.to_thread(apply_approved_patch, docs_repo, prepared_patch, approved=True)


async def persist_review_draft(
	database: AsyncSession,
	draft: PipelineDraft,
	*,
	actor_id: int,
	source_repo: str,
	docs_repo: str,
	model_name: str,
	change_summary: str | None = None,
) -> ReviewSessionRecord:
	if draft.proposal is None or draft.prepared_patch is None:
		raise PatchError("Cannot persist a review without a prepared documentation patch")
	proposal = ProposalRecord(
		model_name=model_name,
		prompt_version=PROMPT_VERSION,
		decision=ProposalDecision(draft.proposal.decision),
		rationale=draft.proposal.rationale,
		raw_response=draft.raw_response or "",
		uncertainties=list(draft.proposal.uncertainties),
		tests_to_run=list(draft.proposal.tests_to_run),
		edits=[
			DocumentEditRecord(
				file_path=edit.path,
				section_heading=edit.section,
				action=EditAction(edit.action),
				content=edit.content,
				evidence=list(edit.evidence),
				start_line=edit.start_line,
			)
			for edit in draft.proposal.changes
		],
	)
	record = ReviewSessionRecord(
		source_commit=draft.source_diff.base_commit,
		head_commit=draft.source_diff.head_commit,
		status=ReviewStatus.AWAITING_REVIEW,
		created_by=actor_id,
		source_repo=source_repo,
		docs_repo=docs_repo,
		source_diff=draft.source_diff.patch,
		changed_files=list(draft.analysis.changed_files),
		evidence_refs=list(draft.analysis.evidence_refs),
		analysis_decision=draft.analysis.decision,
		analysis_rationale=draft.analysis.rationale,
		impact_statement=draft.impact_statement,
		change_summary=change_summary,
		patch_snapshot=serialize_patch_snapshot(draft.prepared_patch),
		proposal=proposal,
	)
	database.add(record)
	await database.commit()
	return record