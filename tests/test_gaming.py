from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from src.gaming import GamingError, GamingStore, PartyPlayer, SteamLink, make_teams


def test_steam_links_persist_and_are_guild_scoped(tmp_path):
    path = tmp_path / "nested" / "gaming.sqlite3"
    store = GamingStore(str(path))
    assert not path.exists()  # Initialization is lazy.
    store.link_steam(10, 7, "76561198000000007", "Seven")
    store.close()

    reopened = GamingStore(str(path))
    assert reopened.get_steam(10, 7) == SteamLink("76561198000000007", "Seven")
    assert reopened.get_steam(11, 7) is None
    assert reopened.unlink_steam(10, 7)
    assert not reopened.unlink_steam(10, 7)
    reopened.close()


def test_parties_are_scoped_upserted_and_deleted(tmp_path):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    store.join_party(1, 20, PartyPlayer(3, "Three", 4, "tank"))
    store.join_party(1, 20, PartyPlayer(3, "Updated", 8, "support"))
    store.join_party(2, 20, PartyPlayer(3, "Other guild"))
    store.join_party(1, 21, PartyPlayer(4, "Other channel"))

    assert store.party(1, 20) == [PartyPlayer(3, "Updated", 8, "support")]
    assert store.party(2, 20) == [PartyPlayer(3, "Other guild")]
    assert store.leave_party(1, 20, 3)
    assert not store.leave_party(1, 20, 3)
    store.join_party(1, 20, PartyPlayer(5, "Five"))
    store.clear_party(1, 20)
    assert store.party(1, 20) == []
    assert store.party(1, 21) == [PartyPlayer(4, "Other channel")]
    store.close()


def test_party_capacity_is_atomic_and_duplicate_join_does_not_consume_slot(tmp_path):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    for user_id in range(1, 21):
        store.join_party(1, 2, PartyPlayer(user_id, f"Player {user_id}"))
    store.join_party(1, 2, PartyPlayer(1, "Updated", 10, "flex"))
    assert len(store.party(1, 2)) == 20
    assert store.party(1, 2)[0] == PartyPlayer(1, "Updated", 10, "flex")
    with pytest.raises(GamingError, match="full"):
        store.join_party(1, 2, PartyPlayer(21, "Overflow"))
    assert len(store.party(1, 2)) == 20
    store.close()


@pytest.mark.parametrize(
    "player",
    [
        PartyPlayer(0, "Bad"),
        PartyPlayer(1, ""),
        PartyPlayer(1, "x" * 81),
        PartyPlayer(1, "Bad rating", 0),
        PartyPlayer(1, "Bad rating", 11),
        PartyPlayer(1, "Bad rating", True),
        PartyPlayer(1, "Bad role", role="x" * 25),
    ],
)
def test_join_rejects_invalid_player_fields(tmp_path, player):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    with pytest.raises(GamingError):
        store.join_party(1, 2, player)
    store.close()


def test_memory_database_and_close_are_supported():
    store = GamingStore(":memory:")
    store.link_steam(1, 2, "76561198000000002", "Name")
    assert store.get_steam(1, 2) == SteamLink("76561198000000002", "Name")
    store.close()
    assert store.get_steam(1, 2) is None


def test_expanded_home_path_is_used_for_database(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = "~/nested/gaming.sqlite3"
    store = GamingStore(path)
    store.link_steam(1, 2, "76561198000000002", "Name")
    store.close()
    assert (tmp_path / "nested" / "gaming.sqlite3").exists()


@pytest.mark.parametrize(
    "steam_id", ["123", "7656119800000000x", "１２３４５６７８９０１２３４５６７"]
)
def test_steam_link_requires_ascii_17_digit_id(tmp_path, steam_id):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    with pytest.raises(GamingError, match="17 digits"):
        store.link_steam(1, 2, steam_id, "Name")
    store.close()


def test_steam_display_name_accepts_adapter_limit_of_100(tmp_path):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    name = "N" * 100
    store.link_steam(1, 2, "76561198000000002", name)
    assert store.get_steam(1, 2) == SteamLink("76561198000000002", name)
    with pytest.raises(GamingError, match="100"):
        store.link_steam(1, 2, "76561198000000002", name + "N")
    store.close()


@pytest.mark.parametrize("identifier", [0, -1, 2**63])
def test_store_rejects_ids_outside_sqlite_integer_range(tmp_path, identifier):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    with pytest.raises(GamingError, match="64-bit"):
        store.get_steam(identifier, 1)
    store.close()


def test_write_failure_rolls_back_and_store_recovers(tmp_path):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))
    db = store._db()
    db.execute(
        "CREATE TRIGGER reject_link BEFORE INSERT ON steam_links "
        "BEGIN SELECT RAISE(ABORT, 'rejected'); END"
    )
    db.commit()
    with pytest.raises(GamingError, match="Could not save"):
        store.link_steam(1, 2, "76561198000000002", "Name")
    db.execute("DROP TRIGGER reject_link")
    db.commit()
    store.link_steam(1, 2, "76561198000000002", "Name")
    assert store.get_steam(1, 2) == SteamLink("76561198000000002", "Name")
    store.close()


def test_shared_store_serializes_threaded_party_joins_and_enforces_cap(tmp_path):
    store = GamingStore(str(tmp_path / "gaming.sqlite3"))

    def join(user_id: int) -> bool:
        try:
            store.join_party(1, 2, PartyPlayer(user_id, f"P{user_id}"))
            return True
        except GamingError as exc:
            assert "full" in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(join, range(1, 26)))
    assert sum(accepted) == 20
    assert len(store.party(1, 2)) == 20
    store.close()


def test_team_builder_handles_skewed_skills_and_balanced_sizes():
    players = [
        PartyPlayer(index, f"P{index}", skill)
        for index, skill in enumerate([10, 10, 9, 8, 1, 1], 1)
    ]
    teams = make_teams(players)
    assert sorted(map(len, teams)) == [3, 3]
    assert {player.user_id for team in teams for player in team} == {p.user_id for p in players}
    assert abs(sum(p.skill for p in teams[0]) - sum(p.skill for p in teams[1])) <= 2


def test_team_builder_seed_is_reproducible_and_rejects_duplicate_players():
    players = [
        PartyPlayer(index, f"P{index}", 1 + index % 10, ["top", "mid"][index % 2])
        for index in range(1, 13)
    ]
    assert make_teams(players, 3, seed=91) == make_teams(players, 3, seed=91)
    with pytest.raises(GamingError, match="unique"):
        make_teams([PartyPlayer(1, "A"), PartyPlayer(1, "B")])


def test_team_builder_distributes_roles_when_feasible():
    players = [
        PartyPlayer(index, f"P{index}", 5, role)
        for index, role in enumerate(["tank"] * 4 + ["heal"] * 4, 1)
    ]
    teams = make_teams(players, 2, seed=4)
    for team in teams:
        roles = Counter(player.role for player in team)
        assert roles["tank"] <= 2
        assert roles["heal"] <= 2


def test_random_seedless_balanced_build_keeps_team_sizes_and_improves_skill_gap():
    players = [
        PartyPlayer(index, f"P{index}", skill)
        for index, skill in enumerate([10, 10, 9, 8, 1, 1], 1)
    ]
    teams = make_teams(players)
    assert sorted(map(len, teams)) == [3, 3]
    assert abs(sum(p.skill for p in teams[0]) - sum(p.skill for p in teams[1])) <= 1


def test_unbalanced_team_builder_still_preserves_each_player_once():
    players = [PartyPlayer(index, f"P{index}") for index in range(1, 10)]
    teams = make_teams(players, 4, balanced=False, seed=12)
    ids = [player.user_id for team in teams for player in team]
    assert len(teams) == 4
    assert max(map(len, teams)) - min(map(len, teams)) <= 1
    assert sorted(ids) == list(range(1, 10))
