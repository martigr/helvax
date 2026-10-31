"""Fictional demo data for `flask seed-demo` (local development and screenshots only).

All names and dates are made up. Tests don't use this; they build their own state in fixtures.
"""
import sqlite3

from . import audit
from .db import get_db
from .users import create_user, get_user_by_username

FAMILY = 'Familie Muster'
USERS = [
    # username, password, display name, admin
    ('anna', 'password123', 'Anna', True),
    ('ben', 'password456', 'Ben', False),
]


def seed():
    """Idempotent: creates what is missing."""
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
    return family_id
