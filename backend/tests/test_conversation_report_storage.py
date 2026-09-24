from types import SimpleNamespace
from uuid import uuid4

from pipe_inspector.storage.conversation import ConversationRepository


def test_list_turns_none_uses_unbounded_ordered_query(monkeypatch):
    session_id = uuid4()
    calls = []

    class Connection:
        def execute(self, query, params):
            rendered = query.as_string()
            calls.append((rendered, params))
            if "SELECT 1" in rendered:
                return SimpleNamespace(fetchone=lambda: (1,))
            return SimpleNamespace(fetchall=lambda: ())

    class Context:
        def __enter__(self):
            return Connection()

        def __exit__(self, *args):
            return False

    repository = object.__new__(ConversationRepository)
    repository._schema = "public"
    monkeypatch.setattr(repository, "_connect", lambda: Context())

    assert repository.list_turns(session_id, limit=None) == ()
    query, params = calls[-1]
    assert "ORDER BY created_at, request_id" in query
    assert "LIMIT" not in query
    assert params == (session_id,)
