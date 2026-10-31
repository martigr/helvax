import pytest

from helvax import create_app


def test_login_and_logout(client, csrf):
    t = csrf()
    r = client.post('/login', data={'username': 'bob', 'password': 'wrong', 'csrf_token': t})
    assert 'Benutzername oder Passwort falsch' in r.get_data(as_text=True)

    r = client.post('/login', data={'username': ' Bob ', 'password': 'secret1234', 'csrf_token': t})
    assert r.status_code == 302
    assert 'Übersicht' in client.get('/').get_data(as_text=True)

    client.post('/logout', data={'csrf_token': csrf()})
    assert client.get('/').headers['Location'].endswith('/login')


def test_pages_require_login(client):
    for url in ['/', '/account', '/admin']:
        assert client.get(url).headers['Location'].endswith('/login')


def test_post_without_csrf_rejected(client, query, login):
    login('bob')
    assert client.post('/account', data={'action': 'name', 'display_name': 'X'}).status_code == 400
    assert client.post('/logout').status_code == 400
    assert query("SELECT display_name FROM users WHERE username = 'bob'") == [('Bob',)]


def test_deleted_user_is_logged_out(client, app, login, query):
    bob = app.test_client()
    login('bob', c=bob)
    assert bob.get('/').status_code == 200

    t = login('alice')
    bob_id = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob_id}/delete', data={'csrf_token': t})
    assert bob.get('/').headers['Location'].endswith('/login')


def test_secret_key_required_in_production(tmp_path, monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app({'DATABASE': str(tmp_path / 'x.db')})


def test_account(client, query, login, csrf):
    t = login('bob')
    client.post('/account', data={'action': 'name', 'display_name': 'Bob M.', 'csrf_token': t})
    client.post('/account', data={'action': 'password', 'old_password': 'wrong',
                                  'new_password': 'newsecret1', 'new_password2': 'newsecret1', 'csrf_token': t})
    client.post('/account', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'short', 'new_password2': 'short', 'csrf_token': t})
    client.post('/account', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'newsecret1', 'new_password2': 'newsecreT1', 'csrf_token': t})
    assert 'Die neuen Passwörter stimmen nicht überein.' in client.get('/account').get_data(as_text=True)
    client.post('/account', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'newsecret1', 'csrf_token': t})  # repetition missing
    assert query("SELECT display_name FROM users WHERE username = 'bob'") == [('Bob M.',)]
    t = login('bob', 'secret1234')  # old password still valid
    page = client.get('/account').get_data(as_text=True)
    assert 'Familie <strong>Muster</strong>, zusammen mit Alice' in page

    assert 'name="new_password2"' in client.get('/account').get_data(as_text=True)
    client.post('/account', data={'action': 'password', 'old_password': 'secret1234',
                                  'new_password': 'newsecret1', 'new_password2': 'newsecret1', 'csrf_token': t})
    client.post('/logout', data={'csrf_token': csrf()})
    login('bob', 'newsecret1')
    assert client.get('/').status_code == 200


def test_account_without_family(client, app, login):
    with app.app_context():
        from helvax.db import get_db
        with get_db() as con:
            con.execute("UPDATE users SET family_id = NULL WHERE username = 'carol'")
    login('carol')
    assert 'noch zu keiner Familie' in client.get('/account').get_data(as_text=True)


def test_change_email_needs_confirmation(client, query, login, mailbox, app):
    t = login('bob')
    client.post('/account', data={'action': 'email', 'email': 'not an address', 'csrf_token': t})
    assert query("SELECT email FROM users WHERE username = 'bob'") == [(None,)]
    client.post('/account', data={'action': 'email', 'email': 'bob@example.ch', 'csrf_token': t})
    assert query("SELECT email, email_verified FROM users WHERE username = 'bob'") == [('bob@example.ch', 0)]
    assert query("SELECT action, detail FROM audit_log WHERE action = 'email_changed'") == [
        ('email_changed', 'bob@example.ch')]
    with app.app_context():
        from helvax import notifications
        notifications.run_once()
    assert [m[0] for m in mailbox] == ['bob@example.ch']
    link = next(line for line in mailbox[0][2].splitlines() if '/verify-email/' in line)
    path = link.replace('https://hx.test', '')
    assert 'Ja, bestätigen' in client.get(path).get_data(as_text=True)
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(0,)]  # GET changes nothing
    client.post(path, data={'csrf_token': t})
    assert query("SELECT email_verified FROM users WHERE username = 'bob'") == [(1,)]
