"""Simple schema migration manager for MySQL.

Tracks applied migrations in a ``_migrations`` table and applies
new DDL statements in order.  Migrations are idempotent — each
statement is wrapped in ``CREATE TABLE IF NOT EXISTS`` or similar.

Usage::

    from research_agent.persistence.mysql_repository import _DDL_STATEMENTS
    from research_agent.persistence.migrations import MigrationManager

    mgr = MigrationManager(conn_kwargs)
    mgr.migrate(_DDL_STATEMENTS)
"""

from __future__ import annotations

from typing import Any, Dict, List


class MigrationManager:
    """Applies DDL migrations and tracks applied versions."""

    def __init__(self, conn_kwargs: Dict[str, Any]) -> None:
        self._conn_kwargs = conn_kwargs
        self._pymysql = None

    def _ensure_pymysql(self) -> None:
        if self._pymysql is not None:
            return
        try:
            import pymysql
            self._pymysql = pymysql
        except ImportError:
            raise ImportError(
                "pymysql is required for MigrationManager.  Install with: pip install pymysql"
            )

    def _conn(self):
        self._ensure_pymysql()
        conn = self._pymysql.connect(**self._conn_kwargs)
        return conn

    def migrate(self, ddl_statements: List[str]) -> int:
        """Apply *ddl_statements* that haven't been applied yet.

        Returns the number of new migrations applied.
        """
        self._ensure_pymysql()
        conn = self._conn()
        try:
            self._ensure_migrations_table(conn)

            applied = self._get_applied_hashes(conn)
            new_count = 0

            for idx, ddl in enumerate(ddl_statements):
                ddl_hash = _hash_ddl(ddl)
                if ddl_hash in applied:
                    continue
                with conn.cursor() as cur:
                    cur.execute(ddl)
                    cur.execute(
                        "INSERT INTO _migrations (migration_index, ddl_hash, ddl_preview) VALUES (%s, %s, %s)",
                        (idx, ddl_hash, ddl[:200]),
                    )
                conn.commit()
                new_count += 1

            return new_count
        finally:
            conn.close()

    def _ensure_migrations_table(self, conn: Any) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS _migrations (
                    migration_index INT NOT NULL,
                    ddl_hash        VARCHAR(64) NOT NULL PRIMARY KEY,
                    ddl_preview     VARCHAR(256),
                    applied_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
            )
        conn.commit()

    def _get_applied_hashes(self, conn: Any) -> set:
        with conn.cursor() as cur:
            cur.execute("SELECT ddl_hash FROM _migrations")
            return {row[0] for row in cur.fetchall()}


def _hash_ddl(ddl: str) -> str:
    import hashlib
    return hashlib.sha256(ddl.encode("utf-8")).hexdigest()[:64]
