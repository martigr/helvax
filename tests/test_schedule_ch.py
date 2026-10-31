"""The real Swiss schedule (helvax/schedules/ch-2026.yaml): typical life courses.

These tests document how the rules of the BAG schedule are read. If one fails after a change to the
YAML file, check the change against the PDF (docs/sources/) before adjusting the test.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from helvax import engine, schedule
from helvax.engine import Dose, Profile

ROOT = Path(__file__).parent.parent
CH = schedule.load(ROOT / 'helvax' / 'schedules' / 'ch-2026.yaml')
D = date
TODAY = date(2026, 10, 15)
HEXA = ('dip', 'tet', 'pert', 'polio', 'hib', 'hbv')  # e.g. a DTPa-IPV-Hib-HBV vaccine
DTPA_IPV = ('dip', 'tet', 'pert', 'polio')
DTPA = ('dip', 'tet', 'pert')
DT = ('dip', 'tet')
MMRV = ('measles', 'mumps', 'rubella', 'varicella')


def entries(*items):
    """entries((date, antigens), ...) -> Dose list, one Dose per antigen, like combination vaccines."""
    return [Dose(when, a, i) for i, (when, antigens) in enumerate(items) for a in antigens]


def evaluate(birth, doses=(), today=TODAY, flags=(), immunities=()):
    profile = Profile(birth, frozenset(flags), frozenset(immunities))
    return {r.antigen: r for r in engine.evaluate(profile, list(doses), CH, today)}


def test_file_header():
    assert CH.id == 'ch-2026'
    assert CH.title == 'Schweizerischer Impfplan 2026'
    assert CH.version == 'ch-2026 (Stand 16.04.2026)'
    assert not CH.placeholder
    assert all(rule.ref for rule in CH.rules.values())


def test_rules_marked_for_review_are_listed_in_open_questions():
    text = (ROOT / 'OPEN_QUESTIONS.md').read_text(encoding='utf-8')
    listed = set(re.findall(r'`(\w+)`', text))
    missing = [a for a, rule in CH.rules.items() if rule.review and a not in listed]
    assert not missing, f'add these antigens to OPEN_QUESTIONS.md: {missing}'


def test_infant_on_time():
    birth = D(2026, 1, 10)
    first = entries((D(2026, 3, 10), HEXA + ('pcv',)))
    r = evaluate(birth, first, today=D(2026, 3, 20))
    for a in HEXA + ('pcv',):
        assert (r[a].status, r[a].next.number, r[a].next.recommended) == ('upcoming', 2, D(2026, 5, 10)), a
    two = first + entries((D(2026, 5, 12), HEXA + ('pcv',)))
    r = evaluate(birth, two, today=D(2026, 6, 1))
    # Third dose at 12 months, not before 11 months and 6 months after the second
    assert (r['hib'].next.earliest, r['hib'].next.recommended, r['hib'].next.latest) == \
        (D(2026, 12, 10), D(2027, 1, 10), D(2027, 2, 10))
    three = two + entries((D(2027, 1, 12), HEXA + ('pcv',)))
    r = evaluate(birth, three, today=D(2027, 2, 1))
    assert {a: r[a].status for a in ('hib', 'hbv', 'pcv')} == {'hib': 'complete', 'hbv': 'complete',
                                                               'pcv': 'complete'}
    # Diphtheria, tetanus, pertussis and polio: booster at 4–7 years
    for a in DTPA_IPV:
        assert (r[a].status, r[a].next.recommended, r[a].next.latest) == \
            ('upcoming', D(2030, 1, 10), D(2034, 1, 9)), a


def test_combination_vaccine_updates_every_antigen():
    birth = D(2026, 1, 10)
    before = evaluate(birth, today=D(2026, 3, 20))
    after = evaluate(birth, entries((D(2026, 3, 10), HEXA)), today=D(2026, 3, 20))
    for a in HEXA:
        assert before[a].status == 'due' and before[a].next.number == 1
        assert after[a].status == 'upcoming' and after[a].next.number == 2
    assert after['pcv'].status == 'due'  # not part of this vaccine
    mmrv = evaluate(birth, entries((D(2026, 10, 12), MMRV)), today=D(2026, 10, 15))
    assert {mmrv[a].next.recommended for a in MMRV} == {D(2027, 1, 10)}


def test_late_start_at_seven_months_gets_the_catch_up_schema():
    birth = D(2026, 3, 1)
    r = evaluate(birth, today=D(2026, 10, 1))
    assert r['hib'].status == 'overdue'
    # Tab. 2, 6–11 months: 0, 1, 8 months
    assert [d.recommended for d in r['hib'].plan] == [D(2026, 10, 1), D(2026, 11, 1), D(2027, 6, 1)]
    assert [d.recommended for d in r['dip'].plan][:3] == [D(2026, 10, 1), D(2026, 11, 1), D(2027, 6, 1)]


def test_missed_dose_is_overdue_and_the_plan_continues():
    birth = D(2026, 1, 10)
    r = evaluate(birth, entries((D(2026, 3, 10), HEXA)), today=D(2026, 10, 15))
    assert (r['hib'].status, r['hib'].next.number, r['hib'].next.recommended) == ('overdue', 2, D(2026, 5, 10))
    # Third dose 6 months after the second, which can only be given today
    assert r['hib'].plan[1].recommended == D(2027, 4, 15)


def test_hib_and_pneumococci_only_until_five():
    r = evaluate(D(2020, 6, 1), today=D(2026, 10, 15))
    for a in ('hib', 'pcv'):
        assert (r[a].status, r[a].reason) == ('not_recommended', 'Nur bis 5 Jahre empfohlen')
    r = evaluate(D(2023, 6, 1), today=D(2026, 10, 15))
    assert [d.recommended for d in r['hib'].plan] == [D(2026, 10, 15)]  # 15–59 months: one dose


def test_hepatitis_b_waits_for_eleven_if_not_given_as_infant():
    r = evaluate(D(2020, 6, 1))
    assert (r['hbv'].status, r['hbv'].next.recommended) == ('not_recommended', D(2031, 6, 1))
    r = evaluate(D(2013, 6, 1))
    assert (r['hbv'].status, [d.recommended for d in r['hbv'].plan]) == ('due', [D(2024, 6, 1), D(2027, 2, 15)])


def adult_with_childhood_record(birth):
    """2+1, 4–7 and 11–15 boosters, all on time."""
    return entries((birth.replace(month=3), HEXA), (birth.replace(month=5), HEXA),
                   (birth.replace(year=birth.year + 1), HEXA),
                   (birth.replace(year=birth.year + 5), DTPA_IPV), (birth.replace(year=birth.year + 12), DTPA))


@pytest.mark.parametrize('booster_age, next_age', [(25, 45), (45, 65), (65, 75), (75, 85)])
def test_adult_dt_boosters(booster_age, next_age):
    birth = D(1940, 1, 10)
    doses = adult_with_childhood_record(birth) + entries((birth.replace(year=1940 + booster_age), DT))
    r = evaluate(birth, doses, today=D(1940 + booster_age, 6, 1))
    assert (r['tet'].status, r['tet'].next.recommended) == ('complete', birth.replace(year=1940 + next_age))


def test_adult_boosters_from_the_childhood_record():
    birth = D(2000, 1, 10)
    doses = adult_with_childhood_record(birth)
    r = evaluate(birth, doses, today=D(2024, 6, 1))
    # After the 11–15 booster: dT at 25, and pertussis once at 25
    assert (r['tet'].status, r['tet'].next.recommended) == ('complete', D(2025, 1, 10))
    assert (r['pert'].status, r['pert'].next.recommended) == ('complete', D(2025, 1, 10))
    r = evaluate(birth, doses, today=D(2025, 3, 1))
    assert r['tet'].status == 'due' and r['pert'].status == 'due'
    assert r['polio'].status == 'complete' and r['polio'].next is None  # no routine boosters after 4–7
    r = evaluate(birth, doses + entries((D(2025, 3, 1), DTPA)), today=D(2025, 3, 2))
    assert (r['tet'].next.recommended, r['pert'].status) == (D(2045, 3, 1), 'complete')
    r = evaluate(birth, doses, today=D(2026, 10, 15))  # missed the dose at 25
    assert (r['tet'].status, r['pert'].status) == ('overdue', 'complete')


def test_missed_childhood_booster_needs_the_doctor():
    birth = D(2016, 1, 10)
    doses = entries((D(2016, 3, 10), HEXA), (D(2016, 5, 10), HEXA), (D(2017, 1, 10), HEXA))
    r = evaluate(birth, doses, today=D(2024, 6, 1))
    assert (r['tet'].status, r['tet'].needs_doctor) == ('overdue', True)
    assert r['tet'].reason == 'Nachholschema mit Ärztin/Arzt festlegen'


def test_unvaccinated_adult_dt_catch_up():
    r = evaluate(D(1990, 5, 3))
    assert r['tet'].status == 'overdue'
    assert [d.recommended for d in r['tet'].plan] == [D(2026, 10, 15), D(2026, 12, 15), D(2027, 6, 15)]


def test_supplementary_vaccinations_are_opt_in():
    birth = D(2026, 6, 1)
    r = evaluate(birth, today=D(2026, 7, 1))
    assert r['menb'].status == 'not_recommended'
    assert r['menb'].reason == 'Ergänzende Impfung, im Profil nicht gewählt'
    r = evaluate(birth, today=D(2026, 7, 1), flags=['optin:menb', 'optin:rota'])
    assert (r['menb'].status, r['menb'].next.recommended) == ('upcoming', D(2026, 9, 1))
    assert (r['rota'].status, r['rota'].next.recommended) == ('upcoming', D(2026, 8, 1))
    assert r['menb'].category == 'supplementary'


def test_meningococci_adolescent_course_is_independent():
    birth = D(2012, 2, 1)
    doses = [Dose(d, 'menb') for d in (D(2012, 5, 1), D(2012, 7, 1), D(2013, 3, 1))]
    r = evaluate(birth, doses, flags=['optin:menb'])
    assert (r['menb'].status, r['menb'].next.recommended) == ('due', D(2023, 2, 1))
    r = evaluate(D(2004, 2, 1), flags=['optin:menb'])
    assert (r['menb'].status, r['menb'].reason) == ('not_recommended', 'Nur bis 20 Jahre empfohlen')


def test_mmr_born_before_1964():
    r = evaluate(D(1960, 1, 1))
    for a in ('measles', 'mumps', 'rubella'):
        assert (r[a].status, r[a].reason) == ('not_recommended', 'Vor 1964 geboren: gilt als immun')
    assert r['varicella'].reason == 'Nur bis 40 Jahre empfohlen'
    r = evaluate(D(1964, 1, 1))
    assert r['measles'].status == 'overdue'


def test_mmr_dose_before_nine_months_does_not_count():
    birth = D(2026, 1, 10)
    r = evaluate(birth, [Dose(D(2026, 7, 15), 'measles')], today=D(2026, 8, 1))
    assert r['measles'].doses[0].number is None
    assert (r['measles'].next.number, r['measles'].next.recommended) == (1, D(2026, 10, 10))


def test_varicella_immunity_from_the_disease():
    r = evaluate(D(1995, 1, 1), immunities=['varicella'])
    assert r['varicella'].status == 'not_recommended'
    assert r['measles'].status == 'overdue'


@pytest.mark.parametrize('birth_month, recommended', [(5, D(2026, 10, 1)), (9, D(2026, 10, 1)),
                                                      (11, D(2026, 11, 3)), (2, D(2026, 2, 3))])
def test_rsv_by_birth_month(birth_month, recommended):
    birth = D(2026, birth_month, 3)
    r = evaluate(birth, today=birth)
    assert r['rsv'].next.recommended == recommended
    assert r['rsv'].category == 'basic'


def test_rsv_after_maternal_vaccination_or_when_too_old():
    birth = D(2026, 5, 3)
    assert evaluate(birth, today=birth, flags=['rsv_maternal'])['rsv'].status == 'not_recommended'
    assert evaluate(birth, today=D(2027, 5, 3))['rsv'].reason == 'Nur bis 12 Monate empfohlen'
    r = evaluate(birth, [Dose(D(2026, 10, 5), 'rsv')], today=D(2026, 10, 15))
    assert r['rsv'].status == 'complete'


def test_influenza_and_covid_every_season_from_65():
    birth = D(1955, 4, 1)
    flags = ['optin:influenza', 'optin:covid19']
    r = evaluate(birth, today=D(2026, 10, 20), flags=flags)
    assert (r['influenza'].status, r['influenza'].next.recommended) == ('due', D(2026, 10, 15))
    r = evaluate(birth, [Dose(D(2026, 10, 20), 'influenza'), Dose(D(2026, 5, 1), 'covid19')],
                 today=D(2026, 11, 1), flags=flags)
    assert (r['influenza'].status, r['influenza'].next.recommended) == ('complete', D(2027, 10, 15))
    assert (r['covid19'].status, r['covid19'].next.recommended) == ('due', D(2026, 11, 1))  # 6 months after
    assert evaluate(D(1970, 1, 1), flags=flags)['influenza'].status == 'not_recommended'


def test_zoster_and_pneumococci_from_65():
    birth = D(1960, 3, 1)
    flags = ['optin:zoster', 'optin:pcv']
    r = evaluate(birth, today=D(2026, 10, 15), flags=flags)
    assert (r['zoster'].status, [d.recommended for d in r['zoster'].plan]) == \
        ('due', [D(2026, 10, 15), D(2026, 12, 15)])
    assert (r['pcv'].status, r['pcv'].category) == ('due', 'supplementary')
    r = evaluate(D(1965, 3, 1), today=D(2026, 10, 15), flags=flags)
    assert (r['zoster'].status, r['zoster'].next.recommended) == ('not_recommended', D(2030, 3, 1))


def product_doses(antigen, *items):
    """(date, catalogue tags) -> Dose list, as profiles.evaluate() builds it from catalogue products."""
    return [Dose(d, antigen, i, frozenset(tags)) for i, (d, tags) in enumerate(items)]


def test_pneumococci_from_65_depend_on_the_vaccine():
    """S. 16: PCV20/PCV21 complete it; after PCV13 or PCV15 a higher-valent PCV at least 1 year later."""
    birth, flags = D(1960, 1, 1), ['optin:pcv']
    for tags in (['pcv20'], ['pcv21']):
        r = evaluate(birth, product_doses('pcv', (D(2025, 3, 1), tags)), flags=flags)
        assert (r['pcv'].status, r['pcv'].warnings) == ('complete', ()), tags
    for tags in (['pcv13'], ['pcv15']):
        r = evaluate(birth, product_doses('pcv', (D(2025, 3, 1), tags)), flags=flags)
        assert (r['pcv'].status, r['pcv'].next.number, r['pcv'].next.earliest) == ('due', 2, D(2026, 3, 1)), tags
        r = evaluate(birth, product_doses('pcv', (D(2026, 3, 1), tags)), flags=flags)
        assert (r['pcv'].status, r['pcv'].next.recommended) == ('not_recommended', D(2027, 3, 1)), tags
    r = evaluate(birth, product_doses('pcv', (D(2025, 3, 1), ['pcv13']), (D(2026, 4, 1), ['pcv20'])), flags=flags)
    assert (r['pcv'].status, r['pcv'].warnings) == ('complete', ())
    # Doses before 65 (e.g. as a risk person) don't count for the dose from 65
    r = evaluate(birth, product_doses('pcv', (D(2023, 3, 1), ['pcv20'])), flags=flags)
    assert r['pcv'].status == 'due'
    # "Anderer Impfstoff": counts, with a warning
    r = evaluate(birth, [Dose(D(2025, 3, 1), 'pcv')], flags=flags)
    assert r['pcv'].status == 'complete' and 'Impfstoff unbekannt' in r['pcv'].warnings[0]


def test_meningococci_acwy_depend_on_the_vaccine():
    """S. 12 j), S. 19, S. 26: Menveo 2 doses at 12–23 months (at least 2 months apart), MenQuadfi 1."""
    birth, flags = D(2025, 1, 1), ['optin:menacwy']
    r = evaluate(birth, product_doses('menacwy', (D(2026, 2, 1), ['menquadfi'])), flags=flags)
    assert (r['menacwy'].status, r['menacwy'].next.recommended) == ('complete', D(2036, 1, 1))  # at 11
    r = evaluate(birth, product_doses('menacwy', (D(2026, 2, 1), ['menveo'])), flags=flags, today=D(2026, 3, 1))
    assert (r['menacwy'].status, r['menacwy'].next.number, r['menacwy'].next.recommended) == \
        ('upcoming', 2, D(2026, 4, 1))
    r = evaluate(birth, product_doses('menacwy', (D(2026, 2, 1), ['menveo'])), flags=flags)
    assert r['menacwy'].status == 'overdue'
    two = product_doses('menacwy', (D(2026, 2, 1), ['menveo']), (D(2026, 4, 1), ['menveo']))
    assert evaluate(birth, two, flags=flags)['menacwy'].status == 'complete'
    # Catch-up at 19–23 months: also 2 doses with Menveo; from 24 months 1 dose
    r = evaluate(D(2024, 9, 1), product_doses('menacwy', (D(2026, 6, 1), ['menveo'])), flags=flags)
    assert (r['menacwy'].status, r['menacwy'].next.number) == ('overdue', 2)
    r = evaluate(D(2024, 3, 1), product_doses('menacwy', (D(2026, 6, 1), ['menveo'])), flags=flags)
    assert r['menacwy'].status == 'complete'
    # No dose yet: one dose is planned; "Anderer Impfstoff": one dose, with a warning
    r = evaluate(D(2025, 9, 1), flags=flags)
    assert [p.number for p in r['menacwy'].plan if p.course == 'kleinkind'] == [1]
    r = evaluate(birth, [Dose(D(2026, 2, 1), 'menacwy')], flags=flags)
    assert r['menacwy'].status == 'complete' and 'Impfstoff unbekannt' in r['menacwy'].warnings[0]


def test_tick_borne_encephalitis_needs_the_flag():
    r = evaluate(D(1990, 1, 1))
    assert r['tbe'].status == 'not_recommended'
    assert r['tbe'].reason.startswith('Nur bei: Wohnt in einem FSME-Risikogebiet')
    r = evaluate(D(1990, 1, 1), flags=['fsme_exposure'])
    assert (r['tbe'].status, r['tbe'].category) == ('due', 'risk_group')
    doses = [Dose(d, 'tbe') for d in (D(2015, 3, 1), D(2015, 4, 1), D(2015, 10, 1))]
    r = evaluate(D(1990, 1, 1), doses, flags=['fsme_exposure'])
    assert (r['tbe'].status, r['tbe'].next.recommended) == ('overdue', D(2025, 10, 1))  # more than a year late
    assert evaluate(D(2025, 1, 1), flags=['fsme_exposure'])['tbe'].next.recommended == D(2028, 1, 1)


def test_hpv_two_or_three_doses_by_age_at_the_first():
    r = evaluate(D(2014, 1, 1), [Dose(D(2026, 3, 1), 'hpv')], today=D(2026, 9, 1))
    assert (r['hpv'].status, len(r['hpv'].plan)) == ('due', 1)
    r = evaluate(D(2014, 1, 1), [Dose(D(2026, 3, 1), 'hpv'), Dose(D(2026, 9, 1), 'hpv')])
    assert r['hpv'].status == 'complete'
    r = evaluate(D(2010, 1, 1), [Dose(D(2026, 3, 1), 'hpv'), Dose(D(2026, 9, 1), 'hpv')])
    assert (r['hpv'].status, r['hpv'].next.number) == ('upcoming', 3)
    r = evaluate(D(2004, 1, 1))
    assert r['hpv'].status == 'not_recommended'  # 20–26: supplementary, opt-in
    assert evaluate(D(2004, 1, 1), flags=['optin:hpv'])['hpv'].status == 'due'


def test_complete_childhood_record_shows_nothing_to_do_until_the_next_booster():
    birth = D(2010, 1, 10)
    doses = adult_with_childhood_record(birth) + entries(
        (D(2010, 3, 10), ('pcv',)), (D(2010, 5, 10), ('pcv',)), (D(2011, 1, 10), ('pcv',)),
        (D(2010, 10, 10), MMRV), (D(2011, 1, 12), MMRV), (D(2021, 2, 1), ('hpv',)), (D(2021, 8, 1), ('hpv',)))
    r = evaluate(birth, doses)
    actionable = engine.summary(list(r.values()))
    assert [(s.antigen, s.status) for s in actionable] == []
    assert {a for a, s in r.items() if s.status == 'complete'} == \
        set(HEXA) | {'pcv', 'hpv'} | set(MMRV)


def test_adult_with_primary_series_and_a_late_booster():
    """Infant series, no childhood boosters, a dT dose at 50: no childhood catch-up for adults (Tab. 2 Fn. 5);
    the dose at 50 is the adult booster, the next one 20 years later."""
    birth = D(1958, 3, 31)
    doses = entries((D(1958, 6, 30), DT), (D(1958, 8, 31), DT), (D(1959, 5, 31), DT), (D(2008, 3, 31), DT),
                    (D(1965, 3, 31), ('polio',)), (D(1965, 5, 31), ('polio',)), (D(1966, 3, 31), ('polio',)))
    r = evaluate(birth, doses)
    assert (r['tet'].status, r['tet'].needs_doctor, r['tet'].next.recommended) == ('complete', False, D(2028, 3, 31))
    assert (r['polio'].status, r['polio'].next) == ('complete', None)


def test_adult_with_only_the_primary_series_needs_a_booster():
    birth = D(1986, 5, 1)
    doses = entries((D(1986, 7, 1), DT), (D(1986, 9, 1), DT), (D(1987, 5, 1), DT))
    r = evaluate(birth, doses)
    assert (r['tet'].status, r['tet'].needs_doctor, r['tet'].next.recommended) == ('overdue', False, D(2011, 5, 1))
