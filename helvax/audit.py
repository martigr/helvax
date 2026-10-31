"""Audit log: logins (also failed ones), changes to profiles and vaccination entries, settings.

Entries are written in the caller's transaction with `record(con, ...)`. Each write also deletes
entries older than the `audit_days` setting, so the table rotates without a separate job.
"""
from datetime import timedelta

from flask import g, has_request_context, request

from . import dates
from .db import DEFAULT_SETTINGS

# action: (German label, kind)
ACTIONS = {
    'login': ('Angemeldet', 'login'),
    'login_failed': ('Anmeldung fehlgeschlagen', 'login'),
    'login_blocked': ('Anmeldung blockiert', 'login'),
    'logout': ('Abgemeldet', 'login'),
    'password_reset_link': ('Passwort per Mail-Link zurückgesetzt', 'login'),
    'profile_created': ('Profil angelegt', 'record'),
    'profile_changed': ('Profil geändert', 'record'),
    'profile_deleted': ('Profil gelöscht', 'record'),
    'vaccination_created': ('Impfung erfasst', 'record'),
    'vaccination_changed': ('Impfung geändert', 'record'),
    'vaccination_deleted': ('Impfung gelöscht', 'record'),
    'immunity_created': ('Immunität erfasst', 'record'),
    'immunity_deleted': ('Immunität gelöscht', 'record'),
    'display_name': ('Anzeigename geändert', 'settings'),
    'email_changed': ('E-Mail-Adresse geändert', 'settings'),
    'email_confirmed': ('E-Mail-Adresse bestätigt', 'settings'),
    'password_changed': ('Passwort geändert', 'settings'),
    'reminders': ('Erinnerungen geändert', 'settings'),
    'unsubscribe': ('Mail-Erinnerungen abbestellt', 'settings'),
    'push_enabled': ('Push auf einem Gerät aktiviert', 'settings'),
    'push_disabled': ('Push auf einem Gerät deaktiviert', 'settings'),
    'user_created': ('Benutzer angelegt', 'admin'),
    'user_deleted': ('Benutzer gelöscht', 'admin'),
    'user_password_reset': ('Passwort zurückgesetzt', 'admin'),
    'user_admin': ('Admin-Rechte vergeben', 'admin'),
    'family_assigned': ('Familie zugeordnet', 'admin'),
    'family_created': ('Familie angelegt', 'admin'),
    'family_deleted': ('Familie gelöscht', 'admin'),
    'settings': ('Einstellungen geändert', 'admin'),
}
KINDS = {'login': 'Anmeldungen', 'record': 'Impfausweise', 'settings': 'Konto', 'admin': 'Verwaltung'}
MAX_TEXT = 200  # usernames of failed logins are whatever was typed


def record(con, action, detail='', user=None, username=None):
    """Add an entry. `user` is who acted (default: the logged-in user, False: nobody);
    `username` overrides the name, e.g. what was typed at a failed login."""
    if action not in ACTIONS:
        raise ValueError(f'unknown audit action {action!r}')
    in_request = has_request_context()
    if user is None and in_request:
        user = g.get('user')
    now = dates.now()
    con.execute('INSERT INTO audit_log (at, user_id, username, action, detail, ip) VALUES (?, ?, ?, ?, ?, ?)',
                (now.isoformat(timespec='seconds'), user['id'] if user else None,
                 (username if username is not None else user['username'] if user else '')[:MAX_TEXT],
                 action, detail[:MAX_TEXT], request.remote_addr if in_request else None))
    prune(con, now)


def retention_days(con):
    row = con.execute("SELECT value FROM settings WHERE key = 'audit_days'").fetchone()
    return int(row['value'] if row else DEFAULT_SETTINGS['audit_days'])


def prune(con, now):
    con.execute('DELETE FROM audit_log WHERE at < ?',
                ((now - timedelta(days=retention_days(con))).isoformat(timespec='seconds'),))


def recently_logged(con, action, username, since):
    """True if `action` was logged for this username and client address after `since`."""
    return con.execute('SELECT 1 FROM audit_log WHERE action = ? AND username = ? AND ip IS ? AND at > ?',
                       (action, username[:MAX_TEXT], request.remote_addr,
                        since.isoformat(timespec='seconds'))).fetchone() is not None
