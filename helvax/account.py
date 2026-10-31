"""The own account: display name, mail address and password; the confirmation link from mails.

(Named `account` rather than `profile`: profiles are the people whose vaccinations are recorded.)
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from . import audit, mail
from .auth import keep_session, login_required
from .db import get_db
from .notifications import queue_email_verification, read_verify_token
from .users import MAX_NAME_LENGTH, MIN_PASSWORD_LENGTH, UserError, clean_email, get_user, set_password

bp = Blueprint('account', __name__)


@bp.route('/account', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'GET':
        family = get_db().execute('SELECT name FROM families WHERE id = ?', (g.user['family_id'],)).fetchone()
        members = [r['display_name'] for r in get_db().execute(
            'SELECT display_name FROM users WHERE family_id = ? AND id != ? ORDER BY display_name COLLATE NOCASE',
            (g.user['family_id'], g.user['id']))]
        return render_template('account.html', min_pw=MIN_PASSWORD_LENGTH, max_name=MAX_NAME_LENGTH,
                               family=family and family['name'], members=members)

    action = request.form.get('action')
    if action == 'name':
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
