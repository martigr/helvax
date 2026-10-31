import logging
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from helvax import auth, create_app, dates
from helvax.db import get_db

NOW = datetime(2026, 10, 15, 14, 20)
PASSWORD = 'secret1234'


def post_login(client, csrf, username, password, **kwargs):
    return client.post('/login', data={'username': username, 'password': password, 'csrf_token': csrf(client)},
                       **kwargs)


# --- Headers -------------------------------------------------------------------------------------

def test_security_headers(client, login):
    login('bob')
    h = client.get('/').headers
    assert "script-src 'self'" in h['Content-Security-Policy']
    assert "frame-ancestors 'none'" in h['Content-Security-Policy']
    assert h['X-Frame-Options'] == 'DENY'
    assert h['X-Content-Type-Options'] == 'nosniff'
    assert h['Referrer-Policy'] == 'no-referrer'
    assert h['Cache-Control'] == 'no-store'
    assert h['X-Robots-Tag'] == 'noindex, nofollow'
    assert 'Strict-Transport-Security' not in h  # plain http


def test_hsts_only_over_https(client):
    assert client.get('/login', base_url='https://localhost').headers['Strict-Transport-Security']


def test_static_files_stay_cacheable(client):
    r = client.get('/static/style.css')
    assert r.headers.get('Cache-Control') != 'no-store'
    assert r.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('url', ['/login', '/privacy', '/.well-known/security.txt', '/static/style.css', '/sw.js',
                                 '/does-not-exist'])
def test_public_responses_are_noindex(client, url):
    assert client.get(url).headers['X-Robots-Tag'] == 'noindex, nofollow'


def test_no_inline_scripts(client, login):
    """The CSP blocks inline scripts, so the templates must not have any."""
    login('bob')
    for url in ['/', '/account', '/admin', '/login', '/privacy']:
        assert '<script>' not in client.get(url).get_data(as_text=True)
    login('alice')
    assert '<script>' not in client.get('/admin').get_data(as_text=True)


# --- Input limits --------------------------------------------------------------------------------

def test_large_requests_are_rejected(client, csrf):
    r = post_login(client, csrf, 'bob', 'x' * 100_000)
    assert r.status_code == 413


def test_password_length_limits(client, login, query):
    t = login('alice')
    for password in ['a' * 9, 'a' * 129]:
        client.post('/admin/users', data={'username': 'carl', 'password': password, 'csrf_token': t})
    assert query("SELECT COUNT(*) FROM users WHERE username = 'carl'") == [(0,)]
    client.post('/admin/users', data={'username': 'carl', 'password': 'a' * 128, 'csrf_token': t})
    assert query("SELECT COUNT(*) FROM users WHERE username = 'carl'") == [(1,)]


def test_short_admin_password_from_config_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match='ADMIN_PASSWORD'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'new.db'),
                    'ADMIN_USERNAME': 'root', 'ADMIN_PASSWORD': 'short'})


# --- Login throttling ----------------------------------------------------------------------------

def test_login_is_blocked_after_too_many_failures(client, csrf, monkeypatch):
    for _ in range(auth.MAX_FAILURES_PER_USER):
        assert post_login(client, csrf, 'bob', 'wrong').status_code == 200
    r = post_login(client, csrf, 'Bob', PASSWORD)  # right password, but blocked
    assert r.status_code == 429
    assert 'Zu viele Fehlversuche' in r.get_data(as_text=True)
    assert post_login(client, csrf, 'alice', PASSWORD).status_code == 302  # other accounts still work

    monkeypatch.setattr(dates, 'now', lambda: NOW + auth.LOGIN_WINDOW + timedelta(seconds=1))
    assert post_login(client, csrf, 'bob', PASSWORD).status_code == 302


def test_successful_login_resets_the_user_counter(client, csrf):
    for _ in range(auth.MAX_FAILURES_PER_USER - 1):
        post_login(client, csrf, 'bob', 'wrong')
    assert post_login(client, csrf, 'bob', PASSWORD).status_code == 302
    client.post('/logout', data={'csrf_token': csrf()})
    for _ in range(auth.MAX_FAILURES_PER_USER - 1):
        post_login(client, csrf, 'bob', 'wrong')
    assert post_login(client, csrf, 'bob', PASSWORD).status_code == 302


def test_login_is_blocked_per_client_address(client, app, csrf):
    for i in range(auth.MAX_FAILURES_PER_IP):
        post_login(client, csrf, f'guess{i}', 'wrong')
    assert post_login(client, csrf, 'bob', PASSWORD).status_code == 429
    other = app.test_client()
    assert post_login(other, csrf, 'bob', PASSWORD, environ_base={'REMOTE_ADDR': '10.0.0.7'}).status_code == 302


def test_failed_logins_are_logged(client, csrf, caplog):
    with caplog.at_level(logging.WARNING):
        post_login(client, csrf, 'bob\nfake log line', 'wrong')
    assert "Failed login for 'bob\\nfake log line' from 127.0.0.1" in caplog.text


def test_unknown_user_costs_a_hash_check(client, csrf, monkeypatch):
    checked = []
    monkeypatch.setattr(auth, 'check_password_hash', lambda h, p: checked.append(h) or False)
    post_login(client, csrf, 'nobody', 'wrong')
    assert checked == [auth._DUMMY_HASH]


def test_trusted_proxy_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(dates, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'p.db'), 'TRUSTED_PROXIES': 1})
    client = app.test_client()
    with client.session_transaction() as s:
        s['csrf'] = 't'
    headers = {'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https'}
    r = client.post('/login', data={'username': 'x', 'password': 'y', 'csrf_token': 't'}, headers=headers)
    assert r.headers['Strict-Transport-Security']
    with app.app_context():
        from helvax.db import get_db
        assert get_db().execute('SELECT key FROM login_failures ORDER BY key').fetchall()[0][0] == 'ip:203.0.113.9'


def test_proxy_headers_ignored_without_trusted_proxies(client, query):
    with client.session_transaction() as s:
        s['csrf'] = 't'
    client.post('/login', data={'username': 'x', 'password': 'y', 'csrf_token': 't'},
                headers={'X-Forwarded-For': '203.0.113.9'})
    assert ('ip:127.0.0.1',) in query('SELECT key FROM login_failures')


# --- Sessions ------------------------------------------------------------------------------------

def test_password_change_ends_other_sessions(client, app, login):
    other = app.test_client()
    login('bob', c=other)
    t = login('bob')
    client.post('/account', data={'action': 'password', 'old_password': PASSWORD, 'new_password': 'brandnew123',
                                  'new_password2': 'brandnew123', 'csrf_token': t})
    assert client.get('/').status_code == 200  # this session stays
    assert other.get('/').headers['Location'].endswith('/login')


def test_admin_reset_ends_the_users_sessions(client, app, login, query):
    bob = app.test_client()
    login('bob', c=bob)
    t = login('alice')
    bob_id = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob_id}/reset', data={'csrf_token': t})
    assert bob.get('/').headers['Location'].endswith('/login')


def test_admin_resetting_own_password_stays_logged_in(client, login, query):
    t = login('alice')
    alice_id = query("SELECT id FROM users WHERE username = 'alice'")[0][0]
    r = client.post(f'/admin/users/{alice_id}/reset', data={'csrf_token': t}, follow_redirects=True)
    assert 'Neues Passwort für Alice' in r.get_data(as_text=True)


def test_sessions_from_before_the_upgrade_stay_valid(client, query):
    """Sessions created before session_version existed have no 'sv'; they match version 0."""
    with client.session_transaction() as s:
        s['user_id'] = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    assert client.get('/').status_code == 200


# --- Password reset mails ------------------------------------------------------------------------

def test_password_reset_mails_are_throttled(app, client, csrf, login, mailbox, monkeypatch):
    t = login('bob')
    client.post('/account', data={'action': 'email', 'email': 'bob@example.ch', 'csrf_token': t})
    with app.app_context(), get_db() as con:
        con.execute("UPDATE users SET email_verified = 1 WHERE username = 'bob'")
        con.execute('DELETE FROM notifications')
    client.post('/logout', data={'csrf_token': csrf()})

    def ask(minutes):
        monkeypatch.setattr(dates, 'now', lambda: NOW + timedelta(minutes=minutes))
        client.post('/forgot-password', data={'identifier': 'bob', 'csrf_token': csrf()})
    for minutes in [0, 1, 14]:
        ask(minutes)
    ask(16)
    with app.app_context():
        from helvax import notifications
        notifications.run_once()
    assert len(mailbox) == 2


# --- security.txt --------------------------------------------------------------------------------

def test_security_txt(client):
    r = client.get('/.well-known/security.txt')
    assert r.status_code == 200
    assert r.mimetype == 'text/plain'
    text = r.get_data(as_text=True)
    assert 'Contact: https://github.com/martigr/helvax/security/advisories/new' in text
    assert 'Expires: 2027-04-13T00:00:00Z' in text  # NOW + 180 days
    assert 'Canonical: https://hx.test/.well-known/security.txt' in text


def test_security_txt_with_operator_contact(tmp_path, monkeypatch):
    monkeypatch.setattr(dates, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 's.db'),
                      'SECURITY_CONTACT': 'verwaltung@example.ch'})
    text = app.test_client().get('/.well-known/security.txt').get_data(as_text=True)
    assert text.startswith('Contact: mailto:verwaltung@example.ch\nContact: https://github.com/')


# --- Backup --------------------------------------------------------------------------------------

def test_backup_command(app, tmp_path):
    target = tmp_path / 'backup.db'
    r = app.test_cli_runner().invoke(args=['backup', str(target)])
    assert r.exit_code == 0, r.output
    con = sqlite3.connect(target)
    assert con.execute('SELECT username FROM users ORDER BY username').fetchall() == [('alice',), ('bob',), ('carol',)]
    con.close()


# --- Access log ----------------------------------------------------------------------------------

def test_access_log_hides_tokens():
    from types import SimpleNamespace

    from gunicorn.config import Config

    from helvax.accesslog import RedactingLogger
    environ = {'REQUEST_METHOD': 'GET', 'RAW_URI': '/reset-password/SECRET.tok-en?x=1',
               'PATH_INFO': '/reset-password/SECRET.tok-en', 'SERVER_PROTOCOL': 'HTTP/1.1',
               'HTTP_REFERER': 'https://hx.test/unsubscribe/SECRET2'}
    resp = SimpleNamespace(status='200 OK', sent=10, headers=[])
    req = SimpleNamespace(headers=[('REFERER', 'https://hx.test/verify-email/SECRET3'),
                                   ('X-ORIGINAL-URI', '/unsubscribe/SECRET4')])
    atoms = RedactingLogger(Config()).atoms(resp, req, environ, timedelta(seconds=1))
    assert atoms['r'] == 'GET /reset-password/***?x=1 HTTP/1.1'
    assert atoms['U'] == '/reset-password/***'
    assert atoms['f'] == 'https://hx.test/unsubscribe/***'
    assert atoms['{raw_uri}e'] == '/reset-password/***?x=1'
    assert 'SECRET' not in str(atoms)


def test_startup_waits_for_another_worker(tmp_path):
    # A second Gunicorn worker starts while the first one still holds the write lock in init_db().
    # Switching to WAL doesn't wait for a lock in SQLite, so init_db() has to retry.
    path = tmp_path / 'new.db'
    other = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    other.execute('BEGIN IMMEDIATE')
    other.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    release = threading.Timer(0.3, other.execute, ['COMMIT'])
    release.start()
    try:
        create_app({'TESTING': True, 'DATABASE': str(path)})
    finally:
        release.join()
        other.close()
    assert sqlite3.connect(path).execute('PRAGMA journal_mode').fetchone() == ('wal',)
