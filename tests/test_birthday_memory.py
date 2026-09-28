from datetime import datetime
from pathlib import Path
import importlib


def api():
    assert Path('src/birthday_memory.py').exists(), 'birthday history/identity feature is not implemented'
    return importlib.import_module('src.birthday_memory')


def when(value='2026-08-12T01:00:00+00:00'):
    return datetime.fromisoformat(value).timestamp()


def message(text='Happy birthday <@42>!', mid=100, author=8, channel=10, stamp=None, **extra):
    return dict(message_id=mid, guild_id=1, channel_id=channel, user_id=author,
                content=text, created_at=stamp or when(), **extra)


def store(tmp_path):
    return api().BirthdayStore(str(tmp_path / 'memory.sqlite3'))


def test_stable_id_survives_name_change(tmp_path):
    s = store(tmp_path)
    s.observe_identity(1, 42, ['OldName', 'oldhandle'], 1)
    s.ingest([message()])
    s.observe_identity(1, 42, ['NewName', 'newhandle'], 2)
    assert s.resolve_alias(1, 'OldName') == 42
    assert s.resolve_alias(1, 'NewName') == 42
    assert s.summary(1, [10])[0]['user_id'] == 42
    assert s.current_name(1, 42) == 'NewName'


def test_duplicate_alias_is_never_merged(tmp_path):
    s = store(tmp_path)
    s.observe_identity(1, 42, ['Mike'], 1)
    s.observe_identity(1, 43, ['Mike'], 2)
    assert s.resolve_alias(1, 'Mike') is None
    s.ingest([message('Happy birthday Mike!')])
    assert s.summary(1, [10]) == []
    assert s.stats(1)['unresolved'] == 1


def test_later_alias_collision_invalidates_inference(tmp_path):
    s = store(tmp_path)
    s.observe_identity(1, 42, ['Mike'], 1)
    s.ingest([message('Happy birthday Mike!')])
    assert s.summary(1, [10])[0]['user_id'] == 42
    s.observe_identity(1, 43, ['Mike'], 2)
    assert s.summary(1, [10]) == []


def test_guild_isolation(tmp_path):
    s = store(tmp_path)
    s.observe_identity(1, 42, ['Mike'], 1)
    s.observe_identity(2, 43, ['Mike'], 2)
    assert s.resolve_alias(1, 'Mike') == 42
    s.ingest([message()])
    assert s.summary(2, [10]) == []


def test_greeting_uses_pacific_day_not_utc(tmp_path):
    s = store(tmp_path)
    s.ingest([message()])
    row = s.summary(1, [10])[0]
    assert (row['month'], row['day']) == (8, 11)
    assert row['status'] == 'tentative'


def test_belated_without_date_is_not_birthday_on_posting_day(tmp_path):
    s = store(tmp_path)
    s.ingest([message('Happy belated birthday <@42>!')])
    assert s.summary(1, [10]) == []
    assert s.stats(1)['candidates'] == 1


def test_explicit_relative_day(tmp_path):
    s = store(tmp_path)
    s.ingest([message('Happy birthday tomorrow <@42>!')])
    assert s.summary(1, [10])[0]['day'] == 12


def test_self_report_and_date(tmp_path):
    s = store(tmp_path)
    s.ingest([message('My birthday is August 11.', author=42)])
    row = s.summary(1, [10])[0]
    assert row['user_id'] == 42 and row['day'] == 11
    assert row['status'] == 'self-reported'


def test_iso_self_report_does_not_store_birth_year(tmp_path):
    s = store(tmp_path)
    s.ingest([message('My birthday is 1995-08-11', author=42)])
    row = s.summary(1, [10])[0]
    assert (row['month'], row['day']) == (8, 11)
    assert 'birth_year' not in row


def test_conflicting_dates_stay_conflicting(tmp_path):
    s = store(tmp_path)
    s.ingest([message(), message('My birthday is August 12, not August 11.', mid=101, author=42)])
    rows = s.summary(1, [10])
    assert {r['day'] for r in rows} == {11, 12}
    assert all(r['status'] == 'conflicting evidence' for r in rows)


def test_duplicates_do_not_strengthen_evidence(tmp_path):
    s = store(tmp_path)
    s.ingest([message(), message()])
    assert s.summary(1, [10])[0]['signals'] == 1


def test_many_wishes_one_year_not_independent_years(tmp_path):
    s = store(tmp_path)
    s.ingest([message(mid=100+i, author=8+i) for i in range(10)])
    row = s.summary(1, [10])[0]
    assert row['years'] == 1 and row['status'] == 'tentative'


def test_repeated_years_still_labelled_inference(tmp_path):
    s = store(tmp_path)
    s.ingest([message(mid=100+i, author=8+i, stamp=when(f'{year}-08-12T01:00:00+00:00'))
              for i, year in enumerate([2024, 2025, 2026])])
    assert s.summary(1, [10])[0]['status'] == 'repeated pattern, not confirmed'


def test_channel_scope_never_leaks_private_source(tmp_path):
    s = store(tmp_path)
    s.ingest([message(channel=99)])
    assert s.summary(1, [10]) == []
    assert s.summary(1, []) == []


def test_edit_replaces_evidence_and_delete_removes_it(tmp_path):
    s = store(tmp_path)
    s.ingest([message()])
    s.ingest([message('No, that was a joke')])
    assert s.summary(1, [10]) == []
    s.ingest([message()])
    s.delete_message(1, 100)
    assert s.summary(1, [10]) == []


def test_cursor_is_persistent_and_atomic(tmp_path):
    s = store(tmp_path)
    s.ingest([message()], checkpoint=(1, 10, 100, False, 1))
    s2 = api().BirthdayStore(str(tmp_path / 'memory.sqlite3'))
    assert s2.progress(1, 10)['before_id'] == 100
    assert s2.progress(1, 10)['scanned'] == 1
    assert s2.summary(1, [10])[0]['signals'] == 1


def test_unknown_names_and_multiple_mentions_are_not_guessed(tmp_path):
    s = store(tmp_path)
    s.ingest([message('Happy birthday Unknown!', mid=100),
              message('Happy birthday <@42> and <@43>!', mid=101)])
    assert s.summary(1, [10]) == []


def test_quoted_or_reported_birthday_is_not_a_wish(tmp_path):
    s = store(tmp_path)
    s.ingest([message('> Happy birthday <@42>!', mid=100),
              message('He said happy birthday <@42>', mid=101)])
    assert s.summary(1, [10]) == []


def test_ambiguous_numeric_date_is_not_guessed(tmp_path):
    s = store(tmp_path)
    s.ingest([message('My birthday is 03/04', author=42)])
    assert s.summary(1, [10]) == []


def test_reopen_preserves_old_tables(tmp_path):
    import sqlite3
    path = tmp_path/'memory.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE discord_messages (message_id INTEGER PRIMARY KEY, content TEXT)')
        db.execute('INSERT INTO discord_messages VALUES (1, ?)', ('old memory',))
    store(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM discord_messages').fetchone()[0] == 'old memory'


def test_explicit_denial_is_not_a_positive_birthday(tmp_path):
    s = store(tmp_path)
    s.ingest([message('My birthday is not August 11.', author=42)])
    assert s.summary(1, [10]) == []


def test_denial_reduces_confidence_in_greetings(tmp_path):
    s = store(tmp_path)
    s.ingest([message(), message('My birthday is not August 11.', mid=101, author=42)])
    assert s.summary(1, [10])[0]['status'] == 'conflicting evidence'


def test_own_optout_survives_backfill(tmp_path):
    s = store(tmp_path)
    s.ingest([message()])
    s.forget(1, 42)
    s.ingest([message(mid=102)])
    assert s.summary(1, [10]) == []
