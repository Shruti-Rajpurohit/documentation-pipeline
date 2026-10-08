from __future__ import annotations

import asyncio
from typing import Any

from app.db.models import DocumentEdit as DocumentEditRecord, ReviewSession as ReviewSessionRecord
from app.llm_client import DocumentEdit as ProposedDocumentEdit
from app.patch_writer import (
	FilePatch,
	PatchError,
	PreparedPatch,
	apply_approved_patch,
	prepare_patch_from_originals,
)


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