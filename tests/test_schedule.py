"""The schedule file format: strict validation, and results that follow the file."""
from datetime import date
from pathlib import Path

import pytest
import yaml

from helvax import engine, schedule
from helvax.engine import Profile

TEST_FILE = Path(__file__).parent / 'data' / 'test-schedule.yaml'


def data():
    return yaml.safe_load(TEST_FILE.read_text(encoding='utf-8'))


def rule(d, antigen):
    return next(r for r in d['rules'] if r['antigen'] == antigen)


def test_header_and_generated_optin_flags():
    s = schedule.load(TEST_FILE)
    assert (s.id, s.title, s.published, s.placeholder) == ('test-1', 'Testplan', date(2026, 1, 1), True)
    assert s.version == 'test-1 (Stand 01.01.2026)'
    assert s.flags['tick'].kind == 'condition'
    # Every rule with supplementary parts gets an opt-in flag; basic rules don't
    assert {k for k, f in s.flags.items() if f.kind == 'optin'} == {'optin:menb', 'optin:rota', 'optin:flu',
                                                                     'optin:hpv'}
    assert s.flags['optin:menb'].label == 'MenB-artig (ergänzende Impfung)'


def mutate(change):
    d = data()
    change(d)
    return d


@pytest.mark.parametrize('change, message', [
    (lambda d: d['schedule'].update(extra=1), 'schedule: unknown key(s) extra'),
    (lambda d: d['schedule'].pop('source_url'), 'schedule: missing source_url'),
    (lambda d: rule(d, 'hib').update(antigen='xyz'), "unknown antigen 'xyz'"),
    (lambda d: rule(d, 'hib').update(category='optional'), "unknown category 'optional'"),
    (lambda d: rule(d, 'tbe').update(requires_flag='nope'), "requires_flag 'nope' is not declared"),
    (lambda d: rule(d, 'hib')['courses'][0]['series'][0]['doses'][0].update(at='2 months'),
     "invalid duration '2 months'"),
    (lambda d: rule(d, 'hib')['courses'][0]['series'][0]['doses'][0].update(after='1m'),
     'use only one of at, window, after'),
    (lambda d: rule(d, 'hib')['courses'][0]['series'][0]['doses'][0].update(if_missed='ignore'),
     'if_missed must be one of'),
    (lambda d: rule(d, 'hib')['courses'][0].update(ages=[0]), 'expected [from, until]'),
    (lambda d: rule(d, 'hib')['courses'][0].update(birth_month=[{'birth_months': [1], 'at': '0d'}]),
     'either series or birth_month'),
    (lambda d: rule(d, 'hib')['courses'][0]['series'][0].update(typo=True), 'unknown key(s) typo'),
    (lambda d: rule(d, 'flu')['seasonal'].update(recommend=['10-15', '13-01']), "expected MM-DD, got '13-01'"),
    (lambda d: rule(d, 'dt')['recurring'][0].update(interval='10y'), 'use one of interval, next_at_age'),
    (lambda d: d['rules'].append(dict(rule(d, 'hib'))), "a second rule for 'hib'"),
    (lambda d: d['flags'].update({'optin:x': {'label': 'x'}}), 'opt-in flags are generated'),
    (lambda d: rule(d, 'flu').pop('seasonal'), 'a rule needs courses or seasonal'),
    (lambda d: rule(d, 'pnc')['courses'][0]['series'][0]['doses'][0].update(products='high'),
     'products: expected a list of product tags'),
    (lambda d: rule(d, 'acw')['courses'][0]['series'][0]['doses'][1].update(if_previous_product=[]),
     'if_previous_product: expected a list of product tags'),
])
def test_invalid_files_are_rejected(change, message):
    with pytest.raises(schedule.ScheduleError, match=message.replace('(', r'\(').replace(')', r'\)')
                       .replace('[', r'\[').replace(']', r'\]')):
        schedule.parse(mutate(change))


def test_product_tags_of_the_rules():
    assert schedule.load(TEST_FILE).product_tags == {'two', 'high'}


def test_error_names_the_file(tmp_path):
    path = tmp_path / 'broken.yaml'
    path.write_text('schedule: [', encoding='utf-8')
    with pytest.raises(schedule.ScheduleError, match='broken.yaml'):
        schedule.load(path)


def test_changing_the_file_changes_the_result():
    """The rules are data: moving the first dose from 2 to 3 months moves the recommendation."""
    profile = Profile(date(2026, 9, 1))
    today = date(2026, 9, 10)
    before = engine.evaluate_rule(schedule.parse(data()).rules['hib'], profile, [], schedule.parse(data()), today)
    assert before.next.recommended == date(2026, 11, 1)

    changed = schedule.parse(mutate(lambda d: rule(d, 'hib')['courses'][0]['series'][0]['doses'][0]
                                    .update(at='3m')))
    after = engine.evaluate_rule(changed.rules['hib'], profile, [], changed, today)
    assert after.next.recommended == date(2026, 12, 1)
