"""Session storage regression coverage without external services."""
import asyncio
import sys
import types

from dexter_py.utils import session_store
from dexter_py.utils.message_history import MessageHistory


def test_in_memory_expiry(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(session_store.time, "monotonic", lambda: clock[0])
    store = session_store.InMemorySessionStore(default_expiry=5)
    history = MessageHistory()
    store.set("a", history)
    assert store.exists("a")
    assert store.get("a") is history
    clock[0] += 6
    assert not store.exists("a")
    assert not store.get("a").has_messages()


def test_empty_history_identity_is_preserved():
    store = session_store.InMemorySessionStore()
    history = MessageHistory()
    store.set("a", history)
    assert store.get("a") is history


def test_redis_serialization_preserves_timestamp(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.db = {}
        def ping(self):
            return True
        def get(self, key):
            return self.db.get(key)
        def setex(self, key, ttl, value):
            self.db[key] = value
        def delete(self, key):
            self.db.pop(key, None)
        def exists(self, key):
            return key in self.db

    fake = FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(
        from_url=lambda *args, **kwargs: fake
    ))
    store = session_store.RedisSessionStore(redis_url="redis://fake")

    async def scenario():
        history = MessageHistory(model="gpt-4o-mini")
        await history.add_message("Question", "Answer")
        store.set("test-session", history)
        restored = store.get("test-session")
        assert restored.get_messages()[0].timestamp == history.get_messages()[0].timestamp
        assert restored.get_messages()[0].query == "Question"
        assert restored.get_messages()[0].summary == history.get_messages()[0].summary
        assert restored._model == "gpt-4o-mini"

    asyncio.run(scenario())
