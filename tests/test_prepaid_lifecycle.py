from src.member_settings import ProfileStore
from src.prepaid import Ledger
from src.prepaid_lifecycle import maintain_storage

from tests.test_prepaid import NOW, payment


def test_expired_paid_storage_does_not_erase_free_profile(tmp_path):
    path = tmp_path / "profiles.db"
    store = ProfileStore(str(path))
    store.apply(1, 2, "birthday", {"month": 1, "day": 2}, 0)
    ledger = Ledger(str(tmp_path / "paid.db"), clock=lambda: NOW)
    ledger.credit(payment(ends=NOW + 10))
    paths = {"profiles": str(path)}
    maintain_storage(ledger, paths, now=NOW)
    assert ledger.summary(1)["storage_used"] > 0
    ledger.clock = lambda: NOW + 20
    maintain_storage(ledger, paths, now=NOW + 20)
    assert store.get(1, 2)["birthday"] is not None
    maintain_storage(ledger, paths, now=NOW + 20 + 49 * 3600)
    assert store.get(1, 2)["birthday"] is not None
    assert ledger.summary(1)["storage_used"] > 0


def test_active_paid_profiles_remain_and_other_servers_are_separate(tmp_path):
    path = tmp_path / "profiles.db"
    store = ProfileStore(str(path))
    for guild in (1, 2):
        store.apply(guild, 2, "birthday", {"month": 1, "day": 2}, 0)
    ledger = Ledger(str(tmp_path / "paid.db"), clock=lambda: NOW)
    ledger.credit(payment())
    paths = {"profiles": str(path)}
    maintain_storage(ledger, paths, now=NOW)
    maintain_storage(ledger, paths, now=NOW + 49 * 3600)
    assert store.get(1, 2)["birthday"] is not None
    assert store.get(2, 2)["birthday"] is not None
