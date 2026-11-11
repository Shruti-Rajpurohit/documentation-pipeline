from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_current_user, require_roles
from app.api.schemas import (
	CreateSessionRequest,
	EditRequest,
	RejectRequest,
	ReviewSessionResponse,
	ReviewSessionSummaryResponse,
	SessionActionResponse,
	UserResponse,
)
from app.db.database import get_db
from app.db.models import (
	EditAuditEvent,
	Proposal as ProposalRecord,
	ReviewSession as ReviewSessionRecord,
	User,
)
from app.document_finder import DocumentSearchLimitError, classify_document_kind
from app.git_tools import GitCommandError, InvalidRevisionError
from app.llm_client import (
	LLMConfigurationError,
	ModelRequestError,
	ProposalValidationError,
	get_llm_client,
)
from app.models.types import ReviewStatus, UserRole
from app.patch_writer import PatchError, StalePatchError
from app.services.pipeline import analyze_repository_change
from app.services.repositories import RepositoryAccessError, RepositoryConfigurationError, validate_repository_paths
from app.services.review import (
	apply_review_patch,
	persist_review_draft,
	rebuild_review_patch,
	restore_patch_snapshot,
	serialize_patch_snapshot,
)


router = APIRouter(prefix="/sessions", tags=["review sessions"])
Database = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[User, Depends(get_current_user)]
Writer = Annotated[User, Depends(require_roles(UserRole.WRITER))]
Editor = Annotated[User, Depends(require_roles(UserRole.WRITER, UserRole.REVIEWER))]
Reviewer = Annotated[User, Depends(require_roles(UserRole.REVIEWER, UserRole.APPROVER))]
Approver = Annotated[User, Depends(require_roles(UserRole.REVIEWER, UserRole.APPROVER))]


@router.get("", response_model=list[ReviewSessionSummaryResponse])
async def list_sessions(
	database: Database,
	actor: Reader,
	status_filter: ReviewStatus | None = Query(default=None, alias="status"),
	search: str | None = Query(default=None, min_length=1, max_length=100),
	limit: int = Query(default=50, ge=1, le=100),
	offset: int = Query(default=0, ge=0),
) -> list[ReviewSessionSummaryResponse]:
	statement = select(ReviewSessionRecord).options(selectinload(ReviewSessionRecord.creator))
	if status_filter is not None:
		statement = statement.where(ReviewSessionRecord.status == status_filter)
	if search is not None:
		search_pattern = f"%{search.strip()}%"
		statement = statement.where(
			or_(
				ReviewSessionRecord.source_commit.ilike(search_pattern),
				ReviewSessionRecord.head_commit.ilike(search_pattern),
			)
		)
	statement = statement.order_by(ReviewSessionRecord.updated_at.desc()).limit(limit).offset(offset)
	records = (await database.scalars(statement)).all()
	return [ReviewSessionSummaryResponse.model_validate(record) for record in records]


@router.get("/me", response_model=UserResponse)
async def current_user(actor: Reader) -> UserResponse:
	return UserResponse.model_validate(actor)


@router.post("", response_model=ReviewSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(payload: CreateSessionRequest, database: Database, actor: Writer) -> ReviewSessionResponse:
	try:
		source_repo, docs_repo = await asyncio.to_thread(
			validate_repository_paths,
			payload.source_repo,
			payload.docs_repo,
			os.getenv("REPOSITORY_ROOTS"),
		)
	except RepositoryConfigurationError as error:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
	except RepositoryAccessError as error:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
	except (OSError, ValueError) as error:
		raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Repository path is invalid") from error
	try:
		model_client = get_llm_client(
			provider=payload.provider or "ollama",
			model=payload.model_name,
			base_url=payload.ollama_url
		)
	except LLMConfigurationError as error:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
	try:
		draft = await analyze_repository_change(
			source_repo,
			docs_repo,
			payload.base_revision,
			payload.head_revision,
			model_client,
			model_name=payload.model_name,
			database=database,
			change_summary=payload.change_summary.strip() if payload.change_summary else None,
		)
	except ModelRequestError as error:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"{payload.provider.title()} model request failed: {error}",
		) from error
	except ProposalValidationError as error:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"Documentation proposal validation failed: {error}",
		) from error
	except (GitCommandError, InvalidRevisionError, DocumentSearchLimitError, PatchError, ValueError, OSError) as error:
		raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

	if draft.outcome != "awaiting_review" or draft.proposal is None or draft.prepared_patch is None:
		if draft.outcome == "human_investigation":
			reason = draft.note or "The system could not produce a verified documentation patch."
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail=f"Human investigation required: {reason}",
			)
		reason = draft.note or draft.analysis.rationale
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=f"No documentation patch was created: {reason}",
		)

	record = await persist_review_draft(
		database,
		draft,
		actor_id=actor.id,
		source_repo=source_repo,
		docs_repo=docs_repo,
		model_name=payload.model_name,
		change_summary=payload.change_summary.strip() if payload.change_summary else None,
	)
	return await _get_session_response(database, record.id)


@router.get("/{session_id}", response_model=ReviewSessionResponse)
async def get_session(session_id: UUID, database: Database, actor: Reader) -> ReviewSessionResponse:
	return await _get_session_response(database, session_id)


@router.post("/{session_id}/edit", response_model=ReviewSessionResponse)
async def edit_session(
	session_id: UUID,
	payload: EditRequest,
	database: Database,
	actor: Editor,
) -> ReviewSessionResponse:
	record = await _load_session(database, session_id)
	if record.status != ReviewStatus.AWAITING_REVIEW or record.proposal is None:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is not awaiting review")
	edit = next((item for item in record.proposal.edits if item.id == payload.document_edit_id), None)
	if edit is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document edit not found in this session")
	try:
		prepared_patch = await asyncio.to_thread(rebuild_review_patch, record, edit, payload.content)
	except (PatchError, ValueError) as error:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

	audit_event = EditAuditEvent(
		session_id=record.id,
		document_edit_id=edit.id,
		actor_id=actor.id,
		previous_content=edit.content,
		updated_content=payload.content,
	)
	record.audit_events.append(audit_event)
	edit.content = payload.content
	record.patch_snapshot = serialize_patch_snapshot(prepared_patch)
	record.updated_at = datetime.now(timezone.utc)
	await database.commit()
	return await _get_session_response(database, record.id)


@router.post("/{session_id}/approve", response_model=SessionActionResponse)
async def approve_session(
	session_id: UUID,
	database: Database,
	actor: Approver,
) -> SessionActionResponse:
	record = await _load_session(database, session_id)
	if record.status != ReviewStatus.AWAITING_REVIEW:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is not awaiting review")
	if record.patch_snapshot is None:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No patch available to approve")
	try:
		prepared_patch = restore_patch_snapshot(record.patch_snapshot)
		await apply_review_patch(record.docs_repo, prepared_patch)
	except StalePatchError as error:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
	except PatchError as error:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

	record.status = ReviewStatus.APPROVED
	record.reviewed_by = actor.id
	record.review_note = None
	record.updated_at = datetime.now(timezone.utc)
	await database.commit()
	await database.refresh(record)
	return SessionActionResponse.model_validate(record)


@router.post("/{session_id}/reject", response_model=SessionActionResponse)
async def reject_session(
	session_id: UUID,
	database: Database,
	actor: Reviewer,
	payload: RejectRequest | None = None,
) -> SessionActionResponse:
	record = await _load_session(database, session_id)
	if record.status != ReviewStatus.AWAITING_REVIEW:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is not awaiting review")
	record.status = ReviewStatus.REJECTED
	record.reviewed_by = actor.id
	record.review_note = payload.reason.strip() if payload is not None and payload.reason and payload.reason.strip() else None
	record.updated_at = datetime.now(timezone.utc)
	await database.commit()
	await database.refresh(record)
	return SessionActionResponse.model_validate(record)


async def _load_session(database: AsyncSession, session_id: UUID) -> ReviewSessionRecord:
	statement = (
		select(ReviewSessionRecord)
		.options(
			selectinload(ReviewSessionRecord.creator),
			selectinload(ReviewSessionRecord.proposal).selectinload(ProposalRecord.edits),
			selectinload(ReviewSessionRecord.audit_events).selectinload(EditAuditEvent.actor),
		)
		.where(ReviewSessionRecord.id == session_id)
	)
	record = await database.scalar(statement)
	if record is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review session not found")
	return record


async def _get_session_response(database: AsyncSession, session_id: UUID) -> ReviewSessionResponse:
	record = await _load_session(database, session_id)

	prepared_patch = None
	if record.patch_snapshot is not None:
		prepared_patch = restore_patch_snapshot(record.patch_snapshot)

	patch_diff = ""
	original_by_path = {}
	if prepared_patch is not None:
		patch_diff = "".join(file_patch.unified_diff for file_patch in prepared_patch.files)
		original_by_path = {file_patch.path: file_patch.before for file_patch in prepared_patch.files}

	response = ReviewSessionResponse.model_validate(record)
	proposal = response.proposal
	if proposal is not None:
		proposal = proposal.model_copy(
			update={
				"edits": [
					edit.model_copy(
						update={
							"original_content": original_by_path.get(edit.file_path, ""),
							"doc_kind": classify_document_kind(edit.file_path),
						}
					)
					for edit in proposal.edits
				]
			}
		)
	audit_events = [
		event.model_copy(update={"actor_username": record.audit_events[index].actor.username})
		for index, event in enumerate(response.audit_events)
	]
	return response.model_copy(
		update={"patch_diff": patch_diff, "proposal": proposal, "audit_events": audit_events}
	)