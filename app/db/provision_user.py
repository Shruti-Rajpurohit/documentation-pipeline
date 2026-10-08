from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.exc import IntegrityError

from app.db.database import async_session_factory, close_db, init_db
from app.db.models import User
from app.models.types import UserRole


async def create_user(username: str, email: str, role: UserRole) -> int:
	await init_db()
	try:
		async with async_session_factory() as database:
			user = User(username=username, email=email, role=role)
			database.add(user)
			await database.commit()
			await database.refresh(user)
			return user.id
	finally:
		await close_db()


def main() -> None:
	parser = argparse.ArgumentParser(description="Provision a user identity for the documentation pipeline API.")
	parser.add_argument("username")
	parser.add_argument("email")
	parser.add_argument("role", choices=[role.value for role in UserRole])
	args = parser.parse_args()
	try:
		user_id = asyncio.run(create_user(args.username, args.email, UserRole(args.role)))
	except IntegrityError as error:
		parser.error("Username or email already exists")
	print(f"Created user ID {user_id} with role {args.role}.")


if __name__ == "__main__":
	main()