#!/usr/bin/env python3
"""Create the idempotent application tables in the configured PostgreSQL database."""

import psycopg
from pipe_inspector.config import Settings
from pipe_inspector.storage.repository import PostgresRepository


def main() -> None:
    try:
        settings = Settings()
        PostgresRepository(settings.database_dsn).initialize()
    except psycopg.Error:
        raise SystemExit("application storage initialization failed") from None
    print("application storage initialized")


if __name__ == "__main__":
    main()
