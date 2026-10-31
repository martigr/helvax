"""Import of vaccination entries from a CSV file (used by `flask import-vaccinations`).

Each row is turned into the fields of the vaccination form and checked with `profiles.parse_vaccination_form()`,
so an import accepts exactly what the form accepts. `parse()` checks the whole file and inserts nothing;
`save()` inserts all entries in one transaction. A file with any error is not imported at all.

Columns (header row required, `,` or `;` as separator, UTF-8 or Windows-1252):
    date             2015-03-01 or 01.03.2015
    product          a key of the product catalogue (`flask list-products`) or `other`
    product_name     only with `other`: the name of the vaccine
    antigens         only with `other`: antigen keys, separated by blanks, `,`, `+` or `|`
    doses            optional dose numbers that differ from the count, e.g. `pert=4 polio=3`
    lot, administered_by, note   optional
"""
import csv
import io
import re
from datetime import datetime

from werkzeug.datastructures import MultiDict

from . import rules
from .db import get_db
from .profiles import OTHER_PRODUCT, create_entry, parse_vaccination_form
from .users import UserError

COLUMNS = ('date', 'product', 'product_name', 'antigens', 'doses', 'lot', 'administered_by', 'note')
REQUIRED = ('date', 'product')
MAX_ROWS = 1000
_SPLIT = re.compile(r'[\s,+|]+')


class FileFormatError(ValueError):
    """The file can't be read at all (encoding, header); the message is German."""


def decode(raw):
    """Text of the file: UTF-8 (with or without BOM, as Excel writes it), else Windows-1252."""
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def _day(value):
    """ISO date or the Swiss notation 01.03.2015 / 1.3.2015 as ISO string ('' if neither)."""
    value = value.strip()
    try:
        return datetime.strptime(value, '%d.%m.%Y').date().isoformat()
    except ValueError:
        return value


def _row_form(row, schedule, catalogue):
    """The form fields (MultiDict) of one CSV row; raises UserError."""
    form = MultiDict()
    for key in ('product', 'product_name', 'lot', 'administered_by', 'note'):
        form[key] = (row.get(key) or '').strip()
    form['date'] = _day(row.get('date') or '')
    antigens = [a for a in _SPLIT.split((row.get('antigens') or '').strip().lower()) if a]
    if form['product'] == OTHER_PRODUCT:
        unknown = [a for a in antigens if a not in schedule.antigens]
        if unknown:
            raise UserError(f'Unbekanntes Antigen: {", ".join(unknown)} (siehe list-products --antigens).')
        form.setlist('antigens', antigens)
    elif catalogue.get(form['product']) is None:
        raise UserError(f'Unbekannter Impfstoff «{form["product"]}» (siehe list-products, sonst «other»).')
    elif antigens or form['product_name']:
        raise UserError('antigens und product_name nur zusammen mit product «other» angeben.')
    for part in _SPLIT.split((row.get('doses') or '').strip().lower()):
        if not part:
            continue
        antigen, sep, number = part.partition('=')
        if not sep or not antigen:
            raise UserError(f'doses: «{part}» ist nicht im Format antigen=Nummer.')
        form[f'dose_{antigen}'] = number
    return form


def parse(text, profile):
    """(entries, errors, skipped): the checked entries of the file, German messages 'Zeile n: ...' for the
    rows with errors, and for the rows skipped because the profile (or an earlier row) already has an entry
    with the same date and vaccine, so importing a file twice doesn't double the record."""
    schedule, catalogue = rules.schedule(), rules.catalogue()
    sample = text.split('\n', 1)[0]
    delimiter = ';' if sample.count(';') > sample.count(',') else ','
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [(h or '').strip().lower() for h in (reader.fieldnames or [])]
    unknown = [h for h in header if h not in COLUMNS]
    if unknown:
        raise FileFormatError(f'Unbekannte Spalte(n): {", ".join(unknown)}. Erlaubt: {", ".join(COLUMNS)}.')
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise FileFormatError(f'Spalte(n) fehlen: {", ".join(missing)} (erste Zeile = Spaltennamen).')
    reader.fieldnames = header
    seen = {(r['date'], r['product_name'].casefold()) for r in get_db().execute(
        'SELECT date, product_name FROM vaccinations WHERE profile_id = ?', (profile['id'],))}
    entries, errors, skipped = [], [], []
    for row in reader:
        line = reader.line_num
        if None in row:
            errors.append(f'Zeile {line}: mehr Werte als Spalten.')
            continue
        if not any((v or '').strip() for v in row.values()):
            continue  # empty line
        if len(entries) + len(errors) + len(skipped) >= MAX_ROWS:
            raise FileFormatError(f'Höchstens {MAX_ROWS} Zeilen pro Datei.')
        try:
            form = _row_form(row, schedule, catalogue)
            data = parse_vaccination_form(form, profile)
            extra = [k[5:] for k in form if k.startswith('dose_') and k[5:] not in data['antigens']]
            if extra:
                raise UserError(f'doses: {", ".join(extra)} gehört nicht zu diesem Impfstoff.')
        except UserError as e:
            errors.append(f'Zeile {line}: {e}')
            continue
        key = (data['date'].isoformat(), data['product_name'].casefold())
        if key in seen:
            skipped.append(f'Zeile {line}: {data["product_name"]} vom {data["date"]:%d.%m.%Y} ist schon erfasst.')
            continue
        seen.add(key)
        entries.append(data)
    return entries, errors, skipped


def save(con, profile, entries):
    """Insert the checked entries (inside the caller's transaction)."""
    for data in entries:
        create_entry(con, profile['id'], data)
