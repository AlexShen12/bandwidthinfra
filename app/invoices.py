"""PostgreSQL invoice access; table and index definitions are in db_schema.sql."""

from datetime import datetime
from uuid import UUID

from psycopg import Connection


def recent_invoices(connection: Connection, account_id: UUID, since: datetime) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT id, account_id, created_at, total_cents
               FROM invoices
               ORDER BY created_at DESC"""
        )
        rows = cursor.fetchall()
    return [(row[0], row[2], row[3]) for row in rows if row[1] == account_id and row[2] >= since][
        :100
    ]
