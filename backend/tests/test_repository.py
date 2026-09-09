import traceback

import psycopg
import pytest
from pipe_inspector.storage.repository import PostgresRepository
from pydantic import SecretStr


def test_connection_failure_redacts_driver_message_and_suppresses_its_traceback(monkeypatch):
    fake_secret = "unit-test-secret-never-a-real-credential"

    def fail_connect(*args, **kwargs):
        raise psycopg.OperationalError(f"connection failed password={fake_secret}")

    monkeypatch.setattr(psycopg, "connect", fail_connect)
    repository = PostgresRepository(
        SecretStr(f"postgresql://fixture:{fake_secret}@127.0.0.1:5432/fixture")
    )

    with pytest.raises(psycopg.OperationalError) as captured:
        repository._connect()

    error = captured.value
    rendered = "".join(traceback.format_exception(error))
    assert str(error) == "database connection failed"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert fake_secret not in rendered
