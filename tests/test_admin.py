def test_admin_requires_admin(client, login):
    t = login('bob')
    assert client.get('/admin').status_code == 403
    r = client.post('/admin/users', data={'username': 'x', 'password': 'secret1234', 'csrf_token': t})
    assert r.status_code == 403


def test_create_and_delete_user(client, query, login):
    t = login('alice')
    client.post('/admin/users', data={'username': 'Dave', 'display_name': 'Dave', 'password': 'secret1234',
                                      'csrf_token': t})
    client.post('/admin/users', data={'username': 'dave', 'password': 'secret1234', 'csrf_token': t})
    client.post('/admin/users', data={'username': 'dora', 'password': 'short', 'csrf_token': t})
    assert query('SELECT username FROM users ORDER BY id') == [('alice',), ('bob',), ('carol',), ('dave',)]

    dave = query("SELECT id FROM users WHERE username = 'dave'")[0][0]
    client.post(f'/admin/users/{dave}/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM users') == [(3,)]


def test_deleting_a_user_keeps_the_family_profiles(client, query, login, app):
    with app.app_context():
        from helvax.db import get_db
        with get_db() as con:
            con.execute("INSERT INTO profiles (family_id, display_name, birth_date, created_at) "
                        "VALUES (1, 'Mia', '2025-01-01', '2026-01-01T00:00:00')")
    t = login('alice')
    client.post('/admin/users/2/delete', data={'csrf_token': t})
    assert query('SELECT display_name FROM profiles') == [('Mia',)]


def test_delete_and_reset_ask_for_confirmation(client, login):
    login('alice')
    page = client.get('/admin').get_data(as_text=True)
    assert 'data-confirm-title="Benutzer löschen?"' in page
    assert 'data-confirm="Das Konto von Bob wird endgültig gelöscht.' in page
    assert 'data-confirm-title="Passwort zurücksetzen?"' in page
    assert 'onsubmit=' not in page


def test_cannot_delete_self(client, query, login):
    t = login('alice')
    client.post('/admin/users/1/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM users') == [(3,)]


def test_reset_password(client, login):
    t = login('alice')
    r = client.post('/admin/users/2/reset', data={'csrf_token': t})
    assert r.status_code == 200  # shown in this response, not via a redirect and the session
    new_password = r.get_data(as_text=True).split('Neues Passwort für Bob: <code>')[1].split('<')[0]
    assert new_password not in str(client.get_cookie('session').value)
    assert new_password not in client.get('/admin').get_data(as_text=True)  # only once
    client.post('/logout', data={'csrf_token': t})
    login('bob', new_password)
    assert client.get('/').status_code == 200


def test_audit_days_setting(client, query, login):
    t = login('alice')
    client.post('/admin/settings', data={'audit_days': '0', 'csrf_token': t})
    assert query("SELECT value FROM settings WHERE key = 'audit_days'") == []
    client.post('/admin/settings', data={'audit_days': '30', 'csrf_token': t})
    assert query("SELECT value FROM settings WHERE key = 'audit_days'") == [('30',)]


def test_families(client, query, login):
    t = login('alice')
    client.post('/admin/families', data={'name': ' Familie Test ', 'csrf_token': t})
    resp = client.post('/admin/families', data={'name': 'familie test', 'csrf_token': t}, follow_redirects=True)
    assert 'gibt es schon' in resp.get_data(as_text=True)
    client.post('/admin/families', data={'name': '', 'csrf_token': t})
    client.post('/admin/families', data={'name': 'x' * 41, 'csrf_token': t})
    assert query('SELECT name FROM families ORDER BY id') == [('Muster',), ('Beispiel',), ('Familie Test',)]
    fid = query("SELECT id FROM families WHERE name = 'Familie Test'")[0][0]

    resp = client.post('/admin/users/2/family', data={'family_id': str(fid), 'csrf_token': t},
                       follow_redirects=True)
    assert 'Bob gehört jetzt zur Familie «Familie Test».' in resp.get_data(as_text=True)
    assert query("SELECT family_id FROM users WHERE username = 'bob'") == [(fid,)]
    page = client.get('/admin').get_data(as_text=True)
    assert f'<option value="{fid}" selected>Familie Test</option>' in page
    assert '1 Konto, 0 Profile' in page
    assert 'data-confirm-title="Familie löschen?"' in page

    client.post('/admin/users/2/family', data={'family_id': '', 'csrf_token': t})
    assert query("SELECT family_id FROM users WHERE username = 'bob'") == [(None,)]
    client.post(f'/admin/families/{fid}/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM families') == [(2,)]


def test_family_with_profiles_cannot_be_deleted(client, query, login, app):
    with app.app_context():
        from helvax.db import get_db
        with get_db() as con:
            con.execute("INSERT INTO profiles (family_id, display_name, birth_date, created_at) "
                        "VALUES (2, 'Leo', '2020-05-01', '2026-01-01T00:00:00')")
    t = login('alice')
    resp = client.post('/admin/families/2/delete', data={'csrf_token': t}, follow_redirects=True)
    assert 'hat noch Profile' in resp.get_data(as_text=True)
    assert query('SELECT COUNT(*) FROM families') == [(2,)]
    assert query('SELECT COUNT(*) FROM profiles') == [(1,)]


def test_family_errors(client, query, login):
    t = login('alice')
    assert client.post('/admin/users/2/family', data={'family_id': '999', 'csrf_token': t}).status_code == 400
    assert client.post('/admin/users/2/family', data={'family_id': 'x', 'csrf_token': t}).status_code == 400
    assert client.post('/admin/users/99/family', data={'family_id': '1', 'csrf_token': t}).status_code == 404
    assert client.post('/admin/families/999/delete', data={'csrf_token': t}).status_code == 404
    assert client.post('/admin/users/2/family', data={'family_id': '2'}).status_code == 400  # CSRF

    t = login('bob')
    assert client.post('/admin/families', data={'name': 'Meins', 'csrf_token': t}).status_code == 403
    assert client.post('/admin/users/2/family', data={'family_id': '2', 'csrf_token': t}).status_code == 403
    assert client.post('/admin/families/1/delete', data={'csrf_token': t}).status_code == 403
    assert query('SELECT family_id FROM users ORDER BY id') == [(1,), (1,), (2,)]
