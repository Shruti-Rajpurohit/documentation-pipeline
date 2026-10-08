from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.db.database import async_session_factory, close_db, init_db
from app.db.models import User
from app.models.types import UserRole


async def create_user(username: str, email: str, role: UserRole, *, ensure_existing: bool = False) -> int:
	await init_db()
	try:
		async with async_session_factory() as database:
			if ensure_existing:
				existing = await database.scalar(
					select(User).where(or_(User.username == username, User.email == email))
				)
				if existing is not None:
					if existing.username != username or existing.email != email or existing.role != role:
						raise ValueError("Existing user does not match the requested development identity")
					return existing.id
			user = User(username=username, email=email, role=role)
			database.add(user)
			await database.commit()
			await database.refresh(user)
			return user.id
	finally:
		await close_db()


def main() -> None:
	parser = argparse.ArgumentParser(description="Provision a user identity for the documentation pipeline API.")
	parser.add_argument("--ensure", action="store_true", help="Reuse the exact matching user if it already exists")
	parser.add_argument("username")
	parser.add_argument("email")
	parser.add_argument("role", choices=[role.value for role in UserRole])
	args = parser.parse_args()
	try:
		user_id = asyncio.run(
			create_user(args.username, args.email, UserRole(args.role), ensure_existing=args.ensure)
		)
	except IntegrityError as error:
		parser.error("Username or email already exists")
	except ValueError as error:
		parser.error(str(error))
	if args.ensure:
		print(user_id)
	else:
		print(f"Created user ID {user_id} with role {args.role}.")


if __name__ == "__main__":
	main()