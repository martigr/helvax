"""Reminders of due vaccinations by mail (M6): settings on /account, the scan in notifications.py,
the unsubscribe link. Uses the real schedule; the clock starts at 2026-10-15.

Profiles used:
- Mia, born 2015-11-20 (10): hepatitis B and HPV are recommended from her 11th birthday (2026-11-20),
  and with an empty record the catch-up of the basic vaccinations is overdue.
- Tom, born 1990-03-01, empty record: catch-up overdue at once.
"""
import re
from datetime import datetime

import pytest
from test_notifications import link_in, run, set_prefs

from helvax import dates, notifications, rules
from helvax.db import get_db


def at(monkeypatch, *args):
    monkeypatch.setattr(dates, 'now', lambda: datetime(*args, 14, 20))


def add_profile(client, token, name, birth):
    client.post('/profiles/new', data={'display_name': name, 'birth_date': birth, 'csrf_token': token})
    with client.application.app_context():
        return get_db().execute('SELECT id FROM profiles WHERE display_name = ?', (name,)).fetchone()['id']


def save(client, token, mail=True, push=False, leads=(56, 7), overdue=30, watch=None):
    """Post the reminder form of /account; `watch` defaults to every profile of family 1 (bob's)."""
    if watch is None:
        with client.application.app_context():
            watch = [r['id'] for r in get_db().execute('SELECT id FROM profiles WHERE family_id = 1')]
    data = {'action': 'reminders', 'leads': [str(v) for v in leads], 'overdue_days': str(overdue),
            'csrf_token': token}
    if mail:
        data['remind_mail'] = '1'
    if push:
        data['remind_push'] = '1'
    data['watch'] = [str(v) for v in watch]
    return client.post('/account', data=data)


def reminders(mailbox):
    return [m for m in mailbox if m[1].startswith('helvax: Impfung bei')]


def health_words():
    """Everything that would tell which vaccination it is: antigen keys, labels and short names, products."""
    schedule = rules.schedule()
    words = set()
    for key, antigen in schedule.antigens.items():
        words |= {key, antigen.label, antigen.short}
    for product in rules.catalogue().products:
        words.add(product.name)
    return {w for w in words if w}


def assert_no_health_details(app, *texts):
    with app.app_context():
        words = health_words()
    for text in texts:
        prose = re.sub(r'https?://\S+', '', text)  # links carry ids and random tokens, checked elsewhere
        for word in words:
            assert not re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', prose, re.IGNORECASE), (word, text)


# --- Lead times ----------------------------------------------------------------------------------

def test_reminder_before_the_recommended_date(app, client, login, mailbox, monkeypatch, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    mia = add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, overdue=0)  # only the lead times

    assert run(app) == (1, 0)  # 36 days ahead: the 8-week reminder (hepatitis B and HPV in one mail)
    (to, subject, body), = reminders(mailbox)
    assert to == 'bob@example.ch'
    assert subject == 'helvax: Impfung bei Mia'
    assert 'Hallo Bob\n\nBei Mia ist bald eine Impfung fällig.' in body
    assert f'https://hx.test/profiles/{mia}\n' in body
    assert link_in(body, '/unsubscribe/')
    assert_no_health_details(app, subject, body)
    assert query('SELECT COUNT(*) FROM reminder_log') == [(2,)]  # one per group

    assert run(app) == (0, 0)  # not twice
    at(monkeypatch, 2026, 11, 12)
    assert run(app) == (0, 0)
    at(monkeypatch, 2026, 11, 13)  # one week before
    assert run(app) == (1, 0)
    at(monkeypatch, 2026, 11, 14)
    assert run(app) == (0, 0)
    assert len(reminders(mailbox)) == 2


def test_a_missed_lead_time_is_not_sent_late(app, client, login, mailbox, monkeypatch):
    """A profile created 5 days before the date gets the 1-week reminder only, not the 8-week one too."""
    at(monkeypatch, 2026, 11, 15)
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, overdue=0)
    assert run(app) == (1, 0)
    at(monkeypatch, 2026, 11, 16)
    assert run(app) == (0, 0)


def test_on_the_day_and_after(app, client, login, mailbox, monkeypatch):
    at(monkeypatch, 2026, 11, 20)
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, leads=[0], overdue=0)
    assert run(app) == (1, 0)
    assert 'Bei Mia ist eine Impfung fällig.' in reminders(mailbox)[0][2]


def test_no_lead_times_no_reminder(app, client, login, mailbox):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, leads=[], overdue=0)
    assert run(app) == (0, 0)


def test_sent_reminders_are_remembered_longer_than_the_outbox(app, client, login, mailbox, monkeypatch, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, leads=[56], overdue=0)
    assert run(app) == (1, 0)
    at(monkeypatch, 2026, 11, 16)  # outbox rows are pruned after 30 days
    assert run(app) == (0, 0)
    assert query("SELECT COUNT(*) FROM notifications WHERE dedupe_key LIKE 'due:%'") == [(0,)]
    assert query('SELECT COUNT(*) FROM reminder_log') == [(2,)]


# --- Overdue -------------------------------------------------------------------------------------

def test_overdue_reminder_repeats(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, leads=[], overdue=30)
    assert run(app) == (1, 0)
    (_, subject, body), = reminders(mailbox)
    assert 'Bei Tom ist eine Impfung überfällig.' in body
    assert_no_health_details(app, subject, body)

    at(monkeypatch, 2026, 11, 13)  # 29 days later
    assert run(app) == (0, 0)
    at(monkeypatch, 2026, 11, 14)  # 30 days later
    assert run(app) == (1, 0)
    assert run(app) == (0, 0)


def test_overdue_reminders_switched_off(app, client, login, mailbox):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, leads=[56, 7], overdue=0)
    assert run(app) == (0, 0)


def test_overdue_and_upcoming_of_one_profile_are_one_mail(app, client, login, mailbox):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t)
    assert run(app) == (1, 0)
    assert 'überfällig' in reminders(mailbox)[0][2]


def test_a_deleted_profile_gets_no_more_reminders(app, client, login, mailbox, monkeypatch):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    tom = add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, leads=[], overdue=7)
    assert run(app) == (1, 0)
    client.post(f'/profiles/{tom}/delete', data={'csrf_token': t})
    at(monkeypatch, 2026, 10, 30)
    assert run(app) == (0, 0)


# --- Who gets what -------------------------------------------------------------------------------

def test_only_confirmed_addresses_get_reminders(app, client, login, mailbox, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch', verified=False)
    add_profile(client, t, 'Tom', '1990-03-01')
    run(app)
    assert reminders(mailbox) == []
    assert query('SELECT COUNT(*) FROM reminder_log') == [(0,)]  # nothing counts as sent
    assert 'Mails gehen nur an eine bestätigte Adresse' in client.get('/account').get_data(as_text=True)

    with app.app_context(), get_db() as con:
        con.execute("UPDATE users SET email_verified = 1 WHERE username = 'bob'")
    run(app)
    assert len(reminders(mailbox)) == 1


def test_mail_switched_off(app, client, login, mailbox):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, mail=False)
    assert run(app) == (0, 0)


def test_every_member_gets_their_own_reminder(app, client, login, mailbox):
    set_prefs(client, login, 'alice', 'alice@example.ch')
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Tom', '1990-03-01')
    set_prefs(client, login, 'carol', 'carol@example.ch')  # other family
    run(app)
    assert sorted(m[0] for m in reminders(mailbox)) == ['alice@example.ch', 'bob@example.ch']
    alice_mail = next(m for m in reminders(mailbox) if m[0] == 'alice@example.ch')
    assert 'Hallo Alice' in alice_mail[2]


def test_new_profiles_are_watched_by_the_whole_family(client, login, query):
    t = login('bob')
    tom = add_profile(client, t, 'Tom', '1990-03-01')
    assert query('SELECT u.username FROM profile_watches w JOIN users u ON u.id = w.user_id '
                 'WHERE w.profile_id = ? ORDER BY u.username', tom) == [('alice',), ('bob',)]


def test_unwatched_profile_gets_no_reminder(app, client, login, mailbox, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    tom = add_profile(client, t, 'Tom', '1990-03-01')
    mia = add_profile(client, t, 'Mia', '2015-11-20')
    save(client, t, watch=[mia])
    page = client.get('/account').get_data(as_text=True)
    assert f'name="watch" value="{tom}">' in page and f'name="watch" value="{mia}" checked>' in page
    run(app)
    assert [m[1] for m in reminders(mailbox)] == ['helvax: Impfung bei Mia']

    save(client, t, watch=[])
    assert query("SELECT COUNT(*) FROM profile_watches w JOIN users u ON u.id = w.user_id "
                 "WHERE u.username = 'bob'") == [(0,)]
    leo = add_profile(client, t, 'Leo', '1991-01-01')  # new profiles come in again
    assert query("SELECT w.profile_id FROM profile_watches w JOIN users u ON u.id = w.user_id "
                 "WHERE u.username = 'bob'") == [(leo,)]


def test_profiles_of_other_families_cant_be_watched(app, client, login, query):
    carols = add_profile(client, login('carol'), 'Cleo', '2001-01-01')
    t = login('bob')
    mine = add_profile(client, t, 'Tom', '1990-03-01')
    for data in [{'watch': [str(carols)]}, {'watch': [str(mine), str(carols)]}, {'watch': ['x']},
                 {'leads': ['3']}, {'leads': ['x']}, {'overdue_days': '5'}, {'overdue_days': ''}]:
        form = {'action': 'reminders', 'leads': ['7'], 'overdue_days': '30', 'remind_mail': '1', 'csrf_token': t,
                **data}
        assert client.post('/account', data=form).status_code == 400, data
    assert query("SELECT w.profile_id FROM profile_watches w JOIN users u ON u.id = w.user_id "
                 "WHERE u.username = 'bob'") == [(mine,)]
    assert query("SELECT remind_leads, remind_overdue_days FROM users WHERE username = 'bob'") == [('56,7', 30)]


def test_a_stale_watch_of_another_family_sends_nothing(app, client, login, mailbox):
    """Defence in depth: the scan only follows watches within the account's own family."""
    carols = add_profile(client, login('carol'), 'Cleo', '1990-03-01')
    set_prefs(client, login, 'bob', 'bob@example.ch')
    with app.app_context(), get_db() as con:
        con.execute("INSERT INTO profile_watches SELECT id, ? FROM users WHERE username = 'bob'", (carols,))
    run(app)
    assert reminders(mailbox) == []


def test_moving_to_another_family_moves_the_watches(client, login, query):
    tom = add_profile(client, login('bob'), 'Tom', '1990-03-01')
    cleo = add_profile(client, login('carol'), 'Cleo', '2001-01-01')
    t = login('alice')
    bob = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob}/family', data={'family_id': '2', 'csrf_token': t})
    assert query('SELECT profile_id FROM profile_watches WHERE user_id = ?', bob) == [(cleo,)]
    client.post(f'/admin/users/{bob}/family', data={'family_id': '', 'csrf_token': t})
    assert query('SELECT profile_id FROM profile_watches WHERE user_id = ?', bob) == []
    client.post(f'/admin/users/{bob}/family', data={'family_id': '1', 'csrf_token': t})
    assert query('SELECT profile_id FROM profile_watches WHERE user_id = ?', bob) == [(tom,)]


def test_reassigning_the_same_family_keeps_the_choice(client, login, query):
    t = login('bob')
    add_profile(client, t, 'Tom', '1990-03-01')
    save(client, t, watch=[])
    t = login('alice')
    bob = query("SELECT id FROM users WHERE username = 'bob'")[0][0]
    client.post(f'/admin/users/{bob}/family', data={'family_id': '1', 'csrf_token': t})
    assert query('SELECT profile_id FROM profile_watches WHERE user_id = ?', bob) == []


# --- Settings and unsubscribing --------------------------------------------------------------------

def test_settings_are_saved_and_logged(client, login, query):
    t = login('bob')
    add_profile(client, t, 'Tom', '1990-03-01')
    page = client.get('/account').get_data(as_text=True)
    assert 'name="remind_mail" value="1" checked' in page
    assert 'name="leads" value="56" checked' in page and 'name="leads" value="7" checked' in page
    assert 'name="leads" value="28">' in page
    assert '<option value="30" selected>' in page
    assert 'per Push' not in page  # push isn't configured

    save(client, t, mail=False, leads=[7, 28, 0], overdue=91, watch=[])
    assert query("SELECT remind_mail, remind_leads, remind_overdue_days FROM users WHERE username = 'bob'") == \
        [(0, '28,7,0', 91)]
    assert query("SELECT detail FROM audit_log WHERE action = 'reminders'") == [
        ('aus; vorher: 4 Wochen vorher, 1 Woche vorher, Am empfohlenen Tag; überfällig: Alle 3 Monate; '
         'Profile: keine',)]


def test_unsubscribe_link(app, client, login, csrf, mailbox, query):
    t = set_prefs(client, login, 'bob', 'bob@example.ch')
    add_profile(client, t, 'Tom', '1990-03-01')
    run(app)
    path = link_in(reminders(mailbox)[0][2], '/unsubscribe/')

    other = app.test_client()  # works without being logged in
    assert 'Ja, abbestellen' in other.get(path).get_data(as_text=True)
    assert query("SELECT remind_mail FROM users WHERE username = 'bob'") == [(1,)]  # GET changes nothing
    r = other.post(path, data={'csrf_token': csrf(other)})
    assert 'Du bekommst keine Erinnerungen an fällige Impfungen per Mail mehr' in r.get_data(as_text=True)
    assert query("SELECT remind_mail FROM users WHERE username = 'bob'") == [(0,)]
    assert query("SELECT username, detail FROM audit_log WHERE action = 'unsubscribe'") == \
        [('bob', 'Erinnerungen an fällige Impfungen per Mail')]
    assert other.post(path, data={}).status_code == 400  # CSRF applies


@pytest.mark.parametrize('token', ['forged', None])
def test_invalid_unsubscribe_links(app, client, query, token):
    if token is None:  # signed, but of a deleted user / unknown kind
        with app.test_request_context():
            token = notifications.unsubscribe_token(999, 'reminder')
            other_kind = notifications.unsubscribe_token(1, 'something')
        assert client.get('/unsubscribe/' + other_kind).status_code == 404
    assert client.get('/unsubscribe/' + token).status_code == 404


def test_reminder_scan_without_profiles_or_family(app, client, login, mailbox, query):
    set_prefs(client, login, 'bob', 'bob@example.ch')
    with app.app_context(), get_db() as con:
        con.execute("UPDATE users SET family_id = NULL WHERE username = 'bob'")
    assert run(app) == (0, 0)
