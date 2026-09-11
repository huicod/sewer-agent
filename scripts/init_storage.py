#!/usr/bin/env python3
"""Create the idempotent application tables in the configured PostgreSQL database."""

import psycopg
from pipe_inspector.config import Settings
from pipe_inspector.storage.human_review import HumanReviewRepository


def main() -> None:
    try:
        settings = Settings()
        HumanReviewRepository(settings.database_dsn).initialize()
    except psycopg.Error:
        raise SystemExit("application storage initialization failed") from None
    print("application storage initialized")


if __name__ == "__main__":
    main()
