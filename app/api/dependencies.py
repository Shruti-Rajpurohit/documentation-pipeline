from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import User
from app.models.types import UserRole


async def get_current_user(
	database: Annotated[AsyncSession, Depends(get_db)],
	user_id: Annotated[int | None, Header(alias="X-Authenticated-User-ID")] = None,
	proxy_secret: Annotated[str | None, Header(alias="X-Auth-Proxy-Secret")] = None,
) -> User:
	configured_secret = os.getenv("AUTH_PROXY_SECRET")
	if not configured_secret:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured")
	if user_id is None or proxy_secret is None:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required")
	if not hmac.compare_digest(proxy_secret, configured_secret):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service identity")
	user = await database.get(User, user_id)
	if user is None:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
	return user


def require_roles(*allowed_roles: UserRole) -> Callable:
	async def dependency(user: Annotated[User, Depends(get_current_user)]) -> User:
		if user.role not in allowed_roles:
			raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
		return user

	return dependency