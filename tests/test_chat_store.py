import sqlite3
import time

from src.chat_store import ChatStore


def test_store_enforces_retention_and_conversation_count(tmp_path):
    path = str(tmp_path / "chat.sqlite3")
    store = ChatStore(path, retention_days=1, max_conversations=1)
    store.append_turn((1, 2, 0), "hello", "hi", max_messages=20, max_chars=100)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE chat_turns SET created_at=?", (time.time() - 2 * 86400,))
    assert store.load((1, 2, 0), max_messages=20, max_chars=100) == []

    store.append_turn((1, 2, 0), "new shared", "answer", max_messages=20, max_chars=100)
    store.append_turn((1, 3, 0), "other channel", "answer", max_messages=20, max_chars=100)
    assert store.load((1, 2, 0), max_messages=20, max_chars=100) == []
    assert store.load((1, 3, 0), max_messages=20, max_chars=100) == [
        {"role": "user", "content": "other channel"},
        {"role": "assistant", "content": "answer"},
    ]


def test_store_keeps_complete_recent_pairs_within_history_bounds(tmp_path):
    store = ChatStore(str(tmp_path / "chat.sqlite3"))
    store.append_turn((1, 2, 0), "first", "reply one", max_messages=2, max_chars=40)
    store.append_turn((1, 2, 0), "second", "reply two", max_messages=2, max_chars=40)
    assert store.load((1, 2, 0), max_messages=2, max_chars=40) == [
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "reply two"},
    ]
