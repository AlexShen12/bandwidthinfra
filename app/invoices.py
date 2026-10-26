"""PostgreSQL invoice access; table and index definitions are in db_schema.sql."""

from datetime import datetime
from uuid import UUID

from psycopg import Connection


def recent_invoices(connection: Connection, account_id: UUID, since: datetime) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT id, created_at, total_cents
               FROM invoices
               WHERE account_id = %s AND created_at >= %s
               ORDER BY created_at DESC
               LIMIT 100""",
            (account_id, since),
        )
        return cursor.fetchall()
