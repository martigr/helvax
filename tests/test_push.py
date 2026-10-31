"""Web Push (M7): subscribing devices on /account, reminders by push, push.deliver and the VAPID key."""
import base64
import json

import pytest
import pywebpush
import requests
from conftest import MAIL_CONFIG, NOW, REAL_PUSH_DELIVER, VAPID_KEY
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from test_notifications import run, set_prefs
from test_reminders import add_profile, assert_no_health_details, at, reminders, save

from helvax import create_app, dates, push
from helvax.db import get_db

ENDPOINT = 'https://fcm.googleapis.com/fcm/send/device-1'


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def browser_keys():
    """p256dh and auth as a browser would send them."""
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    raw = key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64(raw), b64(b'0123456789abcdef')


def subscribe(client, token, endpoint=ENDPOINT):
    p256dh, auth = browser_keys()
    return client.post('/push/subscribe', data={'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth,
                                                'csrf_token': token})


def audit_entries(query, *actions):
    marks = ','.join('?' * len(actions))
    return query(f'SELECT username, action, detail FROM audit_log WHERE action IN ({marks}) ORDER BY id', *actions)


def push_public_key(client):
    with client.application.app_context():
        return push.public_key()


# --- Subscribing devices -------------------------------------------------------------------------

def test_subscribe_and_unsubscribe(client, login, pushbox, query):
    t = login('bob')
    page = client.get('/account').get_data(as_text=True)
    assert 'id="push-device"' in page and 'push.js' in page and 'per Push' in page
    assert f'data-key="{push_public_key(client)}"' in page

    assert subscribe(client, t).status_code == 204
    assert query('SELECT u.username, s.endpoint FROM push_subscriptions s JOIN users u ON u.id = s.user_id') == \
        [('bob', ENDPOINT)]
    assert f'data-endpoints=\'["{ENDPOINT}"]\'' in client.get('/account').get_data(as_text=True)

    r = client.post('/push/unsubscribe', data={'endpoint': ENDPOINT, 'csrf_token': t})
    assert r.status_code == 204
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]
    client.post('/push/unsubscribe', data={'endpoint': ENDPOINT, 'csrf_token': t})  # already gone: no entry
    assert audit_entries(query, 'push_enabled', 'push_disabled') == [
        ('bob', 'push_enabled', 'fcm.googleapis.com'),
        ('bob', 'push_disabled', 'fcm.googleapis.com'),
    ]


@pytest.mark.parametrize('endpoint', [
    '',
    'http://fcm.googleapis.com/fcm/send/x',          # not https
    'https://evil.example/fcm/send/x',               # not a push service
    'https://fcm.googleapis.com.evil.example/x',     # only looks like one
    'https://fcm.googleapis.com:8443/x',             # other port
    'https://user@fcm.googleapis.com/x',             # credentials
    'https://127.0.0.1/x',
    'https://[::1/x',                                # not parseable
    'https://fcm.googleapis.com/' + 'x' * 1024,      # too long
])
def test_subscribe_rejects_other_endpoints(client, login, pushbox, query, endpoint):
    assert subscribe(client, login('bob'), endpoint).status_code == 400
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]


def test_subscribe_rejects_bad_keys_csrf_and_anonymous(client, login, csrf, pushbox, query):
    p256dh, auth = browser_keys()
    t = login('bob')
    for fields in [{'p256dh': p256dh, 'auth': b64(b'short')},
                   {'p256dh': b64(b'\x04' + b'x' * 10), 'auth': auth},
                   {'p256dh': 'not base64!', 'auth': auth},
                   {'p256dh': p256dh}]:
        r = client.post('/push/subscribe', data={'endpoint': ENDPOINT, 'csrf_token': t, **fields})
        assert r.status_code == 400
    assert client.post('/push/subscribe', data={'endpoint': ENDPOINT, 'p256dh': p256dh,
                                                'auth': auth}).status_code == 400  # no CSRF token
    assert client.post('/push/unsubscribe', data={'endpoint': '', 'csrf_token': t}).status_code == 400

    client.post('/logout', data={'csrf_token': t})
    r = client.post('/push/subscribe', data={'endpoint': ENDPOINT, 'p256dh': p256dh, 'auth': auth,
                                             'csrf_token': csrf()})
    assert r.status_code == 302 and '/login' in r.location
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]


@pytest.mark.parametrize('endpoint', [
    'https://fcm.googleapis.com/fcm/send/abc',
    'https://updates.push.services.mozilla.com/wpush/v2/abc',
    'https://web.push.apple.com/abc',
    'https://wns2-par02p.notify.windows.com/w/?token=abc',
])
def test_known_push_services(endpoint):
    assert push.valid_subscription(endpoint, *browser_keys())


def test_without_push_config(client, login, query):
    t = login('bob')
    page = client.get('/account').get_data(as_text=True)
    assert 'push-device' not in page and 'push.js' not in page and 'per Push' not in page
    assert subscribe(client, t).status_code == 404

    # The push choice made earlier survives saving the form, which has no push box now
    with client.application.app_context(), get_db() as con:
        con.execute("UPDATE users SET remind_push = 1 WHERE username = 'bob'")
    save(client, t, mail=True, push=False)
    assert query("SELECT remind_mail, remind_push FROM users WHERE username = 'bob'") == [(1, 1)]


def test_push_choice_is_saved(client, login, pushbox, query):
    t = login('bob')
    save(client, t, mail=False, push=True)
    assert query("SELECT remind_mail, remind_push FROM users WHERE username = 'bob'") == [(0, 1)]
    save(client, t, mail=True, push=False)
    assert query("SELECT remind_mail, remind_push FROM users WHERE username = 'bob'") == [(1, 0)]
    assert [d.split(';')[0] for _, _, d in audit_entries(query, 'reminders')] == ['Push', 'Mail']


def test_a_device_belongs_to_whoever_subscribed_it_last(app, client, login, pushbox, query):
    t = login('bob')
    subscribe(client, t)
    add_profile(client, t, 'Tom', '1990-03-01')
    with app.app_context(), get_db() as con:
        from helvax import notifications
        notifications.queue_due_vaccinations(con)
    assert query('SELECT COUNT(*) FROM notifications') == [(1,)]

    subscribe(client, login('alice'))  # same browser, other account
    assert query('SELECT u.username FROM push_subscriptions s JOIN users u ON u.id = s.user_id') == [('alice',)]
    assert query('SELECT COUNT(*) FROM notifications') == [(0,)]  # bob's reminder no longer goes there


def test_at_most_ten_devices_per_user(client, login, pushbox, query):
    t = login('bob')
    for i in range(12):
        subscribe(client, t, f'{ENDPOINT}-{i}')
    assert [e for e, in query('SELECT endpoint FROM push_subscriptions ORDER BY id')] == \
        [f'{ENDPOINT}-{i}' for i in range(2, 12)]


def test_deleting_a_user_removes_their_devices(client, login, pushbox, query):
    subscribe(client, login('bob'))
    t = login('alice')
    bob_id = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob_id}/delete', data={'csrf_token': t})
    assert query('SELECT COUNT(*) FROM push_subscriptions') == [(0,)]


# --- Reminders by push ---------------------------------------------------------------------------

def test_reminder_by_push_only(app, client, login, mailbox, pushbox, query):
    t = login('bob')  # no mail address: push works without one
    subscribe(client, t)
    subscribe(client, t, ENDPOINT + '-tablet')
    tom = add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, mail=False, push=True)

    assert run(app) == (2, 0)
    assert mailbox == []
    assert sorted(e for e, _, _ in pushbox) == [ENDPOINT, ENDPOINT + '-tablet']
    _, payload, ttl = pushbox[0]
    assert payload == {'title': 'Impfung bei Tom', 'body': 'Bei Tom ist eine Impfung überfällig. Details in helvax.',
                       'url': f'https://hx.test/profiles/{tom}', 'tag': f'due:{tom}'}
    assert ttl == 7 * 86400
    assert_no_health_details(app, json.dumps(payload, ensure_ascii=False))
    assert query("SELECT DISTINCT to_addr FROM notifications") == [('fcm.googleapis.com',)]  # not the endpoint

    assert run(app) == (0, 0)  # once


def test_reminder_by_mail_and_push(app, client, login, mailbox, pushbox, monkeypatch, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    subscribe(client, t)
    add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, mail=True, push=True, overdue=0)
    assert run(app) == (2, 0)
    assert len(reminders(mailbox)) == 1 and len(pushbox) == 1
    assert pushbox[0][1]['body'] == 'Bei Mia ist bald eine Impfung fällig. Details in helvax.'
    assert query('SELECT channel, COUNT(*) FROM reminder_log GROUP BY channel ORDER BY channel') == \
        [('mail', 2), ('push', 2)]


def test_push_switched_off_or_no_device(app, client, login, pushbox):
    t = login('bob')
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, mail=False, push=True)
    assert run(app) == (0, 0)  # no device yet
    subscribe(client, t)
    save(client, t, mail=False, push=False)
    assert run(app) == (0, 0)


def test_a_new_device_doesnt_get_old_reminders(app, client, login, pushbox):
    t = login('bob')
    subscribe(client, t)
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, mail=False, push=True)
    assert run(app) == (1, 0)
    subscribe(client, t, ENDPOINT + '-tablet')
    assert run(app) == (0, 0)


def test_gone_subscription_is_removed(app, client, login, pushbox, monkeypatch, query):
    t = login('bob')
    subscribe(client, t)
    subscribe(client, login('alice'), ENDPOINT + '-alice')
    add_profile(client, login('bob'), 'Tom', '1990-03-01')
    add_profile(client, login('bob'), 'Mia', '2015-11-20')
    with app.app_context(), get_db() as con:
        from helvax import notifications
        notifications.queue_due_vaccinations(con)
    assert query('SELECT COUNT(*) FROM notifications') == [(4,)]  # two profiles, two devices

    calls = []

    def gone(sub, payload, ttl):
        calls.append(sub['endpoint'])
        if sub['endpoint'] == ENDPOINT:
            raise push.Gone('push service answered 410')
    monkeypatch.setattr(push, 'deliver', gone)
    assert run(app) == (2, 1)
    assert calls.count(ENDPOINT) == 1  # not tried again for the second message
    assert query('SELECT endpoint FROM push_subscriptions') == [(ENDPOINT + '-alice',)]
    assert query('SELECT COUNT(*) FROM notifications WHERE sent_at IS NULL') == [(0,)]


def test_failed_push_is_retried(app, client, login, pushbox, monkeypatch, query):
    t = login('bob')
    subscribe(client, t)
    add_profile(client, t, 'Tom', '1990-03-01')

    def broken(sub, payload, ttl):
        raise push.PushError('push service answered 500')
    monkeypatch.setattr(push, 'deliver', broken)
    assert run(app) == (0, 1)
    assert query('SELECT to_addr, attempts, last_error, sent_at FROM notifications') == \
        [('fcm.googleapis.com', 1, 'push service answered 500', None)]

    monkeypatch.setattr(push, 'deliver', lambda sub, payload, ttl: pushbox.append(sub['endpoint']))
    assert run(app) == (1, 0)


def test_push_waits_while_switched_off(app, client, login, pushbox, query):
    t = login('bob')
    subscribe(client, t)
    add_profile(client, t, 'Tom', '1990-03-01')
    with app.app_context(), get_db() as con:
        from helvax import notifications
        notifications.queue_due_vaccinations(con)
    app.config['VAPID_PRIVATE_KEY'] = None
    assert run(app) == (0, 0)
    assert query('SELECT attempts, sent_at FROM notifications') == [(0, None)]


def test_stale_push_reminder_expires(app, client, login, pushbox, monkeypatch):
    t = login('bob')
    subscribe(client, t)
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, mail=False, push=True, overdue=30)
    with app.app_context(), get_db() as con:
        from helvax import notifications
        notifications.queue_due_vaccinations(con)
    at(monkeypatch, 2026, 10, 23)  # the notifier was down for 8 days; the next repeat is after 30
    assert run(app) == (0, 0)
    assert pushbox == []


def test_mail_unsubscribe_link_keeps_push(app, client, login, csrf, mailbox, pushbox, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    subscribe(client, t)
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, mail=True, push=True)
    run(app)
    path = next(w for w in reminders(mailbox)[0][2].split() if '/unsubscribe/' in w)[len('https://hx.test'):]
    other = app.test_client()
    other.post(path, data={'csrf_token': csrf(other)})
    assert query("SELECT remind_mail, remind_push FROM users WHERE username = 'bob'") == [(0, 1)]


def test_push_text_has_no_health_details_for_every_profile(app, client, login, pushbox, monkeypatch):
    """Profiles of all ages, with what is due or overdue for them: the pushes only name the profile."""
    t = login('bob')
    subscribe(client, t)
    for name, birth in [('Baby', '2026-06-25'), ('Kind', '2015-11-20'), ('Erwachsen', '1990-03-01'),
                        ('Senior', '1955-02-01')]:
        add_profile(client, t, name, birth)
    save(client, t, mail=False, push=True, leads=[56, 28, 14, 7, 1, 0], overdue=7)
    for day in (15, 22, 29):
        at(monkeypatch, 2026, 10, day)
        run(app)
    assert len(pushbox) >= 4
    assert_no_health_details(app, *(json.dumps(p, ensure_ascii=False) for _, p, _ in pushbox))


# --- push.deliver and the key ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.text = 'details'


@pytest.mark.parametrize('error, expected', [
    (pywebpush.WebPushException('x', response=FakeResponse(410)), 'push service answered 410'),
    (pywebpush.WebPushException('x', response=FakeResponse(404)), 'push service answered 404'),
    (pywebpush.WebPushException('x', response=FakeResponse(500)), 'push service answered 500'),
    (requests.ConnectionError(f'cannot reach {ENDPOINT}'), 'ConnectionError'),
])
def test_deliver_errors(app, pushbox, monkeypatch, error, expected):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(push, 'webpush', fail)
    gone = expected.endswith(('404', '410'))
    with app.app_context(), pytest.raises(push.Gone if gone else push.PushError) as info:
        REAL_PUSH_DELIVER({'endpoint': ENDPOINT, 'p256dh': 'p', 'auth': 'a'}, '{}', 60)
    assert str(info.value) == expected  # never the endpoint, it is the device's secret


def test_deliver_encrypts_and_signs(app, pushbox, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # curl mode writes the encrypted body to ./encrypted.data
    calls = []
    real_webpush = pywebpush.webpush
    monkeypatch.setattr(push, 'webpush', lambda *a, **kw: calls.append(real_webpush(*a, curl=True, **kw)))
    p256dh, auth = browser_keys()
    with app.app_context():
        REAL_PUSH_DELIVER({'endpoint': ENDPOINT, 'p256dh': p256dh, 'auth': auth}, '{"title": "Hallo"}', 3300.4)
    request = calls[0]
    assert request.startswith(f'curl -vX POST {ENDPOINT}')
    assert '"ttl: 3300"' in request.lower() and 'aes128gcm' in request
    assert 'vapid t=' in request.lower()  # signed with our key


def test_vapid_subject(app, pushbox):
    with app.app_context():
        assert push.subject() == 'mailto:helvax@example.ch'
        app.config['MAIL_FROM'] = None
        assert push.subject() == 'https://hx.test'
        app.config['VAPID_SUBJECT'] = 'mailto:admin@example.org'
        assert push.subject() == 'mailto:admin@example.org'


def test_invalid_key_stops_the_start(tmp_path):
    with pytest.raises(RuntimeError, match='VAPID_PRIVATE_KEY is not a valid key'):
        create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'), 'VAPID_PRIVATE_KEY': 'nonsense'})


def test_gen_vapid_key(tmp_path, monkeypatch):
    monkeypatch.setattr(dates, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db')})
    out = app.test_cli_runner().invoke(args=['gen-vapid-key']).output.strip()
    assert out.startswith('VAPID_PRIVATE_KEY=')
    key = out.removeprefix('VAPID_PRIVATE_KEY=')
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'), 'VAPID_PRIVATE_KEY': key, **MAIL_CONFIG})
    with app.app_context():
        assert len(base64.urlsafe_b64decode(push.public_key() + '=')) == 65


def test_push_only_notifier_runs_without_mail(tmp_path, monkeypatch):
    monkeypatch.setattr(dates, 'now', lambda: NOW)
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'x.db'), 'VAPID_PRIVATE_KEY': VAPID_KEY})
    result = app.test_cli_runner().invoke(args=['send-notifications'])
    assert result.exit_code == 0


def test_service_worker_shows_push_messages(client):
    js = client.get('/sw.js').get_data(as_text=True)
    assert "addEventListener('push'" in js and "addEventListener('notificationclick'" in js
    assert '"/static/icons/icon-192.png"' in js


def test_privacy_mentions_push_only_when_configured(app, client, login):
    login('bob')
    assert 'Push' not in client.get('/privacy').get_data(as_text=True)
    app.config['VAPID_PRIVATE_KEY'] = VAPID_KEY
    page = client.get('/privacy').get_data(as_text=True)
    assert 'Push-Dienst deines Browsers' in page and 'Push-Geräte' in page
