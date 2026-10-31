"""Conversion of an eVACDOC file (HL7 CDA-CH-VACD, e.g. the export of the former meineimpfungen.ch) into rows
for the CSV import (`flask convert-evacdoc`).

Only the section "History of immunizations" (LOINC 11369-6) is read: every `substanceAdministration` with
moodCode EVN (given, not planned), status completed and not negated. Its antigens come from the SNOMED CT codes
of its reasons (`entryRelationship/observation/code`); codes that are not in `SNOMED_ANTIGENS` are reported
and left out, and an entry without any known antigen is left out as a whole. The product is a catalogue
product only if its name matches and it covers exactly the same antigens, otherwise `other`. Other sections
(past illnesses, problem list, the plan) are only reported: they need a decision in the app.

The file is parsed with the standard library; files with a DOCTYPE (entities) are refused.
"""
import re
import xml.etree.ElementTree as ET

from .importer import COLUMNS
from .profiles import OTHER_PRODUCT

NS = {'h': 'urn:hl7-org:v3'}
SNOMED = '2.16.840.1.113883.6.96'
LOINC_IMMUNIZATIONS = '11369-6'
LOINC_PLAN = '18776-5'  # recommendations of the exporting system: not a record, ignored
MAX_BYTES = 10 * 1024 * 1024

# SNOMED CT procedure codes "<disease> vaccination (procedure)" → antigen of the schedule. Only codes whose
# meaning is unambiguous for one antigen: "Meningococcus vaccination" (41088001) doesn't say the serogroup,
# BCG (42284007) has no antigen in the schedule; both are reported instead.
SNOMED_ANTIGENS = {
    '76668005': 'dip',       # Diphtheria vaccination
    '127786006': 'tet',      # Tetanus vaccination
    '39343008': 'pert',      # Pertussis vaccination
    '72093006': 'polio',     # Poliomyelitis vaccination
    '127787002': 'hib',      # Haemophilus influenzae type b vaccination
    '16584000': 'hbv',       # Hepatitis B vaccination
    '47435007': 'measles',   # Measles vaccination
    '50583002': 'mumps',     # Mumps vaccination
    '82314000': 'rubella',   # Rubella vaccination
    '281658005': 'tbe',      # Tick-borne encephalitis vaccination
}


class EvacdocError(ValueError):
    """The file isn't an eVACDOC document; the message is German."""


def _norm(name):
    return re.sub(r'[^0-9a-z]+', ' ', name.casefold()).strip()


def _product(catalogue, name, antigens):
    """The catalogue product with this name (or a name it starts with) and exactly these antigens, or None."""
    wanted, doc = set(antigens), _norm(name)
    candidates = []
    for p in catalogue.products:
        names = {_norm(p.key), _norm(p.name), _norm(re.sub(r'\(.*?\)', '', p.name))}
        if doc in names:
            candidates.insert(0, p)
        elif any(n and doc.startswith(n + ' ') for n in names):
            candidates.append(p)
    return next((p for p in candidates if set(p.antigens) == wanted), None)


def _day(value):
    m = re.fullmatch(r'(\d{4})(\d{2})(\d{2})\d*([+-]\d{4})?', value or '')
    return f'{m[3]}.{m[2]}.{m[1]}' if m else ''


def _text(el, path):
    found = el.find(path, NS)
    return ' '.join((found.text or '').split()) if found is not None else ''


def convert(raw, schedule, catalogue):
    """(rows, notes): CSV rows (dicts with importer.COLUMNS) and German notes about what was left out."""
    if len(raw) > MAX_BYTES:
        raise EvacdocError('Die Datei ist zu gross für ein Impfdossier.')
    if re.search(rb'<!(DOCTYPE|ENTITY)', raw, re.IGNORECASE):
        raise EvacdocError('Dateien mit DOCTYPE werden nicht gelesen.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise EvacdocError(f'Kein gültiges XML ({e}).') from None
    if root.tag != '{urn:hl7-org:v3}ClinicalDocument':
        raise EvacdocError('Kein HL7-CDA-Dokument (eVACDOC) erwartet.')
    rows, notes, found = [], [], False
    for section in root.findall('.//h:section', NS):
        code = section.find('h:code', NS)
        title = _text(section, 'h:title') or '(ohne Titel)'
        if code is None or code.get('code') != LOINC_IMMUNIZATIONS:
            entries = section.findall('h:entry', NS)
            if entries and (code is None or code.get('code') != LOINC_PLAN):
                notes.append(f'Abschnitt «{title}» ({len(entries)} Eintrag/Einträge) nicht übernommen: '
                             'bei Bedarf im Profil von Hand erfassen (Immunität, Risikofaktoren).')
            continue
        found = True
        for entry in section.findall('h:entry/h:substanceAdministration', NS):
            row = _entry(entry, schedule, catalogue, notes)
            if row:
                rows.append(row)
    if not found:
        raise EvacdocError('Das Dokument hat keinen Abschnitt «Impfungen» (LOINC 11369-6).')
    rows.sort(key=lambda r: r['date'][6:] + r['date'][3:5] + r['date'][:2])
    return rows, notes


def _entry(sa, schedule, catalogue, notes):
    material = sa.find('h:consumable/h:manufacturedProduct/h:manufacturedMaterial', NS)
    name = _text(material, 'h:name') if material is not None else ''
    if not name and material is not None and material.find('h:code', NS) is not None:
        name = ' '.join((material.find('h:code', NS).get('displayName') or '').split())
    name = name or 'Unbekannter Impfstoff'
    time = sa.find('h:effectiveTime', NS)
    day = _day(time.get('value') if time is not None else '')
    label = f'{name} vom {day or "(ohne Datum)"}'
    status = sa.find('h:statusCode', NS)
    if sa.get('moodCode') != 'EVN' or sa.get('negationInd') == 'true' or \
            (status is not None and status.get('code') != 'completed'):
        notes.append(f'{label}: nicht verabreicht oder nicht abgeschlossen, weggelassen.')
        return None
    if not day:
        notes.append(f'{label}: kein Datum, weggelassen.')
        return None
    antigens, unknown = [], []
    for code in sa.findall('h:entryRelationship/h:observation/h:code', NS):
        antigen = SNOMED_ANTIGENS.get(code.get('code')) if code.get('codeSystem') == SNOMED else None
        if antigen and antigen in schedule.antigens:
            if antigen not in antigens:
                antigens.append(antigen)
        else:
            unknown.append(code.get('displayName') or code.get('code') or '?')
    if unknown:
        notes.append(f'{label}: {", ".join(unknown)} hat kein Antigen im Impfplan'
                     + (', weggelassen.' if not antigens else ', nur der Rest übernommen.'))
    if not antigens:
        return None
    order = list(schedule.antigens)
    antigens.sort(key=order.index)
    lot = _text(material, 'h:lotNumberText') if material is not None else ''
    performer = sa.find('h:performer/h:assignedEntity/h:assignedPerson/h:name', NS)
    by = ' '.join(' '.join(performer.itertext()).split()) if performer is not None else ''
    row = dict.fromkeys(COLUMNS, '')
    row.update(date=day, lot='' if lot in ('-', '') else lot, administered_by=by)
    product = _product(catalogue, name, antigens)
    if product:
        row['product'] = product.key
    else:
        row.update(product=OTHER_PRODUCT, product_name=name, antigens=' '.join(antigens))
    return row
