import os

import pytest

from helvax import create_app, dates
from helvax.db import get_db


def test_create_user_and_make_admin(app, query):
    runner = app.test_cli_runner()
    r = runner.invoke(args=['create-user', 'Carl', '--name', 'Carl'], input='secret1234\nsecret1234\n')
    assert r.exit_code == 0, r.output
    assert runner.invoke(args=['create-user', 'carl'], input='secret1234\nsecret1234\n').exit_code == 1

    assert runner.invoke(args=['make-admin', 'carl']).exit_code == 0
    assert runner.invoke(args=['make-admin', 'nobody']).exit_code == 1
    assert query("SELECT is_admin FROM users WHERE username = 'carl'") == [(1,)]


def test_revoke_admin(app, query):
    runner = app.test_cli_runner()
    r = runner.invoke(args=['make-admin', 'alice'])
    assert r.exit_code == 0 and 'schon Admin' in r.output
    r = runner.invoke(args=['make-admin', '--revoke', 'bob'])
    assert r.exit_code == 0 and 'kein Admin' in r.output
    r = runner.invoke(args=['make-admin', '--revoke', 'alice'])
    assert r.exit_code == 1 and 'letzte Admin' in r.output
    assert runner.invoke(args=['make-admin', '--revoke', 'nobody']).exit_code == 1
    assert query('SELECT is_admin FROM users ORDER BY id') == [(1,), (0,), (0,)]

    assert runner.invoke(args=['make-admin', 'bob']).exit_code == 0
    r = runner.invoke(args=['make-admin', '--revoke', 'alice'])
    assert r.exit_code == 0 and 'alice ist kein Admin mehr' in r.output
    assert query('SELECT is_admin FROM users ORDER BY id') == [(0,), (1,), (0,)]
    assert query("SELECT username, action, detail FROM audit_log ORDER BY id") == [
        ('Kommandozeile', 'user_admin', 'bob'), ('Kommandozeile', 'user_admin_revoked', 'alice')]


def test_seed_demo_is_idempotent(app, query):
    runner = app.test_cli_runner()
    assert runner.invoke(args=['seed-demo']).exit_code == 0
    assert runner.invoke(args=['seed-demo']).exit_code == 0
    assert query("SELECT username FROM users WHERE username IN ('anna', 'ben') ORDER BY username") == [
        ('anna',), ('ben',)]
    assert query("SELECT COUNT(*) FROM families WHERE name = 'Familie Muster'") == [(1,)]
    assert query("SELECT display_name FROM profiles ORDER BY id") == [('Lena',), ('Noah',), ('Anna',), ('Erika',)]
    assert query('SELECT COUNT(*) FROM vaccinations')[0][0] > 20


def test_first_admin_from_config(tmp_path):
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'new.db'),
                      'ADMIN_USERNAME': 'root', 'ADMIN_PASSWORD': 'secret1234'})
    with app.app_context():
        from helvax.users import get_user_by_username
        assert get_user_by_username('root')['is_admin'] == 1


def test_unwritable_database_gives_clear_error(tmp_path, monkeypatch):
    # Simulates a Docker volume owned by another UID (os.access is always True for root)
    (tmp_path / 'helvax.db').touch()
    monkeypatch.setattr(os, 'access', lambda path, mode: not str(path).endswith('helvax.db'))
    with pytest.raises(RuntimeError, match='helvax.db is not writable for uid'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'helvax.db')})


def test_unwritable_database_without_uids(tmp_path, monkeypatch):
    # Windows has no os.getuid/os.getgid
    monkeypatch.delattr(os, 'getuid')
    monkeypatch.delattr(os, 'getgid')
    create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'helvax.db')})

    monkeypatch.setattr(os, 'access', lambda path, mode: not str(path).endswith('helvax.db'))
    with pytest.raises(RuntimeError, match=r'helvax.db is not writable\. SQLite'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'helvax.db')})


# --- list-products, import-vaccinations ------------------------------------------------------------

def test_list_products(app):
    runner = app.test_cli_runner()
    r = runner.invoke(args=['list-products'])
    assert r.exit_code == 0, r.output
    assert 'Kombinationsimpfstoffe' in r.output and 'infanrix-hexa' in r.output
    r = runner.invoke(args=['list-products', 'BOOST'])
    assert 'boostrix' in r.output and 'infanrix-hexa' not in r.output
    assert runner.invoke(args=['list-products', 'gibtsnicht']).exit_code == 1
    r = runner.invoke(args=['list-products', '--antigens'])
    assert 'measles' in r.output and 'Masern' in r.output
    assert 'bcg           Tuberkulose (BCG) (nur erfasst, ohne Auswertung)' in r.output


@pytest.fixture
def lena(app):
    """Profile 'Lena' (born 2020-01-10) in family 1 with one entry; carol's family has a 'Lena' too."""
    from helvax.profiles import create_profile
    with app.app_context(), get_db() as con:
        pid = create_profile(con, 1, 'Lena', dates.parse_day('2020-01-10'))
        create_profile(con, 2, 'Lena', dates.parse_day('2019-01-10'))
        con.execute("INSERT INTO vaccinations (profile_id, date, product_key, product_name, lot, administered_by, "
                    "note, created_at, updated_at) VALUES (?, '2020-03-10', 'infanrix-hexa', 'Infanrix hexa', "
                    "'', '', '', '', '')", (pid,))
    return pid


def run_import(app, tmp_path, text, *args, encoding='utf-8'):
    path = tmp_path / 'import.csv'
    path.write_bytes(text.encode(encoding))
    return app.test_cli_runner().invoke(args=['import-vaccinations', *args, str(path)])


def test_import_vaccinations(app, tmp_path, lena, query):
    text = ('﻿date;product;product_name;antigens;doses;lot;administered_by;note\n'
            '10.05.2020;infanrix-hexa;;;;A1;Dr. Test;\n'
            '2021-02-01;priorix-tetra;;;measles=2;;;\n'
            '\n'
            '1.3.2022;other;Testimpfstoff;dip+tet  polio;;;;aus dem Ausland\n'
            '2020-03-10;infanrix-hexa;;;;;;\n')
    r = run_import(app, tmp_path, text, 'bob', 'lena', '--dry-run')
    assert r.exit_code == 0, r.output
    assert 'Übersprungen: Zeile 6' in r.output and 'nichts gespeichert' in r.output
    assert query('SELECT COUNT(*) FROM vaccinations WHERE profile_id = ?', lena) == [(1,)]

    r = run_import(app, tmp_path, text, 'bob', str(lena), '--yes')
    assert r.exit_code == 0, r.output
    assert '3 Impfung(en) für Lena gespeichert' in r.output
    assert query('SELECT date, product_key, product_name, lot, administered_by, note FROM vaccinations '
                 'WHERE profile_id = ? ORDER BY date', lena) == [
        ('2020-03-10', 'infanrix-hexa', 'Infanrix hexa', '', '', ''),
        ('2020-05-10', 'infanrix-hexa', 'Infanrix hexa', 'A1', 'Dr. Test', ''),
        ('2021-02-01', 'priorix-tetra', 'Priorix-Tetra (MMRV)', '', '', ''),
        ('2022-03-01', None, 'Testimpfstoff', '', '', 'aus dem Ausland')]
    assert query("SELECT a.antigen, a.dose_override FROM vaccination_antigens a JOIN vaccinations v "
                 "ON v.id = a.vaccination_id WHERE v.date = '2022-03-01' ORDER BY a.antigen") == [
        ('dip', None), ('polio', None), ('tet', None)]
    assert query("SELECT a.dose_override FROM vaccination_antigens a JOIN vaccinations v "
                 "ON v.id = a.vaccination_id WHERE v.date = '2021-02-01' AND a.antigen = 'measles'") == [(2,)]
    assert query("SELECT username, action, detail FROM audit_log WHERE action = 'vaccinations_imported'") == [
        ('Kommandozeile (bob)', 'vaccinations_imported', 'Lena: 3')]

    # Importing the same file again adds nothing
    r = run_import(app, tmp_path, text, 'bob', 'Lena', '--yes')
    assert r.exit_code == 0 and 'Keine neuen Impfungen' in r.output
    assert query('SELECT COUNT(*) FROM vaccinations WHERE profile_id = ?', lena) == [(4,)]


def test_import_asks_before_saving(app, tmp_path, lena, query):
    text = 'date,product\n2020-05-10,boostrix\n'
    r = run_import(app, tmp_path, text, 'bob', 'Lena')
    assert r.exit_code == 1 and 'speichern?' in r.output  # no input: aborted
    assert query('SELECT COUNT(*) FROM vaccinations') == [(1,)]
    path = tmp_path / 'import.csv'
    r = app.test_cli_runner().invoke(args=['import-vaccinations', 'bob', 'Lena', str(path)], input='y\n')
    assert r.exit_code == 0, r.output
    assert query('SELECT COUNT(*) FROM vaccinations') == [(2,)]


def test_import_windows_1252(app, tmp_path, lena, query):
    r = run_import(app, tmp_path, 'date;product;note\n10.05.2020;boostrix;Röteln-Titer\n', 'bob', 'Lena', '-y',
                   encoding='cp1252')
    assert r.exit_code == 0, r.output
    assert query("SELECT note FROM vaccinations WHERE product_key = 'boostrix'") == [('Röteln-Titer',)]


@pytest.mark.parametrize('row, message', [
    ('31.02.2020;boostrix;;;', 'gültiges Impfdatum'),
    ('2019-12-31;boostrix;;;', 'vor dem Geburtsdatum'),
    ('2026-10-16;boostrix;;;', 'in der Zukunft'),
    ('2020-05-10;;;;', 'Unbekannter Impfstoff'),
    ('2020-05-10;gibtsnicht;;;', 'Unbekannter Impfstoff «gibtsnicht»'),
    ('2020-05-10;boostrix;;dip;', 'nur zusammen mit product «other»'),
    ('2020-05-10;other;;dip;', 'Namen des Impfstoffs'),
    ('2020-05-10;other;X;;', 'wogegen der Impfstoff'),
    ('2020-05-10;other;X;dip ebola;', 'Unbekanntes Antigen: ebola'),
    ('2020-05-10;boostrix;;;pert', 'antigen=Nummer'),
    ('2020-05-10;boostrix;;;pert=0', 'Zahl von 1 bis'),
    ('2020-05-10;boostrix;;;hib=2', 'hib gehört nicht'),
    ('2020-05-10;boostrix;;;;zuviel', 'mehr Werte als Spalten'),
])
def test_import_rejects_invalid_rows(app, tmp_path, lena, query, row, message):
    text = 'date;product;product_name;antigens;doses\n2020-05-10;boostrix;;;\n' + row + '\n'
    r = run_import(app, tmp_path, text, 'bob', 'Lena', '--yes')
    assert r.exit_code == 1
    assert 'Zeile 3: ' in r.output and message in r.output, r.output
    assert 'nichts gespeichert' in r.output
    assert query('SELECT COUNT(*) FROM vaccinations') == [(1,)]  # the valid row isn't saved either


@pytest.mark.parametrize('text, message', [
    ('datum;product\n', 'Unbekannte Spalte(n): datum'),
    ('date;lot\n', 'Spalte(n) fehlen: product'),
    ('', 'Spalte(n) fehlen: date, product'),
])
def test_import_rejects_bad_header(app, tmp_path, lena, text, message):
    r = run_import(app, tmp_path, text, 'bob', 'Lena')
    assert r.exit_code == 1 and message in r.output, r.output


def test_import_only_into_own_family(app, tmp_path, lena, query):
    text = 'date;product\n2020-05-10;boostrix\n'
    r = run_import(app, tmp_path, text, 'carol', str(lena), '-y')  # bob's Lena, by id
    assert r.exit_code == 1 and 'nicht gefunden' in r.output
    r = run_import(app, tmp_path, text, 'carol', 'Lena', '-y')  # carol's own Lena
    assert r.exit_code == 0, r.output
    assert query("SELECT p.family_id FROM vaccinations v JOIN profiles p ON p.id = v.profile_id "
                 "WHERE v.product_key = 'boostrix'") == [(2,)]
    assert run_import(app, tmp_path, text, 'nobody', 'Lena').exit_code == 1


def test_import_ambiguous_and_familyless(app, tmp_path, lena):
    from helvax.profiles import create_profile
    with app.app_context(), get_db() as con:
        create_profile(con, 1, 'Lena', dates.parse_day('2021-01-10'))
        con.execute("UPDATE users SET family_id = NULL WHERE username = 'alice'")
    text = 'date;product\n2021-05-10;boostrix\n'
    r = run_import(app, tmp_path, text, 'bob', 'Lena')
    assert r.exit_code == 1 and 'Mehrere Profile' in r.output
    r = run_import(app, tmp_path, text, 'alice', 'Lena')
    assert r.exit_code == 1 and 'keiner Familie' in r.output


# --- convert-evacdoc -------------------------------------------------------------------------------

EVACDOC = os.path.join(os.path.dirname(__file__), 'data', 'evacdoc-test.xml')


def test_convert_evacdoc(app, tmp_path, lena, query):
    target = tmp_path / 'out.csv'
    r = app.test_cli_runner().invoke(args=['convert-evacdoc', EVACDOC, str(target)])
    assert r.exit_code == 0, r.output
    assert target.read_bytes().startswith(b'\xef\xbb\xbfdate;product;product_name;antigens;doses;lot;')
    assert target.read_text(encoding='utf-8-sig').splitlines()[1:] == [
        '05.01.2020;other;Testpolio oral;polio;;;;',
        '05.01.2020;other;Boostrix;dip tet;;;;',
        '05.01.2020;bcg;;;;;;',  # record-only antigen, matched to the catalogue product
        '10.02.2020;other;Testkombi;dip;;;;',
        '01.06.2020;fsme-immun;;;;;;',
        '15.03.2021;boostrix;;;;TEST01;T. Muster;']
    out = r.output
    assert 'Testkombi vom 10.02.2020: Meningococcus vaccination (procedure) hat kein Antigen' in out
    assert 'nur der Rest übernommen' in out
    assert 'BCG vom' not in out
    assert out.count('nicht verabreicht oder nicht abgeschlossen') == 2
    assert 'Revaxis vom (ohne Datum): kein Datum' in out
    assert 'Abschnitt «Bisherige Krankheiten»' in out and 'Abschnitt «Impfplan»' not in out
    assert '6 Impfung(en)' in out

    # The result is accepted by the import (Lena is born 2020-01-10: the rows from 05.01.2020 are too early)
    r = app.test_cli_runner().invoke(args=['import-vaccinations', 'bob', 'Lena', str(target), '--dry-run'])
    assert r.exit_code == 1 and r.output.count('vor dem Geburtsdatum') == 3, r.output
    with app.app_context(), get_db() as con:
        con.execute("UPDATE profiles SET birth_date = '2019-01-01' WHERE id = ?", (lena,))
    r = app.test_cli_runner().invoke(args=['import-vaccinations', 'bob', 'Lena', str(target), '--yes'])
    assert r.exit_code == 0 and '6 Impfung(en) für Lena gespeichert' in r.output, r.output


@pytest.mark.parametrize('content, message', [
    (b'kein xml', 'Kein gültiges XML'),
    (b'<root/>', 'Kein HL7-CDA-Dokument'),
    (b'<ClinicalDocument xmlns="urn:hl7-org:v3"/>', 'keinen Abschnitt «Impfungen»'),
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>', 'DOCTYPE'),
])
def test_convert_evacdoc_rejects_other_files(app, tmp_path, content, message):
    source = tmp_path / 'in.xml'
    source.write_bytes(content)
    r = app.test_cli_runner().invoke(args=['convert-evacdoc', str(source), str(tmp_path / 'out.csv')])
    assert r.exit_code == 1 and message in r.output, r.output
    assert not (tmp_path / 'out.csv').exists()
