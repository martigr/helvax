"""Engine tests against a small made-up schedule (tests/data/test-schedule.yaml), so they test the
engine and the file format, independent of the real Swiss rules (see test_schedule_ch.py for those)."""
from datetime import date
from pathlib import Path

import pytest

from helvax import engine, schedule
from helvax.engine import Dose, Profile

TEST_SCHEDULE = schedule.load(Path(__file__).parent / 'data' / 'test-schedule.yaml')
D = date
TODAY = date(2026, 10, 15)


def status(antigen, birth, doses=(), today=TODAY, flags=(), immunities=(), s=TEST_SCHEDULE):
    profile = Profile(birth, frozenset(flags), frozenset(immunities))
    dose_list = [Dose(when, a, i) for i, (a, when) in enumerate(doses)]
    result = {r.antigen: r for r in engine.evaluate(profile, dose_list, s, today)}
    return result[antigen]


def doses_of(antigen, *dates):
    return [(antigen, d) for d in dates]


# (description, antigen, birth, doses, today, flags, expected status, expected next recommended date)
CASES = [
    # Infant series on time
    ('newborn: first dose in 2 months', 'hib', D(2026, 9, 1), [], D(2026, 9, 10), [], 'upcoming', D(2026, 11, 1)),
    ('first dose due on the day', 'hib', D(2026, 8, 15), [], D(2026, 10, 15), [], 'due', D(2026, 10, 15)),
    ('first dose late but within grace', 'hib', D(2026, 8, 1), [], D(2026, 10, 15), [], 'due', D(2026, 10, 1)),
    ('after 2 doses the third waits for 12 months', 'hib', D(2026, 1, 1),
     doses_of('hib', D(2026, 3, 1), D(2026, 5, 1)), D(2026, 10, 15), [], 'upcoming', D(2027, 1, 1)),
    ('series complete', 'hib', D(2025, 1, 1),
     doses_of('hib', D(2025, 3, 1), D(2025, 5, 1), D(2026, 1, 5)), D(2026, 10, 15), [], 'complete', None),
    # Missed doses
    ('second dose missed: overdue', 'hib', D(2026, 1, 1), doses_of('hib', D(2026, 3, 1)), D(2026, 10, 15), [],
     'overdue', D(2026, 5, 1)),
    ('third dose after 13 months: overdue', 'hib', D(2025, 1, 1), doses_of('hib', D(2025, 3, 1), D(2025, 5, 1)),
     D(2026, 3, 1), [], 'overdue', D(2026, 1, 1)),
    # Late start: catch-up series chosen by age, first dose at once and already overdue
    ('late start at 7 months', 'hib', D(2026, 3, 1), [], D(2026, 10, 15), [], 'overdue', D(2026, 10, 15)),
    ('catch-up only until 5 years', 'hib', D(2020, 1, 1), [], D(2026, 10, 15), [], 'not_recommended', None),
    ('catch-up series continues after its first dose', 'hib', D(2026, 3, 1), doses_of('hib', D(2026, 10, 1)),
     D(2026, 10, 15), [], 'upcoming', D(2026, 12, 1)),
    # Adult boosters: interval by age at the last dose
    ('childhood done, next at 25', 'dt', D(2010, 1, 1),
     doses_of('dt', D(2010, 3, 1), D(2010, 5, 1), D(2011, 1, 1)), D(2026, 10, 15), [], 'complete', D(2035, 1, 1)),
    ('dose at 30, next 20 years later', 'dt', D(1980, 6, 1),
     doses_of('dt', D(1980, 8, 1), D(1980, 10, 1), D(1981, 6, 1), D(2010, 6, 1)), D(2026, 10, 15), [],
     'complete', D(2030, 6, 1)),
    ('booster within the horizon is upcoming', 'dt', D(1980, 6, 1),
     doses_of('dt', D(1980, 8, 1), D(1980, 10, 1), D(1981, 6, 1), D(2006, 12, 1)), D(2026, 10, 15), [],
     'upcoming', D(2026, 12, 1)),
    ('booster due, within a year of grace', 'dt', D(1980, 6, 1),
     doses_of('dt', D(1980, 8, 1), D(1980, 10, 1), D(1981, 6, 1), D(2006, 6, 1)), D(2026, 10, 15), [],
     'due', D(2026, 6, 1)),
    ('booster more than a year late: overdue', 'dt', D(1950, 6, 1),
     doses_of('dt', D(1950, 8, 1), D(1950, 10, 1), D(1951, 6, 1), D(2015, 6, 1)), D(2026, 10, 15), [],
     'overdue', D(2025, 6, 1)),
    ('from 65 every 10 years', 'dt', D(1950, 6, 1),
     doses_of('dt', D(1950, 8, 1), D(1950, 10, 1), D(1951, 6, 1), D(2016, 6, 1)), D(2026, 10, 15), [],
     'due', D(2026, 6, 1)),
    ('unvaccinated adult: catch-up series', 'dt', D(1990, 1, 1), [], D(2026, 10, 15), [], 'overdue',
     D(2026, 10, 15)),
    # Supplementary: opt-in per profile, independent courses
    ('supplementary without opt-in', 'menb', D(2026, 1, 1), [], D(2026, 3, 1), [], 'not_recommended', None),
    ('supplementary with opt-in', 'menb', D(2026, 1, 1), [], D(2026, 3, 1), ['optin:menb'], 'upcoming',
     D(2026, 4, 1)),
    ('infant course done, teen course later', 'menb', D(2020, 1, 1),
     doses_of('menb', D(2020, 4, 1), D(2020, 6, 1), D(2021, 1, 10)), D(2026, 10, 15), ['optin:menb'],
     'complete', D(2031, 1, 1)),
    ('teen course independent of infant doses', 'menb', D(2012, 1, 1),
     doses_of('menb', D(2012, 4, 1), D(2012, 6, 1), D(2013, 1, 10)), D(2026, 10, 15), ['optin:menb'],
     'due', D(2023, 1, 1)),
    ('teen course: second dose a month later', 'menb', D(2012, 1, 1),
     doses_of('menb', D(2026, 10, 1)), D(2026, 10, 15), ['optin:menb'], 'upcoming', D(2026, 11, 1)),
    # Doses before the minimum age don't count
    ('early dose does not count', 'mmr', D(2026, 1, 1), doses_of('mmr', D(2026, 7, 15)), D(2026, 9, 20), [],
     'upcoming', D(2026, 10, 1)),
    ('born before 1964: not recommended', 'mmr', D(1960, 1, 1), [], D(2026, 10, 15), [], 'not_recommended', None),
    # Hard age limits
    ('rota started in time', 'rota', D(2026, 8, 1), doses_of('rota', D(2026, 10, 1)), D(2026, 10, 15),
     ['optin:rota'], 'upcoming', D(2026, 12, 1)),
    ('rota too old for the first dose', 'rota', D(2026, 5, 1), [], D(2026, 10, 15), ['optin:rota'],
     'not_recommended', None),
    ('rota second dose missed', 'rota', D(2026, 1, 1), doses_of('rota', D(2026, 3, 1)), D(2026, 10, 15),
     ['optin:rota'], 'not_recommended', None),
    # Condition flags
    ('tbe without flag', 'tbe', D(1990, 1, 1), [], D(2026, 10, 15), [], 'not_recommended', None),
    ('tbe with flag: due now', 'tbe', D(1990, 1, 1), [], D(2026, 10, 15), ['tick'], 'due', D(2026, 10, 15)),
    ('tbe with flag but too young', 'tbe', D(2025, 1, 1), [], D(2026, 10, 15), ['tick'], 'not_recommended',
     D(2028, 1, 1)),
    ('tbe booster after 10 years', 'tbe', D(1990, 1, 1),
     doses_of('tbe', D(2010, 3, 1), D(2010, 4, 1), D(2010, 10, 1)), D(2026, 10, 15), ['tick'], 'overdue',
     D(2020, 10, 1)),
    # Seasonal
    ('flu: in the window', 'flu', D(1950, 1, 1), [], D(2026, 10, 20), ['optin:flu'], 'due', D(2026, 10, 15)),
    ('flu: before the window, within the horizon', 'flu', D(1950, 1, 1), [], D(2026, 9, 1), ['optin:flu'],
     'upcoming', D(2026, 10, 15)),
    ('flu: covered this season', 'flu', D(1950, 1, 1), doses_of('flu', D(2026, 10, 1)), D(2026, 11, 1),
     ['optin:flu'], 'complete', D(2027, 10, 15)),
    ('flu: last season does not cover this one', 'flu', D(1950, 1, 1), doses_of('flu', D(2025, 11, 1)),
     D(2026, 10, 20), ['optin:flu'], 'due', D(2026, 10, 15)),
    ('flu: missed the window', 'flu', D(1950, 1, 1), [], D(2027, 2, 1), ['optin:flu'], 'overdue',
     D(2026, 10, 15)),
    ('flu: under 65', 'flu', D(1970, 1, 1), [], D(2026, 10, 20), ['optin:flu'], 'not_recommended',
     D(2035, 10, 15)),
    ('flu: turns 65 during the window', 'flu', D(1961, 11, 20), [], D(2026, 10, 20), ['optin:flu'],
     'upcoming', D(2026, 11, 20)),
    # Birth month
    ('rsv born in winter: first week', 'rsv', D(2026, 1, 10), [], D(2026, 1, 12), [], 'due', D(2026, 1, 10)),
    ('rsv born in winter: overdue after a week', 'rsv', D(2026, 1, 10), [], D(2026, 2, 1), [], 'overdue',
     D(2026, 1, 10)),
    ('rsv born in winter, missed until March: next October', 'rsv', D(2026, 1, 10), [], D(2026, 5, 1), [],
     'not_recommended', D(2026, 10, 1)),
    ('rsv born in summer: in October', 'rsv', D(2026, 6, 10), [], D(2026, 8, 1), [], 'upcoming',
     D(2026, 10, 1)),
    ('rsv born in summer: overdue after October', 'rsv', D(2026, 6, 10), [], D(2026, 11, 5), [], 'overdue',
     D(2026, 10, 1)),
    ('rsv given', 'rsv', D(2026, 6, 10), doses_of('rsv', D(2026, 10, 3)), D(2026, 11, 5), [], 'complete', None),
    ('rsv not needed with maternal vaccination', 'rsv', D(2026, 1, 10), [], D(2026, 1, 12), ['maternal'],
     'not_recommended', None),
    ('rsv after the first birthday', 'rsv', D(2025, 6, 10), [], D(2026, 10, 15), [], 'not_recommended', None),
    # Series by age at the first dose, with a category per series
    ('hpv at 12: two doses', 'hpv', D(2014, 1, 1), doses_of('hpv', D(2026, 3, 1)), D(2026, 9, 20), [], 'due',
     D(2026, 9, 1)),
    ('hpv two-dose series complete', 'hpv', D(2014, 1, 1), doses_of('hpv', D(2026, 3, 1), D(2026, 9, 1)),
     D(2026, 10, 15), [], 'complete', None),
    ('hpv started at 16: three doses', 'hpv', D(2010, 1, 1), doses_of('hpv', D(2026, 3, 1), D(2026, 5, 1)),
     D(2026, 9, 20), [], 'due', D(2026, 9, 1)),
    ('hpv: third dose late', 'hpv', D(2010, 1, 1), doses_of('hpv', D(2026, 3, 1), D(2026, 5, 1)),
     D(2026, 10, 15), [], 'overdue', D(2026, 9, 1)),
    ('hpv at 22: supplementary, needs opt-in', 'hpv', D(2004, 1, 1), [], D(2026, 10, 15), [], 'not_recommended',
     None),
    ('hpv at 22 with opt-in', 'hpv', D(2004, 1, 1), [], D(2026, 10, 15), ['optin:hpv'], 'due', D(2026, 10, 15)),
    ('hpv before 11', 'hpv', D(2020, 1, 1), [], D(2026, 10, 15), [], 'not_recommended', D(2031, 1, 1)),
]


@pytest.mark.parametrize('desc, antigen, birth, doses, today, flags, expected, next_date', CASES,
                         ids=[c[0] for c in CASES])
def test_status(desc, antigen, birth, doses, today, flags, expected, next_date):
    r = status(antigen, birth, doses, today, flags)
    assert r.status == expected, r
    assert (r.next.recommended if r.next else None) == next_date, r


def test_overdue_dates_and_window():
    r = status('hib', D(2026, 1, 1), doses_of('hib', D(2026, 3, 1)), D(2026, 4, 1))
    assert (r.status, r.next.number, r.next.earliest, r.next.recommended, r.next.latest) == \
        ('upcoming', 2, D(2026, 3, 29), D(2026, 5, 1), D(2026, 6, 1))
    # The end of a window is the last day before its upper age
    r = status('menb', D(2020, 1, 1), doses_of('menb', D(2020, 4, 1), D(2020, 6, 1)), D(2021, 1, 15),
               flags=['optin:menb'])
    assert (r.status, r.next.recommended, r.next.latest) == ('due', D(2021, 1, 1), D(2021, 7, 31))


def test_catch_up_plan_respects_minimum_intervals():
    r = status('hib', D(2026, 3, 1), [], D(2026, 10, 15))
    assert [(d.number, d.recommended) for d in r.plan] == [(1, D(2026, 10, 15)), (2, D(2026, 12, 15)),
                                                         (3, D(2027, 6, 15))]
    assert r.plan[0].asap


def test_plan_starts_today_for_overdue_doses():
    r = status('hib', D(2026, 1, 1), doses_of('hib', D(2026, 3, 1)), D(2026, 10, 15))
    assert r.status == 'overdue'
    # Dose 2 was due in May; dose 3 can only come 6 months after it is given (today at the earliest)
    assert [(d.number, d.recommended) for d in r.plan] == [(2, D(2026, 5, 1)), (3, D(2027, 4, 15))]


def test_plan_ends_at_the_course_age_limit():
    r = status('hib', D(2021, 9, 1), [], D(2026, 6, 1))
    assert [(d.number, d.recommended) for d in r.plan] == [(1, D(2026, 6, 1)), (2, D(2026, 8, 1))]


def test_combination_product_updates_every_antigen():
    """A combination vaccine is one Dose per antigen; each antigen's status moves on."""
    birth = D(2026, 1, 1)
    entry = [('hib', D(2026, 3, 1)), ('dt', D(2026, 3, 1)), ('pol', D(2026, 3, 1))]
    before = {a: status(a, birth, [], D(2026, 3, 2)) for a in ('hib', 'dt', 'pol')}
    after = {a: status(a, birth, entry, D(2026, 3, 2)) for a in ('hib', 'dt', 'pol')}
    for a in ('hib', 'dt'):
        assert before[a].next.number == 1 and after[a].next.number == 2
        assert after[a].status == 'upcoming'
    assert after['pol'].next.recommended == D(2030, 1, 1)


def test_dose_warnings():
    r = status('hib', D(2026, 1, 1), doses_of('hib', D(2026, 2, 1), D(2026, 2, 15)), D(2026, 3, 1))
    assert r.warnings == ('Dosis 1 vom 01.02.2026 wurde jünger als 6 Wochen gegeben.',
                          'Dosis 2 vom 15.02.2026: weniger als 4 Wochen nach der vorherigen Dosis.')
    r = status('mmr', D(2026, 1, 1), doses_of('mmr', D(2026, 7, 15)), D(2026, 9, 1))
    assert r.doses[0].number is None
    assert r.warnings == ('Zählt nicht: jünger als 9 Monate gegeben.',)


def test_immunity_and_reasons():
    r = status('mmr', D(2000, 1, 1), immunities=['mmr'])
    assert (r.status, r.reason) == ('not_recommended', 'Immun (durchgemachte Krankheit oder Serologie)')
    assert status('mmr', D(1960, 1, 1)).reason == 'Vor 1964 geboren: gilt als immun'
    assert status('tbe', D(1990, 1, 1)).reason == 'Nur bei: Zeckengebiet'
    assert status('rsv', D(2026, 10, 1), flags=['maternal']).reason == 'Nicht nötig: Mutter geimpft'
    assert status('menb', D(2026, 1, 1)).reason == 'Ergänzende Impfung, im Profil nicht gewählt'
    assert status('hib', D(2020, 1, 1)).reason == 'Nur bis 5 Jahre empfohlen'


def test_missed_window_needs_the_doctor():
    r = status('pol', D(2015, 1, 1), doses_of('pol', D(2015, 3, 1)), D(2026, 10, 15))
    assert (r.status, r.needs_doctor, r.reason) == ('overdue', True, 'Nachholschema mit Ärztin/Arzt festlegen')
    r = status('pol', D(2020, 1, 1), doses_of('pol', D(2020, 3, 1)), D(2026, 10, 15))
    assert (r.status, r.needs_doctor) == ('due', False)
    assert r.review  # the rule is marked for review


def test_category_of_the_series_is_reported():
    assert status('hpv', D(2004, 1, 1), flags=['optin:hpv']).category == 'supplementary'
    assert status('hpv', D(2010, 1, 1)).category == 'basic'


def test_number_doses():
    profile = Profile(D(2026, 1, 1))
    doses = [Dose(D(2026, 7, 15), 'mmr', 'a'), Dose(D(2026, 10, 1), 'mmr', 'b'), Dose(D(2027, 1, 1), 'mmr', 'c'),
             Dose(D(2026, 3, 1), 'hib', 'a')]
    assert engine.number_doses(profile, doses, TEST_SCHEDULE) == {
        ('a', 'mmr'): None, ('b', 'mmr'): 1, ('c', 'mmr'): 2, ('a', 'hib'): 1}


def test_evaluation_is_deterministic_and_ordered():
    profile = Profile(D(2026, 1, 1), frozenset({'optin:menb'}))
    doses = [Dose(D(2026, 3, 1), 'hib'), Dose(D(2026, 3, 1), 'dt')]
    first = engine.evaluate(profile, doses, TEST_SCHEDULE, D(2026, 10, 15))
    assert first == engine.evaluate(profile, list(reversed(doses)), TEST_SCHEDULE, D(2026, 10, 15))
    assert [r.antigen for r in first] == list(TEST_SCHEDULE.rules)


def test_summary_orders_by_urgency():
    profile = Profile(D(2026, 1, 1))
    statuses = engine.evaluate(profile, [Dose(D(2026, 3, 1), 'hib')], TEST_SCHEDULE, D(2026, 10, 15))
    actionable = engine.summary(statuses)
    assert [(s.antigen, s.status) for s in actionable] == [
        ('dt', 'overdue'), ('pol', 'overdue'), ('hib', 'overdue'),  # oldest recommended date first
        ('mmr', 'due'), ('rsv', 'due')]
