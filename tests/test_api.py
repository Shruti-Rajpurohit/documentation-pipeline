import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_current_user
from app.api.main import app
from app.db.database import Base, get_db
from app.db.models import User
from app.git_tools import GitDiff
from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection
from app.llm_client import DocumentationProposal, DocumentEdit as ProposedDocumentEdit
from app.models.pipeline import PipelineDraft
from app.models.types import UserRole
from app.patch_writer import prepare_patch


class ApiSessionTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		self.root = Path(self.temp_dir.name)
		self.source = self.root / "source"
		self.source.mkdir()
		self.docs = self.root / "docs"
		(self.docs / "docs").mkdir(parents=True)
		(self.docs / "docs" / "api.md").write_text("# API\n\nOld behavior.\n", encoding="utf-8")
		self.engine = create_async_engine(
			f"sqlite+aiosqlite:///{self.root / 'test.db'}",
			poolclass=NullPool,
		)
		self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
		await self._create_schema_and_users()
		self.actors = {
			UserRole.WRITER: User(id=1, username="writer", email="writer@example.com", role=UserRole.WRITER),
			UserRole.REVIEWER: User(id=2, username="reviewer", email="reviewer@example.com", role=UserRole.REVIEWER),
			UserRole.APPROVER: User(id=3, username="approver", email="approver@example.com", role=UserRole.APPROVER),
		}
		self.actor_role = UserRole.WRITER
		app.dependency_overrides[get_db] = self._get_db
		app.dependency_overrides[get_current_user] = self._get_actor
		self.client = AsyncClient(
			transport=ASGITransport(app=app, raise_app_exceptions=False),
			base_url="http://testserver",
		)
		self.edit = ProposedDocumentEdit(
			path="docs/api.md",
			section="API",
			action="replace",
			content="Updated behavior.",
			evidence=("head456:src/api.py",),
			start_line=1,
		)
		self.draft = PipelineDraft(
			source_diff=GitDiff("base123", "head456", ("src/api.py",), "+def createPayment(): pass"),
			analysis=ChangeAnalysis(
				decision="update_required",
				changed_files=("src/api.py",),
				evidence_refs=("head456:src/api.py",),
				rationale="Product source changed.",
				base_commit="base123",
				head_commit="head456",
			),
			sections=(DocumentSection("docs/api.md", "API", 1, 3, "# API\n\nOld behavior.", ("api",)),),
			proposal=DocumentationProposal(
				decision="update_required",
				rationale="The API behavior changed.",
				changes=(self.edit,),
				uncertainties=(),
				tests_to_run=(),
			),
			prepared_patch=prepare_patch(self.docs, [self.edit]),
			raw_response=json.dumps({"decision": "update_required"}),
			outcome="awaiting_review",
		)

	async def asyncTearDown(self) -> None:
		await self.client.aclose()
		app.dependency_overrides.clear()
		await self.engine.dispose()
		self.temp_dir.cleanup()

	async def _create_schema_and_users(self) -> None:
		async with self.engine.begin() as connection:
			await connection.run_sync(Base.metadata.create_all)
		async with self.session_factory() as database:
			database.add_all(self.actors_for_db())
			await database.commit()

	def actors_for_db(self) -> list[User]:
		return [
			User(id=1, username="writer", email="writer@example.com", role=UserRole.WRITER),
			User(id=2, username="reviewer", email="reviewer@example.com", role=UserRole.REVIEWER),
			User(id=3, username="approver", email="approver@example.com", role=UserRole.APPROVER),
		]

	async def _get_db(self):
		async with self.session_factory() as database:
			yield database

	async def _get_actor(self) -> User:
		return self.actors[self.actor_role]

	async def _post_session(self) -> dict[str, object]:
		with (
			patch("app.api.routes.analyze_repository_change", new=AsyncMock(return_value=self.draft)),
			patch.dict(os.environ, {"REPOSITORY_ROOTS": str(self.root)}),
		):
			response = await self.client.post(
				"/sessions",
				json={
					"source_repo": str(self.source),
					"docs_repo": str(self.docs),
					"base_revision": "base123",
					"head_revision": "head456",
					"model_name": "local-model:version",
				},
			)
		self.assertEqual(response.status_code, 201, response.text)
		return response.json()

	async def test_create_get_edit_audit_and_approve(self) -> None:
		created = await self._post_session()
		session_id = created["id"]
		self.assertEqual(created["status"], "AWAITING_REVIEW")
		self.assertIn("createPayment", created["source_diff"])
		fetched = await self.client.get(f"/sessions/{session_id}")
		self.assertEqual(fetched.status_code, 200, fetched.text)
		self.assertEqual(fetched.json()["id"], session_id)
		self.assertEqual(fetched.json()["proposal"]["model_name"], "local-model:version")
		self.assertIn("Old behavior.", fetched.json()["proposal"]["edits"][0]["original_content"])

		listed = await self.client.get("/sessions", params={"status": "AWAITING_REVIEW", "search": "head456"})
		self.assertEqual(listed.status_code, 200, listed.text)
		self.assertEqual([item["id"] for item in listed.json()], [session_id])
		current_user = await self.client.get("/sessions/me")
		self.assertEqual(current_user.json(), {"id": 1, "username": "writer", "role": "WRITER"})

		edit_id = created["proposal"]["edits"][0]["id"]
		edit_response = await self.client.post(
			f"/sessions/{session_id}/edit",
			json={"document_edit_id": edit_id, "content": "Edited by reviewer."},
		)
		self.assertEqual(edit_response.status_code, 200, edit_response.text)
		self.assertEqual(edit_response.json()["proposal"]["edits"][0]["content"], "Edited by reviewer.")
		self.assertEqual(edit_response.json()["audit_events"][0]["previous_content"], "Updated behavior.")
		self.assertEqual(edit_response.json()["audit_events"][0]["updated_content"], "Edited by reviewer.")
		self.assertEqual(edit_response.json()["audit_events"][0]["actor_username"], "writer")

		self.actor_role = UserRole.WRITER
		forbidden = await self.client.post(f"/sessions/{session_id}/approve")
		self.assertEqual(forbidden.status_code, 403)

		self.actor_role = UserRole.APPROVER
		approved = await self.client.post(f"/sessions/{session_id}/approve")
		self.assertEqual(approved.status_code, 200, approved.text)
		self.assertEqual(approved.json()["status"], "APPROVED")
		self.assertEqual(approved.json()["reviewed_by"], 3)
		self.assertIn("Edited by reviewer.", (self.docs / "docs" / "api.md").read_text(encoding="utf-8"))

	async def test_reject_persists_reason_and_does_not_apply_patch(self) -> None:
		created = await self._post_session()
		self.actor_role = UserRole.REVIEWER

		rejected = await self.client.post(
			f"/sessions/{created['id']}/reject",
			json={"reason": "Needs more product context."},
		)

		self.assertEqual(rejected.status_code, 200, rejected.text)
		self.assertEqual(rejected.json()["status"], "REJECTED")
		self.assertEqual(rejected.json()["review_note"], "Needs more product context.")
		self.assertIn("Old behavior.", (self.docs / "docs" / "api.md").read_text(encoding="utf-8"))

	async def test_validation_errors_are_clean_json(self) -> None:
		response = await self.client.post("/sessions", json={"unexpected": "field"})

		self.assertEqual(response.status_code, 422)
		self.assertEqual(response.json()["detail"], "Request validation failed")
		self.assertNotIn("Traceback", response.text)

	async def test_create_rejects_repository_outside_configured_roots(self) -> None:
		with patch.dict(os.environ, {"REPOSITORY_ROOTS": str(self.docs)}):
			response = await self.client.post(
				"/sessions",
				json={
					"source_repo": str(self.source),
					"docs_repo": str(self.docs),
					"base_revision": "base123",
					"head_revision": "head456",
					"model_name": "local-model:version",
				},
			)

		self.assertEqual(response.status_code, 403)

	async def test_missing_proxy_identity_returns_clean_unauthorized(self) -> None:
		app.dependency_overrides.pop(get_current_user)
		with patch.dict(os.environ, {"AUTH_PROXY_SECRET": "test-proxy-secret"}):
			response = await self.client.get(f"/sessions/{uuid4()}")

		self.assertEqual(response.status_code, 401)
		self.assertEqual(response.json(), {"detail": "Authentication is required"})

	async def test_proxy_secret_resolves_database_user(self) -> None:
		created = await self._post_session()
		app.dependency_overrides.pop(get_current_user)
		with patch.dict(os.environ, {"AUTH_PROXY_SECRET": "test-proxy-secret"}):
			valid = await self.client.get(
				f"/sessions/{created['id']}",
				headers={
					"X-Authenticated-User-ID": "1",
					"X-Auth-Proxy-Secret": "test-proxy-secret",
				},
			)
			invalid = await self.client.get(
				f"/sessions/{created['id']}",
				headers={
					"X-Authenticated-User-ID": "1",
					"X-Auth-Proxy-Secret": "wrong-secret",
				},
			)

		self.assertEqual(valid.status_code, 200, valid.text)
		self.assertEqual(valid.json()["creator"]["role"], "WRITER")
		self.assertEqual(invalid.status_code, 401)

	async def test_unexpected_errors_return_clean_json(self) -> None:
		with self.assertLogs("app.api.main", level="ERROR"):
			with patch("app.api.routes._load_session", new=AsyncMock(side_effect=RuntimeError("private failure detail"))):
				response = await self.client.get(f"/sessions/{uuid4()}")

		self.assertEqual(response.status_code, 500)
		self.assertEqual(response.json(), {"detail": "Internal server error"})
		self.assertNotIn("private failure detail", response.text)


if __name__ == "__main__":
	unittest.main()