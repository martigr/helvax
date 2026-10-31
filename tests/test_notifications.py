import smtplib
import sqlite3
from datetime import datetime

import pytest

from helvax import create_app, dates, mail, notifications
from helvax.db import get_db

REAL_DELIVER = mail.deliver  # captured before the mailbox fixture replaces it
NOW = datetime(2026, 10, 15, 14, 20)


def set_prefs(client, login, username, email, verified=True):
    t = login(username)
    client.post('/account', data={'action': 'email', 'email': email, 'csrf_token': t})
    if verified:
        verify(client.application, username)
    return t


def verify(app, username):
    """Mark the address as confirmed and drop the verification mail, as if the link was used."""
    with app.app_context(), get_db() as con:
        con.execute('UPDATE users SET email_verified = 1 WHERE username = ?', (username,))
        con.execute("DELETE FROM notifications WHERE dedupe_key LIKE 'verify:%'")


def run(app):
    with app.app_context():
        return notifications.run_once()


def at(monkeypatch, *args):
    monkeypatch.setattr(dates, 'now', lambda: datetime(*args))


def link_in(body, prefix):
    return next(word for word in body.split() if word.startswith('https://hx.test' + prefix))[len('https://hx.test'):]


def test_password_reset(app, client, csrf, login, mailbox, monkeypatch):
    set_prefs(client, login, 'bob', 'Bob@Example.ch')
    client.post('/logout', data={'csrf_token': csrf()})

    for identifier in ['bob@example.ch', 'nobody']:
        r = client.post('/forgot-password', data={'identifier': identifier, 'csrf_token': csrf()},
                        follow_redirects=True)
        assert 'Falls zu diesem Konto' in r.get_data(as_text=True)
    run(app)
    assert [m[0] for m in mailbox] == ['Bob@Example.ch']
    path = link_in(mailbox[0][2], '/reset-password/')

    assert client.get(path).status_code == 200
    client.post(path, data={'password': 'brandnew12', 'csrf_token': csrf()})
    login('bob', 'brandnew12')
    assert client.get('/').status_code == 200
    assert client.get(path).status_code == 400  # single use


def test_password_reset_link_expires(app, client, csrf, login, mailbox, monkeypatch):
    set_prefs(client, login, 'bob', 'bob@example.ch')
    client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})
    run(app)
    path = link_in(mailbox[0][2], '/reset-password/')

    import itsdangerous.timed
    real = itsdangerous.timed.TimestampSigner.get_timestamp
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, 'get_timestamp', lambda self: real(self) + 2 * 3600)
    assert client.get(path).status_code == 400


def test_failed_mail_is_retried(app, client, csrf, login, mailbox, monkeypatch, query):
    set_prefs(client, login, 'bob', 'bob@example.ch')
    client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})

    def broken(*args):
        raise OSError('connection refused')
    monkeypatch.setattr(mail, 'deliver', broken)
    assert run(app) == (0, 1)
    assert query('SELECT attempts, last_error, sent_at FROM notifications') == [(1, 'connection refused', None)]

    monkeypatch.setattr(mail, 'deliver', lambda *a: mailbox.append(a))
    assert run(app) == (1, 0)


def test_without_mail_config_nothing_is_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(dates, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'x.db')})
    client = app.test_client()
    assert client.get('/forgot-password').status_code == 404
    assert 'Passwort vergessen' not in client.get('/login').get_data(as_text=True)
    with app.app_context():
        assert notifications.run_once() == (0, 0)
    assert app.test_cli_runner().invoke(args=['send-notifications']).exit_code == 0


def test_admin_creates_user_with_email(client, login, query):
    t = login('alice')
    client.post('/admin/users', data={'username': 'dave', 'password': 'secret1234', 'email': 'dave@example.ch',
                                      'csrf_token': t})
    client.post('/admin/users', data={'username': 'dora', 'password': 'secret1234', 'email': 'no-at-sign',
                                      'csrf_token': t})
    assert query("SELECT username, email FROM users WHERE username IN ('dave', 'dora')") == \
        [('dave', 'dave@example.ch')]


def test_columns_are_added_to_an_existing_database(tmp_path, monkeypatch):
    """db.ADDED_COLUMNS is applied to databases created before a column existed (idempotently)."""
    from helvax import db
    path = tmp_path / 'old.db'
    create_app({'TESTING': True, 'DATABASE': str(path)})
    monkeypatch.setattr(db, 'ADDED_COLUMNS', [('profiles', 'example', "TEXT NOT NULL DEFAULT 'x'")])
    con = sqlite3.connect(path)
    con.execute("INSERT INTO families (name) VALUES ('F')")
    con.execute("INSERT INTO profiles (family_id, display_name, birth_date, created_at) "
                "VALUES (1, 'P', '2020-01-01', '')")
    con.commit()
    con.close()
    create_app({'TESTING': True, 'DATABASE': str(path)})
    create_app({'TESTING': True, 'DATABASE': str(path)})
    con = sqlite3.connect(path)
    assert con.execute('SELECT display_name, example FROM profiles').fetchall() == [('P', 'x')]


@pytest.mark.parametrize('security', ['starttls', 'ssl', 'none'])
def test_deliver_uses_smtp_settings(app, monkeypatch, security):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, **kwargs):
            calls.append(('connect', host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def starttls(self, **kwargs):
            calls.append(('starttls',))

        def login(self, user, password):
            calls.append(('login', user, password))

        def send_message(self, msg):
            calls.append(('send', msg['To'], msg['From'], msg['Subject']))

    monkeypatch.setattr(smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTP)
    app.config.update(SMTP_HOST='mail.infomaniak.com', SMTP_PORT=587, SMTP_SECURITY=security,
                      SMTP_USER='helvax@example.ch', SMTP_PASSWORD='pw')
    with app.app_context():
        REAL_DELIVER('bob@example.ch', 'Betreff', 'Text')
    assert calls[0] == ('connect', 'mail.infomaniak.com', 587)
    assert (('starttls',) in calls) == (security == 'starttls')
    assert ('login', 'helvax@example.ch', 'pw') in calls
    assert calls[-1] == ('send', 'bob@example.ch', 'helvax <helvax@example.ch>', 'Betreff')


def test_new_address_must_be_confirmed(app, client, login, csrf, mailbox, monkeypatch, query):
    set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    assert 'Noch nicht bestätigt' in client.get('/account').get_data(as_text=True)
    run(app)
    assert [m[1] for m in mailbox] == ['helvax: E-Mail-Adresse bestätigen']

    path = link_in(mailbox[0][2], '/verify-email/')
    other = app.test_client()  # works without being logged in
    assert 'Ja, bestätigen' in other.get(path).get_data(as_text=True)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(0,)]  # GET changes nothing
    r = other.post(path, data={'csrf_token': csrf(other)})
    assert 'Adresse bestätigt' in r.get_data(as_text=True)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(1,)]
    assert 'Noch nicht bestätigt' not in client.get('/account').get_data(as_text=True)

    # A changed address needs a new confirmation, and the old link no longer fits
    set_prefs(client, login, 'bob', 'bob@neu.example.ch', verified=False)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(0,)]
    assert other.post(path, data={'csrf_token': csrf(other)}).status_code == 400
    run(app)
    assert [m[0] for m in mailbox if 'bestätigen' in m[1]] == ['bob@example.ch', 'bob@neu.example.ch']


def test_reset_mail_only_to_confirmed_address(app, client, csrf, login, mailbox):
    set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    client.post('/logout', data={'csrf_token': csrf()})
    client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})
    run(app)
    assert [m[1] for m in mailbox] == ['helvax: E-Mail-Adresse bestätigen']


def test_verification_mail_can_be_resent(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    client.post('/account', data={'action': 'verify_email', 'csrf_token': t})  # within 15 min: not again
    at(monkeypatch, 2026, 10, 15, 14, 40)
    r = client.post('/account', data={'action': 'verify_email', 'csrf_token': t}, follow_redirects=True)
    assert 'Bestätigungsmail an bob@example.ch verschickt' in r.get_data(as_text=True)
    run(app)
    assert len(mailbox) == 2


def test_invalid_or_expired_verification_link(app, client, login, mailbox, monkeypatch):
    assert client.get('/verify-email/forged').status_code == 400
    set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    run(app)
    path = link_in(mailbox[0][2], '/verify-email/')
    import itsdangerous.timed
    real = itsdangerous.timed.TimestampSigner.get_timestamp
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, 'get_timestamp', lambda self: real(self) + 8 * 86400)
    assert client.get(path).status_code == 400


def test_admin_created_address_must_be_confirmed(app, client, login, mailbox, query):
    t = login('alice')
    client.post('/admin/users', data={'username': 'dave', 'password': 'secret1234', 'email': 'dave@example.ch',
                                      'csrf_token': t})
    assert '(unbestätigt)' in client.get('/admin').get_data(as_text=True)
    run(app)
    assert [(m[0], m[1]) for m in mailbox] == [('dave@example.ch', 'helvax: E-Mail-Adresse bestätigen')]
