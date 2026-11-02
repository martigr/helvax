"""Profiles (the people whose vaccinations are recorded), their vaccination entries and immunities.

Every account of a family sees and edits all profiles of that family. Access goes through
`get_family_profile()` / `get_family_vaccination()`, which answer 404 for anything outside the user's
family, so ids of other families can't even be confirmed to exist.
"""
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import audit, dates, engine, rules
from .auth import login_required
from .db import get_db
from .users import UserError

bp = Blueprint('profiles', __name__)

MAX_NAME = 40
MAX_PRODUCT = 80
MAX_LOT = 40
MAX_BY = 80
MAX_NOTE = 200
MAX_DOSE = 20
IMMUNITY_KINDS = {'disease': 'Krankheit durchgemacht', 'serology': 'Serologie (Antikörper nachgewiesen)'}
OTHER_PRODUCT = 'other'


# --- Access --------------------------------------------------------------------------------------

def family_id():
    """The user's family, or 403: accounts without a family have no profiles."""
    if g.user['family_id'] is None:
        abort(403)
    return g.user['family_id']


def get_family_profile(profile_id):
    row = get_db().execute('SELECT * FROM profiles WHERE id = ? AND family_id = ?',
                           (profile_id, g.user['family_id'])).fetchone()
    return row or abort(404)


def get_family_vaccination(vaccination_id):
    row = get_db().execute("""
        SELECT v.* FROM vaccinations v JOIN profiles p ON p.id = v.profile_id
        WHERE v.id = ? AND p.family_id = ?
    """, (vaccination_id, g.user['family_id'])).fetchone()
    return row or abort(404)


def family_profiles():
    """The profiles of the user's family, oldest first."""
    if g.user['family_id'] is None:
        return []
    return get_db().execute('SELECT * FROM profiles WHERE family_id = ? ORDER BY birth_date, id',
                            (g.user['family_id'],)).fetchall()


# --- Reminders: which accounts watch which profiles ------------------------------------------------

def create_profile(con, family_id, name, birth):
    """Insert a profile; every account of the family gets reminders for it (until it unticks it)."""
    pid = con.execute('INSERT INTO profiles (family_id, display_name, birth_date, created_at) VALUES (?, ?, ?, ?)',
                      (family_id, name, birth.isoformat(), dates.now().isoformat(timespec='seconds'))).lastrowid
    con.execute('INSERT OR IGNORE INTO profile_watches (user_id, profile_id) '
                'SELECT id, ? FROM users WHERE family_id = ?', (pid, family_id))
    return pid


def watch_family(con, user_id, family_id):
    """The account moved to another family (or none): it watches all of that family's profiles, no others."""
    con.execute('DELETE FROM profile_watches WHERE user_id = ?', (user_id,))
    if family_id is not None:
        con.execute('INSERT INTO profile_watches (user_id, profile_id) SELECT ?, id FROM profiles WHERE family_id = ?',
                    (user_id, family_id))


# --- Reading a record and evaluating it ------------------------------------------------------------

def profile_flags(profile_id):
    return {r['flag'] for r in get_db().execute('SELECT flag FROM profile_flags WHERE profile_id = ?',
                                                (profile_id,))}


def load_record(profile):
    """Everything the engine and the record table need for one profile."""
    db = get_db()
    vaccinations = db.execute('SELECT * FROM vaccinations WHERE profile_id = ? ORDER BY date, id',
                              (profile['id'],)).fetchall()
    antigens = {}
    for r in db.execute("""
        SELECT a.* FROM vaccination_antigens a JOIN vaccinations v ON v.id = a.vaccination_id
        WHERE v.profile_id = ?
    """, (profile['id'],)):
        antigens.setdefault(r['vaccination_id'], []).append(r)
    immunities = db.execute('SELECT * FROM immunities WHERE profile_id = ? ORDER BY antigen',
                            (profile['id'],)).fetchall()
    return vaccinations, antigens, immunities, profile_flags(profile['id'])


def _product_tags(catalogue, vaccination):
    """The tags of the entry's catalogue product; None for "Anderer Impfstoff" or a product no longer listed."""
    product = catalogue.get(vaccination['product_key']) if vaccination['product_key'] else None
    return product.tags if product else None


def evaluate(profile, record=None):
    """(statuses, dose numbers, record) of a profile, for today."""
    vaccinations, antigens, immunities, flags = record or load_record(profile)
    schedule = rules.schedule()
    engine_profile = engine.Profile(dates.parse_day(profile['birth_date']), frozenset(flags),
                                    frozenset(i['antigen'] for i in immunities))
    catalogue = rules.catalogue()
    doses = [engine.Dose(dates.parse_day(v['date']), a['antigen'], v['id'], _product_tags(catalogue, v))
             for v in vaccinations for a in antigens.get(v['id'], []) if a['antigen'] in schedule.antigens]
    # Record-only antigens (no rule) are only numbered for the record, never evaluated
    statuses = engine.evaluate(engine_profile, [d for d in doses if d.antigen in schedule.rules], schedule,
                               dates.today())
    numbers = engine.number_doses(engine_profile, doses, schedule)
    return statuses, numbers, (vaccinations, antigens, immunities, flags)


def record_rows(vaccinations, antigens, numbers):
    """The vaccination record as rows for the table: entry + its antigens with the dose numbers."""
    schedule = rules.schedule()
    order = list(schedule.antigens)
    rows = []
    for v in vaccinations:
        items = sorted(antigens.get(v['id'], []), key=lambda a: order.index(a['antigen'])
                       if a['antigen'] in order else len(order))
        rows.append({'entry': v, 'date': dates.parse_day(v['date']), 'antigens': [
            {'key': a['antigen'], 'label': schedule.antigens[a['antigen']].label
             if a['antigen'] in schedule.antigens else a['antigen'],
             'override': a['dose_override'], 'computed': numbers.get((v['id'], a['antigen'])),
             'number': a['dose_override'] or numbers.get((v['id'], a['antigen']))}
            for a in items]})
    return rows


def record_matrix(rows, statuses):
    """The record as a matrix like the paper booklet: one row per entry, one column per antigen (in schedule
    order) that occurs in the record or is needed now (overdue, due, upcoming), so a vaccination that was never
    given shows as an empty column. Cells are the antigen dicts of `record_rows()` (or None); each column
    carries the antigen's status for the footer row."""
    schedule = rules.schedule()
    by_antigen = {s.antigen: s for s in statuses}
    present = {a['key'] for r in rows for a in r['antigens']}
    needed = {s.antigen for s in statuses if s.status in ('overdue', 'due', 'upcoming')}
    wanted = present | needed
    columns = [k for k in schedule.antigens if k in wanted] + sorted(present - set(schedule.antigens))
    return {'columns': [{'key': k, 'label': schedule.antigens[k].label if k in schedule.antigens else k,
                         'short': schedule.antigens[k].short if k in schedule.antigens else k,
                         'record_only': k not in schedule.rules, 'status': by_antigen.get(k)} for k in columns],
            'rows': [{**r, 'cells': [next((a for a in r['antigens'] if a['key'] == k), None) for k in columns]}
                     for r in rows]}


VIEWS = {'list': 'Liste', 'table': 'Tabelle'}


def record_view():
    """?view=list|table: how the vaccination record is shown (default: list); 400 for anything else."""
    view = request.args.get('view', 'list')
    if view not in VIEWS:
        abort(400)
    return view


def group_actionable(statuses):
    """Due/upcoming statuses with the same next dose (e.g. the antigens of one combination vaccine) as one
    group: [{'status', 'labels', 'first'}], most urgent first."""
    groups = {}
    for s in engine.summary(statuses):
        key = (s.status, s.next.recommended, s.next.latest, s.next.asap, s.next.label, s.needs_doctor)
        groups.setdefault(key, []).append(s)
    return [{'status': items[0].status, 'labels': [s.label for s in items], 'first': items[0],
             'antigens': [s.antigen for s in items]} for items in groups.values()]


# --- Profiles ------------------------------------------------------------------------------------

def parse_profile_form(form):
    name = form.get('display_name', '').strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise UserError(f'Der Name muss 1–{MAX_NAME} Zeichen lang sein.')
    birth = dates.parse_day(form.get('birth_date', ''))
    if birth is None or birth > dates.today():
        raise UserError('Bitte ein gültiges Geburtsdatum angeben (nicht in der Zukunft).')
    schedule = rules.schedule()
    flags = set(form.getlist('flags'))
    if flags - set(schedule.flags):
        abort(400)
    return name, birth, flags


def save_flags(con, profile_id, flags):
    con.execute('DELETE FROM profile_flags WHERE profile_id = ?', (profile_id,))
    con.executemany('INSERT INTO profile_flags (profile_id, flag) VALUES (?, ?)',
                    [(profile_id, f) for f in sorted(flags)])


def flag_groups():
    """Schedule flags for the profile form: opt-ins of the supplementary vaccinations and conditions."""
    schedule = rules.schedule()
    flags = schedule.flags.values()
    return ([f for f in flags if f.kind == 'optin'], [f for f in flags if f.kind == 'condition'])


@bp.route('/profiles/new', methods=['GET', 'POST'])
@login_required
def new():
    fid = family_id()
    if request.method == 'POST':
        try:
            name, birth, flags = parse_profile_form(request.form)
        except UserError as e:
            flash(str(e), 'warn')
            return render_template('profile_form.html', profile=None, form=request.form,
                                   flags=set(request.form.getlist('flags')), flag_groups=flag_groups()), 400
        with get_db() as con:
            pid = create_profile(con, fid, name, birth)
            save_flags(con, pid, flags)
            audit.record(con, 'profile_created', name)
        flash(f'Profil «{name}» angelegt. Erfasse jetzt die Impfungen aus dem Impfausweis.', 'ok')
        return redirect(url_for('profiles.show', profile_id=pid))
    return render_template('profile_form.html', profile=None, form={}, flags=set(), flag_groups=flag_groups())


@bp.route('/profiles/<int:profile_id>')
@login_required
def show(profile_id):
    profile = get_family_profile(profile_id)
    statuses, numbers, record = evaluate(profile)
    vaccinations, antigens, immunities, flags = record
    schedule = rules.schedule()
    rows = record_rows(vaccinations, antigens, numbers)
    return render_template('profile.html', profile=profile, birth=dates.parse_day(profile['birth_date']),
                           statuses=statuses, groups=group_actionable(statuses), rows=rows,
                           view=record_view(), views=VIEWS, matrix=record_matrix(rows, statuses),
                           immunities=immunities, immunity_kinds=IMMUNITY_KINDS, schedule=schedule,
                           active_flags=[schedule.flags[f] for f in sorted(flags) if f in schedule.flags],
                           today=dates.today(), max_note=MAX_NOTE)


@bp.route('/profiles/<int:profile_id>/print')
@login_required
def print_view(profile_id):
    profile = get_family_profile(profile_id)
    statuses, numbers, record = evaluate(profile)
    vaccinations, antigens, immunities, _ = record
    rows = record_rows(vaccinations, antigens, numbers)
    return render_template('profile_print.html', profile=profile, birth=dates.parse_day(profile['birth_date']),
                           statuses=statuses, rows=rows, view=record_view(), matrix=record_matrix(rows, statuses),
                           immunities=immunities, immunity_kinds=IMMUNITY_KINDS, today=dates.today())


@bp.route('/profiles/<int:profile_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(profile_id):
    profile = get_family_profile(profile_id)
    if request.method == 'POST':
        try:
            name, birth, flags = parse_profile_form(request.form)
        except UserError as e:
            flash(str(e), 'warn')
            return render_template('profile_form.html', profile=profile, form=request.form,
                                   flags=set(request.form.getlist('flags')), flag_groups=flag_groups()), 400
        first = get_db().execute('SELECT MIN(date) FROM vaccinations WHERE profile_id = ?', (profile_id,)).fetchone()[0]
        if first and birth > dates.parse_day(first):
            flash('Das Geburtsdatum liegt nach der ersten erfassten Impfung.', 'warn')
            return render_template('profile_form.html', profile=profile, form=request.form, flags=flags,
                                   flag_groups=flag_groups()), 400
        with get_db() as con:
            con.execute('UPDATE profiles SET display_name = ?, birth_date = ? WHERE id = ?',
                        (name, birth.isoformat(), profile_id))
            save_flags(con, profile_id, flags)
            audit.record(con, 'profile_changed', name)
        flash('Profil gespeichert.', 'ok')
        return redirect(url_for('profiles.show', profile_id=profile_id))
    return render_template('profile_form.html', profile=profile, form=profile, flags=profile_flags(profile_id),
                           flag_groups=flag_groups())


@bp.route('/profiles/<int:profile_id>/delete', methods=['POST'])
@login_required
def delete(profile_id):
    profile = get_family_profile(profile_id)
    with get_db() as con:
        con.execute('DELETE FROM profiles WHERE id = ?', (profile_id,))  # entries, flags, immunities cascade
        audit.record(con, 'profile_deleted', profile['display_name'])
    flash(f'Profil «{profile["display_name"]}» mit allen Impfungen gelöscht.', 'ok')
    return redirect(url_for('dashboard.index'))


# --- Vaccination entries -------------------------------------------------------------------------

def _text(form, key, limit, label):
    value = ' '.join(form.get(key, '').split())  # one line, no surrounding blanks
    if len(value) > limit:
        raise UserError(f'{label}: höchstens {limit} Zeichen.')
    return value


def parse_vaccination_form(form, profile):
    """The fields of a vaccination entry from the form; raises UserError with a German message."""
    schedule, catalogue = rules.schedule(), rules.catalogue()
    day = dates.parse_day(form.get('date', ''))
    if day is None:
        raise UserError('Bitte ein gültiges Impfdatum angeben.')
    if day < dates.parse_day(profile['birth_date']):
        raise UserError('Das Impfdatum liegt vor dem Geburtsdatum.')
    if day > dates.today():
        raise UserError('Das Impfdatum liegt in der Zukunft. Erfasst werden nur erfolgte Impfungen.')
    product_key = form.get('product', '')
    if product_key == OTHER_PRODUCT:
        product_name = _text(form, 'product_name', MAX_PRODUCT, 'Impfstoff')
        if not product_name:
            raise UserError('Bitte den Namen des Impfstoffs angeben.')
        antigens = form.getlist('antigens')
        if not antigens:
            raise UserError('Bitte angeben, wogegen der Impfstoff schützt.')
        if set(antigens) - set(schedule.antigens):
            abort(400)
        product_key = None
    else:
        product = catalogue.get(product_key)
        if product is None:
            raise UserError('Bitte einen Impfstoff auswählen.')
        product_name, antigens = product.name, list(product.antigens)
    overrides = {}
    for a in antigens:
        raw = form.get(f'dose_{a}', '').strip()
        if raw:
            if not raw.isdigit() or not 1 <= int(raw) <= MAX_DOSE:
                raise UserError(f'Dosis-Nummer: bitte eine Zahl von 1 bis {MAX_DOSE}.')
            overrides[a] = int(raw)
    return {'date': day, 'product_key': product_key, 'product_name': product_name,
            'antigens': list(dict.fromkeys(antigens)), 'overrides': overrides,
            'lot': _text(form, 'lot', MAX_LOT, 'Charge'),
            'administered_by': _text(form, 'administered_by', MAX_BY, 'Geimpft von'),
            'note': _text(form, 'note', MAX_NOTE, 'Notiz')}


def create_entry(con, profile_id, data):
    """Insert a vaccination entry with its antigens; shared by the form and future imports."""
    now = dates.now().isoformat(timespec='seconds')
    vid = con.execute('INSERT INTO vaccinations (profile_id, date, product_key, product_name, lot, '
                      'administered_by, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      (profile_id, data['date'].isoformat(), data['product_key'], data['product_name'], data['lot'],
                       data['administered_by'], data['note'], now, now)).lastrowid
    _save_antigens(con, vid, data)
    return vid


def _save_antigens(con, vaccination_id, data):
    con.execute('DELETE FROM vaccination_antigens WHERE vaccination_id = ?', (vaccination_id,))
    con.executemany('INSERT INTO vaccination_antigens (vaccination_id, antigen, dose_override) VALUES (?, ?, ?)',
                    [(vaccination_id, a, data['overrides'].get(a)) for a in data['antigens']])


def vaccination_form(profile, entry=None, form=None, status=200):
    schedule = rules.schedule()
    selected = None
    if form is not None:
        selected = set(form.getlist('antigens'))
    elif entry is not None:
        selected = {r['antigen'] for r in get_db().execute(
            'SELECT antigen FROM vaccination_antigens WHERE vaccination_id = ?', (entry['id'],))}
    overrides = {}
    computed = {}
    if entry is not None:
        overrides = {r['antigen']: r['dose_override'] for r in get_db().execute(
            'SELECT antigen, dose_override FROM vaccination_antigens WHERE vaccination_id = ?', (entry['id'],))}
        _, numbers, _ = evaluate(profile)
        computed = {a: numbers.get((entry['id'], a)) for a in overrides}
    values = form if form is not None else (entry or {})
    product = values.get('product') if form is not None else (
        (entry['product_key'] or OTHER_PRODUCT) if entry is not None else '')
    return render_template('vaccination_form.html', profile=profile, entry=entry, values=values, product=product,
                           selected=selected or set(), catalogue=rules.catalogue(), antigens=schedule.antigens,
                           overrides=overrides, computed=computed, other=OTHER_PRODUCT, today=dates.today(),
                           limits={'product': MAX_PRODUCT, 'lot': MAX_LOT, 'by': MAX_BY, 'note': MAX_NOTE}), status


@bp.route('/profiles/<int:profile_id>/vaccinations/new', methods=['GET', 'POST'])
@login_required
def new_vaccination(profile_id):
    profile = get_family_profile(profile_id)
    if request.method == 'POST':
        try:
            data = parse_vaccination_form(request.form, profile)
        except UserError as e:
            flash(str(e), 'warn')
            return vaccination_form(profile, form=request.form, status=400)
        with get_db() as con:
            create_entry(con, profile_id, data)
            audit.record(con, 'vaccination_created', profile['display_name'])
        flash(f'Impfung vom {data["date"]:%d.%m.%Y} erfasst.', 'ok')
        if request.form.get('another'):
            return redirect(url_for('profiles.new_vaccination', profile_id=profile_id))
        return redirect(url_for('profiles.show', profile_id=profile_id) + '#record')
    return vaccination_form(profile)


@bp.route('/vaccinations/<int:vaccination_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_vaccination(vaccination_id):
    entry = get_family_vaccination(vaccination_id)
    profile = get_family_profile(entry['profile_id'])
    if request.method == 'POST':
        try:
            data = parse_vaccination_form(request.form, profile)
        except UserError as e:
            flash(str(e), 'warn')
            return vaccination_form(profile, entry, form=request.form, status=400)
        with get_db() as con:
            con.execute('UPDATE vaccinations SET date = ?, product_key = ?, product_name = ?, lot = ?, '
                        'administered_by = ?, note = ?, updated_at = ? WHERE id = ?',
                        (data['date'].isoformat(), data['product_key'], data['product_name'], data['lot'],
                         data['administered_by'], data['note'], dates.now().isoformat(timespec='seconds'),
                         vaccination_id))
            _save_antigens(con, vaccination_id, data)
            audit.record(con, 'vaccination_changed', profile['display_name'])
        flash('Impfung gespeichert.', 'ok')
        return redirect(url_for('profiles.show', profile_id=profile['id']) + '#record')
    return vaccination_form(profile, entry)


@bp.route('/vaccinations/<int:vaccination_id>/delete', methods=['POST'])
@login_required
def delete_vaccination(vaccination_id):
    entry = get_family_vaccination(vaccination_id)
    profile = get_family_profile(entry['profile_id'])
    with get_db() as con:
        con.execute('DELETE FROM vaccinations WHERE id = ?', (vaccination_id,))
        audit.record(con, 'vaccination_deleted', profile['display_name'])
    flash(f'Impfung vom {dates.parse_day(entry["date"]):%d.%m.%Y} gelöscht.', 'ok')
    return redirect(url_for('profiles.show', profile_id=profile['id']) + '#record')


# --- Immunities ----------------------------------------------------------------------------------

@bp.route('/profiles/<int:profile_id>/immunities', methods=['POST'])
@login_required
def add_immunity(profile_id):
    profile = get_family_profile(profile_id)
    schedule = rules.schedule()
    antigen, kind = request.form.get('antigen', ''), request.form.get('kind', '')
    if antigen not in schedule.rules or kind not in IMMUNITY_KINDS:  # record-only antigens: no immunity
        abort(400)
    raw = request.form.get('date', '').strip()
    day = dates.parse_day(raw) if raw else None
    try:
        if raw and (day is None or day > dates.today()):
            raise UserError('Bitte ein gültiges Datum angeben (nicht in der Zukunft), oder das Feld leer lassen.')
        note = _text(request.form, 'note', MAX_NOTE, 'Notiz')
    except UserError as e:
        flash(str(e), 'warn')
        return redirect(url_for('profiles.show', profile_id=profile_id) + '#immunities')
    try:
        with get_db() as con:
            con.execute('INSERT INTO immunities (profile_id, antigen, kind, date, note) VALUES (?, ?, ?, ?, ?)',
                        (profile_id, antigen, kind, day.isoformat() if day else None, note))
            audit.record(con, 'immunity_created', profile['display_name'])
    except sqlite3.IntegrityError:
        flash(f'Für {schedule.antigens[antigen].label} ist schon eine Immunität erfasst.', 'warn')
    else:
        flash(f'Immunität gegen {schedule.antigens[antigen].label} erfasst.', 'ok')
    return redirect(url_for('profiles.show', profile_id=profile_id) + '#immunities')


@bp.route('/immunities/<int:immunity_id>/delete', methods=['POST'])
@login_required
def delete_immunity(immunity_id):
    row = get_db().execute("""
        SELECT i.*, p.display_name FROM immunities i JOIN profiles p ON p.id = i.profile_id
        WHERE i.id = ? AND p.family_id = ?
    """, (immunity_id, g.user['family_id'])).fetchone() or abort(404)
    with get_db() as con:
        con.execute('DELETE FROM immunities WHERE id = ?', (immunity_id,))
        audit.record(con, 'immunity_deleted', row['display_name'])
    flash('Immunität gelöscht.', 'ok')
    return redirect(url_for('profiles.show', profile_id=row['profile_id']) + '#immunities')
