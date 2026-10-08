import uuid
import unittest

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.database import Base, configure_engine
from app.db.models import DocumentEdit, EditAuditEvent, Proposal, ReviewSession, User
from app.models.types import EditAction, ProposalDecision, ReviewStatus, UserRole


class DatabaseModelTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self) -> None:
		self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
		configure_engine(self.engine)
		async with self.engine.begin() as connection:
			await connection.run_sync(Base.metadata.create_all)
		self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

	async def asyncTearDown(self) -> None:
		await self.engine.dispose()

	async def test_persists_review_and_edit_audit_relationships(self) -> None:
		session_id = uuid.uuid4()
		async with self.session_factory() as session:
			user = User(username="reviewer", email="reviewer@example.com", role=UserRole.REVIEWER)
			session.add(user)
			await session.flush()
			review = ReviewSession(
				id=session_id,
				source_commit="base123",
				head_commit="head456",
				status=ReviewStatus.AWAITING_REVIEW,
				created_by=user.id,
				source_repo="/repos/source",
				docs_repo="/repos/docs",
				source_diff="diff --git ...",
				changed_files=["src/api.py"],
				evidence_refs=["head456:src/api.py"],
				analysis_decision="update_required",
				analysis_rationale="Product source changed.",
				proposal=Proposal(
					model_name="qwen3-coder:30b",
					prompt_version="v1",
					decision=ProposalDecision.UPDATE_REQUIRED,
					rationale="The API parameter is now required.",
					raw_response='{"decision":"update_required"}',
					edits=[
						DocumentEdit(
							file_path="docs/api.md",
							section_heading="Payments",
							action=EditAction.REPLACE,
							content="Pass currency.",
							evidence=["head456:src/api.py"],
							start_line=1,
						)
					],
				),
			)
			session.add(review)
			await session.flush()
			document_edit = review.proposal.edits[0]
			session.add(
				EditAuditEvent(
					session_id=review.id,
					document_edit_id=document_edit.id,
					actor_id=user.id,
					previous_content="Old text.",
					updated_content="New text.",
				)
			)
			await session.commit()

		async with self.session_factory() as session:
			statement = (
				select(ReviewSession)
				.options(
					selectinload(ReviewSession.creator),
					selectinload(ReviewSession.proposal).selectinload(Proposal.edits),
					selectinload(ReviewSession.audit_events),
				)
				.where(ReviewSession.id == session_id)
			)
			review = (await session.scalars(statement)).one()
			self.assertEqual(review.status, ReviewStatus.AWAITING_REVIEW)
			self.assertEqual(review.proposal.decision, ProposalDecision.UPDATE_REQUIRED)
			self.assertEqual(review.proposal.edits[0].evidence, ["head456:src/api.py"])
			self.assertEqual(review.audit_events[0].updated_content, "New text.")
			self.assertEqual(review.creator.username, "reviewer")

	async def test_foreign_keys_are_enforced_by_sqlite(self) -> None:
		async with self.session_factory() as session:
			session.add(
				ReviewSession(
					source_commit="base123",
					head_commit="head456",
					status=ReviewStatus.AWAITING_REVIEW,
					created_by=999,
					source_repo="/repos/source",
					docs_repo="/repos/docs",
					source_diff="diff",
					changed_files=[],
					evidence_refs=[],
					analysis_decision="update_required",
					analysis_rationale="Source changed.",
				)
			)
			with self.assertRaises(IntegrityError):
				await session.commit()


if __name__ == "__main__":
	unittest.main()