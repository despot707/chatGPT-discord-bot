from unittest.mock import Mock

import pytest
from src import prepaid_runtime as rt
from src.chat_store import ChatStore
from src.gaming import GamingStore, PartyPlayer
from src.member_settings import ProfileStore
from src.prepaid import FREE_STORAGE_BYTES, Denied, Ledger

from tests.test_prepaid import NOW, payment


@pytest.fixture
def paid(tmp_path, monkeypatch):
    ledger = Ledger(str(tmp_path / "funds.db"), clock=lambda: NOW, storage_cap=600)
    ledger.credit(payment())
    fake = Mock()
    fake.ledger = ledger
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setattr(rt, "_INSTANCE", fake)
    return ledger, fake


def test_profile_storage_rejects_before_commit_and_delete_frees(paid, tmp_path):
    ledger, _ = paid
    store = ProfileStore(str(tmp_path / "profiles.db"))
    store.apply(1, 2, "birthday", {"month": 1, "day": 2}, 0)
    with pytest.raises(Denied):
        store.apply(1, 3, "birthday", {"month": 1, "day": 2}, 0)
    assert store.get(1, 3)["birthday"] is None
    old = ledger.summary(1)["storage_used"]
    store.apply(1, 2, "remove_birthday", {}, 1)
    assert ledger.summary(1)["storage_used"] < old


def test_chat_store_cannot_bypass_storage_cap(paid, tmp_path):
    ledger, _ = paid
    store = ChatStore(str(tmp_path / "chat.db"))
    with pytest.raises(Denied):
        store.append_turn((1, 2, 3), "x" * 500, "y" * 500, max_messages=20, max_chars=24000)
    assert store.load((1, 2, 3), max_messages=20, max_chars=24000) == []


def test_party_data_bounded_and_rollback_preserves_connection(paid, tmp_path):
    _, _ = paid
    store = GamingStore(str(tmp_path / "gaming.db"))
    store.join_party(1, 2, PartyPlayer(3, "Alice"))
    store.join_party(1, 2, PartyPlayer(4, "Bob"))
    with pytest.raises(Denied):
        store.join_party(1, 2, PartyPlayer(5, "Carol"))
    assert len(store.party(1, 2)) == 2
    store.leave_party(1, 2, 3)
    store.join_party(1, 2, PartyPlayer(5, "Carol"))
    assert len(store.party(1, 2)) == 2


def test_free_profile_and_party_writes_never_require_paid_readiness(tmp_path, monkeypatch):
    ledger = Ledger(str(tmp_path / "funds.db"))
    fake = Mock(ledger=ledger)
    fake.ready.side_effect = Denied("No paid plan")
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setattr(rt, "_INSTANCE", fake)
    profiles = ProfileStore(str(tmp_path / "profiles.db"))
    gaming = GamingStore(str(tmp_path / "gaming.db"))
    profiles.apply(1, 2, "birthday", {"month": 1, "day": 2}, 0)
    gaming.join_party(1, 10, PartyPlayer(2, "Alice"))
    assert profiles.get(1, 2)["birthday"]["day"] == 2
    assert len(gaming.party(1, 10)) == 1
    fake.ready.assert_not_called()
    assert 0 < ledger.summary(1)["storage_used"] <= FREE_STORAGE_BYTES


def test_free_quota_is_shared_and_chat_needs_paid_plan(tmp_path):
    ledger = Ledger(str(tmp_path / "funds.db"))
    ledger.storage(1, "profiles", FREE_STORAGE_BYTES - 100)
    ledger.storage(1, "gaming", 100)
    with pytest.raises(Denied):
        ledger.storage(1, "gaming", 101)
    with pytest.raises(Denied):
        ledger.storage(1, "chat", 1)
    ledger.storage(1, "gaming", 0)
    ledger.storage(1, "profiles", FREE_STORAGE_BYTES)


def test_paid_chat_cannot_spend_free_profile_baseline(tmp_path):
    ledger = Ledger(str(tmp_path / "funds.db"), clock=lambda: NOW)
    ledger.credit(payment())
    ledger.storage(1, "profiles", FREE_STORAGE_BYTES)
    ledger.storage(1, "chat", FREE_STORAGE_BYTES)
    assert ledger.summary(1)["storage_used"] == ledger.summary(1)["storage_limit"]
    with pytest.raises(Denied):
        ledger.storage(1, "chat", FREE_STORAGE_BYTES + 1)
    with pytest.raises(Denied):
        ledger.storage(1, "profiles", FREE_STORAGE_BYTES + 1)
