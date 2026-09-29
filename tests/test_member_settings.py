import importlib.util
import sqlite3

import pytest


def module():
    assert importlib.util.find_spec('src.member_settings'), 'member settings feature is missing'
    from src import member_settings
    return member_settings


def store(tmp_path):
    return module().ProfileStore(str(tmp_path / 'profiles.sqlite3'))


def test_empty_profile_is_private_and_not_created_by_read(tmp_path):
    s = store(tmp_path)
    p = s.get(1, 2)
    assert p == {'revision': 0, 'birthday': None, 'games': [], 'preferences': {}}
    with sqlite3.connect(s.path) as db:
        assert db.execute('SELECT COUNT(*) FROM member_settings').fetchone()[0] == 0


def test_id_scoping_and_restart(tmp_path):
    s = store(tmp_path)
    s.apply(1, 2, 'birthday', {'month': 8, 'day': 11}, 0)
    assert s.get(1, 3)['birthday'] is None
    assert s.get(2, 2)['birthday'] is None
    p = module().ProfileStore(s.path).get(1, 2)
    assert p['birthday'] == {'month': 8, 'day': 11, 'visibility': 'private'}


@pytest.mark.parametrize('data', [{'month': 2,'day': 30}, {'month': 0,'day': 1}, {'month': True,'day': 2}, {'month': 8,'day':11,'year':1995}, {'month':8,'day':11,'user_id':9}, {'month':8,'day':11,'visibility':'friends'}])
def test_invalid_or_unauthorized_birthday_fields_rejected(tmp_path, data):
    with pytest.raises(ValueError):
        store(tmp_path).apply(1, 2, 'birthday', data, 0)


def test_leap_day_allowed(tmp_path):
    assert store(tmp_path).apply(1,2,'birthday',{'month':2,'day':29},0)['birthday']['day'] == 29


def test_conflicting_forms_do_not_overwrite_newer_data(tmp_path):
    s = store(tmp_path)
    s.apply(1,2,'birthday',{'month':8,'day':11},0)
    with pytest.raises(module().StaleProfile):
        s.apply(1,2,'birthday',{'month':8,'day':12},0)
    assert s.get(1,2)['birthday']['day'] == 11


def test_private_values_never_exposed_by_public_projection(tmp_path):
    s = store(tmp_path)
    p = s.apply(1,2,'game',{'name':'League','role':'Jungle'},0)
    p = s.apply(1,2,'birthday',{'month':8,'day':11},p['revision'])
    p = s.apply(1,2,'preferences',{'timezone':'America/Los_Angeles','availability':['Evenings']},p['revision'])
    visible = module().visible_profile(p)
    assert visible == {'birthday':None,'games':[],'preferences':{}}


def test_explicit_sharing_and_hide_all(tmp_path):
    s = store(tmp_path)
    p = s.apply(1,2,'game',{'name':'League','role':'Jungle','visibility':'server'},0)
    assert module().visible_profile(p)['games'][0]['name'] == 'League'
    p = s.apply(1,2,'hide_all',{},p['revision'])
    assert module().visible_profile(p)['games'] == []


def test_games_deduplicate_without_erasing_other_games(tmp_path):
    s = store(tmp_path)
    p = s.apply(1,2,'game',{'name':'League','role':'Jungle'},0)
    p = s.apply(1,2,'game',{'name':'Helldivers 2'},p['revision'])
    p = s.apply(1,2,'game',{'name':'league','role':'Support'},p['revision'])
    assert len(p['games']) == 2
    assert p['games'][0]['role'] == 'Support'
    p = s.apply(1,2,'remove_game',{'name':'LEAGUE'},p['revision'])
    assert [g['name'] for g in p['games']] == ['Helldivers 2']


def test_forget_invalidates_existing_forms(tmp_path):
    s = store(tmp_path)
    p = s.apply(1,2,'game',{'name':'League'},0)
    empty = s.apply(1,2,'forget',{},p['revision'])
    assert empty['games'] == [] and empty['birthday'] is None
    with pytest.raises(module().StaleProfile):
        s.apply(1,2,'game',{'name':'League'},p['revision'])


@pytest.mark.parametrize('op,data', [('personality',{'claim':'angry'}),('preferences',{'password':'x'}),('preferences',{'timezone':'Not/AZone'}),('game',{'name':'x'*81}),('game',{'name':'hi\nthere'}),('game',{'name':'League','visibility':'everyone'})])
def test_unsupported_settings_rejected(tmp_path, op, data):
    with pytest.raises(ValueError):
        store(tmp_path).apply(1,2,op,data,0)


def test_proposal_does_not_write_until_confirmed(tmp_path):
    s = store(tmp_path)
    prepared = module().validate_change('game', {'name':'League'})
    assert prepared['visibility'] == 'private'
    assert s.get(1,2)['revision'] == 0
