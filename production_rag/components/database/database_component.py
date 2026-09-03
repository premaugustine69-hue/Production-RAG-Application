"""Async SQLAlchemy engine and session factory — DI-managed component."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from injector import inject, singleton
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from production_rag.settings.settings import Settings

logger = logging.getLogger(__name__)


@singleton
class DatabaseComponent:
    """Manages the async SQLAlchemy engine and session factory.

    Usage via DI::

        @inject
        def __init__(self, db: DatabaseComponent) -> None:
            self._session_factory = db.session_factory
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @inject
    def __init__(self, settings: Settings) -> None:
        pg = settings.postgres
        if pg is None:
            # Postgres is optional — skip initialisation when not configured.
            logger.info(
                "PostgreSQL not configured (postgres block missing in settings). "
                "Database features will be unavailable."
            )
            self._enabled = False
            return

        self._enabled = True
        connection_url = (
            f"postgresql+asyncpg://{pg.user}:{pg.password}"
            f"@{pg.host}:{pg.port}/{pg.database}"
        )
        logger.info(
            "Initialising async PostgreSQL engine at %s:%s/%s",
            pg.host,
            pg.port,
            pg.database,
        )
        self.engine = create_async_engine(
            connection_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Context-managed async session — commits on success, rolls back on error."""
        if not self._enabled:
            raise RuntimeError(
                "PostgreSQL is not configured. Add a 'postgres' section to settings.yaml."
            )
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Dispose the engine connection pool (call on shutdown)."""
        if self._enabled:
            await self.engine.dispose()
            logger.info("Database engine disposed.")
