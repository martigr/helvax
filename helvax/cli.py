"""Command line: flask --app helvax <command>."""
import csv
import sqlite3
import time

import click
from flask import current_app
from flask.cli import with_appcontext

from . import audit, demo, evacdoc, importer, mail, notifications, push, rules
from .db import get_db
from .users import UserError, create_user, get_user_by_username


@click.command('create-user')
@with_appcontext
@click.argument('username')
@click.option('--name', 'display_name', help='Anzeigename (Standard: Benutzername)')
@click.option('--admin', is_flag=True, help='Benutzer erhält Admin-Rechte')
@click.password_option()
def create_user_command(username, display_name, admin, password):
    """Legt einen Benutzer an."""
    try:
        create_user(username, password, display_name, admin)
    except UserError as e:
        raise click.ClickException(str(e)) from None
    with get_db() as con:
        audit.record(con, 'user_created', username.strip().lower() + (' (Admin)' if admin else ''),
                     username='Kommandozeile')
    click.echo(f'Benutzer {username} angelegt.')


@click.command('make-admin')
@with_appcontext
@click.argument('username')
@click.option('--revoke', is_flag=True, help='Admin-Rechte entziehen statt geben')
def make_admin_command(username, revoke):
    """Gibt einem bestehenden Benutzer Admin-Rechte (oder entzieht sie mit --revoke)."""
    user = get_user_by_username(username)
    if user is None:
        raise click.ClickException(f'Benutzer {username} nicht gefunden.')
    if bool(user['is_admin']) == (not revoke):
        click.echo(f'{username} ist schon Admin.' if not revoke else f'{username} ist kein Admin.')
        return
    with get_db() as con:
        if revoke and not con.execute('SELECT 1 FROM users WHERE is_admin = 1 AND id != ?',
                                      (user['id'],)).fetchone():
            raise click.ClickException(f'{username} ist der letzte Admin. Gib zuerst jemand anderem Admin-Rechte.')
        con.execute('UPDATE users SET is_admin = ? WHERE id = ?', (int(not revoke), user['id']))
        audit.record(con, 'user_admin_revoked' if revoke else 'user_admin', user['username'],
                     username='Kommandozeile')
    click.echo(f'{username} ist kein Admin mehr.' if revoke else f'{username} ist jetzt Admin.')


@click.command('seed-demo')
@with_appcontext
def seed_demo_command():
    """Fiktive Demo-Familie für die lokale Entwicklung (nie produktiv verwenden)."""
    demo.seed()
    click.echo('Demo-Benutzer: ' + ', '.join(f'{u} / {p}' for u, p, _, _ in demo.USERS))


@click.command('send-notifications')
@click.option('--loop', 'interval', type=int, default=0, metavar='SECONDS',
              help='Immer wieder ausführen, alle SECONDS Sekunden (für einen eigenen Container).')
@with_appcontext
def send_notifications_command(interval):
    """Erinnerungen einplanen und alle wartenden Mails und Push-Mitteilungen verschicken."""
    app = current_app._get_current_object()
    if not (mail.is_configured() or push.is_configured()):
        app.logger.warning('Neither mail (SMTP_HOST, MAIL_FROM) nor push (VAPID_PRIVATE_KEY) is configured; '
                           'no notifications are sent.')
    while True:
        with app.app_context():  # fresh DB connection per run
            sent, failed = notifications.run_once()
        if sent or failed:
            app.logger.info('Notifications: %d sent, %d failed', sent, failed)
        if not interval:
            break
        time.sleep(interval)


@click.command('gen-vapid-key')
def gen_vapid_key_command():
    """Erzeugt einen Schlüssel für Push-Mitteilungen (Zeile für die .env-Datei)."""
    click.echo(f'VAPID_PRIVATE_KEY={push.new_private_key()}')


@click.command('backup')
@click.argument('target', type=click.Path(dir_okay=False))
@with_appcontext
def backup_command(target):
    """Sichert die Datenbank in die Datei TARGET (geht auch, während die App läuft)."""
    # SQLite's online backup copies a consistent state, including what is still in the WAL file
    dest = sqlite3.connect(target)
    try:
        get_db().backup(dest)
    finally:
        dest.close()
    click.echo(f'Datenbank gesichert nach {target}.')


@click.command('list-products')
@click.argument('search', required=False)
@click.option('--antigens', 'show_antigens', is_flag=True, help='Die Antigen-Schlüssel statt der Impfstoffe zeigen.')
@with_appcontext
def list_products_command(search, show_antigens):
    """Zeigt die Impfstoffe des Katalogs mit Schlüssel und Antigenen (SEARCH: Teil von Name oder Schlüssel)."""
    schedule = rules.schedule()
    if show_antigens:
        for key, antigen in schedule.antigens.items():
            suffix = ' (nur erfasst, ohne Auswertung)' if antigen.record_only else ''
            click.echo(f'{key:<13} {antigen.label}{suffix}')
        return
    needle = (search or '').casefold()
    found = False
    for group, products in rules.catalogue().groups():
        products = [p for p in products if needle in p.key.casefold() or needle in p.name.casefold()]
        if not products:
            continue
        click.echo(('\n' if found else '') + group)
        found = True
        for p in products:
            click.echo(f'  {p.key:<22} {p.name:<36} {" ".join(p.antigens)}')
    if not found:
        raise click.ClickException(f'Kein Impfstoff passt zu «{search}».')
    click.echo(f'\nNicht dabei: product «{importer.OTHER_PRODUCT}» mit product_name und antigens '
               '(Schlüssel: list-products --antigens).')


def _family_profile(user, profile):
    """The profile (id or name) in the user's family, or a ClickException listing the family's profiles."""
    if user['family_id'] is None:
        raise click.ClickException(f'{user["username"]} gehört zu keiner Familie.')
    rows = get_db().execute('SELECT * FROM profiles WHERE family_id = ? ORDER BY birth_date, id',
                            (user['family_id'],)).fetchall()
    match = [r for r in rows if str(r['id']) == profile.strip()] or \
        [r for r in rows if r['display_name'].casefold() == profile.strip().casefold()]
    if len(match) == 1:
        return match[0]
    names = ', '.join(f'{r["display_name"]} (Nr. {r["id"]})' for r in rows) or 'keine'
    if match:
        raise click.ClickException(f'Mehrere Profile heissen «{profile}», bitte die Nummer angeben: {names}')
    raise click.ClickException(f'Profil «{profile}» nicht gefunden. Profile der Familie: {names}')


@click.command('import-vaccinations')
@click.argument('username')
@click.argument('profile')
@click.argument('file', type=click.File('rb'))
@click.option('--dry-run', is_flag=True, help='Nur prüfen und anzeigen, nichts speichern.')
@click.option('--yes', '-y', is_flag=True, help='Ohne Rückfrage speichern.')
@with_appcontext
def import_vaccinations_command(username, profile, file, dry_run, yes):
    """Erfasst die Impfungen aus der CSV-Datei FILE beim Profil PROFILE (Name oder Nummer) der Familie von
    USERNAME. Spalten: date, product, product_name, antigens, doses, lot, administered_by, note."""
    user = get_user_by_username(username)
    if user is None:
        raise click.ClickException(f'Benutzer {username} nicht gefunden.')
    prof = _family_profile(user, profile)
    try:
        entries, errors, skipped = importer.parse(importer.decode(file.read()), prof)
    except importer.FileFormatError as e:
        raise click.ClickException(str(e)) from None
    for data in entries:
        click.echo(f'{data["date"]:%d.%m.%Y}  {data["product_name"]:<36} {" ".join(data["antigens"])}')
    for message in skipped:
        click.echo(f'Übersprungen: {message}')
    if errors:
        for message in errors:
            click.echo(f'Fehler: {message}', err=True)
        raise click.ClickException(f'{len(errors)} Zeile(n) mit Fehlern, nichts gespeichert.')
    if not entries:
        click.echo('Keine neuen Impfungen in der Datei.')
        return
    summary = f'{len(entries)} Impfung(en) für {prof["display_name"]}'
    if dry_run:
        click.echo(f'{summary} geprüft, nichts gespeichert (--dry-run).')
        return
    if not yes:
        click.confirm(f'{summary} speichern?', abort=True)
    with get_db() as con:
        importer.save(con, prof, entries)
        audit.record(con, 'vaccinations_imported', f'{prof["display_name"]}: {len(entries)}', user=user,
                     username=f'Kommandozeile ({user["username"]})')
    click.echo(f'{summary} gespeichert.')


@click.command('convert-evacdoc')
@click.argument('source', type=click.File('rb'))
@click.argument('target', type=click.Path(dir_okay=False, writable=True))
@with_appcontext
def convert_evacdoc_command(source, target):
    """Wandelt ein eVACDOC-Impfdossier (XML, z. B. von meineimpfungen.ch) in eine CSV-Datei für
    import-vaccinations um. Zeigt an, was nicht übernommen werden konnte."""
    try:
        rows, notes = evacdoc.convert(source.read(), rules.schedule(), rules.catalogue())
    except evacdoc.EvacdocError as e:
        raise click.ClickException(str(e)) from None
    with open(target, 'w', encoding='utf-8-sig', newline='') as f:  # BOM and ';': opens directly in Excel
        writer = csv.DictWriter(f, fieldnames=importer.COLUMNS, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)
    for note in notes:
        click.echo(f'Hinweis: {note}')
    click.echo(f'{len(rows)} Impfung(en) nach {target} geschrieben. Bitte prüfen, dann: '
               f'import-vaccinations <benutzer> <profil> {target} --dry-run')


def init_app(app):
    for command in (create_user_command, make_admin_command, seed_demo_command, send_notifications_command,
                    gen_vapid_key_command, backup_command, list_products_command, import_vaccinations_command,
                    convert_evacdoc_command):
        app.cli.add_command(command)
