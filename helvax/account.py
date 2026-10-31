"""The own account: display name, mail address, reminders, push devices and password; the links from
mails (confirm address, unsubscribe).

(Named `account` rather than `profile`: profiles are the people whose vaccinations are recorded.)
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from . import audit, dates, mail, push
from .auth import keep_session, login_required
from .db import get_db
from .notifications import (
    LEAD_CHOICES,
    OVERDUE_CHOICES,
    parse_leads,
    queue_email_verification,
    read_unsubscribe_token,
    read_verify_token,
)
from .profiles import family_profiles
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, clean_email, get_user, set_password

bp = Blueprint('account', __name__)

# Unsubscribe link kinds: (column to switch off, label for the confirmation page). Only the mail
# channel: push messages are switched off on /account or on the device.
UNSUBSCRIBE = {
    'reminder': ('remind_mail', 'Erinnerungen an fällige Impfungen per Mail'),
}
MAX_PUSH_DEVICES = 10  # per user; subscribing another one drops the oldest


def channels(by_mail, by_push):
    """German summary for the audit log, e.g. 'Mail + Push'."""
    return ' + '.join(name for name, on in (('Mail', by_mail), ('Push', by_push)) if on) or 'aus'


def watched_ids():
    return {r['profile_id'] for r in get_db().execute('SELECT profile_id FROM profile_watches WHERE user_id = ?',
                                                      (g.user['id'],))}


@bp.route('/account', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'GET':
        family = get_db().execute('SELECT name FROM families WHERE id = ?', (g.user['family_id'],)).fetchone()
        members = [r['display_name'] for r in get_db().execute(
            'SELECT display_name FROM users WHERE family_id = ? AND id != ? ORDER BY display_name COLLATE NOCASE',
            (g.user['family_id'], g.user['id']))]
        push_endpoints = [r['endpoint'] for r in get_db().execute(
            'SELECT endpoint FROM push_subscriptions WHERE user_id = ?', (g.user['id'],))]
        return render_template('account.html', min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH,
                               family=family and family['name'], members=members,
                               lead_choices=LEAD_CHOICES, leads=parse_leads(g.user['remind_leads']),
                               overdue_choices=OVERDUE_CHOICES, profiles=family_profiles(), watched=watched_ids(),
                               push_endpoints=push_endpoints,
                               push_key=push.public_key() if push.is_configured() else None)

    action = request.form.get('action')
    if action == 'reminders':
        save_reminders()
    elif action == 'name':
        name = request.form.get('display_name', '').strip()
        if not 1 <= len(name) <= MAX_NAME_LENGTH:
            flash(f'Der Anzeigename muss 1–{MAX_NAME_LENGTH} Zeichen lang sein.', 'warn')
        else:
            with get_db() as con:
                con.execute('UPDATE users SET display_name = ? WHERE id = ?', (name, g.user['id']))
                audit.record(con, 'display_name', f'{g.user["display_name"]} → {name}')
            flash('Anzeigename gespeichert.', 'ok')
    elif action == 'email':
        try:
            email = clean_email(request.form.get('email'))
        except UserError as e:
            flash(str(e), 'warn')
            return redirect(url_for('account.index'))
        if email != g.user['email']:
            with get_db() as con:
                con.execute('UPDATE users SET email = ?, email_verified = 0 WHERE id = ?', (email, g.user['id']))
                audit.record(con, 'email_changed', email or 'keine')
                queue_email_verification(con, get_user(g.user['id']))
            if email and mail.is_configured():
                flash('Adresse gespeichert. Wir haben dir eine Mail geschickt: Bitte bestätige deine Adresse '
                      'über den Link darin, erst dann bekommst du Mails.', 'ok')
            else:
                flash('Adresse gespeichert.', 'ok')
    elif action == 'verify_email':
        if g.user['email'] and not g.user['email_verified']:
            with get_db() as con:
                queue_email_verification(con, g.user)
            flash(f'Bestätigungsmail an {g.user["email"]} verschickt.', 'ok')
    elif action == 'password':
        if not check_password_hash(g.user['password'], request.form.get('old_password', '')):
            flash('Das aktuelle Passwort ist falsch.', 'warn')
        elif request.form.get('new_password', '') != request.form.get('new_password2', ''):
            flash('Die neuen Passwörter stimmen nicht überein.', 'warn')
        else:
            try:
                set_password(g.user['id'], request.form.get('new_password', ''))
                keep_session()
                with get_db() as con:
                    audit.record(con, 'password_changed')
                flash('Passwort geändert. Auf anderen Geräten bist du jetzt abgemeldet.', 'ok')
            except UserError as e:
                flash(str(e), 'warn')
    return redirect(url_for('account.index'))


def save_reminders():
    """action=reminders: channels, lead times, overdue interval, watched profiles. Values the form doesn't
    offer are 400, also the id of a profile of another family."""
    form = request.form
    try:
        leads = [int(v) for v in form.getlist('leads')]
        overdue_days = int(form.get('overdue_days', ''))
        watch = {int(v) for v in form.getlist('watch')}
    except ValueError:
        abort(400)
    if not set(leads) <= dict(LEAD_CHOICES).keys() or overdue_days not in dict(OVERDUE_CHOICES):
        abort(400)
    family = {p['id']: p['display_name'] for p in family_profiles()}
    if not watch <= family.keys():
        abort(400)
    by_mail = bool(form.get('remind_mail'))
    # Without push config the form has no push box; keep the choice for when push comes back
    by_push = bool(form.get('remind_push')) if push.is_configured() else bool(g.user['remind_push'])
    leads_text = ','.join(str(v) for v in sorted(set(leads), reverse=True))
    with get_db() as con:
        con.execute('UPDATE users SET remind_mail = ?, remind_push = ?, remind_leads = ?, remind_overdue_days = ? '
                    'WHERE id = ?', (int(by_mail), int(by_push), leads_text, overdue_days, g.user['id']))
        con.execute('DELETE FROM profile_watches WHERE user_id = ?', (g.user['id'],))
        con.executemany('INSERT INTO profile_watches (user_id, profile_id) VALUES (?, ?)',
                        [(g.user['id'], pid) for pid in sorted(watch)])
        lead_labels = [label for days, label in LEAD_CHOICES if days in leads]
        audit.record(con, 'reminders', f'{channels(by_mail, by_push and push.is_configured())}; '
                     f'vorher: {", ".join(lead_labels) or "nie"}; '
                     f'überfällig: {dict(OVERDUE_CHOICES)[overdue_days]}; '
                     f'Profile: {", ".join(family[pid] for pid in family if pid in watch) or "keine"}')
    flash('Erinnerungen gespeichert.', 'ok')


@bp.route('/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    """static/push.js sends the browser's subscription here (form fields, so the CSRF check applies).
    A device belongs to whoever subscribed it last."""
    if not push.is_configured():
        abort(404)
    endpoint, p256dh, auth = (request.form.get(k, '') for k in ('endpoint', 'p256dh', 'auth'))
    if not push.valid_subscription(endpoint, p256dh, auth):
        abort(400)
    with get_db() as con:
        # Also drops messages still queued for the device's previous owner
        con.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', (endpoint,))
        con.execute('INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (g.user['id'], endpoint, p256dh, auth, dates.now().isoformat(timespec='seconds')))
        con.execute('DELETE FROM push_subscriptions WHERE user_id = ? AND id NOT IN '
                    '(SELECT id FROM push_subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT ?)',
                    (g.user['id'], g.user['id'], MAX_PUSH_DEVICES))
        audit.record(con, 'push_enabled', push.service_host(endpoint))
    return '', 204


@bp.route('/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    """The push button switched this device off. Works without push config too, to clean up."""
    endpoint = request.form.get('endpoint', '')
    if not endpoint:
        abort(400)
    with get_db() as con:
        if con.execute('DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?',
                       (g.user['id'], endpoint)).rowcount:
            audit.record(con, 'push_disabled', push.service_host(endpoint))
    return '', 204


@bp.route('/unsubscribe/<token>', methods=['GET', 'POST'])
def unsubscribe(token):
    """Link in every reminder mail. GET only shows a button, so link scanners change nothing."""
    data = read_unsubscribe_token(token)
    if data is None or data[1] not in UNSUBSCRIBE:
        abort(404)
    user_id, kind = data
    column, label = UNSUBSCRIBE[kind]
    user = get_user(user_id)
    if user is None:
        abort(404)
    done = False
    if request.method == 'POST':
        with get_db() as con:
            con.execute(f'UPDATE users SET {column} = 0 WHERE id = ?', (user_id,))
            audit.record(con, 'unsubscribe', label, user=user)
        done = True
    return render_template('unsubscribe.html', label=label, done=done)


@bp.route('/verify-email/<token>', methods=['GET', 'POST'])
def confirm_email(token):
    """Link in the verification mail. GET only shows a button, so a link scanner at a wrong
    recipient's mail provider can't confirm the address."""
    data = read_verify_token(token)
    user = get_user(data[0]) if data else None
    if user is None or user['email'] != data[1]:  # also when the address changed since
        return render_template('confirm_email.html', invalid=True), 400
    done = bool(user['email_verified'])
    if request.method == 'POST' and not done:
        with get_db() as con:
            con.execute('UPDATE users SET email_verified = 1 WHERE id = ? AND email = ?', (user['id'], data[1]))
            audit.record(con, 'email_confirmed', data[1], user=user)
        done = True
    return render_template('confirm_email.html', email=data[1], done=done)
