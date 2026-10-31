"""Fictional demo data for `flask seed-demo` (local development and screenshots only).

All names, dates and lot numbers are made up. Dates are relative to today, so the dashboard always shows
something due. Tests don't use this; they build their own state in fixtures.
"""
import sqlite3
from datetime import timedelta

from . import audit, dates
from .dates import add_months
from .db import get_db
from .profiles import create_entry, save_flags
from .users import create_user, get_user_by_username

FAMILY = 'Familie Muster'
USERS = [
    # username, password, display name, admin
    ('anna', 'password123', 'Anna', True),
    ('ben', 'password456', 'Ben', False),
]
HEXA = ('infanrix-hexa', 'Infanrix hexa', ['dip', 'tet', 'pert', 'polio', 'hib', 'hbv'])
PCV = ('prevenar-13', 'Prevenar 13', ['pcv'])
MMRV = ('priorix-tetra', 'Priorix-Tetra (MMRV)', ['measles', 'mumps', 'rubella', 'varicella'])
DTPA_IPV = ('boostrix-polio', 'Boostrix Polio (dTpa-IPV)', ['dip', 'tet', 'pert', 'polio'])
DTPA = ('boostrix', 'Boostrix (dTpa)', ['dip', 'tet', 'pert'])
BEXSERO = ('bexsero', 'Bexsero (MenB)', ['menb'])
ROTARIX = ('rotarix', 'Rotarix', ['rota'])


def _months(birth, n):
    return add_months(birth, n)


def _years(birth, n):
    return add_months(birth, 12 * n)


def _profiles(today):
    """(name, birth date, flags, [(date, product, note)]) of the demo family."""
    lena = add_months(today, -5) - timedelta(days=12)
    noah = add_months(today, -12 * 6 - 3)
    anna = add_months(today, -12 * 38 - 2)
    erika = add_months(today, -12 * 68 - 7)
    return [
        ('Lena', lena, ['optin:menb', 'optin:rota'], [
            (_months(lena, 2), HEXA, ''), (_months(lena, 2), PCV, ''), (_months(lena, 2), ROTARIX, 'oral'),
            (_months(lena, 3), BEXSERO, ''),
            (_months(lena, 4), HEXA, ''), (_months(lena, 4), PCV, ''), (_months(lena, 4), ROTARIX, 'oral'),
        ]),
        ('Noah', noah, ['fsme_exposure'], [
            (_months(noah, 2), HEXA, ''), (_months(noah, 2), PCV, ''),
            (_months(noah, 4), HEXA, ''), (_months(noah, 4), PCV, ''),
            (_months(noah, 9), MMRV, ''),
            (_months(noah, 12), HEXA, ''), (_months(noah, 12), PCV, ''), (_months(noah, 12), MMRV, ''),
        ]),
        ('Anna', anna, ['fsme_exposure'], [
            (_months(anna, 2), HEXA, 'Nachgetragen aus dem alten Impfbüchlein'), (_months(anna, 4), HEXA, ''),
            (_months(anna, 12), HEXA, ''), (_years(anna, 5), DTPA_IPV, ''), (_years(anna, 12), DTPA, ''),
            (_months(anna, 15), MMRV, ''), (_months(anna, 18), MMRV, ''),
            (_years(anna, 25), DTPA, 'Auffrischimpfung mit 25'),
        ]),
        ('Erika', erika, ['optin:influenza', 'optin:covid19', 'optin:zoster', 'optin:pcv'], [
            (_months(erika, 3), ('other', 'DT-Impfstoff', ['dip', 'tet']), 'Nachgetragen aus dem alten Impfbüchlein'),
            (_months(erika, 5), ('other', 'DT-Impfstoff', ['dip', 'tet']), ''),
            (_months(erika, 14), ('other', 'DT-Impfstoff', ['dip', 'tet']), ''),
            (_years(erika, 7), ('other', 'Polio-Schluckimpfung', ['polio']), ''),
            (_years(erika, 7) + timedelta(days=60), ('other', 'Polio-Schluckimpfung', ['polio']), ''),
            (_years(erika, 8), ('other', 'Polio-Schluckimpfung', ['polio']), ''),
            (_years(erika, 50), ('td-pur', 'Td-pur (dT)', ['dip', 'tet']), ''),
            (_years(erika, 66) + timedelta(days=30), ('shingrix', 'Shingrix', ['zoster']), ''),
            (add_months(today, -12) - timedelta(days=5), ('grippe', 'Grippeimpfstoff (Saison)', ['influenza']), ''),
        ]),
    ]


def seed():
    """Idempotent: creates the family, the accounts and, if the family has none yet, the profiles."""
    today = dates.today()
    with get_db() as con:
        try:
            con.execute('INSERT INTO families (name) VALUES (?)', (FAMILY,))
        except sqlite3.IntegrityError:
            pass
        family_id = con.execute('SELECT id FROM families WHERE name = ?', (FAMILY,)).fetchone()['id']
    for username, password, name, is_admin in USERS:
        if get_user_by_username(username) is None:
            create_user(username, password, name, is_admin)
            with get_db() as con:
                audit.record(con, 'user_created', username, username='Kommandozeile')
        with get_db() as con:
            con.execute('UPDATE users SET family_id = ? WHERE username = ?', (family_id, username))
    with get_db() as con:
        if con.execute('SELECT 1 FROM profiles WHERE family_id = ?', (family_id,)).fetchone():
            return family_id
        for name, birth, flags, entries in _profiles(today):
            pid = con.execute('INSERT INTO profiles (family_id, display_name, birth_date, created_at) '
                              'VALUES (?, ?, ?, ?)', (family_id, name, birth.isoformat(),
                                                      dates.now().isoformat(timespec='seconds'))).lastrowid
            save_flags(con, pid, flags)
            for i, (day, (key, product_name, antigens), note) in enumerate(entries):
                if day > today:
                    continue
                create_entry(con, pid, {'date': day, 'product_key': None if key == 'other' else key,
                                        'product_name': product_name,
                                        'antigens': antigens, 'overrides': {}, 'lot': f'DEMO{100 + i}',
                                        'administered_by': 'Praxis Beispiel', 'note': note})
        con.execute('INSERT OR IGNORE INTO immunities (profile_id, antigen, kind, note) '
                    "SELECT id, 'varicella', 'disease', 'als Kind' FROM profiles "
                    "WHERE family_id = ? AND display_name = 'Erika'", (family_id,))
    return family_id
