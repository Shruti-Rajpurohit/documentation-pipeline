from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.api.schemas import GitHubPushPayload
from app.db.database import async_session_factory
from app.db.models import User
from app.document_finder import DocumentSearchLimitError
from app.git_tools import GitCommandError, InvalidRevisionError
from app.llm_client import LLMConfigurationError, ModelRequestError, ProposalValidationError, get_llm_client
from app.patch_writer import PatchError
from app.services.pipeline import analyze_repository_change
from app.services.repositories import RepositoryAccessError, RepositoryConfigurationError, validate_repository_paths
from app.services.review import persist_review_draft


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
_EMPTY_SHA = "0" * 40


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
	if not signature_header or not signature_header.startswith("sha256="):
		return False
	expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
	return hmac.compare_digest(expected, signature_header)


async def process_github_push(payload: GitHubPushPayload) -> None:
	source_repo = os.getenv("SOURCE_REPO_PATH")
	docs_repo = os.getenv("DOCS_REPO_PATH")
	model_name = os.getenv("DEFAULT_MODEL")
	provider = os.getenv("DEFAULT_PROVIDER", "ollama")
	webhook_user_id = os.getenv("WEBHOOK_USER_ID")
	if not source_repo or not docs_repo or not model_name or not webhook_user_id:
		logger.error("Webhook processing is missing SOURCE_REPO_PATH, DOCS_REPO_PATH, DEFAULT_MODEL, or WEBHOOK_USER_ID")
		return

	async with async_session_factory() as database:
		try:
			user = await database.get(User, int(webhook_user_id))
			if user is None:
				logger.error("Webhook user %s was not found", webhook_user_id)
				return
			source_repo, docs_repo = await asyncio.to_thread(
				validate_repository_paths,
				source_repo,
				docs_repo,
				os.getenv("REPOSITORY_ROOTS"),
			)
			model_client = get_llm_client(
				provider=provider,
				model=model_name,
				base_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
			)
			draft = await analyze_repository_change(
				source_repo,
				docs_repo,
				payload.before,
				payload.after,
				model_client,
				model_name=model_name,
				database=database,
			)
			if draft.outcome != "awaiting_review" or draft.proposal is None or draft.prepared_patch is None:
				logger.info(
					"Webhook did not create a review for %s: %s",
					payload.after,
					draft.note or draft.analysis.rationale,
				)
				return
			record = await persist_review_draft(
				database,
				draft,
				actor_id=user.id,
				source_repo=source_repo,
				docs_repo=docs_repo,
				model_name=model_name,
			)
			logger.info("Created review session %s for commit %s", record.id, payload.after)
		except (
			RepositoryConfigurationError,
			RepositoryAccessError,
			LLMConfigurationError,
			ModelRequestError,
			ProposalValidationError,
			GitCommandError,
			InvalidRevisionError,
			DocumentSearchLimitError,
			PatchError,
			ValueError,
			OSError,
		) as error:
			logger.exception("Webhook processing failed: %s", error)


@router.post("/github")
async def github_webhook(
	request: Request,
	background_tasks: BackgroundTasks,
	x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
	x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
) -> dict[str, str]:
	secret = os.getenv("GITHUB_WEBHOOK_SECRET")
	if not secret:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Webhook authentication is not configured",
		)
	body = await request.body()
	if not verify_github_signature(secret, body, x_hub_signature_256):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")
	if x_github_event not in {None, "push"}:
		return {"status": "ignored", "message": "Event is not a git push"}

	try:
		payload = GitHubPushPayload.model_validate_json(body)
	except ValidationError as error:
		raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid GitHub push payload") from error
	if payload.after == _EMPTY_SHA or not payload.ref.startswith("refs/heads/"):
		return {"status": "ignored", "message": "Push does not update a branch"}

	background_tasks.add_task(process_github_push, payload)
	return {"status": "accepted", "message": "Processing pushed changes"}
