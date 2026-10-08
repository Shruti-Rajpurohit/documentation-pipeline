from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import EditAction, ProposalDecision, ReviewStatus, UserRole


def _enum_values(enum_type: type[UserRole] | type[ReviewStatus] | type[ProposalDecision] | type[EditAction]) -> list[str]:
	return [member.value for member in enum_type]


class User(Base):
	__tablename__ = "users"

	id: Mapped[int] = mapped_column(primary_key=True)
	username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
	email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
	role: Mapped[UserRole] = mapped_column(
		SqlEnum(UserRole, name="user_role", values_callable=_enum_values),
		default=UserRole.WRITER,
		nullable=False,
	)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
	)

	sessions: Mapped[list[ReviewSession]] = relationship(
		back_populates="creator", foreign_keys="ReviewSession.created_by"
	)
	audit_events: Mapped[list[EditAuditEvent]] = relationship(back_populates="actor")


class ReviewSession(Base):
	__tablename__ = "review_sessions"

	id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
	source_commit: Mapped[str] = mapped_column(String(100), nullable=False)
	head_commit: Mapped[str] = mapped_column(String(100), nullable=False)
	status: Mapped[ReviewStatus] = mapped_column(
		SqlEnum(ReviewStatus, name="review_status", values_callable=_enum_values),
		default=ReviewStatus.AWAITING_REVIEW,
		nullable=False,
	)
	created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
	updated_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
	)
	# Persist enough context to render and approve a session after a process restart.
	source_repo: Mapped[str] = mapped_column(Text, nullable=False)
	docs_repo: Mapped[str] = mapped_column(Text, nullable=False)
	source_diff: Mapped[str] = mapped_column(Text, nullable=False)
	changed_files: Mapped[list[str]] = mapped_column(JSON, nullable=False)
	evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
	analysis_decision: Mapped[str] = mapped_column(String(40), nullable=False)
	analysis_rationale: Mapped[str] = mapped_column(Text, nullable=False)
	patch_snapshot: Mapped[list[dict[str, str | int | None]] | None] = mapped_column(JSON, nullable=True)
	reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
	review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

	creator: Mapped[User] = relationship(back_populates="sessions", foreign_keys=[created_by])
	reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])
	proposal: Mapped[Proposal | None] = relationship(
		back_populates="session", cascade="all, delete-orphan", uselist=False
	)
	audit_events: Mapped[list[EditAuditEvent]] = relationship(
		back_populates="session", cascade="all, delete-orphan", order_by="EditAuditEvent.created_at"
	)


class Proposal(Base):
	__tablename__ = "proposals"

	id: Mapped[int] = mapped_column(primary_key=True)
	session_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("review_sessions.id", ondelete="CASCADE"), unique=True, nullable=False
	)
	model_name: Mapped[str] = mapped_column(String(200), nullable=False)
	prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
	decision: Mapped[ProposalDecision] = mapped_column(
		SqlEnum(ProposalDecision, name="proposal_decision", values_callable=_enum_values), nullable=False
	)
	rationale: Mapped[str] = mapped_column(Text, nullable=False)
	raw_response: Mapped[str] = mapped_column(Text, nullable=False)

	session: Mapped[ReviewSession] = relationship(back_populates="proposal")
	edits: Mapped[list[DocumentEdit]] = relationship(
		back_populates="proposal", cascade="all, delete-orphan"
	)


class DocumentEdit(Base):
	__tablename__ = "document_edits"

	id: Mapped[int] = mapped_column(primary_key=True)
	proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False)
	file_path: Mapped[str] = mapped_column(Text, nullable=False)
	section_heading: Mapped[str] = mapped_column(String(500), nullable=False)
	action: Mapped[EditAction] = mapped_column(
		SqlEnum(EditAction, name="edit_action", values_callable=_enum_values), nullable=False
	)
	content: Mapped[str] = mapped_column(Text, nullable=False)
	evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False)
	start_line: Mapped[int | None] = mapped_column(nullable=True)

	proposal: Mapped[Proposal] = relationship(back_populates="edits")
	audit_events: Mapped[list[EditAuditEvent]] = relationship(back_populates="document_edit")


class EditAuditEvent(Base):
	__tablename__ = "edit_audit_events"

	id: Mapped[int] = mapped_column(primary_key=True)
	session_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("review_sessions.id", ondelete="CASCADE"), nullable=False, index=True
	)
	document_edit_id: Mapped[int] = mapped_column(
		ForeignKey("document_edits.id", ondelete="CASCADE"), nullable=False
	)
	actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
	previous_content: Mapped[str] = mapped_column(Text, nullable=False)
	updated_content: Mapped[str] = mapped_column(Text, nullable=False)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
	)

	session: Mapped[ReviewSession] = relationship(back_populates="audit_events")
	document_edit: Mapped[DocumentEdit] = relationship(back_populates="audit_events")
	actor: Mapped[User] = relationship(back_populates="audit_events")