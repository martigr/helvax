import secrets
from datetime import datetime

import pytest

from helvax import create_app, dates, mail
from helvax.db import get_db
from helvax.users import create_user

# Fixed clock for all tests
NOW = datetime(2026, 10, 15, 14, 20)
PASSWORD = 'secret1234'

MAIL_CONFIG = {'SMTP_HOST': 'smtp.test', 'MAIL_FROM': 'helvax@example.ch', 'BASE_URL': 'https://hx.test'}


@pytest.fixture
def mailbox(monkeypatch):
    """Mails 'sent' during the test as (to, subject, body); nothing reaches a real server."""
    sent = []
    monkeypatch.setattr(mail, 'deliver', lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture
def app(tmp_path, monkeypatch, mailbox):
    """alice (admin) and bob in family 'Muster' (id 1), carol in family 'Beispiel' (id 2)."""
    monkeypatch.setattr(dates, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'test.db'), **MAIL_CONFIG})
    with app.app_context():
        create_user('alice', PASSWORD, 'Alice', is_admin=True)
        create_user('bob', PASSWORD, 'Bob')
        create_user('carol', PASSWORD, 'Carol')
        with get_db() as con:
            con.execute("INSERT INTO families (id, name) VALUES (1, 'Muster'), (2, 'Beispiel')")
            con.execute("UPDATE users SET family_id = 1 WHERE username IN ('alice', 'bob')")
            con.execute("UPDATE users SET family_id = 2 WHERE username = 'carol'")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def query(app):
    """Run a read query against the test database."""
    def run(sql, *params):
        with app.app_context():
            return [tuple(r) for r in get_db().execute(sql, params).fetchall()]
    return run


@pytest.fixture
def csrf(client):
    """csrf() -> the CSRF token of the client's session (or of another client)."""
    def get(c=client):
        with c.session_transaction() as s:
            return s.setdefault('csrf', secrets.token_urlsafe(16))
    return get


@pytest.fixture
def login(client, csrf):
    """login('bob') logs the client in and returns a fresh CSRF token."""
    def run(username, password=PASSWORD, c=client):
        c.post('/login', data={'username': username, 'password': password, 'csrf_token': csrf(c)})
        return csrf(c)
    return run
