import uuid
import unittest

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.database import Base, configure_engine, migrate_sqlite_schema
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

	async def test_sqlite_migration_adds_missing_columns_without_losing_rows(self) -> None:
		migration_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
		try:
			async with migration_engine.begin() as connection:
				await connection.exec_driver_sql(
					"CREATE TABLE review_sessions (id TEXT PRIMARY KEY, summary TEXT)"
				)
				await connection.exec_driver_sql(
					"CREATE TABLE proposals (id INTEGER PRIMARY KEY, summary TEXT)"
				)
				await connection.exec_driver_sql(
					"INSERT INTO review_sessions (id, summary) VALUES ('session-1', 'preserve me')"
				)
				await connection.exec_driver_sql(
					"INSERT INTO proposals (id, summary) VALUES (1, 'keep proposal')"
				)
				await connection.run_sync(migrate_sqlite_schema)
				result = await connection.exec_driver_sql(
					"SELECT id, summary, impact_statement, change_summary FROM review_sessions"
				)
				row = result.one()
				proposal_result = await connection.exec_driver_sql(
					"SELECT id, summary, uncertainties, tests_to_run FROM proposals"
				)
				proposal_row = proposal_result.one()

			self.assertEqual(row, ("session-1", "preserve me", None, None))
			self.assertEqual(proposal_row, (1, "keep proposal", "[]", "[]"))
			async with migration_engine.begin() as connection:
				await connection.run_sync(migrate_sqlite_schema)
				columns = await connection.exec_driver_sql("PRAGMA table_info(review_sessions)")
				proposal_columns = await connection.exec_driver_sql("PRAGMA table_info(proposals)")
				review_session_column_names = [info[1] for info in columns.fetchall()]
				proposal_column_names = [info[1] for info in proposal_columns.fetchall()]

			self.assertEqual(
				review_session_column_names.count("impact_statement"),
				1,
			)
			self.assertEqual(
				review_session_column_names.count("change_summary"),
				1,
			)
			self.assertEqual(
				proposal_column_names.count("uncertainties"),
				1,
			)
			self.assertEqual(
				proposal_column_names.count("tests_to_run"),
				1,
			)
		finally:
			await migration_engine.dispose()


if __name__ == "__main__":
	unittest.main()