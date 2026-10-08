from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.types import EditAction, ProposalDecision, ReviewStatus, UserRole


class CreateSessionRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")

	source_repo: str = Field(min_length=1, max_length=4096)
	docs_repo: str = Field(min_length=1, max_length=4096)
	base_revision: str = Field(min_length=1, max_length=200)
	head_revision: str = Field(default="HEAD", min_length=1, max_length=200)
	model_name: str = Field(min_length=1, max_length=200)
	ollama_url: str = Field(default="http://127.0.0.1:11434", max_length=2048)


class EditRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")

	document_edit_id: int = Field(gt=0)
	content: str = Field(min_length=1, max_length=1_000_000)


class RejectRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")

	reason: str | None = Field(default=None, max_length=2000)


class SessionActionResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: UUID
	status: ReviewStatus
	updated_at: datetime
	reviewed_by: int | None
	review_note: str | None


class DocumentEditResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: int
	file_path: str
	section_heading: str
	action: EditAction
	content: str
	original_content: str = ""
	evidence: list[str]
	start_line: int | None


class ProposalResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: int
	model_name: str
	prompt_version: str
	decision: ProposalDecision
	rationale: str
	raw_response: str
	edits: list[DocumentEditResponse]


class UserResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: int
	username: str
	role: UserRole



class ReviewSessionSummaryResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: UUID
	source_commit: str
	head_commit: str
	status: ReviewStatus
	created_by: int
	updated_at: datetime
	creator: UserResponse


class AuditEventResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: int
	document_edit_id: int
	actor_id: int
	actor_username: str = ""
	previous_content: str
	updated_content: str
	created_at: datetime


class ReviewSessionResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: UUID
	source_commit: str
	head_commit: str
	status: ReviewStatus
	created_by: int
	updated_at: datetime
	reviewed_by: int | None
	review_note: str | None
	source_diff: str
	changed_files: list[str]
	evidence_refs: list[str]
	analysis_decision: str
	analysis_rationale: str
	patch_diff: str | None = None
	creator: UserResponse
	proposal: ProposalResponse | None
	audit_events: list[AuditEventResponse]


class ErrorResponse(BaseModel):
	detail: str


class ValidationIssue(BaseModel):
	location: tuple[str | int, ...]
	message: str
	type: str


class ValidationErrorResponse(ErrorResponse):
	errors: list[ValidationIssue]