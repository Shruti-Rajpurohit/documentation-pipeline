from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
	pass


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/documentation_pipeline.db")
_database_url = make_url(DATABASE_URL)
if _database_url.get_backend_name() == "sqlite" and _database_url.database not in {None, ":memory:"}:
	Path(_database_url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)

def configure_engine(database_engine: AsyncEngine) -> None:
	if database_engine.url.get_backend_name() == "sqlite":
		event.listen(database_engine.sync_engine, "connect", _enable_sqlite_foreign_keys)


def _enable_sqlite_foreign_keys(connection: Any, record: Any) -> None:
	connection.execute("PRAGMA foreign_keys=ON")


engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
configure_engine(engine)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
	async with async_session_factory() as session:
		yield session


def migrate_sqlite_schema(connection: Connection) -> None:
	if connection.dialect.name != "sqlite":
		return

	migrations = (
		("review_sessions", "impact_statement", "TEXT"),
		("review_sessions", "change_summary", "TEXT"),
		("proposals", "uncertainties", "JSON NOT NULL DEFAULT '[]'"),
		("proposals", "tests_to_run", "JSON NOT NULL DEFAULT '[]'"),
	)
	for table_name, column_name, declaration in migrations:
		columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
		if column_name not in columns:
			connection.exec_driver_sql(
				f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}"
			)


async def init_db() -> None:
	from app.db import models as _models

	async with engine.begin() as connection:
		await connection.run_sync(Base.metadata.create_all)
		await connection.run_sync(migrate_sqlite_schema)


async def close_db() -> None:
	await engine.dispose()