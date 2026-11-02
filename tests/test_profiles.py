"""Profiles, vaccination entries and immunities: family access, validation, audit log, views."""
import re

import pytest

from helvax import create_app

HEXA = ['dip', 'tet', 'pert', 'polio', 'hib', 'hbv']


@pytest.fixture
def profile(client, login):
    """Alice (family Muster) creates the profile 'Mia', born 2026-01-10; returns its id."""
    t = login('alice')
    r = client.post('/profiles/new', data={'display_name': 'Mia', 'birth_date': '2026-01-10',
                                           'flags': ['optin:menb'], 'csrf_token': t})
    assert r.status_code == 302
    return int(r.headers['Location'].rsplit('/', 1)[1])


def add_vaccination(client, csrf, profile_id, **fields):
    data = {'date': '2026-03-10', 'product': 'infanrix-hexa', 'csrf_token': csrf(), **fields}
    return client.post(f'/profiles/{profile_id}/vaccinations/new', data=data)


# --- Profiles ------------------------------------------------------------------------------------

def test_create_profile(client, query, profile):
    assert query('SELECT family_id, display_name, birth_date FROM profiles') == [(1, 'Mia', '2026-01-10')]
    assert query('SELECT flag FROM profile_flags') == [('optin:menb',)]
    page = client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert '<h1>Mia</h1>' in page and 'Geboren am 10.01.2026' in page
    assert 'Schweizerischer Impfplan 2026 (ch-2026 (Stand 16.04.2026))' in page
    assert 'Keine medizinische Beratung' in page


def test_profile_validation(client, login, query):
    t = login('bob')
    for data in [{'display_name': '', 'birth_date': '2020-01-01'},
                 {'display_name': 'x' * 41, 'birth_date': '2020-01-01'},
                 {'display_name': 'Leo', 'birth_date': '2027-01-01'},  # future (clock: 2026-10-15)
                 {'display_name': 'Leo', 'birth_date': '01.01.2020'}]:
        assert client.post('/profiles/new', data={**data, 'csrf_token': t}).status_code == 400
    r = client.post('/profiles/new', data={'display_name': 'Leo', 'birth_date': '2020-01-01', 'flags': 'nope',
                                           'csrf_token': t})
    assert r.status_code == 400
    assert query('SELECT COUNT(*) FROM profiles') == [(0,)]


def test_family_members_share_profiles(client, app, login, profile):
    login('bob')
    assert 'Mia' in client.get('/').get_data(as_text=True)
    assert client.get(f'/profiles/{profile}').status_code == 200


def test_other_family_gets_404(client, csrf, login, query, profile):
    t = add_vaccination(client, csrf, profile)  # as alice
    vid = query('SELECT id FROM vaccinations')[0][0]
    client.post(f'/profiles/{profile}/immunities', data={'antigen': 'varicella', 'kind': 'disease',
                                                         'csrf_token': csrf()})
    iid = query('SELECT id FROM immunities')[0][0]
    t = login('carol')
    assert 'Mia' not in client.get('/').get_data(as_text=True)
    for url in [f'/profiles/{profile}', f'/profiles/{profile}/edit', f'/profiles/{profile}/print',
                f'/profiles/{profile}/vaccinations/new', f'/vaccinations/{vid}/edit']:
        assert client.get(url).status_code == 404, url
    for url, data in [(f'/profiles/{profile}/edit', {'display_name': 'X', 'birth_date': '2020-01-01'}),
                      (f'/profiles/{profile}/delete', {}),
                      (f'/profiles/{profile}/vaccinations/new', {'date': '2026-05-01', 'product': 'hexyon'}),
                      (f'/vaccinations/{vid}/edit', {'date': '2026-05-01', 'product': 'hexyon'}),
                      (f'/vaccinations/{vid}/delete', {}),
                      (f'/profiles/{profile}/immunities', {'antigen': 'measles', 'kind': 'disease'}),
                      (f'/immunities/{iid}/delete', {})]:
        assert client.post(url, data={**data, 'csrf_token': t}).status_code == 404, url
    assert query('SELECT display_name FROM profiles') == [('Mia',)]
    assert query('SELECT COUNT(*) FROM vaccinations') == [(1,)]
    assert query('SELECT COUNT(*) FROM immunities') == [(1,)]
    assert client.get('/profiles/999').status_code == 404


def test_account_without_family_has_no_profiles(client, app, login):
    with app.app_context():
        from helvax.db import get_db
        with get_db() as con:
            con.execute("UPDATE users SET family_id = NULL WHERE username = 'carol'")
    t = login('carol')
    assert 'Noch keine Familie' in client.get('/').get_data(as_text=True)
    assert client.get('/profiles/new').status_code == 403
    r = client.post('/profiles/new', data={'display_name': 'X', 'birth_date': '2020-01-01', 'csrf_token': t})
    assert r.status_code == 403


def test_edit_profile(client, csrf, query, profile):
    r = client.post(f'/profiles/{profile}/edit', data={'display_name': 'Mia M.', 'birth_date': '2026-01-11',
                                                       'flags': ['optin:rota', 'rsv_maternal'],
                                                       'csrf_token': csrf()})
    assert r.status_code == 302
    assert query('SELECT display_name, birth_date FROM profiles') == [('Mia M.', '2026-01-11')]
    assert query('SELECT flag FROM profile_flags ORDER BY flag') == [('optin:rota',), ('rsv_maternal',)]
    page = client.get(f'/profiles/{profile}/edit').get_data(as_text=True)
    assert 'value="optin:rota" checked' in page and 'value="optin:menb">' in page
    # The birth date can't move after the first vaccination
    add_vaccination(client, csrf, profile, date='2026-03-10')
    r = client.post(f'/profiles/{profile}/edit', data={'display_name': 'Mia', 'birth_date': '2026-04-01',
                                                       'csrf_token': csrf()})
    assert r.status_code == 400
    assert query('SELECT birth_date FROM profiles') == [('2026-01-11',)]


def test_delete_profile_deletes_everything(client, csrf, query, profile):
    add_vaccination(client, csrf, profile)
    client.post(f'/profiles/{profile}/immunities', data={'antigen': 'varicella', 'kind': 'disease',
                                                         'csrf_token': csrf()})
    page = client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert 'data-confirm-title="Profil löschen?"' in page
    assert client.post(f'/profiles/{profile}/delete', data={'csrf_token': csrf()}).status_code == 302
    for table in ('profiles', 'profile_flags', 'vaccinations', 'vaccination_antigens', 'immunities'):
        assert query(f'SELECT COUNT(*) FROM {table}') == [(0,)], table


# --- Vaccination entries -------------------------------------------------------------------------

def test_combination_vaccine_covers_its_antigens(client, csrf, query, profile):
    r = add_vaccination(client, csrf, profile, lot='A21CB', administered_by='Praxis Beispiel', note='links')
    assert r.status_code == 302
    assert query('SELECT date, product_key, product_name, lot, administered_by, note FROM vaccinations') == [
        ('2026-03-10', 'infanrix-hexa', 'Infanrix hexa', 'A21CB', 'Praxis Beispiel', 'links')]
    assert sorted(a for (a,) in query('SELECT antigen FROM vaccination_antigens')) == sorted(HEXA)
    page = client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert 'Infanrix hexa' in page and 'A21CB' in page
    assert 'Diphtherie <span class="dose-no">1</span>' in page
    # Every antigen of the vaccine moved on to dose 2, shown as one group; pneumococci (not in it) wait for dose 1
    assert page.count('2. Dosis: ') == 1
    assert ('<strong>Diphtherie, Tetanus, Pertussis (Keuchhusten), Kinderlähmung (Polio), '
            'Haemophilus influenzae Typ b, Hepatitis B</strong>') in page
    assert 'Pneumokokken</strong> <span class="status-badge status-overdue">' in page


def test_checkboxes_dont_change_a_catalogue_product(client, csrf, query, profile):
    add_vaccination(client, csrf, profile, antigens=['measles'])
    assert 'measles' not in [a for (a,) in query('SELECT antigen FROM vaccination_antigens')]


def test_other_product(client, csrf, query, profile):
    r = add_vaccination(client, csrf, profile, product='other', product_name='Impfstoff aus dem Ausland',
                        antigens=['measles', 'mumps', 'rubella'], date='2026-10-12')
    assert r.status_code == 302
    assert query('SELECT product_key, product_name FROM vaccinations') == [(None, 'Impfstoff aus dem Ausland')]
    assert sorted(a for (a,) in query('SELECT antigen FROM vaccination_antigens')) == ['measles', 'mumps', 'rubella']


@pytest.mark.parametrize('fields, message', [
    ({'date': ''}, 'gültiges Impfdatum'),
    ({'date': '2025-12-31'}, 'vor dem Geburtsdatum'),
    ({'date': '2026-10-16'}, 'in der Zukunft'),
    ({'product': ''}, 'Impfstoff auswählen'),
    ({'product': 'unknown'}, 'Impfstoff auswählen'),
    ({'product': 'other', 'antigens': ['measles']}, 'Namen des Impfstoffs'),
    ({'product': 'other', 'product_name': 'X'}, 'wogegen'),
    ({'lot': 'x' * 41}, 'Charge: höchstens 40'),
    ({'note': 'x' * 201}, 'Notiz: höchstens 200'),
])
def test_vaccination_validation(client, csrf, query, profile, fields, message):
    r = add_vaccination(client, csrf, profile, **fields)
    assert r.status_code == 400
    assert message in r.get_data(as_text=True)
    assert query('SELECT COUNT(*) FROM vaccinations') == [(0,)]


def test_unknown_antigen_is_rejected(client, csrf, profile):
    r = add_vaccination(client, csrf, profile, product='other', product_name='X', antigens=['nope'])
    assert r.status_code == 400


def test_save_and_add_another(client, csrf, profile):
    r = add_vaccination(client, csrf, profile, another='1')
    assert r.headers['Location'] == f'/profiles/{profile}/vaccinations/new'


def test_edit_vaccination_with_dose_override(client, csrf, query, profile):
    add_vaccination(client, csrf, profile)
    vid = query('SELECT id FROM vaccinations')[0][0]
    page = client.get(f'/vaccinations/{vid}/edit').get_data(as_text=True)
    assert 'value="2026-03-10"' in page and 'name="dose_dip"' in page and 'placeholder="1"' in page
    r = client.post(f'/vaccinations/{vid}/edit', data={'date': '2026-03-12', 'product': 'hexyon', 'dose_dip': '2',
                                                        'csrf_token': csrf()})
    assert r.status_code == 302
    assert query('SELECT date, product_name FROM vaccinations') == [('2026-03-12', 'Hexyon')]
    assert query("SELECT dose_override FROM vaccination_antigens WHERE antigen = 'dip'") == [(2,)]
    assert query("SELECT dose_override FROM vaccination_antigens WHERE antigen = 'tet'") == [(None,)]
    r = client.post(f'/vaccinations/{vid}/edit', data={'date': '2026-03-12', 'product': 'hexyon', 'dose_dip': '0',
                                                        'csrf_token': csrf()})
    assert r.status_code == 400
    # Switching to a product with other antigens replaces them
    client.post(f'/vaccinations/{vid}/edit', data={'date': '2026-03-12', 'product': 'priorix', 'csrf_token': csrf()})
    assert sorted(a for (a,) in query('SELECT antigen FROM vaccination_antigens')) == ['measles', 'mumps', 'rubella']


def test_delete_vaccination(client, csrf, query, profile):
    add_vaccination(client, csrf, profile)
    vid = query('SELECT id FROM vaccinations')[0][0]
    assert 'data-confirm-title="Impfung löschen?"' in client.get(f'/profiles/{profile}').get_data(as_text=True)
    client.post(f'/vaccinations/{vid}/delete', data={'csrf_token': csrf()})
    assert query('SELECT COUNT(*) FROM vaccinations') == [(0,)]
    assert query('SELECT COUNT(*) FROM vaccination_antigens') == [(0,)]


def test_redirects_use_english_anchors(client, csrf, query, profile):
    """Routes, query parameters and fragments are English; the page has matching ids."""
    r = add_vaccination(client, csrf, profile)
    assert r.headers['Location'] == f'/profiles/{profile}#record'
    r = client.post(f'/profiles/{profile}/immunities', data={'antigen': 'varicella', 'kind': 'disease',
                                                             'csrf_token': csrf()})
    assert r.headers['Location'] == f'/profiles/{profile}#immunities'
    page = client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert 'id="record"' in page and 'id="immunities"' in page
    assert f'/profiles/{profile}?view=table#record' in page


def test_forms_need_csrf(client, query, profile):
    assert client.post(f'/profiles/{profile}/vaccinations/new',
                       data={'date': '2026-03-10', 'product': 'hexyon'}).status_code == 400
    assert client.post(f'/profiles/{profile}/delete').status_code == 400
    assert query('SELECT COUNT(*) FROM profiles') == [(1,)]


# --- Immunities ----------------------------------------------------------------------------------

def test_immunity(client, csrf, query, profile):
    r = client.post(f'/profiles/{profile}/immunities', data={'antigen': 'varicella', 'kind': 'disease',
                                                             'date': '2026-05-01', 'note': 'Kita',
                                                             'csrf_token': csrf()}, follow_redirects=True)
    assert 'Immunität gegen Windpocken (Varizellen) erfasst.' in r.get_data(as_text=True)
    page = client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert 'Krankheit durchgemacht, 01.05.2026 · Kita' in page
    assert 'Immun (durchgemachte Krankheit oder Serologie)' in page
    r = client.post(f'/profiles/{profile}/immunities', data={'antigen': 'varicella', 'kind': 'serology',
                                                             'csrf_token': csrf()}, follow_redirects=True)
    assert 'schon eine Immunität erfasst' in r.get_data(as_text=True)
    for data in [{'antigen': 'nope', 'kind': 'disease'}, {'antigen': 'measles', 'kind': 'guess'}]:
        r = client.post(f'/profiles/{profile}/immunities', data={**data, 'csrf_token': csrf()})
        assert r.status_code == 400
    client.post(f'/profiles/{profile}/immunities', data={'antigen': 'measles', 'kind': 'disease',
                                                         'date': '2030-01-01', 'csrf_token': csrf()})
    assert query('SELECT antigen FROM immunities') == [('varicella',)]
    iid = query('SELECT id FROM immunities')[0][0]
    client.post(f'/immunities/{iid}/delete', data={'csrf_token': csrf()})
    assert query('SELECT COUNT(*) FROM immunities') == [(0,)]


# --- Audit, escaping, views ----------------------------------------------------------------------

def test_changes_are_audit_logged_without_health_details(client, csrf, query, profile):
    add_vaccination(client, csrf, profile)
    vid = query('SELECT id FROM vaccinations')[0][0]
    client.post(f'/vaccinations/{vid}/edit', data={'date': '2026-03-11', 'product': 'hexyon', 'csrf_token': csrf()})
    client.post(f'/vaccinations/{vid}/delete', data={'csrf_token': csrf()})
    client.post(f'/profiles/{profile}/immunities', data={'antigen': 'varicella', 'kind': 'disease',
                                                         'csrf_token': csrf()})
    iid = query('SELECT id FROM immunities')[0][0]
    client.post(f'/immunities/{iid}/delete', data={'csrf_token': csrf()})
    client.post(f'/profiles/{profile}/edit', data={'display_name': 'Mia', 'birth_date': '2026-01-10',
                                                   'csrf_token': csrf()})
    client.post(f'/profiles/{profile}/delete', data={'csrf_token': csrf()})
    assert query("SELECT action, detail FROM audit_log WHERE action != 'login' ORDER BY id") == [
        ('profile_created', 'Mia'), ('vaccination_created', 'Mia'), ('vaccination_changed', 'Mia'),
        ('vaccination_deleted', 'Mia'), ('immunity_created', 'Mia'), ('immunity_deleted', 'Mia'),
        ('profile_changed', 'Mia'), ('profile_deleted', 'Mia')]
    page = client.get('/admin/audit?kind=record').get_data(as_text=True)
    assert 'Impfung erfasst' in page and 'Hexyon' not in page


def test_user_input_is_escaped(client, csrf, login, profile):
    client.post(f'/profiles/{profile}/edit', data={'display_name': '<b>Mia</b>', 'birth_date': '2026-01-10',
                                                   'csrf_token': csrf()})
    add_vaccination(client, csrf, profile, product='other', product_name='<script>x</script>', antigens=['tet'],
                    note='"><img>')
    for url in ['/', f'/profiles/{profile}', f'/profiles/{profile}/print']:
        page = client.get(url).get_data(as_text=True)
        assert '<b>Mia</b>' not in page and '&lt;b&gt;Mia&lt;/b&gt;' in page, url
        assert '<script>x' not in page and '"><img>' not in page


def test_dashboard_lists_what_is_due(client, csrf, login, profile):
    add_vaccination(client, csrf, profile)
    page = client.get('/').get_data(as_text=True)
    assert 'Als Nächstes' in page
    assert 'Pneumokokken' in page and 'überfällig' in page
    assert '9 Monate · 1 Eintrag' in page


def test_print_view(client, csrf, profile):
    add_vaccination(client, csrf, profile, lot='A21CB')
    page = client.get(f'/profiles/{profile}/print').get_data(as_text=True)
    assert 'Impfausweis Mia' in page and 'A21CB' in page
    assert 'Stand 15.10.2026' in page and 'Keine medizinische Beratung' in page
    assert 'Abmelden' not in page and '/static/print.js' in page and '<script>' not in page


def test_schedule_page(client, login):
    login('bob')
    page = client.get('/schedule').get_data(as_text=True)
    assert 'Schweizerischer Impfplan 2026' in page and 'ch-2026 (Stand 16.04.2026)' in page
    assert 'https://www.bag.admin.ch/de/schweizerischer-impfplan' in page
    assert 'zu prüfen' in page and 'keine medizinische Beratung' in page
    assert 'Ergänzende Impfung (65 Jahre und älter)' in page  # pneumococci from 65


def test_invalid_schedule_file_stops_the_start(tmp_path):
    bad = tmp_path / 'bad.yaml'
    bad.write_text('schedule: {}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='bad.yaml'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'), 'SCHEDULE_FILE': str(bad)})


def test_catalogue_must_match_the_schedule(tmp_path):
    from pathlib import Path
    with pytest.raises(ValueError, match='unknown antigen'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'),
                    'SCHEDULE_FILE': str(Path(__file__).parent / 'data' / 'test-schedule.yaml')})


def test_swiss_catalogue(app):
    """The real catalogue: antigens as checked against Swissmedic/SL; unauthorised products grouped last."""
    from helvax import rules
    with app.app_context():
        cat, schedule = rules.catalogue(), rules.schedule()
    assert cat.get('enflonsia').antigens == ('rsv',)
    assert cat.get('twinrix').antigens == ('hav', 'hbv')  # hepatitis A is record-only
    assert cat.get('adacel-polio').antigens == ('dip', 'tet', 'pert', 'polio')
    for key in ('zostavax', 'synagis'):
        assert cat.get(key) is None  # would count as the wrong antigen
    # Vaccines the schedule doesn't model are listed with record-only antigens only
    record_only = dict(cat.groups())['Ohne Auswertung']
    assert {'neisvac-c', 'pneumovax-23', 'bcg', 'arexvy'} <= {p.key for p in record_only}
    assert all(a not in schedule.rules for p in record_only for a in p.antigens)
    groups = cat.groups()
    assert groups[-1][0] == 'Nicht in der Schweiz zugelassen'
    assert {p.key for p in groups[-1][1]} == {'hexyon', 'td-pur', 'rotateq', 'nimenrix', 'trumenba'}
    assert all(p.tags for p in cat.products if p.antigens == ('pcv',))
    assert cat.get('menveo').tags == {'menveo'}


def test_catalogue_must_know_the_product_tags_of_the_schedule():
    from pathlib import Path

    from helvax import catalogue, schedule
    from helvax.schedule import ScheduleError
    sched = schedule.load(Path(__file__).parent.parent / 'helvax' / 'schedules' / 'ch-2026.yaml')
    data = {'products': [{'key': 'x', 'name': 'X', 'group': 'G', 'antigens': ['pcv'], 'tags': ['pcv20']}]}
    with pytest.raises(ScheduleError, match='product tag.*menveo, pcv21'):
        catalogue.parse(data, sched)
    data['products'][0]['tags'] = 'pcv20'
    with pytest.raises(ScheduleError, match='tags must be a list'):
        catalogue.parse(data, sched)


def test_the_product_decides_the_next_dose(client, csrf, login):
    """The catalogue product of an entry reaches the engine: Menveo at 12–23 months needs a second dose."""
    t = login('alice')
    r = client.post('/profiles/new', data={'display_name': 'Lea', 'birth_date': '2025-06-01',
                                           'flags': ['optin:menacwy'], 'csrf_token': t})
    pid = int(r.headers['Location'].rsplit('/', 1)[1])
    add_vaccination(client, csrf, pid, date='2026-06-15', product='menveo')
    page = client.get(f'/profiles/{pid}').get_data(as_text=True)
    text = ' '.join(re.sub(r'<[^>]+>', ' ', page).split())
    assert 'Meningokokken ACWY überfällig 2. Dosis: empfohlen seit 15.08.2026' in text
    other = {'product': 'other', 'product_name': 'Impfstoff aus dem Ausland', 'antigens': ['menacwy']}
    r = client.post('/profiles/new', data={'display_name': 'Tim', 'birth_date': '2025-06-01',
                                           'flags': ['optin:menacwy'], 'csrf_token': csrf()})
    pid = int(r.headers['Location'].rsplit('/', 1)[1])
    add_vaccination(client, csrf, pid, date='2026-06-15', **other)
    page = client.get(f'/profiles/{pid}').get_data(as_text=True)
    assert 'Impfstoff unbekannt; ob eine weitere Dosis nötig ist' in page


def test_record_only_vaccinations(app, client, csrf, query, profile):
    """MenC, BCG etc. are recorded and numbered, but not evaluated: no status, no column status, no immunity."""
    with app.app_context():
        from helvax import profiles
        from helvax.db import get_db
        row = get_db().execute('SELECT * FROM profiles WHERE id = ?', (profile,)).fetchone()
        before = profiles.evaluate(row)[0]
    assert add_vaccination(client, csrf, profile, product='neisvac-c', date='2026-06-01').status_code == 302
    assert add_vaccination(client, csrf, profile, product='other', product_name='BCG Ausland', antigens=['bcg'],
                           date='2026-01-20').status_code == 302
    assert sorted(a for (a,) in query('SELECT antigen FROM vaccination_antigens')) == ['bcg', 'menc']
    with app.app_context():
        row = get_db().execute('SELECT * FROM profiles WHERE id = ?', (profile,)).fetchone()
        statuses, numbers, _ = profiles.evaluate(row)
    assert statuses == before  # not even MenACWY changes
    assert {s.antigen for s in statuses}.isdisjoint({'menc', 'bcg'})
    assert sorted(numbers.values()) == [1, 1]
    page = client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert 'Meningokokken C <span class="dose-no">1</span>' in page
    assert 'Tuberkulose (BCG) <span class="dose-no">1</span>' in page
    assert '<option value="bcg">' not in page  # no immunity for record-only antigens
    r = client.post(f'/profiles/{profile}/immunities', data={'antigen': 'bcg', 'kind': 'disease',
                                                             'csrf_token': csrf()})
    assert r.status_code == 400
    page = client.get(f'/profiles/{profile}?view=table').get_data(as_text=True)
    assert '<span class="sr-only">Meningokokken C: nicht ausgewertet</span>' in page
    assert 'Ohne Status: nur erfasst' in page
    form = client.get(f'/profiles/{profile}/vaccinations/new').get_data(as_text=True)
    assert 'Ohne Auswertung (nur erfasst)' in form and 'value="menc"' in form
    boxes = form.split('id="antigen-boxes"')[1]
    assert boxes.index('value="tbe"') < boxes.index('Ohne Auswertung (nur erfasst)') < boxes.index('value="bcg"')
    schedule_page = client.get('/schedule').get_data(as_text=True)
    assert '<h2>Ohne Auswertung</h2>' in schedule_page and '<li>Tuberkulose (BCG)</li>' in schedule_page


def test_record_as_matrix(client, csrf, profile):
    """?view=table: one row per entry, one column per antigen of the record, dose numbers in the cells."""
    add_vaccination(client, csrf, profile, date='2026-03-10', lot='A21CB')
    add_vaccination(client, csrf, profile, date='2026-05-12')
    add_vaccination(client, csrf, profile, date='2026-05-12', product='prevenar-13')
    page = client.get(f'/profiles/{profile}?view=table').get_data(as_text=True)
    assert 'class="record matrix"' in page and 'aria-current="page">Tabelle</a>' in page
    head = page.split('<thead>')[1].split('</thead>')[0]
    columns = [part.split('</span>')[0].split('">')[1] for part in head.split('<span title="')[1:]]
    # Schedule order: what occurs in the record, plus what is needed now even without any dose yet
    # (Mia, 9 months: MMRV due, RSV due, MenB opted in); nothing that isn't recommended (e.g. HPV)
    assert columns == ['Diphtherie', 'Tetanus', 'Pertussis (Keuchhusten)', 'Kinderlähmung (Polio)',
                       'Haemophilus influenzae Typ b', 'Hepatitis B', 'Pneumokokken', 'Meningokokken B',
                       'Masern', 'Mumps', 'Röteln', 'Windpocken (Varizellen)', 'RSV (Antikörper für Säuglinge)']
    foot = page.split('<tfoot>')[1].split('</tfoot>')[0]
    assert 'class="matrix-status status-due" title="Masern: fällig, empfohlen 10.10.2026"' in foot
    assert 'class="matrix-status status-upcoming" title="Diphtherie: demnächst, empfohlen 10.01.2027"' in foot
    body = page.split('<tbody>')[1].split('</tbody>')[0]
    rows = body.split('<tr>')[1:]
    assert len(rows) == 3 and 'A21CB' in rows[0]
    assert rows[1].count('class="matrix-cell on"') == 6 and rows[1].count('>2<span class="sr-only">') == 6
    assert rows[1].count('class="matrix-cell"></td>') == 7  # pneumococci, MenB, MMRV and RSV not in this vaccine
    assert rows[2].count('class="matrix-cell on"') == 1 and '>1<span class="sr-only"> Pneumokokken' in rows[2]
    assert '/print?view=table' in page
    printed = client.get(f'/profiles/{profile}/print?view=table').get_data(as_text=True)
    assert 'class="record matrix"' in printed and '/vaccinations/' not in printed  # no edit links on paper
    # Default is the list
    assert 'class="record matrix"' not in client.get(f'/profiles/{profile}').get_data(as_text=True)
    assert client.get(f'/profiles/{profile}?view=nope').status_code == 400


def test_matrix_without_entries_shows_what_is_needed(client, csrf, profile):
    page = client.get(f'/profiles/{profile}?view=table').get_data(as_text=True)
    assert 'class="record matrix"' in page and 'Noch keine Impfungen erfasst.' in page
    assert 'title="Diphtherie: überfällig' in page
