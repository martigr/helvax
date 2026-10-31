"""Notifications by mail.

Everything goes through the `notifications` table (an outbox): views only queue rows,
`flask send-notifications` sends them. A request never waits for the mail server, a failed send is
retried, and `dedupe_key` makes sure nobody gets the same message twice.

Mails never carry health details (no antigen, no product): only who it is about and a link.
"""
from datetime import timedelta

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

from . import dates, mail
from .db import get_db

RESET_VALID = timedelta(hours=1)
RESET_INTERVAL = timedelta(minutes=15)  # at most one reset (or verification) mail per user in this time
VERIFY_VALID = timedelta(days=7)
MAX_ATTEMPTS = 5
KEEP_SENT = timedelta(days=30)


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

def _queue(con, dedupe_key, to_addr, subject, body, expires_at=None):
    con.execute(
        'INSERT OR IGNORE INTO notifications (dedupe_key, to_addr, subject, body, created_at, expires_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (dedupe_key, to_addr, subject, body, dates.now().isoformat(timespec='seconds'),
         expires_at.isoformat(timespec='seconds') if expires_at else None))


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

def send_pending():
    """Send queued mails. Returns (sent, failed)."""
    con = get_db()
    now = dates.now().isoformat(timespec='seconds')
    rows = con.execute("""
        SELECT * FROM notifications
        WHERE sent_at IS NULL AND attempts < ? AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY id
    """, (MAX_ATTEMPTS, now)).fetchall()
    if not mail.is_configured():  # switched off since they were queued: keep them for later
        return 0, 0
    sent = failed = 0
    for n in rows:
        try:
            mail.deliver(n['to_addr'], n['subject'], n['body'])
        except Exception as e:  # any SMTP/network error: keep it queued and retry next run
            failed += 1
            current_app.logger.warning('Mail %s to %s failed: %s', n['id'], n['to_addr'], e)
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
    """One pass of the notifier: send everything pending, prune old rows."""
    if not mail.is_configured():
        return 0, 0
    con = get_db()
    with con:
        con.execute('DELETE FROM notifications WHERE created_at < ?',
                    ((dates.now() - KEEP_SENT).isoformat(timespec='seconds'),))
    return send_pending()
