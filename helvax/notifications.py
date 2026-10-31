"""Notifications by mail and push.

Everything goes through the `notifications` table (an outbox): views and the reminder scan only
queue rows, `flask send-notifications` sends them. A request never waits for the mail server or a
push service, a failed send is retried, and `dedupe_key` makes sure nobody gets the same message
twice. Push messages have one row per device (`push_subscription_id`); their `body` is the JSON
payload the service worker shows.

Mails and push messages never carry health details (no antigen, no vaccine, no disease): only whose
profile it is about and a link.
"""
import json
from datetime import datetime, timedelta

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

from . import dates, mail, push
from .db import get_db

# Reminder lead times (days before the recommended date) and repeat intervals while overdue, for /account
LEAD_CHOICES = [
    (56, '8 Wochen vorher'),
    (28, '4 Wochen vorher'),
    (14, '2 Wochen vorher'),
    (7, '1 Woche vorher'),
    (1, '1 Tag vorher'),
    (0, 'Am empfohlenen Tag'),
]
OVERDUE_CHOICES = [
    (0, 'Nicht erinnern'),
    (7, 'Jede Woche'),
    (30, 'Alle 30 Tage'),
    (91, 'Alle 3 Monate'),
]
RESET_VALID = timedelta(hours=1)
RESET_INTERVAL = timedelta(minutes=15)  # at most one reset (or verification) mail per user in this time
VERIFY_VALID = timedelta(days=7)
REMINDER_VALID = timedelta(days=7)  # a reminder that couldn't be sent for this long is dropped
MAX_ATTEMPTS = 5
KEEP_SENT = timedelta(days=30)
# reminder_log remembers which reminders went out; a dose can stay due for years (age windows), and
# forgetting the entry would send the reminder again
KEEP_REMINDER_LOG = timedelta(days=3653)
PUSH_TTL = timedelta(days=1)  # how long a push service keeps a message for an offline device, if it doesn't expire


# --- Links and tokens ------------------------------------------------------------------------

def link(path):
    return current_app.config['BASE_URL'].rstrip('/') + path


def _unsubscribe_serializer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='unsubscribe')


def unsubscribe_token(user_id, kind):
    return _unsubscribe_serializer().dumps({'u': user_id, 'k': kind})


def read_unsubscribe_token(token):
    """(user_id, kind) or None."""
    try:
        data = _unsubscribe_serializer().loads(token)
        return data['u'], data['k']
    except (BadSignature, KeyError, TypeError):
        return None


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='password-reset')


def reset_token(user):
    # The hash fragment makes the link single-use: it stops working once the password changes
    return _reset_serializer().dumps({'u': user['id'], 'h': user['password'][-16:]})


def read_reset_token(token):
    """User id if the token is valid, unexpired and unused, else None."""
    try:
        data = _reset_serializer().loads(token, max_age=int(RESET_VALID.total_seconds()))
    except (BadSignature, TypeError):
        return None
    row = get_db().execute('SELECT id, password FROM users WHERE id = ?', (data.get('u'),)).fetchone()
    if row is None or row['password'][-16:] != data.get('h'):
        return None
    return row['id']


def _verify_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='verify-email')


def verify_token(user_id, email):
    return _verify_serializer().dumps({'u': user_id, 'e': email})


def read_verify_token(token):
    """(user_id, email) if the token is valid and unexpired, else None."""
    try:
        data = _verify_serializer().loads(token, max_age=int(VERIFY_VALID.total_seconds()))
        return data['u'], data['e']
    except (BadSignature, KeyError, TypeError):
        return None


# --- Queueing --------------------------------------------------------------------------------

def _queue(con, dedupe_key, to_addr, subject, body, expires_at=None, push_subscription_id=None):
    con.execute(
        'INSERT OR IGNORE INTO notifications (dedupe_key, to_addr, subject, body, created_at, expires_at, '
        'push_subscription_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (dedupe_key, to_addr, subject, body, dates.now().isoformat(timespec='seconds'),
         expires_at.isoformat(timespec='seconds') if expires_at else None, push_subscription_id))


def _push_payload(title, body, url, tag):
    return json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag}, ensure_ascii=False)


def _queue_push(con, dedupe_key, user_id, title, body, url, tag, expires_at):
    """One row per device of the user, so each is retried on its own. `to_addr` is only the push
    service's host (for the logs); the endpoint URL stays in push_subscriptions."""
    payload = _push_payload(title, body, url, tag)
    for s in con.execute('SELECT id, endpoint FROM push_subscriptions WHERE user_id = ?', (user_id,)).fetchall():
        _queue(con, f'{dedupe_key}:push:{s["id"]}', push.service_host(s['endpoint']), title, payload,
               expires_at, s['id'])


def _mail_to(user):
    """Reminders by mail: switched on and a confirmed address."""
    return bool(mail.is_configured() and user['remind_mail'] and user['email'] and user['email_verified'])


def _footer(user_id):
    return ('\n--\nDu bekommst diese Mail, weil du Erinnerungen per Mail eingeschaltet hast.\n'
            f'Abbestellen: {link("/unsubscribe/" + unsubscribe_token(user_id, "reminder"))}\n'
            f'Einstellungen: {link("/account")}\n')


# --- Reminders of due vaccinations -----------------------------------------------------------------

def parse_leads(text):
    """'56,7' -> [56, 7]; unknown values are left out (the form only offers LEAD_CHOICES)."""
    allowed = dict(LEAD_CHOICES)
    leads = {int(v) for v in (text or '').split(',') if v.strip().isdigit() and int(v) in allowed}
    return sorted(leads, reverse=True)


def group_key(profile_id, group):
    """Identifies a group of due doses in reminder_log: '<profile>:<antigens>:<date>'. The date is the
    recommended one, or for "as soon as possible" doses (whose recommended date is always today) the
    earliest possible one; both stay the same until the record changes."""
    planned = group['first'].next
    anchor = planned.earliest if planned.asap else planned.recommended
    return f'{profile_id}:{"+".join(group["antigens"])}:{anchor.isoformat()}'


def lead_step(group, leads, today):
    """The lead time (as text) to remind of a not yet overdue group with today, or None: the smallest one
    that has been reached, so a reminder that was missed (profile created late) isn't sent together with
    the next one."""
    planned = group['first'].next
    reached = [lead for lead in leads if today >= planned.recommended - timedelta(days=lead)]
    return str(min(reached)) if reached else None


def _last_overdue_reminder(con, user_id, channel, gkey):
    row = con.execute('SELECT MAX(created_at) FROM reminder_log WHERE user_id = ? AND channel = ? '
                      'AND substr(key, 1, ?) = ?', (user_id, channel, len(gkey) + 9, gkey + ':overdue:')).fetchone()
    return datetime.fromisoformat(row[0]).date() if row[0] else None


def _reminder_text(profile, overdue, soon):
    """(subject, sentence, url) of a reminder. Never the vaccine, antigen or disease: mails and lock
    screens are read by others too, and pass through mail and push providers."""
    name = profile['display_name']
    if overdue:
        sentence = f'Bei {name} ist eine Impfung überfällig.'
    elif soon:
        sentence = f'Bei {name} ist bald eine Impfung fällig.'
    else:
        sentence = f'Bei {name} ist eine Impfung fällig.'
    return f'Impfung bei {name}', sentence, link(f'/profiles/{profile["id"]}')


def queue_due_vaccinations(con):
    """Queue reminders of due vaccinations for every account, for the profiles it watches.

    Each group of doses due together (one combination vaccine, profiles.group_actionable()) is reminded
    of once per lead time and channel (`reminder_log`), and while it is overdue every remind_overdue_days.
    The groups of one profile that need a reminder in the same run go out as one message.
    """
    from . import profiles  # here: profiles imports auth, which imports this module

    today = dates.today()
    now = dates.now()
    push_on = push.is_configured()
    users = con.execute("""
        SELECT id, display_name, email, email_verified, family_id, remind_mail, remind_push, remind_leads,
               remind_overdue_days
        FROM users WHERE family_id IS NOT NULL AND (remind_mail = 1 OR remind_push = 1)
    """).fetchall()
    groups_of = {}  # profile id -> actionable groups; each profile is evaluated once per run
    for u in users:
        channels = ['mail'] if _mail_to(u) else []
        if push_on and u['remind_push'] and con.execute('SELECT 1 FROM push_subscriptions WHERE user_id = ?',
                                                        (u['id'],)).fetchone():
            channels.append('push')
        if not channels:
            continue
        leads = parse_leads(u['remind_leads'])
        watched = con.execute("""
            SELECT p.* FROM profile_watches w JOIN profiles p ON p.id = w.profile_id
            WHERE w.user_id = ? AND p.family_id = ? ORDER BY p.birth_date, p.id
        """, (u['id'], u['family_id'])).fetchall()
        for p in watched:
            if p['id'] not in groups_of:
                groups_of[p['id']] = profiles.group_actionable(profiles.evaluate(p)[0])
            for channel in channels:
                new = []  # (reminder_log key, group)
                for group in groups_of[p['id']]:
                    gkey = group_key(p['id'], group)
                    if group['status'] == 'overdue':
                        # Again every remind_overdue_days, counted from the last overdue reminder
                        last = _last_overdue_reminder(con, u['id'], channel, gkey)
                        if u['remind_overdue_days'] and (last is None
                                                         or (today - last).days >= u['remind_overdue_days']):
                            new.append((f'{gkey}:overdue:{today.isoformat()}', group))
                    elif (step := lead_step(group, leads, today)) is not None:
                        key = f'{gkey}:{step}'
                        if not con.execute('SELECT 1 FROM reminder_log WHERE user_id = ? AND channel = ? AND key = ?',
                                           (u['id'], channel, key)).fetchone():
                            new.append((key, group))
                if not new:
                    continue
                for key, _ in new:
                    con.execute('INSERT INTO reminder_log (user_id, channel, key, created_at) VALUES (?, ?, ?, ?)',
                                (u['id'], channel, key, now.isoformat(timespec='seconds')))
                subject, sentence, url = _reminder_text(
                    p, overdue=any(g['status'] == 'overdue' for _, g in new),
                    soon=all(today < g['first'].next.recommended for _, g in new))
                key = f'due:{p["id"]}:{u["id"]}:{now.isoformat(timespec="seconds")}'
                if channel == 'mail':
                    body = (f'Hallo {u["display_name"]}\n\n{sentence}\n\n'
                            f'Was fällig ist, siehst du in helvax:\n{url}\n\n'
                            f'Welche Impfung es ist, steht zum Schutz eurer Daten nicht in dieser Mail.\n'
                            + _footer(u['id']))
                    _queue(con, key + ':mail', u['email'], 'helvax: ' + subject, body,
                           expires_at=now + REMINDER_VALID)
                else:
                    _queue_push(con, key, u['id'], subject, sentence + ' Details in helvax.', url,
                                f'due:{p["id"]}', now + REMINDER_VALID)


def queue_password_reset(con, user):
    """Queue a reset link, unless one was queued lately (anyone can ask, so this stops mail floods)."""
    now = dates.now()
    if con.execute('SELECT 1 FROM notifications WHERE dedupe_key LIKE ? AND created_at > ?',
                   (f'reset:{user["id"]}:%', (now - RESET_INTERVAL).isoformat(timespec='seconds'))).fetchone():
        return
    body = (f'Hallo {user["display_name"]}\n\n'
            f'Für dein Konto «{user["username"]}» wurde ein neues Passwort angefordert.\n'
            f'Über diesen Link setzt du es (gültig für 1 Stunde):\n\n'
            f'{link("/reset-password/" + reset_token(user))}\n\n'
            f'Warst du das nicht? Dann ignoriere diese Mail, dein Passwort bleibt unverändert.\n')
    _queue(con, f'reset:{user["id"]}:{now.isoformat(timespec="seconds")}', user['email'],
           'helvax: Passwort zurücksetzen', body, expires_at=now + RESET_VALID)


def queue_email_verification(con, user):
    """Ask the user to confirm their address. Until then it gets no mails: with a typo, reminders
    and reset links (= the account) would go to a stranger."""
    if not mail.is_configured() or not user['email']:
        return
    now = dates.now()
    if con.execute('SELECT 1 FROM notifications WHERE dedupe_key LIKE ? AND to_addr = ? AND created_at > ?',
                   (f'verify:{user["id"]}:%', user['email'],
                    (now - RESET_INTERVAL).isoformat(timespec='seconds'))).fetchone():
        return
    body = (f'Hallo {user["display_name"]}\n\n'
            f'Bitte bestätige, dass diese Adresse zu deinem Konto «{user["username"]}» gehört:\n\n'
            f'{link("/verify-email/" + verify_token(user["id"], user["email"]))}\n\n'
            f'Erst danach bekommst du Erinnerungen und kannst dein Passwort per Mail zurücksetzen.\n'
            f'Der Link gilt 7 Tage. Kennst du helvax nicht? Dann ignoriere diese Mail.\n')
    _queue(con, f'verify:{user["id"]}:{now.isoformat(timespec="seconds")}:{user["email"]}', user['email'],
           'helvax: E-Mail-Adresse bestätigen', body, expires_at=now + VERIFY_VALID)


# --- Sending ---------------------------------------------------------------------------------

def _push_ttl(n):
    """Seconds the push service may hold the message for a device that is offline."""
    if n['expires_at'] is None:
        return PUSH_TTL.total_seconds()
    return (datetime.fromisoformat(n['expires_at']) - dates.now()).total_seconds()


def send_pending():
    """Send queued mails and push messages. Returns (sent, failed)."""
    con = get_db()
    now = dates.now().isoformat(timespec='seconds')
    rows = con.execute("""
        SELECT n.*, s.endpoint, s.p256dh, s.auth FROM notifications n
        LEFT JOIN push_subscriptions s ON s.id = n.push_subscription_id
        WHERE n.sent_at IS NULL AND n.attempts < ? AND (n.expires_at IS NULL OR n.expires_at > ?)
        ORDER BY n.id
    """, (MAX_ATTEMPTS, now)).fetchall()
    mail_on, push_on = mail.is_configured(), push.is_configured()
    sent = failed = 0
    gone = set()  # subscriptions deleted in this run; their other rows were fetched before
    for n in rows:
        is_push = n['push_subscription_id'] is not None
        if not (push_on if is_push else mail_on):  # switched off since it was queued: keep it for later
            continue
        if n['push_subscription_id'] in gone:
            continue
        try:
            if is_push:
                push.deliver(n, n['body'], _push_ttl(n))
            else:
                mail.deliver(n['to_addr'], n['subject'], n['body'])
        except push.Gone:
            # The browser dropped the subscription; forget the device (and its queued messages)
            failed += 1
            gone.add(n['push_subscription_id'])
            current_app.logger.info('Push %s to %s: subscription gone, removed', n['id'], n['to_addr'])
            with con:
                con.execute('DELETE FROM push_subscriptions WHERE id = ?', (n['push_subscription_id'],))
        except Exception as e:  # any SMTP/push/network error: keep it queued and retry next run
            failed += 1
            current_app.logger.warning('%s %s to %s failed: %s', 'Push' if is_push else 'Mail', n['id'],
                                       n['to_addr'], e)
            with con:
                con.execute('UPDATE notifications SET attempts = attempts + 1, last_error = ? WHERE id = ?',
                            (str(e)[:500], n['id']))
        else:
            sent += 1
            with con:
                con.execute('UPDATE notifications SET sent_at = ?, attempts = attempts + 1 WHERE id = ?',
                            (dates.now().isoformat(timespec='seconds'), n['id']))
    return sent, failed


def run_once():
    """One pass of the notifier: queue due reminders, send everything pending, prune old rows."""
    if not (mail.is_configured() or push.is_configured()):
        return 0, 0
    con = get_db()
    now = dates.now()
    with con:
        queue_due_vaccinations(con)
        con.execute('DELETE FROM notifications WHERE created_at < ?',
                    ((now - KEEP_SENT).isoformat(timespec='seconds'),))
        con.execute('DELETE FROM reminder_log WHERE created_at < ?',
                    ((now - KEEP_REMINDER_LOG).isoformat(timespec='seconds'),))
    return send_pending()
